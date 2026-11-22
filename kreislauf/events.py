"""Ereignisprotokoll: JSON Lines, nur anhängend, eine Zeile je Ereignis.

Das Protokoll ist die Schnittstelle zwischen Workflow und Board. Feldnamen
und Ereignisarten sind deshalb englisch. Geschrieben wird es nur auf der
Mac-Seite, aus beobachteten Tatsachen. Was ein Agent berichtet, steht als
Behauptung unter ``evidence.claim``.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Reihenfolge der Felder in jeder Zeile (Vertrag aus Workflow.md).
FIELDS = (
    "ts", "project", "host", "job", "card", "event", "column", "actor",
    "model", "effort", "sandbox", "commit", "source", "reason", "evidence",
    "tokens",
)

EVENTS = frozenset({
    "card_created", "card_moved", "card_returned", "condition_checked",
    "run_started", "run_ended", "subagent", "finding", "decision",
    "limit_reached",
    # Ergänzt beim Bau: Der Trockenlauf baut einen Fehler ein. Das Ereignis
    # hält fest, dass der Fehler vom Skript stammt und nicht vom Agenten.
    "fault_injected",
    # Ergänzt mit dem Zustandsautomaten: Die Karte hält mitten im Ablauf an
    # und wird später am genannten Schritt fortgesetzt. Die Spalte bleibt.
    "card_halted", "card_resumed",
    # Ergänzt am 2026-11-21: Ziele, die eine Sandbox im Netz angesprochen hat,
    # erlaubte und gesperrte, aus `sbx policy log`.
    "network_observed",
})

_FIXED = ("ts", "project", "host", "job", "event")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventLog:
    def __init__(
        self,
        path: os.PathLike | str,
        project: str,
        job: str,
        host: str | None = None,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.path = Path(path)
        self.project = project
        self.job = job
        self.host = host or socket.gethostname().split(".")[0]
        self._clock = clock

    def write(self, event: str, **fields: Any) -> dict:
        if event not in EVENTS:
            raise ValueError(f"unbekannte Ereignisart: {event}")
        for name in fields:
            if name in _FIXED:
                raise ValueError(f"Feld wird vom Protokoll gesetzt: {name}")
            if name not in FIELDS:
                raise ValueError(f"unbekanntes Feld: {name}")
        values = {
            "ts": self._clock(),
            "project": self.project,
            "host": self.host,
            "job": self.job,
            "event": event,
            **{k: v for k, v in fields.items() if v is not None},
        }
        record = {k: values[k] for k in FIELDS if k in values}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return record

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
