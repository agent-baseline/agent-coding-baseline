"""`network_observed` im ganzen Ablauf: nach jedem Lauf in einer Sandbox eine Beobachtung,
ohne Einfluss auf Zustand, Halt und Fortsetzung der Karte."""

import json

import pytest

from helpers import GATE_LEAK, Sandboxes, halt, make_cycle, moves, release
from kreislauf.cycle import Result, Stop


@pytest.fixture
def c(tmp_path):
    cycle = make_cycle(tmp_path)
    release(cycle)
    return cycle


@pytest.fixture
def box(c):
    box = Sandboxes(c)

    def policy_log(argv):  # sbx policy log <sandbox> --json --limit 0
        sandbox = argv[3]
        return Result(0, json.dumps({
            "allowed": [{"sandbox_id": sandbox, "host": "pypi.org:443", "count": 2},
                        {"sandbox_id": "andere", "host": "example.org:443", "count": 1}],
            "blocked": [{"sandbox_id": sandbox, "host": "evil.example:443", "count": 1, "rule": "default-deny-all"}],
        }), "")

    c.sh.on(("sbx", "policy", "log"), policy_log)
    return box


def observed(cycle):
    return [e for e in cycle.events("K1", "network_observed")]


def test_every_sandbox_run_is_followed_by_an_observation_of_that_sandbox(c, box):
    c.run("K1")
    seen = [(e["column"], e["sandbox"]) for e in observed(c)]
    assert seen == [
        ("implementation", "demo-j1-k1"),          # Agentenlauf in der Arbeits-Sandbox
        ("implementation", "demo-j1-k1-pruef-1"),  # Test-Commit
        ("implementation", "demo-j1-k1-pruef-2"),  # Stand, in eigener Sandbox
        ("gate", "demo-j1-k1-gate-1"),
    ]
    for event in observed(c):
        ev = event["evidence"]
        assert ev["ok"] is True and ev["allowed_hosts"] == 1 and ev["blocked_hosts"] == 1
        assert [e["host"] for e in ev["allowed"]] == ["pypi.org:443"]  # nur die eigene Sandbox
    assert moves(c) == ["decomposition", "implementation", "gate", "approval"]


def test_a_blocked_target_is_an_observation_and_does_not_hold_the_card(c, box):
    c.run("K1")
    assert all(e["evidence"]["blocked_requests"] == 1 for e in observed(c))
    assert c.column("K1") == "approval" and halt(c) is None


def test_a_failed_agent_run_is_observed_before_the_card_halts(c, box):
    box.agent = [Result(1, "", "boom")]
    with pytest.raises(Stop):
        c.run("K1")
    kinds = [e["event"] for e in c.events("K1")]
    assert kinds.index("network_observed") < kinds.index("card_halted")
    assert halt(c)["cause"] == "agent_failed"
    c.resume("K1")
    assert c.column("K1") == "approval"
    assert [e["sandbox"] for e in observed(c)][:2] == ["demo-j1-k1", "demo-j1-k1"]  # beide Agentenläufe


def test_every_rework_round_is_observed_again(c, box):
    box.gate = [GATE_LEAK]
    c.run("K1")
    sandboxes = [e["sandbox"] for e in observed(c)]
    assert sandboxes.count("demo-j1-k1") == 2                       # Auftrag und Nachbesserung
    assert len([s for s in sandboxes if "-pruef-" in s]) == 4       # je Prüfung zwei Commits
    assert len([s for s in sandboxes if "-gate-" in s]) == 2


def test_a_missing_policy_log_is_recorded_and_the_card_goes_on(c, box):
    c.sh.on(("sbx", "policy", "log"), Result(1, "", "error: not signed in to Docker"))
    c.run("K1")
    assert len(observed(c)) == 4 and all(e["evidence"]["ok"] is False for e in observed(c))
    assert c.column("K1") == "approval"
