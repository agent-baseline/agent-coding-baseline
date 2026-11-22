"""Aufruf: python3 -m kreislauf <befehl> --project <pfad> --job <auftrag> [--card <karte>]"""

from __future__ import annotations

import argparse
import sys

from .cycle import Cycle, Stop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kreislauf", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, card: bool = True) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--project", required=True, help="Pfad des Projekts auf dem Mac")
        p.add_argument("--job", required=True, help="Name des Auftrags")
        if card:
            p.add_argument("--card", required=True, help="Karte, zum Beispiel K1")
        return p

    add("init", "Karten des Auftrags ins Protokoll aufnehmen", card=False)
    p = add("release", "Entscheidung des Menschen: Karte aus der Zerlegung freigeben")
    p.add_argument("--reason", required=True)
    p = add("run", "Karte durch Umsetzung, Prüfung und Gate bis zur Freigabe führen")
    p.add_argument("--inject-fault", action="store_true",
                   help="Trockenlauf: den in der Karte genannten Fehler einbauen")
    add("resume", "Angehaltene Karte an dem Schritt fortsetzen, den der Halt nennt")
    p = add("decide", "Entscheidung des Menschen festhalten: in der Freigabe oder an einer angehaltenen Karte")
    p.add_argument("--verdict", required=True, choices=["approved", "rejected"])
    p.add_argument("--reason", required=True)
    add("merge", "Freigegebene Karte mergen und Tests auf dem Mac ausführen")
    add("cleanup", "Prüf- und Scan-Sandboxes der Karte löschen (nicht die Arbeits-Sandbox)")
    add("status", "Stand der Karten zeigen", card=False)

    args = parser.parse_args(argv)
    cycle = Cycle(args.project, args.job)
    try:
        if args.command == "init":
            cycle.init()
        elif args.command == "release":
            cycle._init_cards()  # Lücken anderer Karten stehen im Protokoll und halten diese nicht auf
            cycle.decide(args.card, "decomposition", "approved", args.reason)
        elif args.command == "run":
            cycle.run(args.card, inject_fault=args.inject_fault)
        elif args.command == "resume":
            cycle.resume(args.card)
        elif args.command == "decide":
            cycle.decide(args.card, None, args.verdict, args.reason)  # gilt für die Spalte, in der die Karte steht
        elif args.command == "merge":
            cycle.merge(args.card)
        elif args.command == "cleanup":
            for name in cycle.cleanup(args.card):
                print(f"gelöscht: {name}")
        if args.command in ("init", "release", "run", "resume", "decide", "merge", "status"):
            print("\n".join(cycle.status()))
    except Stop as stop:
        print(f"ANGEHALTEN: {stop}", file=sys.stderr)
        print("\n".join(cycle.status()))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
