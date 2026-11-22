"""Der Kreislauf einer Karte: Umsetzung, Prüfung von außen, Gate, Freigabe, Merge.

Läuft auf dem Mac und steuert ``sbx``. Fremder Code wird hier nie ausgeführt:
Tests laufen in einer frischen Prüf-Sandbox, Scanner in einer frischen
Scan-Sandbox, beide aus einer Vorlage ohne Modell und ohne Anmeldung.
Das Protokoll schreibt nur dieses Skript.

Der Ablauf einer Karte ist eine Schrittfolge: start → agent → verify → gate →
Freigabe; ein Befund führt zurück zu agent. Welche Spaltenwechsel es gibt,
steht in ``machine.TRANSITIONS``. Bricht ein Schritt ab, steht ein Halt mit
diesem Schritt im Protokoll, und ``resume`` macht dort weiter.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import shutil
import string
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import checks, machine
from .events import EventLog

WORK_TEMPLATE = os.environ.get("KREISLAUF_WORK_TEMPLATE", "baseline-claude:2026-11-18-3")
SCAN_TEMPLATE = os.environ.get("KREISLAUF_SCAN_TEMPLATE", "baseline-scan:2026-11-20")
WIP_LIMIT = int(os.environ.get("KREISLAUF_WIP", "2"))
RUN_TIMEOUT = int(os.environ.get("KREISLAUF_RUN_TIMEOUT", "2400"))
# Zweiter Rücklauf: frischer Kontext, stärkeres Modell (Workflow.md, Schleife).
ESCALATION = {"model": "claude-opus-5-5", "effort": "high"}
MAX_RETURNS = 2
# Eingebaute Fehler des Trockenlaufs (`dry_run_fault` in der Karte, nur mit --inject-fault):
# `secret` einmal nach dem ersten Agentenlauf, `secret-every-round` nach jedem Agentenlauf,
# damit das Gate jede Runde abweist und die Schleifengrenze erreichbar ist.
DRY_RUN_FAULTS = ("secret", "secret-every-round")
SANDBOX_HOME = "/home/agent"
SANDBOX_SOURCE_GIT = "/run/sandbox/source/.git"


_EXTRACT = r"""extract() {
  g="$HOME/.kreislauf-git"
  export GIT_NO_REPLACE_OBJECTS=1
  rm -rf "$g"
  git init -q --bare "$g" || return 90
  mkdir -p "$g/info" "$g/objects/info"
  echo "@SOURCE@/objects" > "$g/objects/info/alternates"
  printf '* -export-ignore -export-subst -text -crlf -eol -ident -filter -working-tree-encoding\n' > "$g/info/attributes"
  git --git-dir="$g" cat-file -e "$1^{commit}" || return 90
  mkdir -p "$2"
  git --git-dir="$g" archive -o "$2.tar" "$1" || return 90
  tar -x -C "$2" -f "$2.tar" || return 90
  rm -f "$2.tar"
  git --git-dir="$g" ls-tree -r -z "$1" > "$2.treez" || return 90
  tr '\0\n' '\n?' < "$2.treez" > "$2.tree"
  want=$(grep -vc '^160000 ' "$2.tree")
  got=$(find "$2" \( -type f -o -type l \) -print0 | tr -dc '\0' | wc -c | tr -d ' ')
  [ "$want" -eq "$got" ] || return 91
  grep '^100' "$2.tree" | cut -d' ' -f3 | cut -f1 | sort > "$2.want"
  find "$2" -type f -print0 | xargs -0 git --git-dir="$g" hash-object --no-filters -- | sort > "$2.got"
  cmp -s "$2.want" "$2.got" || return 91
  rm -f "$2.treez" "$2.tree" "$2.want" "$2.got"
}
"""


def extract_snippet(source_git: str = SANDBOX_SOURCE_GIT) -> str:
    """Shell-Funktion `extract <commit> <ziel>`: legt den Stand vollständig und unverändert ins Ziel.

    `git archive` wendet Attribute aus dem geprüften Stand an: `export-ignore` lässt
    Dateien aus, `export-subst`, `ident`, `text`/`eol` und `working-tree-encoding`
    ändern ihren Inhalt. Tests und Scanner sähen dann andere Bytes als die, die
    gemergt werden. Die Funktion liest die Objekte deshalb über ein eigenes leeres
    Repository, dessen `info/attributes` diese Attribute für alle Pfade abschaltet
    (höchster Vorrang), und prüft das Ergebnis unabhängig davon gegen den Baum des
    Commits: Zahl der Einträge und Objekt-Hash jeder Datei. Ein Attribut, das hier
    fehlt, führt so zu einem Abbruch statt zu einem veränderten Stand.

    Rückgabe 90: Commit unbekannt oder nicht ausgepackt, 91: unvollständig oder
    verändert. Kein Urteil hängt am Exit-Code einer Pipe. Die Funktion läuft, bevor
    in der Sandbox Code aus dem geprüften Stand ausgeführt wird.
    """
    return _EXTRACT.replace("@SOURCE@", source_git)


_SECRETS = r"""g="$HOME/.kreislauf-git"; m="$HOME/commits"
opts="--ignore-gitleaks-allow --no-banner --redact --report-format json"
for f in .gitleaks.toml .gitleaksignore; do
  rm -rf "$HOME/scan/$f" "$g/$f"
  if git --git-dir="$g" cat-file -e "$base:$f" 2>/dev/null; then
    git --git-dir="$g" cat-file blob "$base:$f" > "$g/$f" || exit 92
    cp "$g/$f" "$HOME/scan/$f" || exit 92
  fi
done
cfg=""; [ -f "$g/.gitleaks.toml" ] && cfg="$g/.gitleaks.toml"
( cd "$HOME/scan" && gitleaks dir . $opts --report-path "$HOME/out/gitleaks.json" ) > "$HOME/out/gitleaks.log" 2>&1
echo "RESULT gitleaks $?"
n=$(git --git-dir="$g" rev-list --count "$base..$sha") && echo "HISTORY commits $n"
( cd "$g" && gitleaks git . --log-opts="--text --no-textconv --no-ext-diff --diff-merges=first-parent $base..$sha" $opts --report-path "$HOME/out/gitleaks-history.json" ) > "$HOME/out/gitleaks-history.log" 2>&1
echo "RESULT gitleaks-history $?"
rm -rf "$m"; mkdir -p "$m"
for c in $(git --git-dir="$g" rev-list "$base..$sha"); do
  git --git-dir="$g" cat-file commit "$c" > "$m/$c" || rm -f "$m/$c"
done
echo "HISTORY messages $(find "$m" -type f | wc -l | tr -d ' ')"
( cd "$m" && gitleaks dir . ${cfg:+--config "$cfg"} $opts --report-path "$HOME/out/gitleaks-messages.json" ) > "$HOME/out/gitleaks-messages.log" 2>&1
echo "RESULT gitleaks-messages $?"
"""


def secrets_snippet() -> str:
    """Shell-Zeilen: Gitleaks über den Stand, über die Commits der Karte und über deren Köpfe.

    Die Zeilen laufen nach `extract "$sha" "$HOME/scan"` und brauchen `$sha` und `$base`.

    Stand (`gitleaks.json`): der ausgepackte Baum.

    Commits (`gitleaks-history.json`): Der Scan des Stands sieht ein Secret nicht, das ein
    früherer Commit der Karte enthielt und ein späterer entfernt hat; mit dem Merge käme
    es trotzdem nach `main`. Das Hilfs-Repository von `extract` liest die Objekte des
    Projekts mit, und dort liegen nach dem Abholen alle Commits der Karte. Ein eigenes
    Repository mit Historie braucht die Scan-Sandbox dafür nicht. Die Optionen für
    `git log`: `--text` zeigt auch Dateien, die git für binär hält oder die ein Attribut
    vom Diff ausnimmt, `--diff-merges=first-parent` zeigt, was ein Merge-Commit gegenüber
    seinem ersten Elternteil ändert, `--no-textconv` und `--no-ext-diff` lassen keinen
    Filter zwischen Objekt und Ausgabe.

    Köpfe (`gitleaks-messages.json`): `git log -p` zeigt Gitleaks nur Dateiinhalte. Je
    Commit liegt deshalb das rohe Commit-Objekt (Autor, Committer, weitere Kopfzeilen,
    Nachricht) als Datei unter `$HOME/commits/<commit>`.

    Regeln und Ausnahmen (`.gitleaks.toml`, `.gitleaksignore`) gelten für alle drei Läufe
    so, wie sie in der Basis stehen: Was die Karte daran ändert oder neu mitbringt, wirkt
    erst nach dem Merge. `--ignore-gitleaks-allow` nimmt dem geprüften Code die Möglichkeit,
    eine Zeile mit `gitleaks:allow` selbst auszunehmen. Lässt sich eine Datei der Basis
    nicht lesen, bricht das Skript mit 92 ab.

    `HISTORY commits <n>` und `HISTORY messages <n>` nennen, wie viele Commits das
    Hilfs-Repository in dem Bereich sieht und wie viele Köpfe geschrieben sind; der
    Koordinator vergleicht beide Zahlen mit seiner eigenen.
    """
    return _SECRETS


def _brief(finding: dict, *keys: str) -> dict:
    """Ein Befund fürs Protokoll. Ein Fund aus der Historie nennt zusätzlich seinen Commit."""
    brief = {k: finding.get(k) for k in keys}
    if finding.get("found_in"):
        brief["found_in"] = finding["found_in"]
    return brief


class Stop(Exception):
    """Der Befehl gilt hier nicht oder eine Bedingung ist nicht erfüllt. Die Karte
    hat sich nicht bewegt; die geprüften Bedingungen stehen im Protokoll."""


class Halt(Stop):
    """Die Karte hält mitten im Ablauf an. Der Halt kommt mit Grund und Schritt ins
    Protokoll; ``resume`` macht an diesem Schritt weiter."""

    def __init__(self, message: str, cause: str, step: str | None = None) -> None:
        super().__init__(message)
        self.cause = cause
        self.step = step  # None: der Schritt, der gerade lief
        self.waiting_for = machine.CAUSES[cause]


@dataclass
class Result:
    rc: int
    out: str
    err: str


class Sh:
    def run(self, argv, cwd=None, timeout=None) -> Result:
        try:
            p = subprocess.run(
                [str(a) for a in argv], cwd=cwd, timeout=timeout,
                capture_output=True, text=True,
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            if isinstance(out, bytes):  # am Zeitlimit liefert subprocess Bytes, trotz text=True
                out = out.decode(errors="replace")
            return Result(124, out, f"Zeitlimit nach {timeout} s")
        return Result(p.returncode, p.stdout, p.stderr)


class Cycle:
    def __init__(self, project: str | os.PathLike, job: str, sh: Sh | None = None) -> None:
        self.project = Path(project).resolve()
        self.job = job
        self.sh = sh or Sh()
        self.cfg = json.loads((self.project / ".baseline" / "project.json").read_text())
        self.name = self.cfg.get("project", self.project.name)
        self.tier = self.cfg.get("tier", "")
        self.sensitive = self.cfg.get("sensitive_paths", [])
        self.run_dir = self.project / ".baseline" / "runs" / job
        self.log = EventLog(self.run_dir / "events.jsonl", project=self.name, job=job)
        self._step: str | None = None  # Schritt, der gerade läuft

    # ---------- Hilfen

    def card(self, card_id: str) -> dict:
        path = self.project / ".baseline" / "jobs" / self.job / "cards" / f"{card_id}.json"
        return json.loads(path.read_text())

    def _card_checked(self, card_id: str) -> dict:
        """Die Karte, wenn sie lesbar und vollständig ist. Sonst hält der Befehl an,
        und die Lücken stehen im Protokoll."""
        try:
            card = self.card(card_id)
            problems = checks.card_problems(card, card_id)
        except (OSError, json.JSONDecodeError) as exc:
            card, problems = {}, [f"card not readable: {type(exc).__name__}: {exc}"]
        if not self.condition(card_id, self.card_state(card_id).column or "decomposition", "ready",
                              "card_valid", not problems, problems=problems):
            raise Stop(f"{card_id}: Karte unvollständig oder nicht lesbar: {'; '.join(problems)}")
        return card

    def card_ids(self) -> list[str]:
        cards = self.project / ".baseline" / "jobs" / self.job / "cards"
        return sorted(p.stem for p in cards.glob("*.json"))

    def _state_path(self, card_id: str) -> Path:
        return self.run_dir / card_id / "state.json"

    def state(self, card_id: str) -> dict:
        path = self._state_path(card_id)
        return json.loads(path.read_text()) if path.exists() else {}

    def save_state(self, card_id: str, state: dict) -> None:
        path = self._state_path(card_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")

    def git(self, *args: str, check: bool = True) -> str:
        r = self.sh.run(["git", *args], cwd=self.project)
        if check and r.rc != 0:
            raise RuntimeError(f"git {' '.join(args)}: {r.err.strip()}")
        return r.out.strip()

    def _names(self, *args: str) -> list[str]:
        """Pfadliste eines git-Befehls. Mit `-z` liefert git die Namen unverändert;
        ohne setzt es Namen mit Umlauten oder Sonderzeichen in Anführungszeichen,
        und sie passen auf kein Muster mehr."""
        r = self.sh.run(["git", *args, "-z"], cwd=self.project)
        if r.rc != 0:
            raise RuntimeError(f"git {' '.join(args)}: {r.err.strip()}")
        return [name for name in r.out.split("\0") if name]

    def sbx(self, *args: str, timeout: int | None = 600) -> Result:
        return self.sh.run(["sbx", *args], timeout=timeout)

    def observe_network(self, card_id: str, column: str, sandbox: str) -> None:
        """Hält fest, welche Ziele die Sandbox im Netz angesprochen hat (``sbx policy log``).

        Eine Beobachtung, keine Bedingung: Fehlt der Bericht, steht das im
        Protokoll (``ok: false``), der Kreislauf läuft weiter. Die Zähler sind
        Summen seit dem Anlegen der Sandbox; je Sandbox gilt der letzte Eintrag.
        """
        r = self.sbx("policy", "log", sandbox, "--json", "--limit", "0", timeout=60)
        try:
            if r.rc != 0:
                raise ValueError((r.err or r.out).strip()[-300:] or "sbx policy log ist gescheitert")
            net = checks.parse_policy_log(r.out, sandbox)
        except ValueError as exc:
            evidence = {"ok": False, "exit_code": r.rc, "detail": str(exc)}
        else:
            evidence = {
                "ok": True, "cumulative": True,
                "allowed_hosts": len(net["allowed"]), "blocked_hosts": len(net["blocked"]),
                "allowed_requests": sum(e["count"] for e in net["allowed"]),
                "blocked_requests": sum(e["count"] for e in net["blocked"]),
                "allowed": net["allowed"], "blocked": net["blocked"],
            }
        self.log.write("network_observed", card=card_id, column=column, actor="script",
                       sandbox=sandbox, evidence=evidence)

    def sandbox_name(self, card_id: str) -> str:
        prefix = self.cfg.get("sandbox_prefix", self.name)
        return f"{prefix}-{self.job}-{card_id}".lower()

    def condition(self, card_id: str, column: str, phase: str, name: str, ok: bool, **detail) -> bool:
        self.log.write(
            "condition_checked", card=card_id, column=column, actor="script",
            evidence={"phase": phase, "condition": name, "ok": bool(ok), **detail},
        )
        return bool(ok)

    def events(self, card_id: str, event: str | None = None) -> list[dict]:
        return [
            e for e in self.log.read()
            if e.get("card") == card_id and (event is None or e["event"] == event)
        ]

    # ---------- Zustand, Übergänge, Halt

    def card_state(self, card_id: str) -> machine.CardState:
        """Spalte und Halt der Karte, allein aus dem Protokoll."""
        return machine.card_state(self.events(card_id))

    def column(self, card_id: str) -> str | None:
        return self.card_state(card_id).column

    def _move(self, card_id: str, to: str, event: str = "card_moved", **fields) -> None:
        """Stellt die Karte in eine Spalte. Nur Übergänge aus der Tabelle gibt es."""
        now = self.column(card_id)
        if not machine.allowed(now, to):
            raise Halt(f"{card_id}: Der Übergang {now or '-'} → {to} steht nicht in der Übergangstabelle.",
                       "illegal_transition")
        self.log.write(event, card=card_id, column=to, **fields)

    def _allowed(self, card_id: str, command: str) -> None:
        """Der Befehl gilt nur in den Zuständen, die der Automat für ihn vorsieht."""
        state = self.card_state(card_id)
        problem = machine.command_problem(state, command)
        halted = {"halted": state.halt["cause"]} if state.halted else {}
        if not self.condition(card_id, state.column or "decomposition", "ready", "command_allowed",
                              problem is None, command=command, **halted,
                              detail=problem or "allowed in this state"):
            raise Stop(f"{card_id}: {command} gilt hier nicht: {problem}")

    def _fitting_step(self, card_id: str) -> str | None:
        """Der Schritt, der in der Spalte der Karte läuft: in `gate` das Gate, in
        `implementation` der anstehende Agentenlauf oder sonst die Prüfung. In den
        anderen Spalten läuft kein Schritt, der sich fortsetzen ließe."""
        column = self.column(card_id)
        if column == "gate":
            return "gate"
        if column == "implementation":
            return "agent" if self.state(card_id).get("pending") else "verify"
        return None

    def _halt(self, card_id: str, cause: str, step: str | None, detail: str) -> bool:
        """Schreibt den Halt. Der Schritt muss in der Spalte laufen, in der die Karte
        steht; sonst gilt der Schritt, der dort läuft. Steht die Karte in einer Spalte
        ohne Schritt (etwa schon in der Freigabe), gibt es nichts fortzusetzen und
        keinen Halt."""
        column = self.column(card_id)
        if machine.STEP_COLUMN.get(step) != column:
            step = self._fitting_step(card_id)
        if step is None:
            return False
        self.log.write(
            "card_halted", card=card_id, column=column, actor="script",
            commit=self.state(card_id).get("head"), reason=detail[:300],
            evidence={"cause": cause, "step": step, "waiting_for": machine.CAUSES[cause]},
        )
        return True

    @contextmanager
    def _locked(self, card_id: str):
        """Eine Karte bearbeitet immer nur ein Prozess. Die Sperre hält das Betriebssystem
        (`flock`); stirbt der Prozess, ist sie frei. Die Datei bleibt liegen und nennt
        den letzten Besitzer."""
        path = self.run_dir / card_id / "lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                holder = os.pread(fd, 32, 0).decode(errors="replace").strip() or "?"
                raise Stop(f"{card_id}: wird gerade von Prozess {holder} bearbeitet "
                           f"(Sperre {path.relative_to(self.project)}).") from None
            os.ftruncate(fd, 0)
            os.pwrite(fd, str(os.getpid()).encode(), 0)
            yield
        finally:
            os.close(fd)  # gibt die Sperre frei

    def _guarded(self, card_id: str, work) -> None:
        """Führt einen Abschnitt des Ablaufs aus. Bricht er mitten in der Arbeit ab,
        steht danach ein Halt im Protokoll, auch bei einem unerwarteten Fehler."""
        with self._locked(card_id):
            try:
                work()
            except Halt as halt:
                self._halt(card_id, halt.cause, halt.step or self._step, str(halt))
                raise
            except Stop:
                raise  # Bedingung nicht erfüllt, nichts hat sich bewegt
            except BaseException as exc:
                interrupted = not isinstance(exc, Exception)
                # Ohne laufenden Schritt ist nichts fortzusetzen: Die Karte hat sich nicht bewegt.
                halted = bool(self._step) and self._halt(
                    card_id, "interrupted" if interrupted else "error", self._step,
                    f"{type(exc).__name__}: {exc}")
                if interrupted:
                    raise
                raise Stop(f"{card_id}: unerwarteter Fehler ({type(exc).__name__}: {exc})."
                           + (" Die Karte hat angehalten." if halted else "")) from exc

    def _wip_free(self, card_id: str, column: str) -> bool:
        """Angehaltene Karten zählen nicht: Sie verbrauchen kein Nutzungsfenster."""
        busy = []
        for other in self.card_ids():
            state = self.card_state(other)
            if other != card_id and state.column == "implementation" and not state.halted:
                busy.append(other)
        return self.condition(card_id, column, "ready", "wip_free", len(busy) < WIP_LIMIT,
                              detail=f"{len(busy)} of {WIP_LIMIT} in implementation")

    # ---------- Zerlegung und Entscheidungen des Menschen

    def init(self) -> None:
        invalid = self._init_cards()
        if invalid:
            raise Stop(f"Karten unvollständig oder nicht lesbar, nicht aufgenommen: {', '.join(invalid)}")

    def _init_cards(self) -> list[str]:
        """Nimmt die Karten des Auftrags ins Protokoll auf. Liefert die Karten, die
        unvollständig sind; ihre Lücken stehen im Protokoll."""
        invalid = []
        for card_id in self.card_ids():
            if self.events(card_id, "card_created"):
                continue
            try:
                card = self._card_checked(card_id)
            except Stop:
                invalid.append(card_id)
                continue
            self._move(
                card_id, "decomposition", "card_created", actor="coordinator",
                model=card["model"], effort=card["effort"],
                evidence={
                    "title": card["title"], "route": card["route"], "branch": card["branch"],
                    "allowed_paths": card["allowed_paths"], "acceptance": card["acceptance"],
                    "tier": self.tier,
                },
            )
        return invalid

    def decide(self, card_id: str, column: str | None, verdict: str, reason: str, actor: str = "human") -> None:
        """Hält eine Entscheidung des Menschen fest: beim Freigeben aus der Zerlegung, in
        der Freigabe oder an einer angehaltenen Karte. Ohne `column` gilt die Spalte,
        in der die Karte steht."""
        with self._locked(card_id):
            self._decide(card_id, column, verdict, reason, actor)

    def _decide(self, card_id: str, column: str | None, verdict: str, reason: str, actor: str) -> None:
        st = self.state(card_id)
        state = self.card_state(card_id)
        column = column or state.column or "decomposition"
        problem = machine.decision_problem(state, column, verdict)
        if not self.condition(card_id, column, "ready", "card_waits_for_decision", problem is None,
                              verdict=verdict, detail=problem or "decision expected here"):
            raise Stop(f"{card_id}: keine Entscheidung festgehalten: {problem}")
        release = column == "decomposition" and verdict == "approved"
        evidence = {"verdict": verdict}
        if state.halted and not release:
            evidence["halt"] = state.halt["cause"]
        self.log.write(
            "decision", card=card_id, column=column, actor=actor,
            commit=st.get("head") if column == "approval" else None,
            reason=reason, evidence=evidence,
        )
        if verdict == "rejected":
            # Abgelehnt: Die Karte geht in die Ablage, der Branch bleibt ungemergt.
            self._move(card_id, "parked", actor="script", commit=st.get("head"),
                       reason=f"rejected in {column}")

    def _decision(self, card_id: str, column: str) -> dict | None:
        found = [e for e in self.events(card_id, "decision") if e.get("column") == column]
        return found[-1] if found else None

    def _gate_green(self, card_id: str, head: str | None) -> bool:
        """Der letzte Gate-Lauf der Karte galt genau diesem Commit und blieb ohne Befund."""
        last = [e for e in self.events(card_id, "condition_checked")
                if e.get("column") == "gate" and e["evidence"].get("condition") == "nothing_above_threshold"]
        return bool(head) and bool(last) and last[-1]["evidence"].get("commit") == head \
            and last[-1]["evidence"].get("ok") is True

    def _approval_state(self, card_id: str, head: str | None) -> dict:
        """Was das Protokoll über die Freigabe dieses Commits sagt.

        Die Entscheidung zählt nur, wenn die Karte mit diesem Commit in der
        Freigabe steht und die Entscheidung danach gefallen ist.
        """
        events = self.events(card_id)
        moves = [i for i, e in enumerate(events) if e["event"] in machine.MOVES]
        last = events[moves[-1]] if moves else {}
        in_approval = bool(head) and last.get("column") == "approval" and last.get("commit") == head
        decisions = [i for i, e in enumerate(events) if e["event"] == "decision" and e.get("column") == "approval"]
        decision = events[decisions[-1]] if decisions else {}
        approved = in_approval and bool(decisions) and decisions[-1] > moves[-1] \
            and decision["evidence"].get("verdict") == "approved" \
            and decision.get("commit") == head and decision.get("actor") == "human"
        return {"in_approval": in_approval, "approved": approved, "gate_green": self._gate_green(card_id, head)}

    # ---------- Umsetzung

    def run(self, card_id: str, inject_fault: bool = False) -> None:
        """Startet eine Karte aus der Zerlegung und führt sie bis in die Freigabe."""
        self._step = None  # bis der Befehl gilt, läuft kein Schritt und es gibt keinen Halt
        self._guarded(card_id, lambda: self._start(card_id, inject_fault))

    def resume(self, card_id: str) -> None:
        """Setzt eine angehaltene Karte an dem Schritt fort, den der Halt nennt."""
        self._step = None
        self._guarded(card_id, lambda: self._resume(card_id))

    def _start(self, card_id: str, inject_fault: bool) -> None:
        if not self.events(card_id, "card_created"):
            self._init_cards()
        card = self._card_checked(card_id)
        self._allowed(card_id, "run")
        self._step = "start"
        self._ready_implementation(card)
        st = {
            "sandbox": self.sandbox_name(card_id),
            "base": self.git("rev-parse", "HEAD"),
            "branch": card["branch"],
            "returns": {"gate": 0, "checks": 0},
            "round": 0,
            "step": "agent",
            # Der anstehende Agentenlauf. Er bleibt stehen, bis der Lauf gelungen ist.
            "pending": {"prompt": self._task_prompt(card), "model": card["model"],
                        "effort": card["effort"], "resume": False},
            # Trockenlauf: welcher Fehler eingebaut wird und ob er gerade ansteht.
            "fault_mode": card.get("dry_run_fault") if inject_fault else None,
            "inject_fault": bool(inject_fault and card.get("dry_run_fault") in DRY_RUN_FAULTS),
        }
        r = self.sbx("create", "--clone", "--pull", "never", "-t", WORK_TEMPLATE,
                     "--name", st["sandbox"], "claude", str(self.project))
        if not self.condition(card_id, "implementation", "ready", "work_sandbox_created", r.rc == 0,
                              sandbox=st["sandbox"], detail=(r.err or r.out).strip()[-300:]):
            raise Stop(f"{card_id}: Arbeits-Sandbox {st['sandbox']} ließ sich nicht anlegen.")
        self.save_state(card_id, st)
        self._move(
            card_id, "implementation", actor="script",
            model=card["model"], effort=card["effort"], sandbox=st["sandbox"], commit=st["base"],
            evidence={"route": card["route"], "template": WORK_TEMPLATE},
        )
        self._advance(card, st, "agent")

    def _resume(self, card_id: str) -> None:
        card = self._card_checked(card_id)
        st = self.state(card_id)
        state = self.card_state(card_id)
        if not state.halted and state.column in ("implementation", "gate"):
            # Die Karte steht mitten im Ablauf, aber kein Prozess hält sie (die Sperre ist
            # frei) und kein Halt steht im Protokoll: Der Lauf wurde abgebrochen. Der
            # Schritt ergibt sich aus der Spalte und daraus, ob ein Agentenlauf ansteht.
            events = self.events(card_id)
            moved = max(i for i, e in enumerate(events) if e["event"] in machine.MOVES)
            at_limit = (events[moved].get("evidence") or {}).get("waiting_for") == "human"
            halted_since = any(e["event"] == "card_halted" for e in events[moved + 1:])
            if at_limit and not halted_since:
                # Abgebrochen zwischen dem Rücklauf an der Schleifengrenze und seinem Halt:
                # Die Karte wartet auf den Menschen. Steht der Halt schon im Protokoll, hat
                # der Mensch entschieden und die Zusatzrunde lief; dann ist es ein Abbruch.
                cause, step = "loop_limit", "agent"
            else:
                cause, step = "interrupted", self._fitting_step(card_id)
            self._halt(card_id, cause, step, "no process holds the card and no halt is logged")
            state = self.card_state(card_id)
        self._allowed(card_id, "resume")
        halt = state.halt
        ready = []
        if halt["waiting_for"] == "human":
            ready.append(self.condition(card_id, state.column, "ready", "human_decision_after_halt",
                                        machine.approved_after_halt(self.events(card_id)), cause=halt["cause"]))
        if halt["step"] in ("start", "agent"):  # diese Schritte brauchen das Nutzungsfenster
            ready.append(self._wip_free(card_id, state.column))
        if not all(ready):
            raise Stop(f"{card_id}: Fortsetzen nicht möglich; die Karte bleibt angehalten ({halt['cause']}).")
        self.log.write("card_resumed", card=card_id, column=state.column, actor="coordinator",
                       commit=st.get("head"), evidence={"cause": halt["cause"], "step": halt["step"]})
        self._step = halt["step"]
        if halt["step"] == "start":
            self._start(card_id, inject_fault=False)
            return
        # Die Zustimmung des Menschen an der Schleifengrenze gilt für genau eine weitere
        # Runde: Der nächste Rücklauf liegt wieder über der Grenze und hält die Karte an.
        self._advance(card, st, halt["step"])

    def _advance(self, card: dict, st: dict, step: str) -> None:
        """Die Schrittfolge ab `step`: agent → verify → gate → Freigabe. Ein Befund führt
        zurück zu agent. Der laufende Schritt steht im Arbeitsstand."""
        cid = card["id"]
        while True:
            self._step = st["step"] = step
            self.save_state(cid, st)
            if step == "agent":
                if st.get("pending"):  # ohne anstehenden Lauf ist der Agent fertig
                    self._agent_run(card, st, **st["pending"])
                    st["pending"] = None
                    if st.get("fault_mode") == "secret-every-round":
                        st["inject_fault"] = True  # der Dauerfehler kommt nach jedem Agentenlauf wieder
                step = "verify"
            elif step == "verify":
                if st.get("inject_fault"):
                    self._inject_secret(card, st)
                    st["inject_fault"] = False  # je Agentenlauf einmal, auch wenn die Prüfung fortgesetzt wird
                    self.save_state(cid, st)
                head, origin = self._fetch(card, st)
                if not self.condition(cid, "implementation", "done", "branch_fetched", bool(head),
                                      branch=card["branch"], detail=origin):
                    raise Halt(f"{cid}: Branch {card['branch']} ließ sich nicht aus der Sandbox abholen ({origin}).",
                               "fetch_failed")
                st["head"] = head
                self.save_state(cid, st)
                problems = self._done_implementation(card, st)
                if problems:
                    step = self._return(card, st, "checks", problems)
                else:
                    self._move(cid, "gate", actor="script", commit=head,
                               evidence={"template": SCAN_TEMPLATE, "run": st.get("gate_runs", 0) + 1})
                    step = "gate"
            elif step == "gate":
                findings = self._gate(card, st)
                if findings:
                    step = self._return(card, st, "gate", findings)
                else:
                    self._to_approval(card, st)
                    return
            else:
                raise Halt(f"{cid}: unbekannter Schritt {step!r}.", "error", step="verify")

    def _return(self, card: dict, st: dict, source: str, findings: list[dict]) -> str:
        """Rücklauf in die Umsetzung. Legt den Auftrag der Nachbesserung als anstehenden
        Agentenlauf ab und liefert den nächsten Schritt; an der Schleifengrenze hält die Karte an."""
        cid, head = card["id"], st["head"]
        st["returns"][source] += 1
        total = sum(st["returns"].values())
        waits = total > MAX_RETURNS
        withdrawn = st.get("test_commit") if any(
            f.get("found_in") and f["found_in"] == st.get("test_commit") for f in findings) else None
        if withdrawn:
            # Das Secret steht im abgenommenen Test-Commit; behalten lässt er sich so nicht. Die
            # Abnahme fällt weg: Die Nachbesserung baut den Branch neu auf, die Tests werden neu abgenommen.
            st["test_commit"] = None
        # Erster Rücklauf: derselbe Umsetzer, Sitzung fortsetzen. Danach: frischer Kontext, stärkeres Modell.
        first = total == 1
        st["pending"] = {
            "prompt": self._rework_prompt(card, st, source, findings),
            "model": card["model"] if first else ESCALATION["model"],
            "effort": card["effort"] if first else ESCALATION["effort"],
            "resume": first,
        }
        st["step"] = "agent"  # der nächste Schritt, auch wenn die Karte gleich an der Grenze anhält
        self.save_state(cid, st)
        self._move(
            cid, "implementation", "card_returned", actor="script",
            source=source, commit=head, sandbox=st["sandbox"],
            reason="waits for human: loop limit reached" if waits
                   else f"{len(findings)} finding(s) above the threshold of tier {self.tier}",
            evidence={"returns": dict(st["returns"]), "total": total, "waiting_for": "human" if waits else None,
                      "findings": [_brief(f, "tool", "rule", "file", "line", "severity") for f in findings],
                      **({"test_commit_withdrawn": withdrawn} if withdrawn else {})},
        )
        if waits:
            raise Halt(f"{cid}: Schleifengrenze erreicht, die Karte wartet auf den Menschen.",
                       "loop_limit", step="agent")
        return "agent"

    def _ready_implementation(self, card: dict) -> None:
        cid = card["id"]
        agents = self.project / "AGENTS.md"
        dirty = self.git("status", "--porcelain")
        released = self._decision(cid, "decomposition")
        route = checks.route_problem(card.get("route", ""), self.tier, card["allowed_paths"], self.sensitive,
                                     self._names("ls-files"))
        results = [
            self.condition(cid, "implementation", "ready", "route_allowed", route is None,
                           route=card.get("route", ""), detail=route or "route allowed for this tier and these paths"),
            self.condition(cid, "implementation", "ready", "repository_committed", dirty == "",
                           detail=dirty[:200] or "working tree clean"),
            self.condition(cid, "implementation", "ready", "tier_set", self.tier in checks.TIER_THRESHOLD,
                           detail=self.tier),
            self.condition(cid, "implementation", "ready", "security_model_in_agents_md",
                           agents.exists() and "## Sicherheitsmodell" in agents.read_text()),
            self.condition(cid, "implementation", "ready", "card_released",
                           bool(released) and released["evidence"].get("verdict") == "approved",
                           detail=(released or {}).get("ts", "no decision in log")),
            self._wip_free(cid, "implementation"),
        ]
        if not all(results):
            raise Stop(f"{cid}: Ready-Bedingungen der Umsetzung nicht erfüllt; keine Sandbox gestartet.")

    def _task_prompt(self, card: dict) -> str:
        acceptance = "\n".join(f"- {a}" for a in card["acceptance"])
        return f"""Du bist der Umsetzer für ein Arbeitspaket im Projekt {self.name}. Lies zuerst AGENTS.md.

Arbeitspaket {card['id']}: {card['title']}

Aufgabe: {card['task']}

Abnahmekriterien:
{acceptance}

Erlaubte Pfade: {', '.join(card['allowed_paths'])}. Alles andere bleibt unberührt.

Ablauf:
1. Lege den Branch `{card['branch']}` von `main` an und arbeite dort.
2. `export UV_PROJECT_ENVIRONMENT=~/venv`, dann `uv sync --locked`.
3. Erster Commit: nur die Tests zu den Abnahmekriterien, vollständig, unter {', '.join(card.get('test_paths', []))}. Sie müssen scheitern; prüfe das mit `uv run --locked pytest -q`.
4. Danach die Umsetzung, in einem oder mehreren Commits, ohne jede Änderung unter den Test-Pfaden. Am Ende muss die ganze Suite bestehen.
5. Nicht pushen. Keine neuen Pakete. Arbeitsverzeichnis sauber hinterlassen.

Antworte am Ende mit einem kurzen Bericht: was umgesetzt ist, welche Befehle du mit welchem Ergebnis ausgeführt hast und was du nicht geprüft hast."""

    def _rework_prompt(self, card: dict, st: dict, source: str, findings: list[dict]) -> str:
        lines = []
        for i, f in enumerate(findings, 1):
            where = f.get("file") or "-"
            if f.get("line"):
                where += f", Zeile {f['line']}"
            if f.get("found_in"):
                where += f", Commit {f['found_in'][:10]}"
            lines.append(
                f"{i}. {where} ({f.get('tool', source)}, Regel {f.get('rule', '-')}, "
                f"Schwere {f.get('severity', '-')}). Risiko: {f.get('risk', '-')}. Verlangt: {f.get('demand', '-')}"
            )
        # Ein Secret in einem Commit der Karte: Ein weiterer Commit behebt das nicht.
        in_history = any(f.get("found_in") for f in findings)
        if st.get("test_commit") and in_history:
            rules = (f"- Die Tests des Pakets sind abgenommen: Der Test-Commit {st['test_commit'][:10]} bleibt "
                     f"unverändert der erste Commit des Branches, keine Änderung unter "
                     f"{', '.join(card.get('test_paths', []))}.\n"
                     "- Ein Secret steht in der Historie des Branches. Ein weiterer Commit, der es entfernt, "
                     "genügt nicht. Ersetze die Commits nach dem Test-Commit (etwa "
                     f"`git reset --soft {st['test_commit'][:10]}` und neu committen), sodass kein Commit "
                     "des Branches das Secret mehr enthält, auch nicht in seiner Nachricht oder in den "
                     "Angaben zu Autor und Committer.")
        elif st.get("test_commit"):
            rules = (f"- Die Tests des Pakets sind abgenommen und bleiben unverändert: keine Änderung unter "
                     f"{', '.join(card.get('test_paths', []))}, der Test-Commit {st['test_commit'][:10]} bleibt.\n"
                     "- Behebe die Befunde in einem neuen Commit auf demselben Branch. Kein Amend, kein Rebase.")
        else:
            rules = (f"- Die Tests des Pakets sind noch nicht abgenommen. Baue den Branch `{card['branch']}` von "
                     "`main` neu auf: erster Commit nur die Tests, vollständig, die ohne die Umsetzung scheitern; "
                     "danach die Umsetzung ohne jede Änderung an den Tests. Dafür darfst du die bisherigen Commits "
                     "ersetzen (Reset, Amend, Rebase).\n"
                     f"- Tests liegen unter {', '.join(card.get('test_paths', []))}.")
            if in_history:
                rules += ("\n- Kein Commit des neu aufgebauten Branches darf das gemeldete Secret enthalten, "
                          "auch der Test-Commit nicht und auch keine Commit-Nachricht.")
        return f"""Nachbesserung für Arbeitspaket {card['id']} im Projekt {self.name}, Branch `{card['branch']}`.
Die Prüfung von außen ({source}) hat den Stand {st['head'][:10]} abgewiesen. Befunde:

{chr(10).join(lines)}

Regeln:
{rules}
- Die ganze Suite muss weiter bestehen (`export UV_PROJECT_ENVIRONMENT=~/venv`, `uv run --locked pytest -q`).
- Nur erlaubte Pfade: {', '.join(card['allowed_paths'])}. Nicht pushen. Arbeitsverzeichnis sauber hinterlassen.

Antworte mit einem kurzen Bericht: was geändert ist, was du geprüft hast und was nicht."""

    def _agent_run(self, card: dict, st: dict, prompt: str, model: str, effort: str, resume: bool) -> None:
        cid = card["id"]
        st["round"] += 1
        self.save_state(cid, st)  # auch ein abgebrochener Lauf zählt als Runde
        argv = ["exec", "-w", str(self.project), st["sandbox"], "claude",
                "--dangerously-skip-permissions", "--model", model, "--effort", effort,
                "--output-format", "json"]
        if resume and st.get("session_id"):
            argv += ["--resume", st["session_id"]]
        argv += ["-p", prompt]
        self.log.write(
            "run_started", card=cid, column="implementation", actor="implementer",
            model=model, effort=effort, sandbox=st["sandbox"],
            evidence={"round": st["round"], "resume": bool(resume and st.get("session_id")),
                      "prompt_chars": len(prompt)},
        )
        r = self.sbx(*argv, timeout=RUN_TIMEOUT)
        (self.run_dir / cid).mkdir(parents=True, exist_ok=True)
        (self.run_dir / cid / f"run-{st['round']}.stdout.json").write_text(r.out)
        try:
            data = json.loads(r.out.strip().splitlines()[-1]) if r.out.strip() else {}
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        usage = data.get("usage") or {}
        tokens = {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_creation": usage.get("cache_creation_input_tokens"),
        }
        claim = str(data.get("result") or "")
        if data.get("session_id"):
            st["session_id"] = data["session_id"]
        self.save_state(cid, st)
        failed = r.rc != 0 or bool(data.get("is_error"))
        self.log.write(
            "run_ended", card=cid, column="implementation", actor="implementer",
            model=model, effort=effort, sandbox=st["sandbox"],
            tokens={k: v for k, v in tokens.items() if v is not None} or None,
            evidence={
                "round": st["round"], "exit_code": r.rc, "is_error": bool(data.get("is_error")),
                "subtype": data.get("subtype"), "num_turns": data.get("num_turns"),
                "duration_ms": data.get("duration_ms"), "session_id": data.get("session_id"),
                # Bericht des Agenten: eine Behauptung, keine beobachtete Tatsache.
                "claim": claim[:2000],
            },
        )
        self.observe_network(cid, "implementation", st["sandbox"])
        if failed:
            text = (claim + " " + r.err + " " + r.out[-500:]).lower()
            cause = "agent_timeout" if r.rc == 124 else "agent_failed"
            if r.rc != 124 and "limit" in text:
                # Heuristik: Exit-Code und Feld für ein erreichtes Abo-Limit sind nicht dokumentiert.
                # Das eigene Zeitlimit (124) zählt nicht dazu, auch wenn sein Text „limit" enthält.
                cause = "limit_reached"
                self.log.write(
                    "limit_reached", card=cid, column="implementation", actor="script",
                    sandbox=st["sandbox"],
                    evidence={"exit_code": r.rc, "heuristic": True, "excerpt": (claim or r.err)[:300]},
                )
            # Der anstehende Lauf bleibt im Arbeitsstand; `resume` führt ihn noch einmal aus.
            raise Halt(f"{cid}: Agentenlauf fehlgeschlagen (Exit-Code {r.rc}). Siehe run-{st['round']}.stdout.json",
                       cause, step="agent")

    def _inject_secret(self, card: dict, st: dict) -> None:
        """Trockenlauf: baut ein erfundenes Secret ein, damit das Gate etwas abzuweisen hat."""
        alphabet = string.ascii_letters + string.digits
        fake = "ghp_" + "".join(secrets.choice(alphabet) for _ in range(36))
        # Der Branch kommt als Argument ($1) in die Shell, nicht als Text des Skripts.
        script = """set -eu
git checkout -q "$1"
cat > src/notiz_api/upstream.py <<EOF
\"\"\"Anbindung an den Upstream-Dienst.\"\"\"

UPSTREAM_URL = "https://upstream.example.invalid/api"
UPSTREAM_TOKEN = "$FAULT_TOKEN"
EOF
git add src/notiz_api/upstream.py
git commit -q -m "Upstream-Anbindung mit festem Token (eingebauter Fehler des Trockenlaufs)"
git rev-parse HEAD
"""
        r = self.sbx("exec", "-w", str(self.project), "-e", f"FAULT_TOKEN={fake}",
                     st["sandbox"], "sh", "-c", script, "sh", card["branch"])
        if r.rc != 0:
            raise Halt(f"{card['id']}: Fehler ließ sich nicht einbauen: {r.err.strip()[-300:]}",
                       "fault_injection_failed", step="verify")
        self.log.write(
            "fault_injected", card=card["id"], column="implementation", actor="script",
            sandbox=st["sandbox"], commit=r.out.strip().splitlines()[-1],
            reason="dry run: planted by the coordinator script, not written by the agent",
            evidence={"kind": "secret", "file": "src/notiz_api/upstream.py",
                      "note": "randomly generated string in the format of a GitHub token; never valid, not stored"},
        )

    # ---------- Abholen und Prüfen von außen

    def _fetch(self, card: dict, st: dict) -> tuple[str | None, str]:
        """Holt den Branch aus der Sandbox. Liefert den Commit und woher er stammt.

        Scheitert das Abholen, gibt es keinen Commit: Ein Ref aus einem früheren
        Abholen wäre ein alter Stand und darf nicht als der aktuelle gelten.
        """
        remote = f"sandbox-{st['sandbox']}"
        self.sbx("exec", st["sandbox"], "true")  # startet eine gestoppte Sandbox, sonst fehlt der Remote
        # --no-tags: Tags aus der Sandbox kommen nicht ins Projekt. Ein Tag namens `main`
        # oder mit der Kurzform eines Commits hätte beim Review Vorrang vor Branch und Commit.
        # --force: Ersetzt die Nachbesserung Commits (Secret in der Historie, Tests noch nicht
        # abgenommen), muss der Ref der Sandbox überschrieben werden, auch wenn der Remote das
        # nicht von sich aus erlaubt. Betroffen sind nur die Refs dieser Sandbox.
        r = self.sh.run(["git", "fetch", "--no-tags", "--force", remote], cwd=self.project)
        if r.rc != 0:
            return None, f"git fetch {remote} failed (exit {r.rc}): {(r.err or r.out).strip()[-200:]}"
        for ref in (f"refs/remotes/{remote}/{card['branch']}",
                    f"refs/sandboxes/{st['sandbox']}/{card['branch']}"):
            sha = self.git("rev-parse", "--verify", "-q", ref, check=False)
            if sha:
                return sha, ref
        return None, f"branch {card['branch']} not found on {remote}"

    def _done_implementation(self, card: dict, st: dict) -> list[dict]:
        """Done der Umsetzung.

        Die Tests des Pakets stehen vollständig im ersten Commit: Nach ihm ändert kein
        Commit mehr etwas unter den Test-Pfaden. So sind die Tests, die am Stand grün
        sein müssen, dieselben, die am Test-Commit ohne die Umsetzung rot waren.

        Abgenommen sind die Tests, sobald das für eine Abgabe gilt (erster Commit nur
        Tests, ohne Umsetzung rot, danach unverändert). Von da an ist der Test-Commit
        festgeschrieben (`test_commit`): Jede spätere Abgabe muss ihn tragen. Vorher
        darf die Nachbesserung den Branch neu aufbauen."""
        cid, base, head = card["id"], st["base"], st["head"]
        commits = self.git("rev-list", "--reverse", f"{base}..{head}").split()
        files = [self._names("diff-tree", "--no-commit-id", "--name-only", "-r", c) for c in commits]
        # --no-renames: Eine Umbenennung zählt als Löschen am alten und Anlegen am neuen Pfad.
        # Sonst zeigt git nur den Zielpfad, und der alte Pfad entgeht der Pfadprüfung.
        changed = self._names("diff", "--no-renames", "--name-only", base, head)
        test_paths = card.get("test_paths", [])
        problems: list[dict] = []

        def problem(rule: str, risk: str, demand: str, file: str = "") -> None:
            problems.append({"tool": "kreislauf", "rule": rule, "file": file, "line": None,
                             "severity": "high", "risk": risk, "demand": demand})

        test_first = checks.first_commit_is_test_only(files, test_paths)
        self.condition(cid, "implementation", "done", "test_commit_then_implementation", test_first,
                       commits=[c[:10] for c in commits])
        if not test_first:
            problem("test_commit_then_implementation",
                    "Der erste Commit des Pakets enthält nicht nur Tests oder es gibt nur einen Commit",
                    "Erst ein Commit nur mit Tests, dann die Umsetzung")
        offenders = checks.outside_allowed(changed, card["allowed_paths"], self.sensitive)
        self.condition(cid, "implementation", "done", "only_allowed_paths", not offenders,
                       changed=changed, offenders=offenders)
        for path in offenders:
            problem("only_allowed_paths", "Änderung außerhalb der erlaubten Pfade",
                    "Änderung an diesem Pfad zurücknehmen", file=path)

        frozen = st.get("test_commit")
        unchanged = True
        if commits and (test_first or frozen):
            same_commit = not frozen or commits[0] == frozen
            touched = [f for f in self._names("diff", "--no-renames", "--name-only", commits[0], head)
                       if checks.path_matches(f, test_paths)]
            unchanged = same_commit and not touched
            self.condition(cid, "implementation", "done", "tests_unchanged_since_test_commit", unchanged,
                           test_commit=commits[0][:10], accepted=(frozen or "")[:10] or "not yet",
                           same_commit=same_commit, touched=touched)
            if not same_commit:
                problem("tests_unchanged_since_test_commit",
                        "Der abgenommene Test-Commit wurde ersetzt",
                        f"Branch wieder auf den Test-Commit {frozen[:10]} aufbauen, ohne ihn zu ändern")
            for path in touched:
                problem("tests_unchanged_since_test_commit",
                        "Tests wurden nach dem Test-Commit geändert; rot geprüft ist nur der Test-Commit",
                        "Alle Tests gehören in den ersten Commit; danach keine Änderung unter den Test-Pfaden",
                        file=path)

        if test_first:
            results = self._run_tests(card, st, [commits[0], head])
            red = results.get(commits[0], {})
            green = results.get(head, {})
            # Ohne Ergebnis oder ohne aufgebaute Umgebung sind die Tests nicht gelaufen. Das ist
            # kein Befund für den Umsetzer: Die Karte hält an, statt eine Runde zu verbrauchen.
            ran = checks.tests_ran(red) and checks.tests_ran(green)
            if not self.condition(cid, "implementation", "done", "tests_ran", ran, sandboxes=results["sandboxes"],
                                  test_commit=red or "no result", head=green or "no result"):
                self.save_state(cid, st)
                raise Halt(f"{cid}: Die Tests sind in der Prüf-Sandbox nicht gelaufen "
                           f"(Test-Commit: {red or 'kein Ergebnis'}, Stand: {green or 'kein Ergebnis'}).",
                           "checks_incomplete", step="verify")
            # pytest: 1 = Tests gescheitert, 2 = Abbruch beim Sammeln (etwa fehlendes Modul)
            red_ok = red.get("sync") == 0 and red.get("pytest") in (1, 2)
            green_ok = green.get("sync") == 0 and green.get("pytest") == 0
            self.condition(cid, "implementation", "done", "test_commit_red", red_ok,
                           commit=commits[0][:10], sandbox=results["sandboxes"][0], **red)
            self.condition(cid, "implementation", "done", "suite_green", green_ok,
                           commit=head[:10], sandbox=results["sandboxes"][-1], **green)
            if not red_ok:
                problem("test_commit_red", "Die Tests des Test-Commits scheitern ohne die Umsetzung nicht",
                        "Tests so schreiben, dass sie ohne die Umsetzung scheitern")
            if not green_ok:
                problem("suite_green", f"Die Suite besteht am Stand {head[:10]} nicht: {green.get('tail', '')}",
                        "Umsetzung korrigieren, bis die ganze Suite besteht")
            if not frozen and red_ok and unchanged:
                st["test_commit"] = commits[0]  # Tests abgenommen
        self.save_state(cid, st)
        return problems

    def _fresh_sandbox(self, card_id: str, st: dict, kind: str) -> str:
        st[f"{kind}_runs"] = st.get(f"{kind}_runs", 0) + 1
        name = f"{st['sandbox']}-{kind}-{st[f'{kind}_runs']}"
        # Zähler und Name stehen im Arbeitsstand, bevor die Sandbox entsteht: Bricht der
        # Lauf danach ab, nimmt `resume` einen neuen Namen, und `cleanup` kennt die alte.
        st.setdefault("helper_sandboxes", []).append(name)
        self.save_state(card_id, st)
        r = self.sbx("create", "--clone", "--pull", "never", "-t", SCAN_TEMPLATE,
                     "--name", name, "shell", str(self.project))
        if r.rc != 0:
            raise Halt(f"Sandbox {name} ließ sich nicht anlegen: {(r.err or r.out).strip()[-300:]}",
                       "sandbox_failed")
        return name

    def _run_tests(self, card: dict, st: dict, shas: list[str]) -> dict:
        """Tests je Commit in einer eigenen frischen Sandbox ohne Modell.

        Eine Sandbox je Commit: Bevor der Stand ausgepackt ist, läuft dort kein Code aus
        dem geprüften Stand, und der Code eines Commits kann das Ergebnis des anderen
        nicht beeinflussen. Je Commit zählt die erste Ergebniszeile.
        """
        setup = self.cfg.get("setup_command", "uv sync --locked -q")
        test = self.cfg.get("test_command", "uv run --locked pytest -q")
        script = "set -u\n" + extract_snippet() + f"""sha="$1"; d="$HOME/check/$sha"
tail1() {{ tail -n 1 "$1" | tr -d '\\r' | cut -c1-200; }}
extract "$sha" "$d"; x=$?
if [ "$x" -ne 0 ]; then printf 'RESULT %s sync=%s pytest=-1\\n' "$sha" "$x"; exit 0; fi
cd "$d"; export UV_PROJECT_ENVIRONMENT="$HOME/venv-$sha"
{setup} > "$d.setup.log" 2>&1; s=$?
if [ "$s" -ne 0 ]; then
  printf 'RESULT %s sync=%s pytest=-1\\n' "$sha" "$s"; printf 'TAIL %s %s\\n' "$sha" "$(tail1 "$d.setup.log")"; exit 0
fi
{test} > "$d.test.log" 2>&1; p=$?
printf 'RESULT %s sync=0 pytest=%s\\n' "$sha" "$p"
printf 'TAIL %s %s\\n' "$sha" "$(tail1 "$d.test.log")"
"""
        results: dict = {"sandboxes": []}
        for sha in shas:
            name = self._fresh_sandbox(card["id"], st, "pruef")
            results["sandboxes"].append(name)
            r = self.sbx("exec", name, "sh", "-c", script, "sh", sha, timeout=900)
            self.observe_network(card["id"], "implementation", name)
            for line in r.out.split("\n"):
                parts = line.split(" ", 2)
                if len(parts) != 3 or parts[1] != sha:
                    continue
                if parts[0] == "RESULT" and sha not in results:
                    try:
                        kv = dict(item.split("=", 1) for item in parts[2].split())
                        results[sha] = {"sync": int(kv["sync"]), "pytest": int(kv["pytest"])}
                    except (KeyError, ValueError):
                        continue  # keine gültige Ergebniszeile
                elif parts[0] == "TAIL" and sha in results and "tail" not in results[sha]:
                    results[sha]["tail"] = parts[2][:200]
        return results

    # ---------- Gate

    def _gate(self, card: dict, st: dict) -> list[dict]:
        cid, head = card["id"], st["head"]
        name = self._fresh_sandbox(cid, st, "gate")
        out_dir = self.run_dir / cid / f"gate-{st['gate_runs']}"
        out_dir.mkdir(parents=True, exist_ok=True)
        # In die Spalte Gate stellt die Karte der Schritt verify; ein fortgesetztes Gate bewegt sie nicht noch einmal.
        self.log.write("run_started", card=cid, column="gate", actor="scanner", sandbox=name, commit=head,
                       evidence={"template": SCAN_TEMPLATE, "run": st["gate_runs"]})
        script = "set -u\n" + extract_snippet() + """sha="$1"; base="$2"; mkdir -p "$HOME/out"
extract "$sha" "$HOME/scan"; x=$?
[ "$x" -eq 0 ] || exit "$x"
""" + secrets_snippet() + """cd "$HOME/scan"
osv-scanner scan source -r . --offline-vulnerabilities --download-offline-databases --format json --output-file "$HOME/out/osv.json" > "$HOME/out/osv.log" 2>&1
echo "RESULT osv-scanner $?"
syft scan dir:. -q -o cyclonedx-json="$HOME/out/sbom.cdx.json" > "$HOME/out/syft.log" 2>&1
echo "RESULT syft $?"
sed 's/^/VERSION /' /usr/local/share/baseline-scan/versions.txt
"""
        r = self.sbx("exec", name, "sh", "-c", script, "sh", head, st["base"], timeout=900)
        codes, versions, seen = {}, {}, {}
        for line in r.out.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] == "RESULT":
                codes[parts[1]] = int(parts[2])
            elif len(parts) == 3 and parts[0] == "HISTORY" and parts[2].isdigit():
                seen[parts[1]] = int(parts[2])
            elif len(parts) >= 3 and parts[0] == "VERSION":
                versions[parts[1]] = parts[2]
        # Die Berichte kommen zuerst in einen Ordner außerhalb des Projekts. Gitleaks schreibt zu einem
        # Fund in den Commits auch Nachricht und Autor des Commits in den Bericht, ungeschwärzt; in den
        # Projektordner gelangen aus seinen Berichten nur die Felder ohne Text aus dem geprüften Code.
        with tempfile.TemporaryDirectory(prefix="kreislauf-gate-") as tmp:
            for fname in ("gitleaks.json", "gitleaks-history.json", "gitleaks-messages.json", "osv.json",
                          "sbom.cdx.json"):
                # Ein Bericht aus einem früheren Lauf darf nicht für diesen gelten.
                (out_dir / fname).unlink(missing_ok=True)
                fetched = Path(tmp) / fname
                self.sbx("cp", f"{name}:{SANDBOX_HOME}/out/{fname}", str(fetched))
                if not fetched.is_file():
                    continue
                if fname.startswith("gitleaks"):
                    safe = checks.gitleaks_report_without_text(fetched.read_text(errors="replace"))
                    if safe is not None:  # ein unlesbarer Bericht bleibt draußen und gilt als fehlend
                        (out_dir / fname).write_text(safe)
                else:
                    shutil.copyfile(fetched, out_dir / fname)
        self.observe_network(cid, "gate", name)

        def read(fname: str) -> str:
            path = out_dir / fname
            return path.read_text() if path.exists() else ""

        def report(fname: str, parse) -> list[dict] | None:
            """Die Funde aus dem Bericht; None, wenn er fehlt, leer oder nicht lesbar ist."""
            text = read(fname)
            if not text.strip():
                return None
            try:
                return parse(text)
            except (json.JSONDecodeError, AttributeError, TypeError):
                return None

        reports = {"gitleaks": report("gitleaks.json", checks.parse_gitleaks),
                   "gitleaks-history": report("gitleaks-history.json",
                                              lambda text: checks.parse_gitleaks(text, history=True)),
                   "gitleaks-messages": report("gitleaks-messages.json", checks.parse_gitleaks_messages),
                   "osv-scanner": report("osv.json", checks.parse_osv)}
        lockfile = checks.has_lockfile(self._names("ls-tree", "-r", "--name-only", head))
        commits = int(self.git("rev-list", "--count", f"{st['base']}..{head}"))
        # Exit-Code und Bericht müssen zusammenpassen: 0 = nichts gefunden, 1 = Funde im Bericht.
        # OSV-Scanner 128 = keine Pakete; das gilt nur, wenn im Stand kein Lockfile liegt.
        # Commits und ihre Köpfe: Die Scan-Sandbox muss zwischen Basis und Stand so viele Commits sehen
        # und so viele Köpfe geschrieben haben, wie das Projekt Commits hat.
        ran = {
            "gitleaks": checks.scan_consistent(codes.get("gitleaks"), reports["gitleaks"]),
            "gitleaks-history": checks.scan_consistent(codes.get("gitleaks-history"), reports["gitleaks-history"])
                                and seen.get("commits") == commits,
            "gitleaks-messages": checks.scan_consistent(codes.get("gitleaks-messages"), reports["gitleaks-messages"])
                                 and seen.get("messages") == commits,
            "osv-scanner": checks.scan_consistent(codes.get("osv-scanner"), reports["osv-scanner"],
                                                  no_packages_code=128, packages_expected=lockfile),
            "syft": codes.get("syft") == 0 and (out_dir / "sbom.cdx.json").exists(),
        }
        for tool, ok in ran.items():
            detail = {}
            if tool in reports:
                detail = {"report_readable": reports[tool] is not None, "findings": len(reports[tool] or [])}
            if tool == "gitleaks-history":
                detail.update(base=st["base"], commits=commits, commits_seen=seen.get("commits"))
            if tool == "gitleaks-messages":
                detail.update(base=st["base"], commits=commits, messages_seen=seen.get("messages"))
            if tool == "osv-scanner":
                detail["lockfile_present"] = lockfile
            self.condition(cid, "gate", "done", f"{tool}_ran", ok, exit_code=codes.get(tool),
                           version=versions.get("gitleaks" if tool.startswith("gitleaks") else tool),
                           sandbox=name, **detail)
        findings: list[dict] = ((reports["gitleaks"] or []) + (reports["gitleaks-history"] or [])
                                + (reports["gitleaks-messages"] or []) + (reports["osv-scanner"] or []))
        try:
            sbom = json.loads(read("sbom.cdx.json") or "{}")
        except json.JSONDecodeError:
            sbom = {}
        self.condition(cid, "gate", "done", "sbom_created", sbom.get("bomFormat") == "CycloneDX",
                       path=str((out_dir / "sbom.cdx.json").relative_to(self.project)),
                       spec=sbom.get("specVersion"), components=len(sbom.get("components") or []))
        blocking = []
        for f in findings:
            f["above_threshold"] = checks.above_threshold(f["severity"], self.tier)
            self.log.write("finding", card=cid, column="gate", actor="scanner", source="gate",
                           commit=head, sandbox=name, reason=f["risk"],
                           evidence=_brief(f, "tool", "rule", "file", "line", "severity", "above_threshold",
                                           "demand"))
            if f["above_threshold"]:
                blocking.append(f)
        for tool, ok in ran.items():
            if not ok:
                blocking.append({"tool": tool, "rule": "scanner_did_not_run", "file": "", "line": None,
                                 "severity": "high", "risk": f"{tool} ist nicht gelaufen",
                                 "demand": "Gate neu laufen lassen; kein Fall für den Umsetzer"})
        self.condition(cid, "gate", "done", "nothing_above_threshold", not blocking,
                       commit=head, tier=self.tier, threshold=checks.TIER_THRESHOLD[self.tier],
                       findings=len(findings), blocking=len(blocking))
        self.log.write("run_ended", card=cid, column="gate", actor="scanner", sandbox=name, commit=head,
                       evidence={"exit_codes": codes, "versions": versions})
        self.save_state(cid, st)
        if any(not ok for ok in ran.values()):
            raise Halt(f"{cid}: Gate unvollständig, ein Scanner ist nicht gelaufen ({codes}).",
                       "gate_incomplete", step="gate")
        return blocking

    # ---------- Freigabe und Merge

    def _to_approval(self, card: dict, st: dict) -> None:
        cid, base, head = card["id"], st["base"], st["head"]
        report = self.run_dir / cid / "diff-report.md"
        changed = self._names("diff", "--no-renames", "--name-only", base, head)
        sensitive = [f for f in changed if checks.path_matches(f, self.sensitive)]
        ref = f"refs/sandboxes/{st['sandbox']}/{card['branch']}"
        text = [
            f"# Diff-Bericht {cid}: {card['title']}",
            "",
            # Volle Commit-Ids und `refs/heads/main`: Kurzformen und der bloße Name `main`
            # wären mehrdeutig, wenn es ein gleichnamiges Tag gibt.
            f"- Stand: `{head}` auf `{card['branch']}`, Basis `{base}`",
            f"- Ansehen: `git diff {base} {head}` oder `git diff refs/heads/main {ref}`",
            f"- Rückläufe: {st['returns']}",
            f"- Heikle Pfade geändert: {', '.join(sensitive) or 'keine'}",
            f"- Gate-Lauf {st.get('gate_runs')}: Berichte und SBOM unter `{(self.run_dir / cid).relative_to(self.project)}/gate-{st.get('gate_runs')}/`",
            "",
            "## Commits", "",
            "```", self.git("log", "--format=%h %an <%ae>%n    %s", f"{base}..{head}"), "```",
            "",
            "## Geänderte Dateien", "",
            "```", self.git("diff", "--no-renames", "--stat", base, head), "```",
            "",
            "Die Commits stammen aus einer Sandbox und sind ein Fremdbeitrag. Der Bericht des Agenten",
            "steht im Protokoll unter `run_ended` als Behauptung.",
        ]
        report.write_text("\n".join(text) + "\n")
        ready = [
            self.condition(cid, "approval", "ready", "gate_green", self._gate_green(cid, head),
                           commit=head[:10], run=st.get("gate_runs")),
            self.condition(cid, "approval", "ready", "diff_report_present", report.exists(),
                           path=str(report.relative_to(self.project))),
        ]
        if not all(ready):
            raise Halt(f"{cid}: Ready-Bedingungen der Freigabe nicht erfüllt.", "approval_not_ready", step="gate")
        self.save_state(cid, st)  # nach dem Wechsel in die Freigabe folgt nichts mehr
        self._move(cid, "approval", actor="script", commit=head,
                   evidence={"waiting_for": "human", "returns": dict(st["returns"])})

    def merge(self, card_id: str) -> None:
        with self._locked(card_id):
            self._merge(card_id)

    def _merge(self, card_id: str) -> None:
        card, st = self._card_checked(card_id), self.state(card_id)
        head = st.get("head")
        approval = self._approval_state(card_id, head)
        clean = self.git("status", "--porcelain") == ""
        on_main = self.git("rev-parse", "--abbrev-ref", "HEAD") == "main"
        # Trägt `main` den Commit schon, ist ein früherer Merge mitten in den Tests abgebrochen
        # worden, ohne dass er zurückgenommen wurde. Dann gäbe es hier nichts zurückzunehmen.
        on_main_already = bool(head) and self.sh.run(
            ["git", "merge-base", "--is-ancestor", head, "HEAD"], cwd=self.project).rc == 0
        ok = [
            self.condition(card_id, "merged", "ready", "commit_not_yet_on_main", not on_main_already,
                           commit=(head or "")[:10], main=self.git("rev-parse", "HEAD")),
            self.condition(card_id, "merged", "ready", "card_in_approval_at_this_commit",
                           approval["in_approval"], commit=(head or "")[:10],
                           detail=self.column(card_id) or "no column"),
            self.condition(card_id, "merged", "ready", "gate_green_for_this_commit",
                           approval["gate_green"], commit=(head or "")[:10]),
            self.condition(card_id, "merged", "ready", "human_decision_for_this_commit",
                           approval["approved"], commit=(head or "")[:10]),
            self.condition(card_id, "merged", "ready", "main_checked_out_and_clean", clean and on_main),
        ]
        if not all(ok):
            raise Stop(f"{card_id}: kein Merge ohne grünes Gate und Freigabe des Menschen für genau diesen Commit.")
        before = self.git("rev-parse", "HEAD")
        r = self.sh.run(["git", "merge", "--no-ff", "-m", f"Karte {card_id}: {card['title']}", head],
                        cwd=self.project)
        merged = self.condition(card_id, "merged", "done", "merged", r.rc == 0, main_before=before,
                                detail=(r.err or r.out).strip()[-200:])
        if not merged:
            self.sh.run(["git", "merge", "--abort"], cwd=self.project)
            raise Stop(f"{card_id}: Merge gescheitert und abgebrochen.")
        # Erst nach der Freigabe läuft der Code auf dem Mac. `main` ist danach entweder
        # gemergt und grün oder unverändert, auch wenn die Tests abgebrochen werden.
        try:
            t = self.sh.run(["sh", "-c", self.cfg.get("test_command", "uv run --locked pytest -q")],
                            cwd=self.project, timeout=900)
            green = self.condition(card_id, "merged", "done", "tests_green_on_host", t.rc == 0, exit_code=t.rc,
                                   tail=t.out.strip().splitlines()[-1][:200] if t.out.strip() else "")
        except BaseException as exc:
            self._roll_back_merge(card_id, head, before, f"host tests aborted ({type(exc).__name__})")
            raise
        if not green:
            rolled_back = self._roll_back_merge(card_id, head, before, "tests red on host after merge")
            raise Stop(f"{card_id}: Tests auf dem Mac nach dem Merge rot (Exit-Code {t.rc}). "
                       + ("Merge zurückgenommen, `main` unverändert." if rolled_back
                          else "Zurücknehmen gescheitert, `main` trägt den Merge.")
                       + " Die Karte wartet auf den Menschen.")
        self._move(card_id, "merged", actor="script", commit=self.git("rev-parse", "HEAD"))

    def _roll_back_merge(self, card_id: str, head: str, before: str, why: str) -> bool:
        """Setzt `main` auf den Stand vor dem Merge zurück. Das Arbeitsverzeichnis war vor
        dem Merge sauber; verworfen wird nur der eben erzeugte Merge-Commit. Die Karte steht
        danach neu in der Freigabe, die bisherige Entscheidung gilt nicht weiter."""
        back = self.sh.run(["git", "reset", "--hard", before], cwd=self.project)
        rolled_back = self.condition(card_id, "merged", "done", "merge_rolled_back", back.rc == 0,
                                     main=before[:10], detail=back.err.strip()[-200:])
        self._move(card_id, "approval", actor="script", commit=head,
                   reason=f"{why}; merge rolled back" if rolled_back
                          else f"{why}; rollback failed, main holds the merge",
                   evidence={"waiting_for": "human"})
        return rolled_back

    def cleanup(self, card_id: str) -> list[str]:
        """Löscht die Prüf- und Scan-Sandboxes der Karte. Die Arbeits-Sandbox bleibt stehen:
        Sie trägt Commits und Sitzung, über ihr Löschen entscheidet der Mensch."""
        with self._locked(card_id):
            st = self.state(card_id)
            removed = []
            for name in list(st.get("helper_sandboxes", [])):
                if name == st.get("sandbox"):
                    continue
                if self.sbx("rm", "--force", name).rc == 0:
                    removed.append(name)
                    st["helper_sandboxes"].remove(name)
            self.save_state(card_id, st)
            return removed

    def status(self) -> list[str]:
        lines = []
        for cid in self.card_ids():
            st, state = self.state(cid), self.card_state(cid)
            line = (f"{cid}: {state.column or '-'}  head={(st.get('head') or '-')[:10]}  "
                    f"returns={st.get('returns', {})}  sandbox={st.get('sandbox', '-')}")
            if state.halted:
                line += (f"  ANGEHALTEN: {state.halt['cause']}, weiter bei Schritt {state.halt['step']}, "
                         f"am Zug: {state.halt['waiting_for']}")
            lines.append(line)
        return lines
