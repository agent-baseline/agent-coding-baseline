"""Zustandsautomat einer Karte: Spalten, erlaubte Übergänge, Halt und Fortsetzung.

Reine Funktionen über dem Ereignisprotokoll, ohne Seiteneffekte. Der Zustand
einer Karte ist ihre Spalte und, wenn sie angehalten hat, der Halt mit dem
Schritt, an dem ``resume`` weitermacht. Beides ergibt sich allein aus dem
Protokoll.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ereignisse, die eine Karte in eine Spalte stellen.
MOVES = ("card_created", "card_moved", "card_returned")

# Übergangstabelle: (von, nach) -> wodurch. Was hier nicht steht, gibt es nicht.
TRANSITIONS: dict[tuple[str | None, str], str] = {
    (None, "decomposition"): "init: Karte angelegt",
    ("decomposition", "implementation"): "run: Ready erfüllt, Arbeits-Sandbox steht",
    ("implementation", "implementation"): "Rücklauf aus der Prüfung von außen",
    ("implementation", "gate"): "Done der Umsetzung erfüllt",
    ("gate", "implementation"): "Rücklauf aus dem Gate",
    ("gate", "approval"): "Gate ohne Befund",
    ("approval", "approval"): "merge: Tests auf dem Mac rot, Merge zurückgenommen",
    ("approval", "merged"): "merge: gemergt, Tests auf dem Mac grün",
    ("approval", "parked"): "decide: in der Freigabe abgelehnt",
    ("decomposition", "parked"): "decide: angehaltene Karte abgelehnt",
    ("implementation", "parked"): "decide: angehaltene Karte abgelehnt",
    ("gate", "parked"): "decide: angehaltene Karte abgelehnt",
}

# Aus diesen Spalten führt nichts heraus.
TERMINAL = ("merged", "parked")

# Schritte des Ablaufs und die Spalte, in der sie laufen. Ein Halt nennt den
# Schritt, an dem ``resume`` weitermacht.
STEP_COLUMN = {
    "start": "decomposition",     # Ready prüfen, Arbeits-Sandbox anlegen
    "agent": "implementation",    # anstehenden Agentenlauf ausführen
    "verify": "implementation",   # abholen, Done der Umsetzung von außen prüfen
    "gate": "gate",               # Scanner auf dem abgeholten Stand
}

# Gründe für einen Halt und wer danach am Zug ist. Wartet die Karte auf den
# Menschen, setzt ``resume`` sie erst nach dessen Entscheidung fort.
CAUSES = {
    "agent_failed": "coordinator",
    "agent_timeout": "coordinator",
    "limit_reached": "coordinator",
    "fault_injection_failed": "coordinator",
    "fetch_failed": "coordinator",
    "sandbox_failed": "coordinator",
    "checks_incomplete": "coordinator",
    "gate_incomplete": "coordinator",
    "approval_not_ready": "coordinator",
    "illegal_transition": "coordinator",
    "interrupted": "coordinator",
    "error": "coordinator",
    "loop_limit": "human",
}


@dataclass(frozen=True)
class CardState:
    column: str | None = None
    commit: str | None = None   # Commit des letzten Spaltenwechsels
    halt: dict | None = None    # {"cause", "step", "waiting_for", "index"} oder None

    @property
    def halted(self) -> bool:
        return self.halt is not None


def card_state(events: list[dict]) -> CardState:
    """Zustand einer Karte aus ihren Ereignissen in der Reihenfolge des Protokolls."""
    column = commit = halt = None
    for index, e in enumerate(events):
        kind = e.get("event")
        if kind in MOVES:
            column, commit, halt = e.get("column"), e.get("commit"), None
        elif kind == "card_halted":
            ev = e.get("evidence") or {}
            halt = {"cause": ev.get("cause"), "step": ev.get("step"),
                    "waiting_for": ev.get("waiting_for"), "index": index}
        elif kind == "card_resumed":
            halt = None
    return CardState(column, commit, halt)


def allowed(frm: str | None, to: str) -> bool:
    return (frm, to) in TRANSITIONS


def command_problem(state: CardState, command: str) -> str | None:
    """Warum der Befehl in diesem Zustand nicht gilt; None, wenn er gilt."""
    where = state.column or "not in the log"
    if command == "run":
        if state.halted:
            return f"card is halted ({state.halt['cause']}); continue with resume"
        if state.column != "decomposition":
            return f"run starts a card from decomposition, the card is in {where}"
    elif command == "resume":
        if not state.halted:
            return f"card is not halted (column {where})"
        step = state.halt["step"]
        if STEP_COLUMN.get(step) != state.column:
            return f"halt names step {step!r}, which does not run in column {where}"
    elif command == "merge":
        if state.halted or state.column != "approval":
            return f"merge needs a card in approval, the card is in {where}"
    else:
        return f"unknown command {command!r}"
    return None


def decision_problem(state: CardState, column: str, verdict: str) -> str | None:
    """Warum der Mensch hier nichts zu entscheiden hat; None, wenn die Karte auf ihn wartet.

    Entschieden wird an drei Stellen: beim Freigeben aus der Zerlegung, in der
    Freigabe und an einer angehaltenen Karte. Eine angehaltene Karte lässt sich
    immer ablehnen; zustimmen kann der Mensch nur, wenn sie auf ihn wartet.
    """
    where = state.column or "not in the log"
    if column != state.column:
        return f"decision is for {column}, the card is in {where}"
    if state.halted:
        if verdict == "rejected" or state.halt["waiting_for"] == "human":
            return None
        if column == "decomposition" and verdict == "approved":
            return None  # Freigabe aus der Zerlegung; der Halt bleibt bestehen
        return f"card is halted ({state.halt['cause']}) and does not wait for the human; continue with resume"
    if column == "decomposition" and verdict == "approved":
        return None
    if column == "approval":
        return None
    return f"a card in {where} does not wait for a decision"


def approved_after_halt(events: list[dict]) -> bool:
    """Der Mensch hat nach dem Halt zugestimmt, in der Spalte, in der die Karte steht."""
    state = card_state(events)
    if not state.halted:
        return False
    later = [e for e in events[state.halt["index"] + 1:]
             if e.get("event") == "decision" and e.get("column") == state.column]
    return bool(later) and later[-1].get("actor") == "human" \
        and (later[-1].get("evidence") or {}).get("verdict") == "approved"
