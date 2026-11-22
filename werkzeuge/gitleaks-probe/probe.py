#!/usr/bin/env python3
"""Erprobt die Gitleaks-Läufe des Gates mit echtem Gitleaks in einer Scan-Sandbox.

Die Tests des Projekts spielen Gitleaks mit einer Attrappe. Ob das Gitleaks und das
git der Scan-Vorlage die Aufrufe des Gates kennen und alles lesen, was sie lesen
sollen, zeigt nur ein Lauf in einer Scan-Sandbox. Nach jedem Neubau der Scan-Vorlage
wiederholen.

    python3 werkzeuge/gitleaks-probe/probe.py [--name gitleaks-probe] [--rm]

Das Skript baut mit `build.sh` ein Wegwerf-Repository außerhalb des Projekts, legt
daraus eine Scan-Sandbox an, führt dort die Shell-Zeilen des Gates aus (dieselben,
die `kreislauf` verwendet) und vergleicht die Berichte mit der Erwartung. Exit 0:
alles wie erwartet, 1: Abweichung, 2: Sandbox oder Repository nicht angelegt.

`sbx create` braucht den entsperrten Schlüsselbund, läuft also im Terminal und nicht
aus einer Agenten-Sitzung. Ohne `--rm` bleiben Sandbox und Repository stehen; das
Skript nennt die Befehle zum Aufräumen.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from kreislauf.checks import gitleaks_report_without_text  # noqa: E402
from kreislauf.cycle import SCAN_TEMPLATE, extract_snippet, secrets_snippet  # noqa: E402

REPORTS = ("gitleaks", "gitleaks-history", "gitleaks-messages")


def gate_lines() -> str:
    """Die Zeilen des Gates bis zum letzten Gitleaks-Lauf; danach je Bericht eine Zeile."""
    return ("set -u\n" + extract_snippet()
            + 'sha="$1"; base="$2"; rm -rf "$HOME/scan" "$HOME/out"; mkdir -p "$HOME/out"\n'
            + 'extract "$sha" "$HOME/scan" || exit\n' + secrets_snippet()
            + 'for r in gitleaks gitleaks-history gitleaks-messages; do\n'
            + '  echo "REPORT $r $(tr -d \'\\n\' < "$HOME/out/$r.json")"\ndone\n'
            + 'echo "VERSION git $(git --version)"; echo "VERSION gitleaks $(gitleaks version)"\n')


def expectations(marks: dict[str, str]) -> list[dict]:
    """Was die drei Läufe je Fall melden sollen: Dateien, bei den Köpfen Commits."""
    return [
        {"title": "karte: Basis ohne Regeln", "head": marks["head"], "base": marks["base"], "commits": 14,
         "gitleaks": {"src/keep.py"},
         "gitleaks-history": {"src/plain.py", "src/bin.dat", "src/attr.py", "src/allow.py", "src/merge.py",
                              "src/keep.py"},
         "gitleaks-messages": {marks["msg"]},
         "commit_of": {"src/merge.py": marks["merge"]}},
        {"title": "karte2: Basis nimmt src/keep.py aus, die Karte lockert die Regeln",
         "head": marks["head2"], "base": marks["head"], "commits": 1,
         "gitleaks": {"src/other.py"},
         "gitleaks-history": {"src/other.py"},
         "gitleaks-messages": {marks["head2"]},
         "commit_of": {}},
    ]


def parse(output: str) -> dict:
    """Die Ausgabe der Gate-Zeilen: Exit-Codes, Zähler, Funde je Bericht, Versionen."""
    result = {"codes": {}, "seen": {}, "found": {}, "commit_of": {}, "versions": {}, "unreadable": [],
              "raw_with_token": [], "with_token": []}
    for line in output.splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[0] == "RESULT" and parts[2].strip().lstrip("-").isdigit():
            result["codes"][parts[1]] = int(parts[2])
        elif len(parts) == 3 and parts[0] == "HISTORY" and parts[2].strip().isdigit():
            result["seen"][parts[1]] = int(parts[2])
        elif len(parts) == 3 and parts[0] == "VERSION":
            result["versions"][parts[1]] = parts[2].strip()
        elif len(parts) >= 2 and parts[0] == "REPORT":
            name = parts[1]
            # Gitleaks schreibt Nachricht und Autor eines Commits ungeschwärzt in den Bericht. In den
            # Projektordner kommt nur, was der Filter des Koordinators übrig lässt.
            if "ghp_" in line:
                result["raw_with_token"].append(name)
            if len(parts) == 3 and "ghp_" in (gitleaks_report_without_text(parts[2]) or ""):
                result["with_token"].append(name)
            try:
                items = json.loads(parts[2]) if len(parts) == 3 else None
            except json.JSONDecodeError:
                items = None
            if not isinstance(items, list):
                result["unreadable"].append(name)
                continue
            files = {str(item.get("File", "")) for item in items}
            if name == "gitleaks-messages":
                files = {f.rsplit("/", 1)[-1] for f in files}  # je Commit eine Datei, benannt nach dem Commit
            result["found"][name] = files
            if name == "gitleaks-history":
                result["commit_of"] = {str(item.get("File", "")): item.get("Commit", "") for item in items}
    return result


def deviations(case: dict, result: dict) -> list[str]:
    """Wo das Ergebnis von der Erwartung abweicht; leer, wenn alles passt."""
    problems = []
    for name in REPORTS:
        want = case[name]
        if name in result["unreadable"] or name not in result["found"]:
            problems.append(f"{name}: Bericht fehlt oder ist nicht lesbar (Exit {result['codes'].get(name)})")
            continue
        got = result["found"][name]
        if got - want:
            problems.append(f"{name}: unerwartet gemeldet {sorted(got - want)}")
        if want - got:
            problems.append(f"{name}: nicht gemeldet {sorted(want - got)}")
        code = result["codes"].get(name)
        if code != int(bool(want)):
            problems.append(f"{name}: Exit-Code {code}, erwartet {int(bool(want))}")
    for name in result["with_token"]:
        problems.append(f"{name}: Der Bericht enthält auch nach dem Filter ein Token im Klartext")
    for counter in ("commits", "messages"):
        if result["seen"].get(counter) != case["commits"]:
            problems.append(f"HISTORY {counter}: {result['seen'].get(counter)}, erwartet {case['commits']}")
    for path, commit in case["commit_of"].items():
        if path in result["commit_of"] and result["commit_of"][path] != commit:
            problems.append(f"gitleaks-history: {path} in Commit {result['commit_of'][path]}, erwartet {commit}")
    return problems


def build(dest: Path) -> dict[str, str]:
    done = subprocess.run(["sh", str(HERE / "build.sh"), str(dest)], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"build.sh ist gescheitert: {done.stderr.strip()[-300:]}")
    return dict(line.split() for line in done.stdout.splitlines() if len(line.split()) == 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gitleaks-Läufe des Gates in einer Scan-Sandbox erproben.")
    parser.add_argument("--name", default="gitleaks-probe", help="Name der Sandbox")
    parser.add_argument("--rm", action="store_true", help="Sandbox und Repository am Ende löschen")
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="gitleaks-probe-"))
    repo = work / "repo"
    try:
        marks = build(repo)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"Repository: {repo}\nVorlage:    {SCAN_TEMPLATE}\nSandbox:    {args.name}")
    created = subprocess.run(["sbx", "create", "--clone", "--pull", "never", "-t", SCAN_TEMPLATE,
                              "--name", args.name, "shell", str(repo)], capture_output=True, text=True)
    if created.returncode != 0:
        print(f"Sandbox ließ sich nicht anlegen: {(created.stderr or created.stdout).strip()[-400:]}",
              file=sys.stderr)
        shutil.rmtree(work, ignore_errors=True)
        return 2

    failed, versions = False, {}
    for case in expectations(marks):
        ran = subprocess.run(["sbx", "exec", args.name, "sh", "-c", gate_lines(), "sh", case["head"], case["base"]],
                             capture_output=True, text=True)
        result = parse(ran.stdout)
        problems = deviations(case, result)
        if ran.returncode != 0:
            problems.insert(0, f"Gate-Zeilen endeten mit Exit {ran.returncode}: {ran.stderr.strip()[-300:]}")
        failed = failed or bool(problems)
        print(f"\n{case['title']}")
        for name in REPORTS:
            print(f"  {name}: {len(result['found'].get(name, []))} gemeldet, erwartet {len(case[name])}")
        for name in result["raw_with_token"]:
            print(f"  Hinweis: Der Rohbericht {name} trägt ein Token im Klartext (Nachricht oder Autor des "
                  "Commits); der Filter des Koordinators nimmt es heraus.")
        for problem in problems:
            print(f"  ABWEICHUNG {problem}")
        versions = result["versions"]
    print(f"\nGitleaks {versions.get('gitleaks', '?')}, {versions.get('git', 'git ?')}")
    print("ABWEICHUNGEN, siehe oben." if failed else "Alle Fälle wie erwartet.")

    if args.rm:
        subprocess.run(["sbx", "rm", "--force", args.name], capture_output=True, text=True)
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"\nAufräumen:\n  sbx rm --force {args.name}\n  rm -rf {work}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
