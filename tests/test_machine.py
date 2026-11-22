"""Der Automat: Übergangstabelle, Zustand aus dem Protokoll, erlaubte Befehle."""

from kreislauf.machine import (
    CAUSES,
    STEP_COLUMN,
    TERMINAL,
    TRANSITIONS,
    CardState,
    allowed,
    approved_after_halt,
    card_state,
    command_problem,
    decision_problem,
)

COLUMNS = {"decomposition", "implementation", "gate", "approval", "merged", "parked"}


def ev(event, column=None, **fields):
    return {"event": event, "card": "K1", "column": column, **fields}


def halted(column, cause="agent_failed", step="agent"):
    return ev("card_halted", column, evidence={"cause": cause, "step": step, "waiting_for": CAUSES[cause]})


def test_the_table_knows_only_the_built_columns():
    for frm, to in TRANSITIONS:
        assert frm is None or frm in COLUMNS
        assert to in COLUMNS


def test_nothing_leads_out_of_merged_and_parked_and_everything_else_leads_somewhere():
    sources = {frm for frm, _ in TRANSITIONS}
    assert not sources & set(TERMINAL)
    assert COLUMNS - set(TERMINAL) <= sources


def test_every_column_is_reachable_from_a_new_card():
    reached, grew = {None}, True
    while grew:
        new = {to for frm, to in TRANSITIONS if frm in reached} - reached
        reached, grew = reached | new, bool(new)
    assert reached == COLUMNS | {None}


def test_transitions_that_are_not_in_the_table_do_not_exist():
    assert allowed("gate", "approval")
    assert not allowed("decomposition", "gate")       # nicht ohne Umsetzung
    assert not allowed("implementation", "approval")  # nicht am Gate vorbei
    assert not allowed("gate", "merged")              # nicht an der Freigabe vorbei
    assert not allowed("parked", "decomposition")
    assert not allowed("merged", "approval")


def test_every_step_runs_in_a_column_and_every_cause_names_who_acts():
    assert set(STEP_COLUMN.values()) <= COLUMNS
    assert set(CAUSES.values()) == {"coordinator", "human"}
    assert CAUSES["loop_limit"] == "human"


def test_state_is_the_last_column_and_an_open_halt():
    events = [ev("card_created", "decomposition"), ev("card_moved", "implementation", commit="a")]
    assert card_state(events) == CardState("implementation", "a", None)
    events.append(halted("implementation"))
    state = card_state(events)
    assert state.column == "implementation" and state.halted
    assert state.halt == {"cause": "agent_failed", "step": "agent", "waiting_for": "coordinator", "index": 2}
    assert not card_state(events + [ev("card_resumed", "implementation")]).halted
    # Ein Spaltenwechsel beendet den Halt, etwa in die Ablage.
    assert card_state(events + [ev("card_moved", "parked")]) == CardState("parked", None, None)
    assert card_state([]) == CardState()


def test_run_starts_only_a_card_that_waits_in_decomposition():
    assert command_problem(CardState("decomposition"), "run") is None
    for column in (None, "implementation", "gate", "approval", "merged", "parked"):
        assert command_problem(CardState(column), "run")
    stopped = card_state([ev("card_created", "decomposition"), halted("decomposition", "error", "start")])
    assert "resume" in command_problem(stopped, "run")


def test_resume_needs_a_halt_whose_step_fits_the_column():
    stopped = card_state([ev("card_moved", "implementation"), halted("implementation")])
    assert command_problem(stopped, "resume") is None
    assert command_problem(CardState("implementation"), "resume")
    misplaced = card_state([ev("card_moved", "approval"), halted("approval", "gate_incomplete", "gate")])
    assert command_problem(misplaced, "resume")


def test_merge_needs_a_card_in_approval():
    assert command_problem(CardState("approval"), "merge") is None
    assert command_problem(CardState("gate"), "merge")
    assert command_problem(CardState("merged"), "merge")


def test_the_human_decides_at_release_in_approval_and_at_a_halted_card():
    assert decision_problem(CardState("decomposition"), "decomposition", "approved") is None
    assert decision_problem(CardState("decomposition"), "decomposition", "rejected")
    assert decision_problem(CardState("approval"), "approval", "approved") is None
    assert decision_problem(CardState("approval"), "approval", "rejected") is None
    assert decision_problem(CardState("gate"), "approval", "approved")   # steht nicht in der Freigabe
    assert decision_problem(CardState("gate"), "gate", "approved")       # wartet auf keine Entscheidung
    assert decision_problem(CardState("merged"), "merged", "rejected")

    failed = card_state([ev("card_moved", "implementation"), halted("implementation")])
    assert decision_problem(failed, "implementation", "rejected") is None   # ablehnen geht immer
    assert "resume" in decision_problem(failed, "implementation", "approved")
    at_limit = card_state([ev("card_moved", "implementation"), halted("implementation", "loop_limit")])
    assert decision_problem(at_limit, "implementation", "approved") is None


def test_approval_after_a_halt_counts_only_from_the_human_and_only_after_it():
    base = [ev("card_moved", "implementation"),
            ev("decision", "implementation", actor="human", evidence={"verdict": "approved"}),
            halted("implementation", "loop_limit")]
    assert not approved_after_halt(base)  # die Entscheidung liegt vor dem Halt
    yes = ev("decision", "implementation", actor="human", evidence={"verdict": "approved"})
    assert approved_after_halt(base + [yes])
    assert not approved_after_halt(base + [{**yes, "actor": "coordinator"}])
    assert not approved_after_halt(base + [{**yes, "column": "approval"}])
    assert not approved_after_halt([ev("card_moved", "implementation"), yes])  # kein Halt
