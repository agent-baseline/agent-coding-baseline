"""Ganze Läufe mit gespielten Sandboxes: Jeder Abbruch hinterlässt einen Halt im
Protokoll, und `resume` macht an dem Schritt weiter, den der Halt nennt."""

import json
import os
import subprocess

import pytest

import kreislauf.cycle as cycle_module
from helpers import (CARD, CLEAN, GATE_LEAK, Sandboxes, commit_on_branch, git, halt, hold_lock, killed,
                     lock_is_free, make_cycle, moves, rebase_branch, release, set_card)
from kreislauf.cycle import Halt, Result, Stop
from kreislauf.machine import TRANSITIONS


@pytest.fixture
def c(tmp_path):
    cycle = make_cycle(tmp_path)
    release(cycle)
    return cycle


@pytest.fixture
def box(c):
    return Sandboxes(c)


def conditions(cycle):
    return {e["evidence"]["condition"]: e["evidence"] for e in cycle.events("K1", "condition_checked")}


def kinds(cycle):
    return [e["event"] for e in cycle.events("K1")]


def assert_only_table_transitions(cycle):
    path = [None] + moves(cycle)
    for step in zip(path, path[1:]):
        assert step in TRANSITIONS, step


# ---------- Ohne Halt


def test_a_run_without_findings_goes_through_to_the_merge(c, box):
    c.run("K1")
    assert moves(c) == ["decomposition", "implementation", "gate", "approval"]
    assert halt(c) is None
    assert len(box.agent_calls) == 1 and box.test_runs == 1 and box.gate_runs == 1
    assert lock_is_free(c)
    c.decide("K1", None, "approved", "ja")
    c.merge("K1")
    assert moves(c)[-1] == "merged"
    assert_only_table_transitions(c)


def test_a_gate_finding_returns_the_card_once_and_then_passes(c, box):
    box.gate = [GATE_LEAK]
    c.run("K1")
    assert moves(c) == ["decomposition", "implementation", "gate", "implementation", "gate", "approval"]
    assert c.state("K1")["returns"] == {"gate": 1, "checks": 0}
    first, rework = box.agent_calls
    assert not first["resume"]
    assert rework["resume"] and rework["model"] == CARD["model"]  # derselbe Umsetzer, Sitzung fortgesetzt
    assert "Nachbesserung" in rework["prompt"]
    assert_only_table_transitions(c)


def test_a_failed_check_from_outside_returns_the_card_within_implementation(c, box):
    box.tests = [(0, 0)]  # die Tests des Test-Commits scheitern ohne die Umsetzung nicht
    c.run("K1")
    assert moves(c) == ["decomposition", "implementation", "implementation", "gate", "approval"]
    assert c.state("K1")["returns"] == {"gate": 0, "checks": 1}
    assert box.gate_runs == 1  # das Gate läuft erst nach bestandener Prüfung
    assert_only_table_transitions(c)


# ---------- Halt und Fortsetzung je Schritt


@pytest.mark.parametrize("result, cause", [
    (Result(1, "", "boom"), "agent_failed"),
    (Result(124, "", "Zeitlimit nach 2400 s"), "agent_timeout"),
    (Result(1, "", "usage limit reached, resets at 5pm"), "limit_reached"),
])
def test_a_failed_agent_run_halts_and_resume_runs_it_again(c, box, result, cause):
    box.agent = [result]
    with pytest.raises(Stop):
        c.run("K1")
    assert c.column("K1") == "implementation"
    assert halt(c)["cause"] == cause and halt(c)["step"] == "agent" and halt(c)["waiting_for"] == "coordinator"
    assert ("limit_reached" in kinds(c)) == (cause == "limit_reached")

    c.resume("K1")
    assert moves(c) == ["decomposition", "implementation", "gate", "approval"]
    assert halt(c) is None and "card_resumed" in kinds(c)
    again, = {call["prompt"] for call in box.agent_calls}  # derselbe Auftrag, zweimal
    assert len(box.agent_calls) == 2 and "Arbeitspaket K1" in again


def test_run_does_not_start_a_halted_card_again(c, box):
    box.agent = [Result(1, "", "boom")]
    with pytest.raises(Stop):
        c.run("K1")
    with pytest.raises(Stop):
        c.run("K1")
    assert conditions(c)["command_allowed"]["ok"] is False
    assert len(box.agent_calls) == 1
    assert halt(c)["cause"] == "agent_failed"  # der Halt steht weiter


def test_a_failed_fetch_halts_and_resume_does_not_run_the_agent_again(c, box):
    box.fetch = Result(1, "", "fatal: no remote")
    with pytest.raises(Stop):
        c.run("K1")
    assert halt(c)["cause"] == "fetch_failed" and halt(c)["step"] == "verify"
    box.fetch = Result(0, "", "")
    c.resume("K1")
    assert c.column("K1") == "approval"
    assert len(box.agent_calls) == 1


def test_an_incomplete_gate_halts_in_the_gate_and_resume_runs_only_the_gate(c, box):
    without_report = {k: v for k, v in CLEAN.items() if k != "osv.json"}
    box.gate = [({"gitleaks": 0, "gitleaks-history": 0, "gitleaks-messages": 0, "osv-scanner": 1, "syft": 0}, without_report)]
    with pytest.raises(Stop):
        c.run("K1")
    assert c.column("K1") == "gate"
    assert halt(c)["cause"] == "gate_incomplete" and halt(c)["step"] == "gate"

    c.resume("K1")
    assert moves(c) == ["decomposition", "implementation", "gate", "approval"]  # kein zweiter Wechsel ins Gate
    assert len(box.agent_calls) == 1 and box.test_runs == 1 and box.gate_runs == 2
    assert c._gate_green("K1", c.state("K1")["head"])


def test_an_unexpected_error_halts_at_the_running_step(c, box):
    box.tests = [RuntimeError("Prüf-Sandbox kaputt")]
    with pytest.raises(Stop):
        c.run("K1")
    assert halt(c)["cause"] == "error" and halt(c)["step"] == "verify"
    assert "RuntimeError" in c.events("K1", "card_halted")[-1]["reason"]
    c.resume("K1")
    assert c.column("K1") == "approval"
    assert len(box.agent_calls) == 1


def test_an_unexpected_error_before_the_start_halts_in_decomposition(c, box):
    def boom(argv):
        raise RuntimeError("git kaputt")

    c.sh.on(("git", "status"), boom)
    with pytest.raises(Stop):
        c.run("K1")
    assert c.column("K1") == "decomposition"
    assert halt(c)["cause"] == "error" and halt(c)["step"] == "start"
    assert not c.sh.called("sbx")

    c.sh.on(("git", "status"), lambda argv: Result(0, "", ""))
    c.resume("K1")
    assert c.column("K1") == "approval"


def test_an_interrupt_is_logged_as_a_halt_and_passed_on(c, box):
    box.agent = [KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):
        c.run("K1")
    assert halt(c)["cause"] == "interrupted" and halt(c)["step"] == "agent"
    assert lock_is_free(c)
    c.resume("K1")
    assert c.column("K1") == "approval"
    # Der abgebrochene Lauf zählt als Runde: Der nächste bekommt eine neue Nummer und eine eigene Ausgabedatei.
    rounds = [e["evidence"]["round"] for e in c.events("K1", "run_started") if e.get("actor") == "implementer"]
    assert rounds == [1, 2]
    assert (c.run_dir / "K1" / "run-2.stdout.json").exists()


@pytest.mark.parametrize("killed_in, step, agent_runs", [("agent", "agent", 2), ("tests", "verify", 1)])
def test_resume_picks_up_a_killed_run_that_left_no_halt(c, box, killed_in, step, agent_runs):
    """Ein abgeschossener Prozess schreibt nichts mehr: Die Karte steht mitten im
    Ablauf, ohne Halt im Protokoll, und seine Sperre liegt noch da."""
    getattr(box, killed_in).append(KeyboardInterrupt())
    with killed(c), pytest.raises(KeyboardInterrupt):
        c.run("K1")
    dead = subprocess.Popen(["true"])
    dead.wait()
    (c.run_dir / "K1" / "lock").write_text(str(dead.pid))
    assert c.column("K1") == "implementation" and halt(c) is None

    c.resume("K1")
    recovered = c.events("K1", "card_halted")[-1]["evidence"]
    assert recovered["cause"] == "interrupted" and recovered["step"] == step
    assert c.column("K1") == "approval"
    assert len(box.agent_calls) == agent_runs


# ---------- Schleifengrenze: Die Karte wartet auf den Menschen


def at_the_loop_limit(c, box, findings=3):
    box.gate = [GATE_LEAK] * findings
    with pytest.raises(Stop):
        c.run("K1")


def test_the_loop_limit_halts_the_card_until_the_human_decides(c, box):
    at_the_loop_limit(c, box)
    assert c.column("K1") == "implementation"
    assert halt(c)["cause"] == "loop_limit" and halt(c)["waiting_for"] == "human"
    assert c.state("K1")["returns"] == {"gate": 3, "checks": 0}
    assert c.state("K1")["step"] == halt(c)["step"] == "agent"  # Arbeitsstand und Protokoll nennen denselben Schritt
    task, first, second = box.agent_calls
    assert first["resume"] and first["model"] == CARD["model"]
    assert not second["resume"] and second["model"] == cycle_module.ESCALATION["model"]  # frisch, stärker

    with pytest.raises(Stop):  # ohne Entscheidung des Menschen geht es nicht weiter
        c.resume("K1")
    assert conditions(c)["human_decision_after_halt"]["ok"] is False
    assert halt(c)["cause"] == "loop_limit" and len(box.agent_calls) == 3

    c.decide("K1", None, "approved", "noch eine Runde")
    c.resume("K1")
    assert c.column("K1") == "approval"
    assert len(box.agent_calls) == 4 and box.agent_calls[-1]["model"] == cycle_module.ESCALATION["model"]
    assert_only_table_transitions(c)


def test_the_approval_at_the_loop_limit_is_good_for_one_more_round(c, box):
    at_the_loop_limit(c, box, findings=4)
    c.decide("K1", None, "approved", "noch eine Runde")
    with pytest.raises(Stop):
        c.resume("K1")
    assert halt(c)["cause"] == "loop_limit"
    assert c.state("K1")["returns"]["gate"] == 4
    with pytest.raises(Stop):  # die alte Zustimmung gilt für den neuen Halt nicht
        c.resume("K1")
    assert len(box.agent_calls) == 4


def test_a_decision_of_the_coordinator_does_not_lift_the_loop_limit(c, box):
    at_the_loop_limit(c, box)
    c.decide("K1", None, "approved", "ich finde, das passt", actor="coordinator")
    with pytest.raises(Stop):
        c.resume("K1")
    assert len(box.agent_calls) == 3


def test_a_rejected_halted_card_goes_to_parked(c, box):
    at_the_loop_limit(c, box)
    c.decide("K1", None, "rejected", "falsch geschnitten")
    assert c.column("K1") == "parked" and halt(c) is None
    with pytest.raises(Stop):
        c.resume("K1")
    assert_only_table_transitions(c)


def test_the_human_cannot_wave_through_a_halt_that_is_not_theirs(c, box):
    box.agent = [Result(1, "", "boom")]
    with pytest.raises(Stop):
        c.run("K1")
    with pytest.raises(Stop):
        c.decide("K1", None, "approved", "weiter")
    assert conditions(c)["card_waits_for_decision"]["ok"] is False
    assert [e for e in c.events("K1", "decision") if e["column"] == "implementation"] == []
    c.decide("K1", None, "rejected", "lassen wir")  # ablehnen geht immer
    assert c.column("K1") == "parked"


# ---------- Befehle gelten nur im passenden Zustand


def test_run_and_resume_refuse_a_card_that_waits_in_approval(c, box):
    c.run("K1")
    with pytest.raises(Stop):
        c.run("K1")
    with pytest.raises(Stop):
        c.resume("K1")
    assert c.column("K1") == "approval" and halt(c) is None
    assert len(box.agent_calls) == 1


def test_a_transition_outside_the_table_is_refused(c):
    with pytest.raises(Halt) as refused:
        c._move("K1", "merged")
    assert refused.value.cause == "illegal_transition"
    assert moves(c) == ["decomposition"]


def test_an_incomplete_card_does_not_start_and_leaves_no_halt(c, box):
    set_card(c, task="")
    with pytest.raises(Stop):
        c.run("K1")
    assert conditions(c)["card_valid"]["ok"] is False
    assert c.column("K1") == "decomposition" and halt(c) is None
    assert not c.sh.called("sbx")


@pytest.mark.parametrize("command", ["run", "resume", "merge", "decide", "cleanup"])
def test_a_card_is_worked_on_by_one_process_at_a_time(c, box, command):
    fd = hold_lock(c)  # ein anderer Prozess hält die Karte
    before = len(c.log.read())
    with pytest.raises(Stop) as refused:
        if command == "decide":
            c.decide("K1", None, "rejected", "nein")
        else:
            getattr(c, command)("K1")
    os.close(fd)
    assert "4711" in str(refused.value)
    assert len(c.log.read()) == before and not c.sh.called("sbx")
    assert lock_is_free(c)


def test_the_lock_file_of_a_dead_process_does_not_block(c, box):
    dead = subprocess.Popen(["true"])
    dead.wait()
    lock = c.run_dir / "K1" / "lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(dead.pid))
    c.run("K1")
    assert c.column("K1") == "approval"


# ---------- WIP


def test_a_halted_card_does_not_count_against_the_wip_limit(c, box, monkeypatch):
    monkeypatch.setattr(cycle_module, "WIP_LIMIT", 1)
    other = c.project / ".baseline" / "jobs" / "j1" / "cards" / "K2.json"
    other.write_text(json.dumps({**CARD, "id": "K2", "branch": "karte/k2"}))
    git(c.project, "add", "-A")
    git(c.project, "commit", "-q", "-m", "K2")
    # Der Branch der Karte entsteht in der Sandbox aus dem Stand beim Start; hier zieht er nach.
    rebase_branch(c)
    c.init()
    c.log.write("card_moved", card="K2", column="implementation", actor="script")

    with pytest.raises(Stop):
        c.run("K1")
    assert conditions(c)["wip_free"]["ok"] is False
    assert c.column("K1") == "decomposition"

    c.log.write("card_halted", card="K2", column="implementation", actor="script",
                evidence={"cause": "agent_failed", "step": "agent", "waiting_for": "coordinator"})
    c.run("K1")
    assert c.column("K1") == "approval"


def test_resume_checks_the_wip_limit_before_an_agent_run(c, box, monkeypatch):
    box.agent = [Result(1, "", "boom")]
    with pytest.raises(Stop):
        c.run("K1")
    monkeypatch.setattr(cycle_module, "WIP_LIMIT", 1)
    c.log.write("card_created", card="K2", column="decomposition", actor="coordinator")
    c.log.write("card_moved", card="K2", column="implementation", actor="script")
    (c.project / ".baseline" / "jobs" / "j1" / "cards" / "K2.json").write_text(json.dumps({**CARD, "id": "K2"}))

    with pytest.raises(Stop):
        c.resume("K1")
    assert conditions(c)["wip_free"]["ok"] is False
    assert halt(c)["cause"] == "agent_failed" and len(box.agent_calls) == 1


# ---------- Nachbesserungen mit echten Commits


def test_a_rework_commit_is_fetched_and_checked_again(c, box):
    box.gate = [GATE_LEAK]
    box.agent = [None, lambda: commit_on_branch(c, "src/a.py", "A = 1  # ohne Token\n")]
    first = git(c.project, "rev-parse", "karte/k1")
    c.run("K1")
    assert c.column("K1") == "approval"
    assert c.state("K1")["head"] == git(c.project, "rev-parse", "karte/k1") != first
    assert conditions(c)["tests_unchanged_since_test_commit"]["ok"] is True
    assert c._gate_green("K1", c.state("K1")["head"]) and not c._gate_green("K1", first)


def test_tests_changed_in_a_rework_stay_rejected_in_the_rounds_after_it(c, box):
    """Die Nachbesserung schwächt den Test ab; die Runde danach lässt ihn so stehen."""
    box.gate = [GATE_LEAK]
    box.agent = [
        None,
        lambda: commit_on_branch(c, "tests/test_a.py", "def test_a():\n    assert True\n"),
        lambda: commit_on_branch(c, "src/b.py", "B = 2\n"),
    ]
    with pytest.raises(Stop):
        c.run("K1")
    assert c.column("K1") == "implementation" and halt(c)["cause"] == "loop_limit"
    assert c.state("K1")["returns"] == {"gate": 1, "checks": 2}
    checked = [e["evidence"] for e in c.events("K1", "condition_checked")
               if e["evidence"]["condition"] == "tests_unchanged_since_test_commit"]
    assert [e["ok"] for e in checked] == [True, False, False]
    assert checked[-1]["touched"] == ["tests/test_a.py"]


def test_a_rework_outside_the_allowed_paths_is_returned(c, box):
    box.gate = [GATE_LEAK]
    box.agent = [None, lambda: commit_on_branch(c, "docs/notiz.md", "x\n"),
                 lambda: commit_on_branch(c, "docs/notiz.md", "")]
    with pytest.raises(Stop):  # die Datei bleibt im Branch, die Karte endet an der Schleifengrenze
        c.run("K1")
    offenders = [e["evidence"]["offenders"] for e in c.events("K1", "condition_checked")
                 if e["evidence"]["condition"] == "only_allowed_paths"]
    assert offenders == [[], ["docs/notiz.md"], ["docs/notiz.md"]]
    assert halt(c)["cause"] == "loop_limit"


# ---------- Abbruch an Stellen, die das Review gefunden hat


@pytest.mark.parametrize("kind, queue", [("pruef", "tests"), ("gate", "gate")])
def test_resume_after_an_abort_in_a_helper_sandbox_uses_a_new_name(c, box, kind, queue):
    getattr(box, queue).append(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        c.run("K1")
    c.resume("K1")
    assert c.column("K1") == "approval"
    created = [call[call.index("--name") + 1] for call in c.sh.calls if call[:2] == ["sbx", "create"]]
    helpers = [name for name in created if f"-{kind}-" in name]
    # Die abgebrochene Sandbox behält ihren Namen; jeder neue Versuch bekommt einen neuen.
    assert len(helpers) == len(set(helpers)) == {"pruef": 3, "gate": 2}[kind]
    assert helpers[0] == f"demo-j1-k1-{kind}-1"
    assert set(helpers) <= set(c.state("K1")["helper_sandboxes"])  # cleanup kennt beide


def test_a_kill_in_the_round_the_human_granted_is_an_interrupt_not_a_new_loop_limit(c, box):
    at_the_loop_limit(c, box)
    c.decide("K1", None, "approved", "noch eine Runde")
    box.tests.append(KeyboardInterrupt())  # der Agent lief durch, der Prozess stirbt in der Prüfung
    with killed(c), pytest.raises(KeyboardInterrupt):
        c.resume("K1")
    assert halt(c) is None and len(box.agent_calls) == 4

    c.resume("K1")  # ohne zweite Entscheidung des Menschen, ohne zweiten Agentenlauf
    recovered = c.events("K1", "card_halted")[-1]["evidence"]
    assert recovered["cause"] == "interrupted" and recovered["step"] == "verify"
    assert c.column("K1") == "approval" and len(box.agent_calls) == 4


def test_a_kill_between_the_last_return_and_its_halt_still_waits_for_the_human(c, box):
    box.gate = [GATE_LEAK] * 3
    with killed(c), pytest.raises(Stop):
        c.run("K1")
    assert halt(c) is None and c.column("K1") == "implementation"

    with pytest.raises(Stop):
        c.resume("K1")
    assert halt(c)["cause"] == "loop_limit" and halt(c)["waiting_for"] == "human"
    assert len(box.agent_calls) == 3
    c.decide("K1", None, "approved", "noch eine Runde")
    c.resume("K1")
    assert c.column("K1") == "approval" and len(box.agent_calls) == 4


def test_a_halt_names_a_step_that_runs_in_the_column_of_the_card(c, box):
    c.run("K1")  # die Karte steht in der Freigabe: dort läuft kein Schritt
    assert c._halt("K1", "error", "gate", "zu spät") is False
    assert halt(c) is None and "card_halted" not in kinds(c)


def test_an_error_before_run_is_allowed_does_not_overwrite_an_open_halt(c, box):
    at_the_loop_limit(c, box)

    def boom(card_id):
        raise RuntimeError("Karte nicht lesbar")

    c._card_checked = boom
    with pytest.raises(Stop):
        c.run("K1")
    assert halt(c)["cause"] == "loop_limit" and halt(c)["step"] == "agent"
    assert len(c.events("K1", "card_halted")) == 1


def test_a_card_with_an_unsafe_branch_does_not_start(c, box):
    set_card(c, branch="karte/k1; touch x", dry_run_fault="secret")
    with pytest.raises(Stop):
        c.run("K1", inject_fault=True)
    assert conditions(c)["card_valid"]["ok"] is False
    assert c.column("K1") == "decomposition" and halt(c) is None
    assert not c.sh.called("sbx")


# ---------- Eingebauter Fehler des Trockenlaufs


def test_the_dry_run_fault_gets_the_branch_as_an_argument(c, box):
    set_card(c, dry_run_fault="secret")
    rebase_branch(c)
    box.gate = [GATE_LEAK]
    c.run("K1", inject_fault=True)
    call = next(a for a in c.sh.calls
                if a[:2] == ["sbx", "exec"] and "-c" in a and "FAULT_TOKEN" in a[a.index("-c") + 1])
    script = call[call.index("-c") + 1]
    assert CARD["branch"] not in script and 'git checkout -q "$1"' in script
    assert call[call.index("-c") + 2:] == ["sh", CARD["branch"]]


def test_the_dry_run_fault_is_planted_once(c, box):
    set_card(c, dry_run_fault="secret")
    rebase_branch(c)
    box.gate = [GATE_LEAK]
    c.run("K1", inject_fault=True)
    assert box.faults == 1 and len(c.events("K1", "fault_injected")) == 1
    assert c.column("K1") == "approval" and c.state("K1")["returns"]["gate"] == 1


def test_without_the_flag_no_fault_is_planted(c, box):
    set_card(c, dry_run_fault="secret-every-round")
    rebase_branch(c)
    c.run("K1")
    assert box.faults == 0 and c.column("K1") == "approval"


def test_the_persistent_dry_run_fault_comes_back_after_every_rework_until_the_loop_limit(c, box):
    """Damit die Schleifengrenze an einer echten Karte erreichbar ist: Der Fehler kehrt
    nach jeder Nachbesserung zurück, das Gate weist jede Runde ab."""
    set_card(c, dry_run_fault="secret-every-round")
    rebase_branch(c)
    box.gate = [GATE_LEAK] * 4
    with pytest.raises(Stop):
        c.run("K1", inject_fault=True)
    assert box.faults == 3 and len(c.events("K1", "fault_injected")) == 3  # nach jedem der drei Agentenläufe
    assert halt(c)["cause"] == "loop_limit" and c.state("K1")["returns"]["gate"] == 3
    assert [call["model"] for call in box.agent_calls] == [CARD["model"], CARD["model"], cycle_module.ESCALATION["model"]]

    c.decide("K1", None, "approved", "noch eine Runde")
    with pytest.raises(Stop):
        c.resume("K1")
    assert box.faults == 4 and halt(c)["cause"] == "loop_limit"  # auch die Zusatzrunde bekommt den Fehler


# ---------- Diff-Bericht


def test_the_diff_report_names_full_commit_ids_and_the_branch_main(c, box):
    """Kurzformen und der bloße Name `main` wären mehrdeutig, wenn ein Tag so heißt."""
    c.run("K1")
    st = c.state("K1")
    report = (c.run_dir / "K1" / "diff-report.md").read_text()
    assert f"git diff {st['base']} {st['head']}" in report
    assert f"git diff refs/heads/main refs/sandboxes/{st['sandbox']}/" in report
    assert "git diff main " not in report
    assert f"Basis `{st['base']}`" in report
