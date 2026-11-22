"""Prüfungen ohne Seiteneffekte: Pfade, Commits, Schwellen, Scanner-Berichte."""

from __future__ import annotations

import json
import re
from typing import Iterable, Sequence

SEVERITIES = ("low", "medium", "high", "critical")

# Die Schutzstufe bestimmt die Schwelle, nicht den Umfang (Konzept, Grundprinzip 4).
TIER_THRESHOLD = {"hobby": "critical", "internal": "high", "public": "high"}


def _pattern_to_regex(pattern: str) -> re.Pattern:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    return any(_pattern_to_regex(p).match(path) for p in patterns)


def outside_allowed(
    changed: Iterable[str], allowed: Sequence[str], sensitive: Sequence[str] = ()
) -> list[str]:
    """Geänderte Pfade, die nicht erlaubt sind. Heikle Pfade bleiben immer gesperrt."""
    return [
        p for p in changed
        if not path_matches(p, allowed) or path_matches(p, sensitive)
    ]


def first_commit_is_test_only(
    commits: Sequence[Sequence[str]], test_patterns: Sequence[str]
) -> bool:
    """Zwei Commits je Paket: Der erste ändert nur Tests, danach folgt die Umsetzung."""
    if len(commits) < 2 or not commits[0]:
        return False
    return all(path_matches(p, test_patterns) for p in commits[0])


BUILT_ROUTES = ("kurz",)

# Dateien, aus denen der OSV-Scanner Pakete liest. Liegt eine davon im Stand,
# ist „keine Pakete gefunden" kein gültiges Ergebnis.
LOCKFILES = frozenset({
    "uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock", "requirements.txt",
    "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock",
    "Cargo.lock", "go.mod", "Gemfile.lock", "composer.lock", "gradle.lockfile",
})


def route_problem(
    route: str, tier: str, allowed: Sequence[str], sensitive: Sequence[str],
    tracked: Iterable[str] = (),
) -> str | None:
    """Warum die Karte mit dieser Route nicht starten darf; None, wenn sie darf.

    `kurz` gilt nur für Pakete ohne heikle Pfade bei `hobby` und `internal`
    (Workflow.md, Route). Eine Route, die nicht gebaut ist, startet nicht.
    """
    if route not in BUILT_ROUTES:
        return f"route {route!r} is not built"
    if tier == "public":
        return "route kurz is not allowed for tier public"
    hit = sorted(p for p in tracked if path_matches(p, allowed) and path_matches(p, sensitive))
    if hit:
        return f"allowed paths cover sensitive paths: {', '.join(hit[:5])}"
    return None


_CARD_TEXT = ("id", "title", "route", "branch", "model", "effort", "task")
_CARD_LISTS = ("allowed_paths", "test_paths", "acceptance")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*\Z")


def branch_problem(name: str) -> str | None:
    """Der Branch der Karte steht in git-Refs, in Shell-Befehlen in der Sandbox und im Auftrag
    an den Agenten. Erlaubt ist deshalb nur ein schlichter Name: Buchstaben, Ziffern, `.`, `_`,
    `-`, mit `/` getrennte Teile, jeder Teil beginnt mit Buchstabe oder Ziffer."""
    if not _BRANCH.match(name):
        return (f"branch {name!r} is not a plain branch name "
                "(letters, digits, '.', '_', '-', parts separated by '/')")
    if ".." in name or any(part.endswith((".", ".lock")) for part in name.split("/")):
        return f"branch {name!r} is not a valid git ref name"
    return None


def card_problems(card, card_id: str) -> list[str]:
    """Was an der Karte fehlt oder nicht passt. Eine Karte mit Lücken startet nicht."""
    if not isinstance(card, dict):
        return ["card is not a JSON object"]
    problems = []
    for name in _CARD_TEXT:
        if not isinstance(card.get(name), str) or not card[name].strip():
            problems.append(f"{name} missing or empty")
    for name in _CARD_LISTS:
        value = card.get(name)
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
            problems.append(f"{name} missing, empty or not a list of strings")
    if isinstance(card.get("id"), str) and card["id"] != card_id:
        problems.append(f"id {card['id']!r} does not match the file name {card_id!r}")
    if isinstance(card.get("branch"), str) and card["branch"].strip():
        problem = branch_problem(card["branch"])
        if problem:
            problems.append(problem)
    return problems


def tests_ran(result: dict) -> bool:
    """Der Testbefehl ist für diesen Commit gelaufen und hat ein Urteil geliefert.

    Nicht gelaufen: kein Ergebnis, Stand oder Umgebung nicht aufgebaut (`sync` nicht 0)
    oder ein Exit-Code der Shell statt des Testbefehls (126 nicht ausführbar, 127 nicht
    gefunden, ab 128 von einem Signal beendet).
    """
    return result.get("sync") == 0 and isinstance(result.get("pytest"), int) and 0 <= result["pytest"] < 126


def has_lockfile(paths: Iterable[str]) -> bool:
    return any(p.rsplit("/", 1)[-1] in LOCKFILES for p in paths)


def scan_consistent(
    exit_code: int | None, findings: list | None, *,
    no_packages_code: int | None = None, packages_expected: bool = False,
) -> bool:
    """Exit-Code und Bericht eines Scanners müssen zusammenpassen.

    `findings` ist die Liste der gelesenen Funde oder None, wenn der Bericht
    fehlt oder nicht lesbar ist. 0 = nichts gefunden, 1 = Funde. Meldet der
    Scanner Funde, ohne dass der Bericht welche enthält, gilt er als nicht
    gelaufen.
    """
    if exit_code == 0:
        return findings is not None
    if exit_code == 1:
        return bool(findings)
    if no_packages_code is not None and exit_code == no_packages_code:
        return not packages_expected
    return False


def above_threshold(severity: str, tier: str) -> bool:
    threshold = TIER_THRESHOLD[tier]
    if severity not in SEVERITIES:
        severity = "high"  # unbekannte Schwere zählt vorsichtshalber wie hoch
    return SEVERITIES.index(severity) >= SEVERITIES.index(threshold)


def severity_from_score(score) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 9.0:
        return "critical"
    if value >= 7.0:
        return "high"
    if value >= 4.0:
        return "medium"
    return "low"


def _load(text: str):
    text = (text or "").strip()
    if not text:
        return None
    return json.loads(text)


_GITLEAKS_SCOPES = {
    "tree": ("gitleaks", "",
             "Secret aus dem Code entfernen und den Wert aus einer Umgebungsvariable lesen. "
             "Ein echtes Secret gilt als offengelegt und muss ersetzt werden."),
    "commits": ("gitleaks-history", " (in einem Commit der Karte, auch wenn der Stand es nicht mehr enthält)",
                "Die Commits des Branches so ersetzen, dass keiner das Secret enthält. "
                "Ein weiterer Commit, der es nur entfernt, genügt nicht. Ein echtes "
                "Secret gilt als offengelegt und muss ersetzt werden."),
    "messages": ("gitleaks-messages", " (im Kopf oder in der Nachricht eines Commits der Karte)",
                 "Den Commit so ersetzen, dass weder seine Nachricht noch die Angaben zu Autor und "
                 "Committer das Secret enthalten. Ein weiterer Commit behebt das nicht. Ein echtes "
                 "Secret gilt als offengelegt und muss ersetzt werden."),
}


def _gitleaks(report: str, scope: str) -> list[dict]:
    tool, where, demand = _GITLEAKS_SCOPES[scope]
    findings = []
    for item in _load(report) or []:
        finding = {
            "tool": tool,
            "rule": item.get("RuleID", ""),
            "file": item.get("File", ""),
            "line": item.get("StartLine"),
            "severity": "critical",
            "risk": item.get("Description", "Secret im Code") + where,
            "demand": demand,
        }
        if scope == "commits":
            finding["found_in"] = item.get("Commit", "")
        if scope == "messages":
            # Je Commit eine Datei, benannt nach dem Commit; die Zeile zählt im rohen Commit-Objekt.
            finding.update(found_in=finding["file"].rsplit("/", 1)[-1],
                           file="(Kopf oder Nachricht des Commits)", line=None)
        findings.append(finding)
    return findings


# Felder eines Gitleaks-Funds, die keinen Text aus dem geprüften Code oder aus einem Commit tragen.
# Nicht dabei: `Match` und `Secret` (mit `--redact` geschwärzt) sowie `Message`, `Author`, `Email`
# und `Date` des Commits, die `--redact` nicht schwärzt.
_GITLEAKS_SAFE_FIELDS = ("RuleID", "Description", "File", "SymlinkFile", "StartLine", "EndLine",
                         "StartColumn", "EndColumn", "Commit", "Entropy", "Tags", "Fingerprint")


def gitleaks_report_without_text(report: str) -> str | None:
    """Der Bericht nur mit den Feldern, die in den Projektordner dürfen; None, wenn er nicht lesbar ist."""
    try:
        items = json.loads(report)
    except json.JSONDecodeError:
        return None
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return None
    kept = [{k: item[k] for k in _GITLEAKS_SAFE_FIELDS if k in item} for item in items]
    return json.dumps(kept, indent=1, ensure_ascii=False) + "\n"


def parse_gitleaks(report: str, history: bool = False) -> list[dict]:
    """Ein Secret im Code liegt in jeder Stufe über der Schwelle. Der Wert wird nicht übernommen.

    Mit `history` stammt der Bericht aus dem Lauf über die Commits der Karte. Der Fund
    nennt dann den Commit (`found_in`), und ein weiterer Commit behebt ihn nicht: Das
    Secret bliebe in der Historie und käme mit dem Merge nach `main`."""
    return _gitleaks(report, "commits" if history else "tree")


def parse_gitleaks_messages(report: str) -> list[dict]:
    """Bericht des Laufs über die Köpfe der Commits: je Commit eine Datei, benannt nach dem Commit.

    Sie enthält das rohe Commit-Objekt, also Autor, Committer, weitere Kopfzeilen und die
    Nachricht. Der Fund nennt den Commit (`found_in`) und zählt wie einer in der Historie."""
    return _gitleaks(report, "messages")


def parse_osv(report: str) -> list[dict]:
    """Je Schwachstellen-Gruppe ein Fund. Schadpaket-Einträge (MAL-…) sind immer kritisch."""
    findings = []
    data = _load(report) or {}
    for result in data.get("results") or []:
        source = (result.get("source") or {}).get("path", "")
        lockfile = source.rsplit("/", 1)[-1]
        for pkg in result.get("packages") or []:
            info = pkg.get("package") or {}
            summaries = {v.get("id"): v.get("summary", "") for v in pkg.get("vulnerabilities") or []}
            for group in pkg.get("groups") or []:
                ids = group.get("ids") or []
                if not ids:
                    continue
                malware = any(i.startswith("MAL-") for i in ids)
                severity = "critical" if malware else severity_from_score(group.get("max_severity"))
                name, version = info.get("name", "?"), info.get("version", "?")
                summary = next((summaries[i] for i in ids if summaries.get(i)), "")
                findings.append({
                    "tool": "osv-scanner",
                    "rule": ids[0],
                    "file": lockfile,
                    "line": None,
                    "severity": severity,
                    "risk": f"{name} {version} ({info.get('ecosystem', '?')}): {summary}".rstrip(": "),
                    "demand": "Paket entfernen und Herkunft klären." if malware
                              else "Auf eine Version ohne diese Schwachstelle heben "
                                   "oder die Ausnahme mit Begründung vom Menschen setzen lassen.",
                })
    return findings


_NET_KEYS = ("host", "count", "proxy_type", "rule", "reason", "first_seen", "last_seen")


def parse_policy_log(report: str, sandbox: str) -> dict:
    """Ausgabe von ``sbx policy log --json`` auf eine Sandbox eingegrenzt.

    Liefert ``{"allowed": [...], "blocked": [...]}``, je Eintrag ein Ziel
    (``host`` mit Port) samt Zähler, sortiert nach ``host``. Die Zähler sind
    Summen seit dem Anlegen der Sandbox. Ein unlesbarer Bericht ist ein
    ``ValueError``: Kein Bericht heißt nicht „kein Verkehr".
    """
    try:
        data = json.loads(report)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Netzwerk-Protokoll ist kein JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Netzwerk-Protokoll ist kein Objekt")
    result = {}
    for kind in ("allowed", "blocked"):
        entries = data.get(kind) or []
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise ValueError(f"Netzwerk-Protokoll: {kind} ist keine Liste von Einträgen")
        cleaned = []
        for e in entries:
            if e.get("sandbox_id") != sandbox:
                continue
            entry = {k: e[k] for k in _NET_KEYS if e.get(k) not in (None, "")}
            try:
                # Der Zähler wird summiert; ein Wert, der keine Zahl ist, macht den Bericht unlesbar.
                entry["count"] = int(e.get("count") or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Netzwerk-Protokoll: Zähler ist keine Zahl ({e.get('count')!r})") from exc
            cleaned.append(entry)
        result[kind] = sorted(cleaned, key=lambda e: str(e.get("host", "")))
    return result
