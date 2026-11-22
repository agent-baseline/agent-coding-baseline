import json

import pytest

from kreislauf.events import EVENTS, FIELDS, EventLog


def make_log(tmp_path, **kw):
    ticks = iter(f"2026-11-20T09:00:{s:02d}Z" for s in range(60))
    return EventLog(
        tmp_path / "runs" / "job1" / "events.jsonl",
        project="demo",
        job="job1",
        host="testhost",
        clock=lambda: next(ticks),
        **kw,
    )


def test_write_appends_one_json_line_per_event(tmp_path):
    log = make_log(tmp_path)
    log.write("card_created", card="K1", column="briefing", actor="coordinator")
    log.write("card_moved", card="K1", column="umsetzung", actor="script")

    lines = log.path.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first == {
        "ts": "2026-11-20T09:00:00Z",
        "project": "demo",
        "host": "testhost",
        "job": "job1",
        "card": "K1",
        "event": "card_created",
        "column": "briefing",
        "actor": "coordinator",
    }


def test_field_order_follows_the_contract(tmp_path):
    log = make_log(tmp_path)
    log.write("finding", reason="secret", source="gate", card="K1", evidence={"file": "a.py"})
    keys = list(json.loads(log.path.read_text()).keys())
    assert keys == [f for f in FIELDS if f in keys]


def test_unknown_event_is_rejected(tmp_path):
    log = make_log(tmp_path)
    with pytest.raises(ValueError):
        log.write("card_teleported", card="K1")
    assert not log.path.exists()


def test_unknown_field_is_rejected(tmp_path):
    log = make_log(tmp_path)
    with pytest.raises(ValueError):
        log.write("card_moved", card="K1", colour="red")


def test_fixed_fields_cannot_be_overridden(tmp_path):
    log = make_log(tmp_path)
    with pytest.raises(ValueError):
        log.write("card_moved", card="K1", project="other")


def test_existing_lines_are_never_rewritten(tmp_path):
    log = make_log(tmp_path)
    log.write("card_created", card="K1")
    before = log.path.read_bytes()
    make_log(tmp_path).write("card_created", card="K2")
    assert log.path.read_bytes().startswith(before)
    assert [e["card"] for e in log.read()] == ["K1", "K2"]


def test_non_ascii_text_stays_readable(tmp_path):
    log = make_log(tmp_path)
    log.write("decision", card="K1", actor="human", reason="Freigabe nach Prüfung")
    assert "Prüfung" in log.path.read_text(encoding="utf-8")


def test_event_names_of_the_first_draft_are_present():
    for name in (
        "card_created card_moved card_returned condition_checked run_started "
        "run_ended subagent finding decision limit_reached"
    ).split():
        assert name in EVENTS
