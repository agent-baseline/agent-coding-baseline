"""Bausteine für die Tests des Ablaufs: ein kleines Projekt mit einer Karte
und eine Shell, die git wirklich ausführt und `sbx` nur spielt."""

from __future__ import annotations

import fcntl
import itertools
import json
import os
import subprocess
from pathlib import Path

from kreislauf.cycle import Cycle, Result, Sh

CARD = {
    "id": "K1", "title": "Demo", "route": "kurz", "branch": "karte/k1",
    "model": "m", "effort": "medium", "allowed_paths": ["src/**", "tests/**"],
    "test_paths": ["tests/**"], "acceptance": ["A liefert 1."], "task": "Lege A an.",
}

# Berichte, wie die Scanner sie schreiben.
CLEAN = {
    "gitleaks.json": "[]",
    "gitleaks-history.json": "[]",
    "gitleaks-messages.json": "[]",
    "osv.json": json.dumps({"results": []}),
    "sbom.cdx.json": json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []}),
}
LEAK = json.dumps([{"RuleID": "github-pat", "Description": "Token", "File": "src/a.py", "StartLine": 1}])
VULN = json.dumps({"results": [{"source": {"path": "/home/agent/scan/uv.lock"}, "packages": [{
    "package": {"name": "jinja2", "version": "3.1.2", "ecosystem": "PyPI"},
    "vulnerabilities": [{"id": "GHSA-h5c8-rqwp-cp95", "summary": "Sandbox breakout"}],
    "groups": [{"ids": ["GHSA-h5c8-rqwp-cp95"], "max_severity": "8.4"}],
}]}]})
CODES = {"gitleaks": 0, "gitleaks-history": 0, "gitleaks-messages": 0, "osv-scanner": 0, "syft": 0}
GATE_CLEAN = (CODES, CLEAN)
GATE_LEAK = ({**CODES, "gitleaks": 1}, {**CLEAN, "gitleaks.json": LEAK})


def history_leak(commit: str, file: str = "src/a.py") -> tuple[dict, dict]:
    """Ein Gate-Lauf, in dem nur der Lauf über die Commits der Karte etwas meldet: Der Stand ist sauber."""
    report = json.dumps([{"RuleID": "github-pat", "Description": "Token", "File": file, "StartLine": 1,
                          "Commit": commit}])
    return {**CODES, "gitleaks-history": 1}, {**CLEAN, "gitleaks-history.json": report}


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


class FakeSh(Sh):
    """`sbx` läuft nie; ohne eingestellte Antwort gelingt jeder `sbx`-Aufruf ohne Ausgabe.
    Alles andere (git, sh) läuft wirklich, außer es ist eine Antwort eingestellt."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._replies: list[tuple[tuple[str, ...], object]] = []

    def on(self, prefix, reply) -> None:
        """Antwort für Aufrufe, die mit `prefix` beginnen: ein Result oder eine Funktion(argv)."""
        self._replies.insert(0, (tuple(prefix), reply))

    def run(self, argv, cwd=None, timeout=None) -> Result:
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        for prefix, reply in self._replies:
            if tuple(argv[:len(prefix)]) == prefix:
                return reply(argv) if callable(reply) else reply
        if argv[0] == "sbx":
            return Result(0, "", "")
        return super().run(argv, cwd=cwd, timeout=timeout)

    def called(self, *prefix: str) -> bool:
        return any(tuple(c[:len(prefix)]) == prefix for c in self.calls)


def make_cycle(tmp_path: Path) -> Cycle:
    """Projekt `demo` auf `main`. Karte K1 hat auf `karte/k1` zwei Commits, erst den Test,
    dann die Umsetzung. Der Arbeitsstand nennt den Kopf des Branches als abgeholten Stand."""
    project = tmp_path / "demo"
    (project / ".baseline" / "jobs" / "j1" / "cards").mkdir(parents=True)
    (project / ".baseline" / "project.json").write_text(json.dumps({
        "project": "demo", "tier": "internal", "sensitive_paths": [".baseline/**", "uv.lock"],
        "test_command": "true",
    }))
    (project / ".baseline" / "jobs" / "j1" / "cards" / "K1.json").write_text(json.dumps(CARD))
    (project / "AGENTS.md").write_text("# Demo\n\n## Sicherheitsmodell\n\nNichts.\n")
    (project / "uv.lock").write_text("version = 1\n")
    (project / ".gitignore").write_text(".baseline/runs/\n")
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.name", "t")
    git(project, "config", "user.email", "t@example.invalid")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "start")
    git(project, "checkout", "-q", "-b", "karte/k1")
    (project / "tests").mkdir()
    (project / "tests" / "test_a.py").write_text("from src.a import A\n\n\ndef test_a():\n    assert A == 1\n")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "test")
    (project / "src").mkdir()
    (project / "src" / "a.py").write_text("A = 1\n")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "karte")
    head = git(project, "rev-parse", "HEAD")
    git(project, "checkout", "-q", "main")
    c = Cycle(project, "j1", sh=FakeSh())
    c.save_state("K1", {"sandbox": "x", "base": git(project, "rev-parse", "main"),
                        "branch": "karte/k1", "head": head, "returns": {"gate": 0, "checks": 0}, "round": 1})
    return c


def set_card(cycle: Cycle, **changes) -> dict:
    """Ändert die Karte und committet sie auf `main`, damit das Arbeitsverzeichnis sauber bleibt."""
    card = {**CARD, **changes}
    path = cycle.project / ".baseline" / "jobs" / "j1" / "cards" / "K1.json"
    path.write_text(json.dumps(card))
    if git(cycle.project, "status", "--porcelain"):
        git(cycle.project, "commit", "-q", "-am", "karte geändert")
    return card


def rebase_branch(cycle: Cycle, branch: str = "karte/k1") -> None:
    """Zieht den Branch der Karte auf den Stand von `main` nach. In der Sandbox entsteht er
    aus dem Stand beim Start; nach einem neuen Commit auf `main` muss er hier folgen."""
    git(cycle.project, "checkout", "-q", branch)
    git(cycle.project, "rebase", "-q", "main")
    git(cycle.project, "checkout", "-q", "main")


def gate_result(cycle: Cycle, head: str, ok: bool = True) -> None:
    """Protokolliert das Ergebnis eines Gate-Laufs für diesen Commit, wie `_gate` es schreibt."""
    cycle.condition("K1", "gate", "done", "nothing_above_threshold", ok, commit=head,
                    tier="internal", threshold="high", findings=0 if ok else 1, blocking=0 if ok else 1)


def to_approval(cycle: Cycle, gate_ok: bool = True) -> str:
    """Stellt die Karte so ins Protokoll, wie `run` sie nach einem grünen Gate hinterlässt."""
    head = cycle.state("K1")["head"]
    cycle.init()
    cycle.log.write("card_moved", card="K1", column="implementation", actor="script")
    cycle.log.write("card_moved", card="K1", column="gate", actor="script", commit=head)
    gate_result(cycle, head, ok=gate_ok)
    cycle.log.write("card_moved", card="K1", column="approval", actor="script", commit=head,
                    evidence={"waiting_for": "human"})
    return head


def release(cycle: Cycle) -> None:
    cycle.init()
    cycle.decide("K1", "decomposition", "approved", "ja")


class Sandboxes:
    """Spielt die Sandboxes eines ganzen Laufs: Agent, Abholen, Tests und Scanner.

    Ohne Vorgabe gelingt alles: Der Agent meldet sich fertig, das Abholen liefert den
    Kopf von `karte/k1`, die Tests sind am Test-Commit rot und am Stand grün, die
    Scanner finden nichts. Die Listen nehmen Vorgaben für die nächsten Aufrufe auf;
    eine Ausnahme in der Liste wird geworfen, eine Funktion vor dem Aufruf ausgeführt.
    """

    def __init__(self, cycle: Cycle) -> None:
        self.cycle = cycle
        self.agent: list = []    # Result oder Ausnahme je Agentenlauf
        self.tests: list = []    # (Exit-Code am Test-Commit, Exit-Code am Stand) oder Ausnahme
        self.gate: list = []     # (Exit-Codes, Berichte) je Gate-Lauf
        self.fetch = Result(0, "", "")
        self.history_commits = None  # so viele Commits sieht die Scan-Sandbox; None: so viele, wie es sind
        self.message_files = None    # so viele Commit-Köpfe hat sie geschrieben; None: so viele, wie es sind
        self.scanner = None          # Funktion(base, head) -> (Exit-Codes, Berichte), statt der Vorgaben in `gate`
        self.agent_calls: list[dict] = []
        self.test_runs = 0
        self.gate_runs = 0
        self.faults = 0
        self._files = CLEAN
        self._verify = None  # Vorgabe der laufenden Prüfung, zwischen ihren beiden Aufrufen
        cycle.sh.on(("sbx", "exec"), self._exec)
        cycle.sh.on(("sbx", "cp"), self._cp)
        cycle.sh.on(("git", "fetch"), self._fetch)

    @staticmethod
    def _next(queue: list, default):
        """Die nächste Vorgabe: eine Ausnahme wird geworfen, eine Funktion vorher
        aufgerufen (etwa um einen Commit anzulegen), None steht für den Normalfall."""
        item = queue.pop(0) if queue else default
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item()
            return default
        return default if item is None else item

    def _exec(self, argv):
        if "claude" in argv:
            self.agent_calls.append({
                "prompt": argv[argv.index("-p") + 1], "model": argv[argv.index("--model") + 1],
                "resume": "--resume" in argv,
            })
            done = json.dumps({"result": "fertig", "session_id": "s1", "usage": {"input_tokens": 1}})
            return self._next(self.agent, Result(0, done, ""))
        script = argv[argv.index("-c") + 1] if "-c" in argv else ""
        if "FAULT_TOKEN" in script:  # der Trockenlauf baut seinen Fehler ein und nennt den Commit
            self.faults += 1
            return Result(0, f"{self.faults:040x}\n", "")
        if "gitleaks" in script:
            self.gate_runs += 1
            head, base = argv[-2], argv[-1]
            codes, self._files = self.scanner(base, head) if self.scanner else self._next(self.gate, GATE_CLEAN)
            commits = git(self.cycle.project, "rev-list", "--count", f"{base}..{head}")
            seen = commits if self.history_commits is None else self.history_commits
            written = commits if self.message_files is None else self.message_files
            return Result(0, "".join(f"RESULT {t} {c}\n" for t, c in codes.items())
                          + f"HISTORY commits {seen}\nHISTORY messages {written}\nVERSION gitleaks v8 x\n", "")
        if "pytest=" in script:
            # Je Commit ein Aufruf in eigener Sandbox: erst der Test-Commit, dann der Stand.
            # Eine Vorgabe gilt für beide Aufrufe einer Prüfung.
            if self._verify is None:
                self.test_runs += 1
                outcome, position = self._next(self.tests, (1, 0)), 0
                self._verify = outcome
            else:
                outcome, position, self._verify = self._verify, 1, None
            if isinstance(outcome, Result):  # die Prüf-Sandbox antwortet mit etwas anderem
                return outcome
            return Result(0, f"RESULT {argv[-1]} sync=0 pytest={outcome[position]}\n", "")
        return Result(0, "", "")

    def _cp(self, argv):
        name = argv[-1].rsplit("/", 1)[-1]
        if name not in self._files:
            return Result(1, "", "no such file")
        Path(argv[-1]).write_text(self._files[name])
        return Result(0, "", "")

    def _fetch(self, argv):
        if self.fetch.rc == 0:
            head = git(self.cycle.project, "rev-parse", "karte/k1")
            git(self.cycle.project, "update-ref", f"refs/remotes/{argv[-1]}/karte/k1", head)  # der Remote steht zuletzt
        return self.fetch


def moves(cycle: Cycle, card_id: str = "K1") -> list[str]:
    """Die Spalten, durch die die Karte gegangen ist, in der Reihenfolge des Protokolls."""
    return [e["column"] for e in cycle.events(card_id) if e["event"] in ("card_created", "card_moved", "card_returned")]


def halt(cycle: Cycle, card_id: str = "K1") -> dict | None:
    return cycle.card_state(card_id).halt


_worktrees = itertools.count(1)


def on_branch(cycle: Cycle, work, branch: str = "karte/k1") -> str:
    """Ändert den Branch der Karte, wie der Agent es in der Sandbox tut: `work(baum)` läuft
    in einem eigenen Arbeitsbaum; `main` bleibt ausgecheckt und sauber. Liefert den neuen Kopf."""
    tree = cycle.project.parent / f"arbeitsbaum-{next(_worktrees)}"
    git(cycle.project, "worktree", "add", "-q", str(tree), branch)
    work(tree)
    git(cycle.project, "worktree", "remove", "--force", str(tree))
    return git(cycle.project, "rev-parse", branch)


def _commit(tree: Path, files: dict[str, str], message: str) -> None:
    for path, content in files.items():
        (tree / path).parent.mkdir(parents=True, exist_ok=True)
        (tree / path).write_text(content)
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", message)


def commit_on_branch(cycle: Cycle, path: str, content: str) -> str:
    """Ein neuer Commit auf dem Branch der Karte."""
    return on_branch(cycle, lambda tree: _commit(tree, {path: content}, f"ändert {path}"))


def rename_on_branch(cycle: Cycle, old: str, new: str) -> str:
    def work(tree):
        (tree / new).parent.mkdir(parents=True, exist_ok=True)
        git(tree, "mv", old, new)
        git(tree, "commit", "-q", "-m", f"verschiebt {old}")
    return on_branch(cycle, work)


def rebuild_branch(cycle: Cycle, *commits: dict[str, str]) -> str:
    """Baut den Branch von `main` neu auf, mit einem Commit je Dateisatz."""
    def work(tree):
        git(tree, "reset", "-q", "--hard", "main")
        for number, files in enumerate(commits, 1):
            _commit(tree, files, f"Commit {number}")
    return on_branch(cycle, work)


TEST_FILE = {"tests/test_a.py": "from src.a import A\n\n\ndef test_a():\n    assert A == 1\n"}
CODE_FILE = {"src/a.py": "A = 1\n"}


def hold_lock(cycle: Cycle, card_id: str = "K1") -> int:
    """Hält die Sperre der Karte, wie ein anderer laufender Prozess. Freigeben mit os.close."""
    path = cycle.run_dir / card_id / "lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.pwrite(fd, b"4711", 0)
    return fd


def lock_is_free(cycle: Cycle, card_id: str = "K1") -> bool:
    try:
        os.close(hold_lock(cycle, card_id))
    except OSError:
        return False
    return True


def killed(cycle: Cycle):
    """Kontext: Der Prozess stirbt, ohne noch einen Halt schreiben zu können."""
    class _Killed:
        def __enter__(self_inner):
            self_inner.write, cycle._halt = cycle._halt, lambda *args, **kwargs: False

        def __exit__(self_inner, *exc):
            cycle._halt = self_inner.write
            return False
    return _Killed()
