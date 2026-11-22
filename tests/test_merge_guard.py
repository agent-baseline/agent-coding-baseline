"""Der Merge braucht ein grünes Gate und die Entscheidung des Menschen für genau den
geprüften Commit. `main` ist danach entweder gemergt und grün oder unverändert."""

import pytest

from helpers import gate_result, git, make_cycle, to_approval
from kreislauf.cycle import Result, Sh, Stop


@pytest.fixture
def cycle(tmp_path):
    c = make_cycle(tmp_path)
    to_approval(c)
    return c


def merged(cycle):
    return (cycle.project / "src" / "a.py").exists()


def main_head(cycle):
    return git(cycle.project, "rev-parse", "main")


def merge_conditions(cycle):
    return {e["evidence"]["condition"]: e["evidence"]["ok"] for e in cycle.events("K1", "condition_checked")
            if e["column"] == "merged"}


def test_no_merge_without_decision(cycle):
    with pytest.raises(Stop):
        cycle.merge("K1")
    assert not merged(cycle)


def test_no_merge_on_a_decision_that_is_not_from_the_human(cycle):
    cycle.decide("K1", "approval", "approved", "sieht gut aus", actor="coordinator")
    with pytest.raises(Stop):
        cycle.merge("K1")
    assert not merged(cycle)


def test_no_merge_on_rejection(cycle):
    cycle.decide("K1", "approval", "rejected", "nein")
    assert cycle.column("K1") == "parked"
    with pytest.raises(Stop):
        cycle.merge("K1")
    assert not merged(cycle)


def test_no_merge_when_the_commit_changed_after_the_decision(cycle):
    cycle.decide("K1", "approval", "approved", "ja")
    state = cycle.state("K1")
    state["head"] = state["base"]  # ein anderer Stand als der freigegebene
    cycle.save_state("K1", state)
    with pytest.raises(Stop):
        cycle.merge("K1")
    assert not merged(cycle)


def test_merge_after_human_approval_for_this_commit(cycle):
    cycle.decide("K1", "approval", "approved", "ja")
    cycle.merge("K1")
    assert merged(cycle)
    events = [e["event"] for e in cycle.log.read()]
    assert events[-1] == "card_moved"
    assert cycle.column("K1") == "merged"


# ---------- Die Karte muss in der Freigabe stehen, das Gate für diesen Commit grün sein


def test_no_decision_for_a_card_that_is_not_in_approval(tmp_path):
    """Eine Karte, die im Gate angehalten hat, lässt sich nicht freigeben und nicht mergen."""
    c = make_cycle(tmp_path)
    head = c.state("K1")["head"]
    c.init()
    c.log.write("card_moved", card="K1", column="gate", actor="script", commit=head)
    with pytest.raises(Stop):
        c.decide("K1", "approval", "approved", "ja")
    assert c.events("K1", "decision") == []
    with pytest.raises(Stop):
        c.merge("K1")
    assert not merged(c)


def test_no_merge_when_the_gate_was_not_green(tmp_path):
    c = make_cycle(tmp_path)
    to_approval(c, gate_ok=False)
    c.decide("K1", "approval", "approved", "ja")
    with pytest.raises(Stop):
        c.merge("K1")
    assert not merged(c)


def test_no_merge_when_the_green_gate_was_for_another_commit(tmp_path):
    c = make_cycle(tmp_path)
    head = c.state("K1")["head"]
    c.init()
    gate_result(c, c.state("K1")["base"], ok=True)  # grün, aber für einen anderen Stand
    c.log.write("card_moved", card="K1", column="approval", actor="script", commit=head)
    c.decide("K1", "approval", "approved", "ja")
    with pytest.raises(Stop):
        c.merge("K1")
    assert not merged(c)


def test_no_second_merge_of_a_merged_card(cycle):
    cycle.decide("K1", "approval", "approved", "ja")
    cycle.merge("K1")
    after = main_head(cycle)
    with pytest.raises(Stop):
        cycle.merge("K1")
    assert main_head(cycle) == after


# ---------- Rote Tests nach dem Merge


def test_red_tests_after_the_merge_roll_it_back(cycle):
    before = main_head(cycle)
    cycle.cfg["test_command"] = "false"
    cycle.decide("K1", "approval", "approved", "ja")
    with pytest.raises(Stop):
        cycle.merge("K1")
    assert main_head(cycle) == before
    assert not merged(cycle)
    assert git(cycle.project, "status", "--porcelain") == ""
    assert cycle.column("K1") == "approval"
    checked = {e["evidence"]["condition"]: e["evidence"]["ok"] for e in cycle.events("K1", "condition_checked")}
    assert checked["tests_green_on_host"] is False
    assert checked["merge_rolled_back"] is True


def test_after_a_rollback_the_old_decision_no_longer_counts(cycle):
    cycle.cfg["test_command"] = "false"
    cycle.decide("K1", "approval", "approved", "ja")
    with pytest.raises(Stop):
        cycle.merge("K1")
    cycle.cfg["test_command"] = "true"
    with pytest.raises(Stop):  # dieselbe Entscheidung reicht nicht für einen zweiten Versuch
        cycle.merge("K1")
    assert not merged(cycle)
    cycle.decide("K1", "approval", "approved", "ja, noch einmal")
    cycle.merge("K1")
    assert merged(cycle)
    assert cycle.column("K1") == "merged"


# ---------- Jede Bindung an den Commit einzeln


def test_a_decision_for_another_commit_does_not_count(cycle):
    other = cycle.state("K1")["base"]
    cycle.log.write("decision", card="K1", column="approval", actor="human", commit=other,
                    reason="ja", evidence={"verdict": "approved"})
    with pytest.raises(Stop):
        cycle.merge("K1")
    checked = merge_conditions(cycle)
    assert checked["human_decision_for_this_commit"] is False
    assert checked["card_in_approval_at_this_commit"] is True and checked["gate_green_for_this_commit"] is True
    assert not merged(cycle)


def test_a_card_that_entered_approval_with_another_commit_does_not_merge(tmp_path):
    c = make_cycle(tmp_path)
    head = c.state("K1")["head"]
    c.init()
    gate_result(c, head, ok=True)
    c.log.write("card_moved", card="K1", column="approval", actor="script", commit=c.state("K1")["base"])
    c.log.write("decision", card="K1", column="approval", actor="human", commit=head,
                reason="ja", evidence={"verdict": "approved"})
    with pytest.raises(Stop):
        c.merge("K1")
    checked = merge_conditions(c)
    assert checked["card_in_approval_at_this_commit"] is False and checked["gate_green_for_this_commit"] is True
    assert not merged(c)


# ---------- Abbruch der Tests auf dem Mac


def test_a_timeout_returns_text_not_bytes():
    result = Sh().run(["sh", "-c", "echo teilweise; sleep 5"], timeout=0.5)
    assert result.rc == 124 and result.out == "teilweise\n"


def test_a_timeout_of_the_host_tests_rolls_the_merge_back(cycle):
    before = main_head(cycle)
    cycle.sh.on(("sh", "-c"), Result(124, "teilweise\n", "Zeitlimit nach 900 s"))
    cycle.decide("K1", "approval", "approved", "ja")
    with pytest.raises(Stop):
        cycle.merge("K1")
    assert main_head(cycle) == before and not merged(cycle)
    assert merge_conditions(cycle)["tests_green_on_host"] is False


def test_an_abort_during_the_host_tests_rolls_the_merge_back(cycle):
    before = main_head(cycle)

    def ctrl_c(argv):
        raise KeyboardInterrupt

    cycle.sh.on(("sh", "-c"), ctrl_c)
    cycle.decide("K1", "approval", "approved", "ja")
    with pytest.raises(KeyboardInterrupt):
        cycle.merge("K1")
    assert main_head(cycle) == before and not merged(cycle)
    assert merge_conditions(cycle)["merge_rolled_back"] is True
    cycle.sh.on(("sh", "-c"), Result(0, "", ""))
    with pytest.raises(Stop):  # die Karte steht neu in der Freigabe und braucht eine neue Entscheidung
        cycle.merge("K1")
    assert not merged(cycle)


def test_a_merge_left_on_main_by_a_killed_run_is_not_reported_as_rolled_back(cycle):
    """Der Prozess wurde in den Tests abgeschossen: `main` trägt den Merge, das Protokoll nichts."""
    cycle.decide("K1", "approval", "approved", "ja")
    git(cycle.project, "merge", "-q", "--no-ff", "-m", "Karte K1", cycle.state("K1")["head"])
    left = main_head(cycle)
    cycle.cfg["test_command"] = "false"
    with pytest.raises(Stop):
        cycle.merge("K1")
    checked = merge_conditions(cycle)
    assert checked["commit_not_yet_on_main"] is False
    assert "merge_rolled_back" not in checked and "merged" not in checked
    assert main_head(cycle) == left and cycle.column("K1") == "approval"
