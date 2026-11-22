"""Das Gate prüft auch die Commits der Karte: Ein Secret, das ein früherer Commit
enthielt und ein späterer entfernt hat, käme mit dem Merge sonst nach `main`. Dazu
gehören Merge-Commits, Dateien, die git für binär hält, und die Köpfe der Commits.
Regeln und Ausnahmen für Gitleaks gelten so, wie sie in der Basis stehen."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from helpers import (CARD, CLEAN, CODE_FILE, CODES, TEST_FILE, Sandboxes, commit_on_branch, git, halt,
                     history_leak, make_cycle, moves, on_branch, rebuild_branch, release)
from kreislauf.checks import gitleaks_report_without_text, parse_gitleaks, parse_gitleaks_messages
from kreislauf.cycle import Stop, extract_snippet, secrets_snippet

# Erfunden, im Format eines GitHub-Tokens. Erst zur Laufzeit zusammengesetzt, damit
# kein Scanner diese Datei meldet.
TOKEN = "ghp_" + "Ab3" * 12
WITH_TOKEN = f'TOKEN = "{TOKEN}"\n'
WITHOUT_TOKEN = 'import os\n\nTOKEN = os.environ["TOKEN"]\n'


@pytest.fixture
def c(tmp_path):
    cycle = make_cycle(tmp_path)
    release(cycle)
    return cycle


@pytest.fixture
def box(c):
    return Sandboxes(c)


def conditions(cycle, name):
    return [e["evidence"] for e in cycle.events("K1", "condition_checked") if e["evidence"]["condition"] == name]


def reads_the_repository(cycle):
    """Attrappe für die drei Gitleaks-Läufe. Sie liest wirklich aus dem Projekt: den Stand
    mit `git grep`, die Commits der Karte mit `git log -p -U0 <basis>..<stand>`, ihre Köpfe
    mit `git cat-file commit`."""
    def scan(base, head):
        stand = subprocess.run(["git", "grep", "-l", "ghp_", head], cwd=cycle.project,
                               capture_output=True, text=True).stdout.split()
        in_tree = [{"RuleID": "github-pat", "Description": "Token", "File": hit.split(":", 1)[1], "StartLine": 1}
                   for hit in stand]
        in_history, commit, file = [], None, None
        for line in git(cycle.project, "log", "-p", "-U0", "--format=commit %H", f"{base}..{head}").splitlines():
            if line.startswith("commit "):
                commit = line.split()[1]
            elif line.startswith("+++ b/"):
                file = line[6:]
            elif line.startswith("+") and "ghp_" in line:
                in_history.append({"RuleID": "github-pat", "Description": "Token", "File": file, "StartLine": 1,
                                   "Commit": commit})
        in_messages = [{"RuleID": "github-pat", "Description": "Token", "File": commit, "StartLine": 6}
                       for commit in git(cycle.project, "rev-list", f"{base}..{head}").split()
                       if "ghp_" in git(cycle.project, "cat-file", "commit", commit)]
        codes = {**CODES, "gitleaks": int(bool(in_tree)), "gitleaks-history": int(bool(in_history)),
                 "gitleaks-messages": int(bool(in_messages))}
        return codes, {**CLEAN, "gitleaks.json": json.dumps(in_tree), "gitleaks-history.json": json.dumps(in_history),
                       "gitleaks-messages.json": json.dumps(in_messages)}
    return scan


def token_on(cycle, ref):
    """Steht das Token in einem Commit, der von `ref` aus erreichbar ist?"""
    return TOKEN in git(cycle.project, "log", "-p", ref)


def replace_commits_after_the_test_commit(cycle, files):
    """Die verlangte Nachbesserung: Der Test-Commit bleibt, die Commits danach entstehen neu."""
    test_commit = cycle.state("K1")["test_commit"]

    def work(tree):
        git(tree, "reset", "-q", "--hard", test_commit)
        for path, content in files.items():
            (tree / path).parent.mkdir(parents=True, exist_ok=True)
            (tree / path).write_text(content)
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "Umsetzung ohne Token")
    return on_branch(cycle, work)


# ---------- Der ganze Lauf


def test_a_token_that_a_later_commit_removed_keeps_the_gate_red_and_off_main(c, box):
    """Der Fall aus `trockenlauf-1` und `lauf-3`: Die Nachbesserung hängt einen Commit an,
    der das Token entfernt. Der Stand ist danach sauber, die Historie nicht."""
    box.scanner = reads_the_repository(c)
    planted = []
    box.agent = [
        lambda: planted.append(commit_on_branch(c, "src/upstream.py", WITH_TOKEN)),
        lambda: commit_on_branch(c, "src/upstream.py", WITHOUT_TOKEN),  # entfernt nur, ersetzt keinen Commit
    ]
    with pytest.raises(Stop):
        c.run("K1")

    head = c.state("K1")["head"]
    assert subprocess.run(["git", "grep", "-q", "ghp_", head], cwd=c.project).returncode == 1  # Stand sauber
    assert token_on(c, head)                                                                   # Historie nicht
    assert "approval" not in moves(c)
    assert c.state("K1")["returns"] == {"gate": 3, "checks": 0} and halt(c)["cause"] == "loop_limit"
    assert not c._gate_green("K1", head)
    last = conditions(c, "nothing_above_threshold")[-1]
    assert last["commit"] == head and last["ok"] is False

    # Zwei Gate-Läufe an diesem Stand (die dritte Runde ändert nichts), beide nur mit dem Fund aus der Historie.
    findings = [e["evidence"] for e in c.events("K1", "finding") if e["commit"] == head]
    assert [f["tool"] for f in findings] == ["gitleaks-history"] * 2
    assert all(f["found_in"] == planted[0] and f["above_threshold"] is True for f in findings)
    returned = c.events("K1", "card_returned")[-1]["evidence"]["findings"]
    assert returned == [{"tool": "gitleaks-history", "rule": "github-pat", "file": "src/upstream.py", "line": 1,
                         "severity": "critical", "found_in": planted[0]}]

    with pytest.raises(Stop):
        c.merge("K1")
    assert not token_on(c, "main")


def test_the_first_round_reports_the_token_in_the_tree_and_in_the_history(c, box):
    box.scanner = reads_the_repository(c)
    box.agent = [lambda: commit_on_branch(c, "src/upstream.py", WITH_TOKEN),
                 lambda: replace_commits_after_the_test_commit(c, {**CODE_FILE, "src/upstream.py": WITHOUT_TOKEN})]
    c.run("K1")
    first_gate = [e["evidence"] for e in c.events("K1", "finding")]
    assert [f["tool"] for f in first_gate] == ["gitleaks", "gitleaks-history"]
    assert "found_in" not in first_gate[0] and first_gate[1]["found_in"]


def test_the_rework_order_asks_to_replace_the_commits_after_the_test_commit(c, box):
    box.scanner = reads_the_repository(c)
    planted = []
    box.agent = [lambda: planted.append(commit_on_branch(c, "src/upstream.py", WITH_TOKEN)),
                 lambda: replace_commits_after_the_test_commit(c, {**CODE_FILE, "src/upstream.py": WITHOUT_TOKEN})]
    c.run("K1")
    prompt = box.agent_calls[1]["prompt"]
    test_commit = c.state("K1")["test_commit"]
    assert f"Commit {planted[0][:10]}" in prompt
    assert f"Der Test-Commit {test_commit[:10]} bleibt" in prompt
    assert "Ersetze die Commits nach dem Test-Commit" in prompt
    assert "Kein Amend" not in prompt  # die Regel für Befunde, die ein weiterer Commit behebt
    assert TOKEN not in prompt


def test_a_branch_rebuilt_without_the_token_passes_the_gate_and_merges_clean(c, box):
    box.scanner = reads_the_repository(c)
    box.agent = [lambda: commit_on_branch(c, "src/upstream.py", WITH_TOKEN),
                 lambda: replace_commits_after_the_test_commit(c, {**CODE_FILE, "src/upstream.py": WITHOUT_TOKEN})]
    c.run("K1")
    assert moves(c) == ["decomposition", "implementation", "gate", "implementation", "gate", "approval"]
    st = c.state("K1")
    assert not token_on(c, st["head"])
    assert conditions(c, "tests_unchanged_since_test_commit")[-1]["ok"] is True  # derselbe Test-Commit
    assert c._gate_green("K1", st["head"])
    c.decide("K1", None, "approved", "ja")
    c.merge("K1")
    assert moves(c)[-1] == "merged"
    assert not token_on(c, "main")


def test_a_finding_in_the_history_alone_returns_the_card(c, box):
    """Auch ohne Fund im Stand: Der Lauf über die Commits allein weist die Karte ab."""
    head = git(c.project, "rev-parse", "karte/k1")
    box.gate = [history_leak(head)]
    c.run("K1")
    assert moves(c) == ["decomposition", "implementation", "gate", "implementation", "gate", "approval"]
    assert c.state("K1")["returns"] == {"gate": 1, "checks": 0}
    assert conditions(c, "gitleaks-history_ran")[0]["findings"] == 1


def test_a_token_in_the_accepted_test_commit_withdraws_the_acceptance(c, box):
    """Der Test-Commit selbst enthält das Token. Behalten lässt er sich so nicht: Die
    Abnahme fällt weg, der Branch entsteht neu, und die Tests werden neu abgenommen."""
    box.scanner = reads_the_repository(c)
    rebuild_branch(c, {**TEST_FILE, "tests/daten.py": WITH_TOKEN}, CODE_FILE)
    first_test_commit = git(c.project, "rev-list", "--reverse", "main..karte/k1").split()[0]
    box.agent = [None, lambda: rebuild_branch(c, {**TEST_FILE, "tests/daten.py": WITHOUT_TOKEN}, CODE_FILE)]
    c.run("K1")

    returned = c.events("K1", "card_returned")[0]["evidence"]
    assert returned["test_commit_withdrawn"] == first_test_commit
    prompt = box.agent_calls[1]["prompt"]
    assert "noch nicht abgenommen" in prompt and "auch der Test-Commit nicht" in prompt
    assert c.column("K1") == "approval"
    st = c.state("K1")
    assert st["test_commit"] == git(c.project, "rev-list", "--reverse", "main..karte/k1").split()[0]
    assert st["test_commit"] != first_test_commit
    assert not token_on(c, st["head"])


def test_a_token_in_a_commit_message_returns_the_card_until_the_commit_is_replaced(c, box):
    """Weder der Stand noch ein Diff enthält das Token, nur die Nachricht eines Commits."""
    box.scanner = reads_the_repository(c)
    test_commit = git(c.project, "rev-list", "--reverse", "main..karte/k1").split()[0]

    def with_message(message):
        def work(tree):
            git(tree, "reset", "-q", "--hard", test_commit)
            for path, content in CODE_FILE.items():
                (tree / path).parent.mkdir(parents=True, exist_ok=True)
                (tree / path).write_text(content)
            git(tree, "add", "-A")
            git(tree, "commit", "-q", "-m", message)
        return on_branch(c, work)

    planted = []
    box.agent = [lambda: planted.append(with_message(f"Umsetzung, Zugang {TOKEN}")),
                 lambda: with_message("Umsetzung")]
    c.run("K1")
    assert moves(c) == ["decomposition", "implementation", "gate", "implementation", "gate", "approval"]
    finding, = [e["evidence"] for e in c.events("K1", "finding")]
    assert finding["tool"] == "gitleaks-messages" and finding["found_in"] == planted[0]
    assert finding["file"] == "(Kopf oder Nachricht des Commits)" and finding["line"] is None
    prompt = box.agent_calls[1]["prompt"]
    assert f"Commit {planted[0][:10]}" in prompt and "auch nicht in seiner Nachricht" in prompt
    assert TOKEN not in prompt
    c.decide("K1", None, "approved", "ja")
    c.merge("K1")
    assert TOKEN not in git(c.project, "log", "--format=%B", "main")


# ---------- Der Lauf über die Commits gehört zum Gate


def test_the_gate_script_scans_the_commits_between_base_and_head(c, box):
    c.run("K1")
    call, = [call for call in c.sh.calls if call[:2] == ["sbx", "exec"] and "gitleaks" in " ".join(call)]
    script = call[call.index("-c") + 1]
    st = c.state("K1")
    assert call[-2:] == [st["head"], st["base"]]
    assert ('gitleaks git . --log-opts="--text --no-textconv --no-ext-diff --diff-merges=first-parent $base..$sha"'
            in script)
    assert script.index('extract "$sha"') < script.index("gitleaks git")  # das Hilfs-Repository liegt dann da
    # Alle drei Läufe mit denselben Optionen, darunter die gegen `gitleaks:allow` im geprüften Code.
    assert 'opts="--ignore-gitleaks-allow ' in script
    assert [line.count(" $opts ") for line in script.splitlines() if "&& gitleaks " in line] == [1, 1, 1]
    ran, = conditions(c, "gitleaks-history_ran")
    assert ran["ok"] is True and ran["commits"] == ran["commits_seen"] == 2 and ran["base"] == st["base"]
    heads, = conditions(c, "gitleaks-messages_ran")
    assert heads["ok"] is True and heads["commits"] == heads["messages_seen"] == 2 and heads["version"] == "v8"
    for fname in ("gitleaks-history.json", "gitleaks-messages.json"):
        assert json.loads((c.run_dir / "K1" / "gate-1" / fname).read_text()) == []


def without(mapping, key):
    return {k: v for k, v in mapping.items() if k != key}


@pytest.mark.parametrize("tool", ["gitleaks-history", "gitleaks-messages"])
@pytest.mark.parametrize("change", [
    lambda tool: ({**CODES, tool: 1}, CLEAN),                           # Funde gemeldet, Bericht leer
    lambda tool: ({**CODES, tool: 1}, without(CLEAN, f"{tool}.json")),  # Funde gemeldet, kein Bericht
    lambda tool: (CODES, without(CLEAN, f"{tool}.json")),               # kein Bericht
    lambda tool: (without(CODES, tool), CLEAN),                         # kein Exit-Code
    lambda tool: ({**CODES, tool: 2}, CLEAN),                           # Gitleaks gescheitert
    lambda tool: ({**CODES, tool: 126}, without(CLEAN, f"{tool}.json")),  # Gitleaks kennt eine Option nicht
])
def test_the_gate_is_incomplete_when_a_scan_of_the_commits_did_not_run(c, box, tool, change):
    box.gate = [change(tool)]
    with pytest.raises(Stop):
        c.run("K1")
    assert c.column("K1") == "gate"
    assert halt(c)["cause"] == "gate_incomplete" and halt(c)["step"] == "gate"
    assert conditions(c, f"{tool}_ran")[-1]["ok"] is False
    assert len(box.agent_calls) == 1  # kein Fall für den Umsetzer
    assert not c._gate_green("K1", c.state("K1")["head"])


@pytest.mark.parametrize("attribute, tool", [("history_commits", "gitleaks-history"),
                                             ("message_files", "gitleaks-messages")])
@pytest.mark.parametrize("seen", [1, 0, "viele"])  # weniger als die zwei Commits der Karte; keine Zahl
def test_the_gate_is_incomplete_when_the_sandbox_does_not_see_all_commits(c, box, attribute, tool, seen):
    setattr(box, attribute, seen)
    with pytest.raises(Stop):
        c.run("K1")
    assert halt(c)["cause"] == "gate_incomplete"
    assert conditions(c, f"{tool}_ran")[-1]["ok"] is False
    assert not c._gate_green("K1", c.state("K1")["head"])


def test_a_history_finding_names_its_commit_and_carries_no_secret():
    report = json.dumps([{"RuleID": "github-pat", "Description": "GitHub Personal Access Token",
                          "File": "src/a.py", "StartLine": 4, "Secret": "REDACTED", "Match": "REDACTED",
                          "Commit": "c0ffee" * 6 + "abcd"}])
    (f,) = parse_gitleaks(report, history=True)
    assert f["tool"] == "gitleaks-history" and f["found_in"] == "c0ffee" * 6 + "abcd"
    assert f["severity"] == "critical" and (f["file"], f["line"]) == ("src/a.py", 4)
    assert "genügt nicht" in f["demand"]
    assert "Secret" not in f and "Match" not in f
    (plain,) = parse_gitleaks(report)
    assert plain["tool"] == "gitleaks" and "found_in" not in plain


def test_a_finding_in_a_commit_head_names_the_commit_not_a_file():
    commit = "c0ffee" * 6 + "abcd"
    report = json.dumps([{"RuleID": "github-pat", "Description": "GitHub Personal Access Token",
                          "File": commit, "StartLine": 6, "Secret": "REDACTED", "Match": "REDACTED"}])
    (f,) = parse_gitleaks_messages(report)
    assert f["tool"] == "gitleaks-messages" and f["found_in"] == commit
    assert f["file"] == "(Kopf oder Nachricht des Commits)" and f["line"] is None
    assert "Nachricht" in f["risk"] and "Ein weiterer Commit behebt das nicht" in f["demand"]
    assert "Secret" not in f and "Match" not in f
    (nested,) = parse_gitleaks_messages(report.replace(commit, f"./{commit}"))
    assert nested["found_in"] == commit


# ---------- Die Berichte im Projektordner tragen keinen Text aus den Commits

# Ein Fund, wie Gitleaks 8.30.1 ihn zu einem Commit schreibt: `--redact` schwärzt `Match` und
# `Secret`, nicht aber Nachricht und Autor des Commits.
def real_finding(commit, message):
    return {"RuleID": "github-pat", "Description": "Token", "StartLine": 1, "EndLine": 1, "StartColumn": 10,
            "EndColumn": 49, "Match": "REDACTED", "Secret": "REDACTED", "File": "src/a.py", "SymlinkFile": "",
            "Commit": commit, "Entropy": 4.8, "Author": f"autor {TOKEN}", "Email": "a@example.invalid",
            "Date": "2026-11-22T08:10:38Z", "Message": message, "Tags": [],
            "Fingerprint": f"{commit}:src/a.py:github-pat:1"}


def test_the_report_in_the_project_folder_holds_no_commit_message_and_no_author(c, box):
    """Ein Commit mit einem Fund im Diff trägt das Token auch in Nachricht und Autor."""
    head = git(c.project, "rev-parse", "karte/k1")
    report = json.dumps([real_finding(head, f"Zugang {TOKEN}")])
    box.gate = [({**CODES, "gitleaks-history": 1}, {**CLEAN, "gitleaks-history.json": report})]
    c.run("K1")
    kept, = json.loads((c.run_dir / "K1" / "gate-1" / "gitleaks-history.json").read_text())
    assert kept["Commit"] == head and kept["File"] == "src/a.py" and kept["Fingerprint"].startswith(head)
    assert not {"Message", "Author", "Email", "Date", "Match", "Secret"} & set(kept)
    everything = "".join(p.read_text() for p in c.run_dir.rglob("*") if p.is_file())
    assert TOKEN not in everything  # weder Berichte noch Protokoll noch Arbeitsstand
    assert conditions(c, "gitleaks-history_ran")[0]["findings"] == 1  # der Fund selbst zählt weiter


def test_a_gitleaks_report_that_is_not_readable_stays_out_of_the_project_folder(c, box):
    box.gate = [(CODES, {**CLEAN, "gitleaks-history.json": f'[{{"Message": "{TOKEN}"'})]
    with pytest.raises(Stop):
        c.run("K1")
    assert halt(c)["cause"] == "gate_incomplete"
    assert conditions(c, "gitleaks-history_ran")[-1]["report_readable"] is False
    assert not (c.run_dir / "K1" / "gate-1" / "gitleaks-history.json").exists()


def test_the_scanners_never_write_into_the_project_folder_directly(c, box):
    c.run("K1")
    targets = [Path(call[-1]) for call in c.sh.calls if call[:2] == ["sbx", "cp"] and call[-1].endswith(".json")]
    assert targets and not any(c.project in target.parents for target in targets)
    assert (c.run_dir / "K1" / "gate-1" / "sbom.cdx.json").exists()  # angekommen sind sie trotzdem


def test_only_fields_without_text_from_the_code_survive_the_filter():
    kept, = json.loads(gitleaks_report_without_text(json.dumps([real_finding("c" * 40, f"m {TOKEN}")])))
    assert sorted(kept) == ["Commit", "Description", "EndColumn", "EndLine", "Entropy", "File", "Fingerprint",
                            "RuleID", "StartColumn", "StartLine", "SymlinkFile", "Tags"]
    assert gitleaks_report_without_text("[]") == "[]\n"
    for broken in ("", "{kaputt", '{"Message": "x"}', '["x"]'):
        assert gitleaks_report_without_text(broken) is None


# ---------- Abholen eines Branches, dessen Commits ersetzt wurden


def test_fetch_overwrites_the_ref_when_the_sandbox_replaced_commits(tmp_path):
    """Ein echtes git-Repository als Sandbox, dessen Remote das Überschreiben nicht von sich aus erlaubt."""
    cycle = make_cycle(tmp_path)
    remote = tmp_path / "sandbox"
    git(tmp_path, "clone", "-q", str(cycle.project), str(remote))
    git(remote, "checkout", "-q", "karte/k1")
    git(cycle.project, "remote", "add", "sandbox-x", str(remote))
    git(cycle.project, "config", "remote.sandbox-x.fetch", "refs/heads/*:refs/remotes/sandbox-x/*")  # ohne `+`
    first, _ = cycle._fetch(CARD, cycle.state("K1"))
    assert first == git(remote, "rev-parse", "HEAD")

    git(remote, "reset", "-q", "--soft", "HEAD~1")
    git(remote, "commit", "-q", "-m", "Umsetzung neu")
    rebuilt = git(remote, "rev-parse", "HEAD")
    plain = subprocess.run(["git", "fetch", "--no-tags", "sandbox-x"], cwd=cycle.project, capture_output=True)
    assert plain.returncode != 0  # Voraussetzung: ohne --force weist git den ersetzten Branch ab
    assert cycle._fetch(CARD, cycle.state("K1")) == (rebuilt, "refs/remotes/sandbox-x/karte/k1")
    assert rebuilt != first


# ---------- Die Shell-Zeilen mit echtem git und sh, ohne `sbx`; Gitleaks ist eine Attrappe

PATH = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"

# `gitleaks git` liest die Commits so, wie Gitleaks es tut (`git -C <repo> log -p -U0 <log-opts>`),
# `gitleaks dir` die Dateien des Ordners. Gemeldet wird ein Token in einer hinzugefügten Zeile
# oder in einer Datei. Je Lauf merkt sich die Attrappe unter dem Namen des Berichts: Argumente,
# Arbeitsverzeichnis und die Regeln und Ausnahmen, die Gitleaks an dieser Stelle läse
# (`--config`, sonst `<quelle>/.gitleaks.toml`; `<quelle>/.gitleaksignore`).
FAKE_GITLEAKS = r"""#!/bin/sh
mode="$1"; src="$2"; config=""; opts=""
printf '%s\n' "$@" > "$HOME/argv.tmp"
while [ $# -gt 0 ]; do
  case "$1" in
    --log-opts=*) opts="${1#--log-opts=}" ;;
    --report-path) report="$2"; shift ;;
    --config) config="$2"; shift ;;
  esac
  shift
done
name=$(basename "$report" .json)
mv "$HOME/argv.tmp" "$HOME/$name.argv"; pwd > "$HOME/$name.cwd"
cat "${config:-$src/.gitleaks.toml}" > "$HOME/$name.rules" 2>/dev/null
cat "$src/.gitleaksignore" > "$HOME/$name.ignore" 2>/dev/null
if [ "$mode" = git ]; then
  git -C "$src" log -p -U0 $opts | awk '
    /^commit / { commit = $2 }
    /^\+\+\+ b\// { file = substr($0, 7) }
    /^\+[^+]/ && /ghp_[A-Za-z0-9]+/ {
      hits = hits sep "{\"RuleID\": \"github-pat\", \"File\": \"" file "\", \"StartLine\": 1, \"Commit\": \"" commit "\"}"
      sep = ", "
    }
    END { print "[" hits "]"; exit (hits == "" ? 0 : 1) }' > "$report"
else
  ( cd "$src" && grep -rl 'ghp_[A-Za-z0-9]' . ) | sed 's|^\./||' | sort | awk '
    { hits = hits sep "{\"RuleID\": \"github-pat\", \"File\": \"" $0 "\", \"StartLine\": 1}"; sep = ", " }
    END { print "[" hits "]"; exit (hits == "" ? 0 : 1) }' > "$report"
fi
"""
REPORTS = ("gitleaks", "gitleaks-history", "gitleaks-messages")


@pytest.fixture
def repo(tmp_path):
    """`main` mit einem Commit; die Karte arbeitet auf `karte`."""
    project = tmp_path / "quelle"
    (project / "src").mkdir(parents=True)
    (project / "src" / "a.py").write_text("A = 1\n")
    git(project, "init", "-q", "-b", "main")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "start")
    git(project, "checkout", "-q", "-b", "karte")
    return project


def commit(project, files, message, *options):
    for path, content in files.items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(content)
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", message, *options)
    return git(project, "rev-parse", "HEAD")


def scan(project, home, base="main", head="HEAD", shell="sh"):
    """Die Zeilen des Gates bis zum letzten Gitleaks-Lauf. Liefert den Prozess und die drei Berichte."""
    bin_dir = home / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gitleaks").write_text(FAKE_GITLEAKS)
    (bin_dir / "gitleaks").chmod(0o755)
    script = ("set -u\n" + extract_snippet(str(project / ".git")) + 'sha="$1"; base="$2"; mkdir -p "$HOME/out"\n'
              'extract "$sha" "$HOME/scan" || exit\n' + secrets_snippet())
    base = git(project, "rev-parse", base) if base else "0" * 40
    done = subprocess.run([shell, "-c", script, "sh", git(project, "rev-parse", head), base],
                          env={"HOME": str(home), "PATH": f"{bin_dir}:{PATH}", "LC_ALL": "C"},
                          capture_output=True, text=True)
    reports = {}
    for name in REPORTS:
        report = home / "out" / f"{name}.json"
        reports[name] = json.loads(report.read_text()) if report.exists() else None
    return done, reports


def seen_by(home, name, what):
    path = home / f"{name}.{what}"
    return path.read_text() if path.exists() else None


def test_the_history_scan_reads_a_commit_whose_token_the_head_no_longer_has(repo, tmp_path):
    planted = commit(repo, {"src/upstream.py": WITH_TOKEN}, "mit Token")
    commit(repo, {"src/upstream.py": WITHOUT_TOKEN}, "ohne Token")
    done, reports = scan(repo, tmp_path)
    assert done.returncode == 0, done.stderr
    assert "ghp_" not in (tmp_path / "scan" / "src" / "upstream.py").read_text()  # der Stand ist sauber
    assert done.stdout.splitlines() == ["RESULT gitleaks 0", "HISTORY commits 2", "RESULT gitleaks-history 1",
                                        "HISTORY messages 2", "RESULT gitleaks-messages 0"]
    assert reports == {"gitleaks": [], "gitleaks-messages": [], "gitleaks-history": [
        {"RuleID": "github-pat", "File": "src/upstream.py", "StartLine": 1, "Commit": planted}]}


def test_the_history_scan_covers_only_the_commits_of_the_card(repo, tmp_path):
    """Ein Token, das vor der Basis in der Historie von `main` stand, ist kein Befund dieser Karte."""
    git(repo, "checkout", "-q", "main")
    commit(repo, {"src/alt.py": WITH_TOKEN}, f"altes Token auf main {TOKEN}")
    commit(repo, {"src/alt.py": WITHOUT_TOKEN}, "auf main entfernt")
    git(repo, "checkout", "-q", "-B", "karte")
    commit(repo, {"src/b.py": "B = 2\n"}, "Karte")
    done, reports = scan(repo, tmp_path)
    assert "HISTORY commits 1" in done.stdout and "HISTORY messages 1" in done.stdout
    assert reports == {"gitleaks": [], "gitleaks-history": [], "gitleaks-messages": []}


@pytest.mark.skipif(not os.path.exists("/bin/dash"), reason="keine dash auf diesem Rechner")
def test_the_lines_run_under_dash_as_in_the_scan_sandbox(repo, tmp_path):
    git(repo, "checkout", "-q", "main")
    commit(repo, {".gitleaks.toml": "regeln der basis\n"}, "Regeln")
    git(repo, "checkout", "-q", "-B", "karte")
    planted = commit(repo, {"src/upstream.py": WITH_TOKEN}, "mit Token")
    commit(repo, {"src/upstream.py": WITHOUT_TOKEN}, f"ohne Token, aber {TOKEN}")
    done, reports = scan(repo, tmp_path, shell="/bin/dash")
    assert done.returncode == 0 and done.stderr == ""
    assert [f["Commit"] for f in reports["gitleaks-history"]] == [planted]
    assert len(reports["gitleaks-messages"]) == 1
    argv = seen_by(tmp_path, "gitleaks-messages", "argv").splitlines()
    assert argv[argv.index("--config") + 1] == f"{tmp_path}/.kreislauf-git/.gitleaks.toml"


# ---------- Was `git log -p` von sich aus nicht zeigt


def test_the_history_scan_reads_what_a_merge_commit_itself_brings_in(repo, tmp_path):
    commit(repo, {"src/b.py": "B = 2\n"}, "Karte")
    git(repo, "checkout", "-q", "-b", "seite")
    commit(repo, {"src/c.py": "C = 3\n"}, "Seite")
    git(repo, "checkout", "-q", "karte")
    commit(repo, {"src/d.py": "D = 4\n"}, "Karte weiter")
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "seite")
    merge = commit(repo, {"src/upstream.py": WITH_TOKEN}, "Merge mit eigener Änderung")
    commit(repo, {"src/upstream.py": WITHOUT_TOKEN}, "ohne Token")
    assert len(git(repo, "rev-list", "--parents", "-1", merge).split()) == 3
    # Voraussetzung: Ohne Option zeigt git nicht, was der Merge-Commit hinzufügt.
    assert f"+{WITH_TOKEN}" not in git(repo, "log", "-p", "main..HEAD") + "\n"
    done, reports = scan(repo, tmp_path)
    assert "HISTORY commits 5" in done.stdout
    assert [(f["File"], f["Commit"]) for f in reports["gitleaks-history"]] == [("src/upstream.py", merge)]
    assert reports["gitleaks"] == []


def test_the_history_scan_reads_a_file_that_git_takes_for_binary(repo, tmp_path):
    (repo / "src" / "daten.bin").write_bytes(WITH_TOKEN.encode() + b"\0\n")
    planted = commit(repo, {}, "Datei mit NUL-Byte")
    git(repo, "rm", "-q", "src/daten.bin")
    commit(repo, {}, "wieder gelöscht")
    in_place = subprocess.run(["git", "log", "-p", "main..HEAD"], cwd=repo, capture_output=True).stdout
    assert b"Binary files" in in_place and TOKEN.encode() not in in_place  # Voraussetzung
    done, reports = scan(repo, tmp_path)
    assert [(f["File"], f["Commit"]) for f in reports["gitleaks-history"]] == [("src/daten.bin", planted)]


def test_attributes_of_the_branch_do_not_hide_a_file_from_the_history_scan(repo, tmp_path):
    """`-diff` im Stand ließe `git log -p` nur „Binary files differ" zeigen."""
    planted = commit(repo, {"src/upstream.py": WITH_TOKEN, ".gitattributes": "*.py -diff\n"}, "mit Token")
    commit(repo, {"src/upstream.py": WITHOUT_TOKEN}, "ohne Token")
    in_place = subprocess.run(["git", "log", "-p", "main..HEAD"], cwd=repo, capture_output=True, text=True).stdout
    assert TOKEN not in in_place  # Voraussetzung: im Projekt selbst versteckt das Attribut die Datei
    done, reports = scan(repo, tmp_path)
    assert [f["Commit"] for f in reports["gitleaks-history"]] == [planted]


def test_a_token_in_a_commit_message_is_found_in_the_head_of_that_commit(repo, tmp_path):
    planted = commit(repo, {"src/b.py": "B = 2\n"}, f"Zugang zum Upstream: {TOKEN}")
    commit(repo, {"src/c.py": "C = 3\n"}, "weiter")
    done, reports = scan(repo, tmp_path)
    assert reports["gitleaks"] == [] and reports["gitleaks-history"] == []  # weder Stand noch Diff enthält es
    assert [f["File"] for f in reports["gitleaks-messages"]] == [planted]
    assert done.stdout.splitlines()[-2:] == ["HISTORY messages 2", "RESULT gitleaks-messages 1"]
    assert sorted(path.name for path in (tmp_path / "commits").iterdir()) == sorted(
        git(repo, "rev-list", "main..HEAD").split())


def test_a_token_in_the_author_of_a_commit_is_found(repo, tmp_path):
    planted = commit(repo, {"src/b.py": "B = 2\n"}, "Karte", f"--author={TOKEN} <a@example.invalid>")
    done, reports = scan(repo, tmp_path)
    assert [f["File"] for f in reports["gitleaks-messages"]] == [planted]


def test_an_unknown_base_leaves_no_commit_count(repo, tmp_path):
    """Ohne Zahl gilt der Lauf beim Koordinator als nicht gelaufen; Köpfe gibt es dann keine."""
    commit(repo, {"src/b.py": "B = 2\n"}, "Karte")
    done, _ = scan(repo, tmp_path, base=None)
    assert "HISTORY commits" not in done.stdout
    assert "HISTORY messages 0" in done.stdout


# ---------- Regeln und Ausnahmen für Gitleaks kommen aus der Basis, nicht aus der Karte

RULES = (".gitleaks.toml", ".gitleaksignore")


def test_rules_and_exceptions_the_card_brings_along_do_not_apply(repo, tmp_path):
    """Die Basis hat keine. Was die Karte anlegt, liegt bei keinem der drei Läufe da, wo Gitleaks es läse."""
    commit(repo, {"src/upstream.py": WITH_TOKEN, ".gitleaksignore": "ausnahme der karte\n",
                  ".gitleaks.toml": "regeln der karte\n"}, "mit Token und eigenen Ausnahmen")
    done, reports = scan(repo, tmp_path)
    assert done.returncode == 0, done.stderr
    for name in REPORTS:
        assert not seen_by(tmp_path, name, "rules") and not seen_by(tmp_path, name, "ignore")
    assert not any((tmp_path / where / name).exists() for where in ("scan", ".kreislauf-git") for name in RULES)
    assert "--config" not in seen_by(tmp_path, "gitleaks-messages", "argv")
    assert len(reports["gitleaks"]) == 1 and len(reports["gitleaks-history"]) == 1


def test_rules_and_exceptions_of_the_base_apply_even_when_the_card_changes_them(repo, tmp_path):
    git(repo, "checkout", "-q", "main")
    commit(repo, {".gitleaks.toml": "regeln der basis\n", ".gitleaksignore": "ausnahme der basis\n"}, "Regeln")
    git(repo, "checkout", "-q", "-B", "karte")
    commit(repo, {".gitleaks.toml": "regeln der karte\n", ".gitleaksignore": "ausnahme der karte\n",
                  "src/b.py": "B = 2\n"}, "Karte lockert die Regeln")
    done, _ = scan(repo, tmp_path)
    assert done.returncode == 0, done.stderr
    assert {seen_by(tmp_path, name, "rules") for name in REPORTS} == {"regeln der basis\n"}
    assert seen_by(tmp_path, "gitleaks", "ignore") == seen_by(tmp_path, "gitleaks-history", "ignore") \
        == "ausnahme der basis\n"
    assert (tmp_path / "scan" / "src" / "b.py").exists()  # der übrige Stand ist der der Karte


def test_a_card_cannot_remove_the_rules_of_the_base(repo, tmp_path):
    git(repo, "checkout", "-q", "main")
    commit(repo, {".gitleaks.toml": "regeln der basis\n"}, "Regeln")
    git(repo, "checkout", "-q", "-B", "karte")
    git(repo, "rm", "-q", ".gitleaks.toml")
    commit(repo, {"src/b.py": "B = 2\n"}, "Karte löscht die Regeln")
    scan(repo, tmp_path)
    assert {seen_by(tmp_path, name, "rules") for name in REPORTS} == {"regeln der basis\n"}


def test_gitleaks_does_not_run_when_the_rules_of_the_base_are_not_a_file(repo, tmp_path):
    """Ohne lesbare Regeln der Basis liefe Gitleaks mit anderen als den freigegebenen."""
    git(repo, "checkout", "-q", "main")
    commit(repo, {".gitleaks.toml/x": "ein Ordner statt einer Datei\n"}, "Regeln als Ordner")
    git(repo, "checkout", "-q", "-B", "karte")
    commit(repo, {"src/b.py": "B = 2\n"}, "Karte")
    done, reports = scan(repo, tmp_path)
    assert done.returncode == 92
    assert "RESULT" not in done.stdout and reports == dict.fromkeys(REPORTS)


def test_every_run_ignores_gitleaks_allow_and_redacts(repo, tmp_path):
    commit(repo, {"src/upstream.py": f'TOKEN = "{TOKEN}"  # gitleaks:allow\n'}, "mit Token")
    scan(repo, tmp_path)
    for name, mode in zip(REPORTS, ("dir", "git", "dir")):
        argv = seen_by(tmp_path, name, "argv").splitlines()
        assert argv[:2] == [mode, "."] and "--ignore-gitleaks-allow" in argv and "--redact" in argv
    base, head = git(repo, "rev-parse", "main"), git(repo, "rev-parse", "HEAD")
    assert seen_by(tmp_path, "gitleaks-history", "argv").splitlines()[2] == (
        f"--log-opts=--text --no-textconv --no-ext-diff --diff-merges=first-parent {base}..{head}")
    assert [seen_by(tmp_path, name, "cwd").strip().rsplit("/", 1)[-1] for name in REPORTS] == [
        "scan", ".kreislauf-git", "commits"]
