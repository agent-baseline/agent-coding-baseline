"""Netzwerk-Protokoll der Sandbox: Was `sbx policy log` meldet, kommt als Ereignis ins Protokoll."""

import json

import pytest

from kreislauf.checks import parse_policy_log
from kreislauf.cycle import Cycle, Result

RAW = json.dumps({
    "allowed": [
        {"sandbox_id": "demo-j1-k1", "host": "pypi.org:443", "proxy_type": "forward",
         "count": 4, "first_seen": "2026-11-21T11:18:45.146954+01:00",
         "last_seen": "2026-11-21T11:18:59.325605+01:00"},
        {"sandbox_id": "demo-j1-k1", "host": "api.anthropic.com:443", "proxy_type": "forward",
         "count": 9, "first_seen": "2026-11-21T11:18:40+01:00", "last_seen": "2026-11-21T11:19:10+01:00"},
        {"sandbox_id": "andere", "host": "example.org:443", "proxy_type": "forward", "count": 1},
    ],
    "blocked": [
        {"sandbox_id": "demo-j1-k1", "host": "evil.example:443", "proxy_type": "forward",
         "rule": "default-deny-all", "reason": "no rule", "count": 2,
         "first_seen": "2026-11-21T11:19:00+01:00", "last_seen": "2026-11-21T11:19:01+01:00"},
    ],
})


def test_only_entries_of_the_sandbox_sorted_by_host():
    net = parse_policy_log(RAW, "demo-j1-k1")
    assert [e["host"] for e in net["allowed"]] == ["api.anthropic.com:443", "pypi.org:443"]
    assert [e["host"] for e in net["blocked"]] == ["evil.example:443"]


def test_entries_keep_count_times_and_rule_but_not_the_sandbox_id():
    net = parse_policy_log(RAW, "demo-j1-k1")
    assert net["allowed"][1] == {
        "host": "pypi.org:443", "count": 4, "proxy_type": "forward",
        "first_seen": "2026-11-21T11:18:45.146954+01:00",
        "last_seen": "2026-11-21T11:18:59.325605+01:00",
    }
    assert net["blocked"][0]["rule"] == "default-deny-all"
    assert net["blocked"][0]["reason"] == "no rule"


def test_empty_log_is_valid_and_means_no_traffic():
    assert parse_policy_log('{"allowed": [], "blocked": []}', "x") == {"allowed": [], "blocked": []}
    assert parse_policy_log('{"allowed": null}', "x") == {"allowed": [], "blocked": []}


@pytest.mark.parametrize("text", ["", "kein json", "[]", '{"allowed": 3, "blocked": []}',
                                  '{"allowed": ["x"], "blocked": []}'])
def test_unreadable_log_raises(text):
    with pytest.raises(ValueError):
        parse_policy_log(text, "x")


class FakeSh:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, argv, cwd=None, timeout=None):
        self.calls.append([str(a) for a in argv])
        return self.result


@pytest.fixture
def project(tmp_path):
    project = tmp_path / "demo"
    (project / ".baseline").mkdir(parents=True)
    (project / ".baseline" / "project.json").write_text(json.dumps({"project": "demo", "tier": "internal"}))
    return project


def test_observation_lands_in_the_log_with_sandbox_and_column(project):
    sh = FakeSh(Result(0, RAW, ""))
    cycle = Cycle(project, "j1", sh=sh)
    cycle.observe_network("K1", "implementation", "demo-j1-k1")
    assert sh.calls == [["sbx", "policy", "log", "demo-j1-k1", "--json", "--limit", "0"]]
    (event,) = cycle.log.read()
    assert event["event"] == "network_observed"
    assert (event["card"], event["column"], event["actor"], event["sandbox"]) == (
        "K1", "implementation", "script", "demo-j1-k1")
    ev = event["evidence"]
    assert ev["ok"] is True
    assert (ev["allowed_hosts"], ev["blocked_hosts"], ev["blocked_requests"]) == (2, 1, 2)
    assert [e["host"] for e in ev["blocked"]] == ["evil.example:443"]
    assert ev["cumulative"] is True


@pytest.mark.parametrize("result", [Result(1, "", "error: not signed in to Docker"), Result(0, "kaputt", "")])
def test_missing_log_is_recorded_and_does_not_stop_the_cycle(project, result):
    cycle = Cycle(project, "j1", sh=FakeSh(result))
    cycle.observe_network("K1", "gate", "demo-j1-k1-gate-1")
    (event,) = cycle.log.read()
    assert event["event"] == "network_observed"
    assert event["evidence"]["ok"] is False
    assert event["evidence"]["exit_code"] == result.rc
    assert "allowed" not in event["evidence"]


BAD_COUNT = json.dumps({"allowed": [{"sandbox_id": "demo-j1-k1", "host": "pypi.org:443", "count": "viele"}],
                        "blocked": []})


def test_count_that_is_not_a_number_makes_the_log_unreadable():
    with pytest.raises(ValueError):
        parse_policy_log(BAD_COUNT, "demo-j1-k1")


def test_counts_come_back_as_numbers():
    raw = json.dumps({"allowed": [{"sandbox_id": "s", "host": "a:443", "count": "3"}], "blocked": []})
    assert parse_policy_log(raw, "s")["allowed"][0]["count"] == 3


def test_bad_count_is_recorded_and_does_not_stop_the_cycle(project):
    cycle = Cycle(project, "j1", sh=FakeSh(Result(0, BAD_COUNT, "")))
    cycle.observe_network("K1", "gate", "demo-j1-k1")
    (event,) = cycle.log.read()
    assert event["evidence"]["ok"] is False
    assert "allowed" not in event["evidence"]
