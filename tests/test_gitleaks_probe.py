"""Die Probe für echtes Gitleaks (`werkzeuge/gitleaks-probe/`): Das Bauskript legt die
Fälle an, die Auswertung erkennt, wenn ein Lauf einen davon nicht meldet. Der Lauf in
einer Scan-Sandbox selbst ist hier nicht getestet."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from helpers import git

PROBE = Path(__file__).resolve().parents[1] / "werkzeuge" / "gitleaks-probe"
spec = importlib.util.spec_from_file_location("gitleaks_probe", PROBE / "probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    repo = tmp_path_factory.mktemp("probe") / "repo"
    return repo, probe.build(repo)


def test_the_build_script_holds_no_token():
    text = (PROBE / "build.sh").read_text() + (PROBE / "probe.py").read_text()
    assert "ghp_%s" in text  # nur das Muster, aus dem die Tokens entstehen
    assert not [word for word in text.split() if word.startswith("ghp_") and len(word) > 12]


def test_the_build_script_lays_out_every_case(built):
    repo, marks = built
    assert set(marks) == {"base", "head", "msg", "merge", "head2"}
    assert git(repo, "rev-parse", "main") == marks["base"] and git(repo, "rev-parse", "karte") == marks["head"]
    assert git(repo, "rev-list", "--count", "main..karte") == "14"
    assert git(repo, "rev-list", "--merges", "main..karte") == marks["merge"]
    assert git(repo, "rev-list", "karte..karte2") == marks["head2"]
    assert "ghp_" in git(repo, "log", "-1", "--format=%B", marks["msg"])
    # Im Stand der Karte bleibt nur das Token, das ihre eigene Ausnahme verdeckt.
    assert git(repo, "grep", "-l", "ghp_", marks["head"]).split() == [f"{marks['head']}:src/keep.py"]
    assert git(repo, "ls-tree", "--name-only", marks["base"]).split() == ["README.md", "src"]  # Basis ohne Regeln
    plain = subprocess.run(["git", "log", "-p", "main..karte"], cwd=repo, capture_output=True).stdout
    assert b"Binary files" in plain  # die Datei mit NUL-Byte und die hinter `-diff`


def test_each_build_makes_new_tokens(built, tmp_path):
    repo, _ = built
    again = tmp_path / "repo"
    probe.build(again)
    assert git(repo, "show", "karte:src/keep.py") != git(again, "show", "karte:src/keep.py")


def output_for(case, **changes):
    """Die Ausgabe der Gate-Zeilen, wie sie bei erfüllter Erwartung aussieht."""
    found = {name: set(case[name]) for name in probe.REPORTS} | changes
    lines = []
    for name in probe.REPORTS:
        items = [{"RuleID": "github-pat", "File": f, "Commit": case["commit_of"].get(f, "")} for f in sorted(found[name])]
        lines += [f"RESULT {name} {int(bool(items))}", f"REPORT {name} {json.dumps(items)}"]
    lines += [f"HISTORY commits {case['commits']}", f"HISTORY messages {case['commits']}",
              "VERSION git git version 2.47.3", "VERSION gitleaks 8.30.1"]
    return "\n".join(lines)


def test_the_expected_result_shows_no_deviation(built):
    for case in probe.expectations(built[1]):
        result = probe.parse(output_for(case))
        assert probe.deviations(case, result) == []
        assert result["versions"] == {"git": "git version 2.47.3", "gitleaks": "8.30.1"}


def test_a_case_that_gitleaks_misses_is_a_deviation(built):
    case = probe.expectations(built[1])[0]
    missed = case["gitleaks-history"] - {"src/merge.py"}
    problems = probe.deviations(case, probe.parse(output_for(case, **{"gitleaks-history": missed})))
    assert problems == ["gitleaks-history: nicht gemeldet ['src/merge.py']"]


def test_an_exception_of_the_card_that_applies_is_a_deviation(built):
    case = probe.expectations(built[1])[0]
    problems = probe.deviations(case, probe.parse(output_for(case, gitleaks=set())))
    assert "gitleaks: nicht gemeldet ['src/keep.py']" in problems


def test_a_missing_report_a_wrong_count_and_a_token_in_a_report_are_deviations(built):
    case = probe.expectations(built[1])[1]
    lines = [line for line in output_for(case).splitlines() if not line.startswith("REPORT gitleaks-messages")]
    lines = [line.replace("HISTORY commits 1", "HISTORY commits 0") for line in lines]
    # Im Dateinamen übersteht ein Token den Filter des Koordinators, in der Commit-Nachricht nicht.
    lines = [line.replace('"RuleID"', '"Message": "ghp_' + "x" * 8 + '", "RuleID"')
             if line.startswith("REPORT gitleaks-history ") else line for line in lines]
    lines = [line.replace("src/other.py", "src/ghp_" + "y" * 8 + ".py") if line.startswith("REPORT gitleaks ")
             else line for line in lines]
    result = probe.parse("\n".join(lines))
    problems = probe.deviations(case, result)
    assert any(p.startswith("gitleaks-messages: Bericht fehlt") for p in problems)
    assert "HISTORY commits: 0, erwartet 1" in problems
    assert result["raw_with_token"] == ["gitleaks", "gitleaks-history"] and result["with_token"] == ["gitleaks"]
    assert "gitleaks: Der Bericht enthält auch nach dem Filter ein Token im Klartext" in problems
    assert not any(p.startswith("gitleaks-history: Der Bericht") for p in problems)


def test_the_gate_lines_of_the_probe_are_those_of_the_cycle():
    from kreislauf.cycle import extract_snippet, secrets_snippet
    lines = probe.gate_lines()
    assert extract_snippet() in lines and secrets_snippet() in lines
    assert subprocess.run(["sh", "-n", "-c", lines]).returncode == 0  # syntaktisch gültig
