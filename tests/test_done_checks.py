"""Done der Umsetzung im Ablauf: Was die Prüfung von außen an echten Commits sieht."""

import pytest

from helpers import (CODE_FILE, GATE_LEAK, TEST_FILE, Sandboxes, commit_on_branch, halt, make_cycle, moves,
                     rebuild_branch, release, rename_on_branch)
from kreislauf.cycle import Result, Stop


@pytest.fixture
def c(tmp_path):
    cycle = make_cycle(tmp_path)
    release(cycle)
    return cycle


@pytest.fixture
def box(c):
    return Sandboxes(c)


def checked(cycle, name):
    return [e["evidence"] for e in cycle.events("K1", "condition_checked") if e["evidence"]["condition"] == name]


# ---------- Der Stand kommt vollständig in die Sandbox


def test_tests_and_scanners_get_the_tree_through_the_complete_extract(c, box):
    c.run("K1")
    scripts = [call[call.index("-c") + 1] for call in c.sh.calls if call[:2] == ["sbx", "exec"] and "-c" in call]
    tests = [s for s in scripts if "pytest=" in s]
    gate, = [s for s in scripts if "gitleaks" in s]
    assert len(tests) == 2  # je Commit eine eigene Prüf-Sandbox
    for script in (*tests, gate):
        assert "-export-ignore -export-subst" in script and "hash-object --no-filters" in script
        # Ausgepackt wird, bevor Code aus dem Stand läuft.
        assert script.index('extract "$sha"') < script.index("cd ")
    names = [call[2] for call in c.sh.calls if call[:2] == ["sbx", "exec"] and "-c" in call and "pytest=" in call[call.index("-c") + 1]]
    assert len(set(names)) == 2


# ---------- Umbenennungen


def test_moving_a_sensitive_file_into_the_allowed_paths_is_seen(c, box):
    box.gate = [GATE_LEAK]
    box.agent = [None, lambda: rename_on_branch(c, "uv.lock", "src/uv.lock")]
    with pytest.raises(Stop):
        c.run("K1")
    # Der Zielpfad liegt im erlaubten Bereich; gemeldet wird die heikle Datei, die verschwindet.
    assert checked(c, "only_allowed_paths")[1]["offenders"] == ["uv.lock"]
    assert c.column("K1") == "implementation"  # an der Schleifengrenze, nicht in der Freigabe


def test_moving_a_test_out_of_the_test_paths_counts_as_changing_it(c, box):
    box.gate = [GATE_LEAK]
    box.agent = [None, lambda: rename_on_branch(c, "tests/test_a.py", "src/helper_a.py")]
    with pytest.raises(Stop):
        c.run("K1")
    first, rework = checked(c, "tests_unchanged_since_test_commit")[:2]
    assert first["ok"] is True
    assert rework["ok"] is False and rework["touched"] == ["tests/test_a.py"]


# ---------- Tests sind erst nach ihrer Abnahme eingefroren


def test_a_first_submission_with_one_mixed_commit_can_be_rebuilt(c, box):
    box.agent = [
        lambda: rebuild_branch(c, {**TEST_FILE, **CODE_FILE}),  # alles in einem Commit
        lambda: rebuild_branch(c, TEST_FILE, CODE_FILE),         # neu aufgebaut: erst Test, dann Umsetzung
    ]
    c.run("K1")
    assert moves(c) == ["decomposition", "implementation", "implementation", "gate", "approval"]
    assert [e["ok"] for e in checked(c, "test_commit_then_implementation")] == [False, True]
    assert "noch nicht abgenommen" in box.agent_calls[1]["prompt"]
    first_commit = c.sh.run(["git", "rev-parse", "karte/k1~1"], cwd=c.project).out.strip()
    assert c.state("K1")["test_commit"] == first_commit


def test_tests_that_were_not_red_may_be_rewritten_and_are_frozen_afterwards(c, box):
    box.tests = [(0, 0)]  # die Tests scheitern ohne die Umsetzung nicht
    strict = {"tests/test_a.py": "from src.a import A\n\n\ndef test_a():\n    assert A == 1 and A != 2\n"}
    box.gate = [GATE_LEAK]
    box.agent = [
        None,
        lambda: rebuild_branch(c, strict, CODE_FILE),                  # Tests neu geschrieben: erlaubt
        lambda: commit_on_branch(c, "tests/test_a.py", "def test_a():\n    assert True\n"),  # jetzt nicht mehr
    ]
    with pytest.raises(Stop):
        c.run("K1")
    assert "noch nicht abgenommen" in box.agent_calls[1]["prompt"]
    assert "abgenommen und bleiben unverändert" in box.agent_calls[2]["prompt"]
    assert [e["ok"] for e in checked(c, "tests_unchanged_since_test_commit")] == [True, True, False]
    assert c.state("K1")["returns"] == {"gate": 1, "checks": 2} and halt(c)["cause"] == "loop_limit"


def test_replacing_the_accepted_test_commit_is_rejected_even_with_the_same_tests(c, box):
    box.gate = [GATE_LEAK]
    box.agent = [None, lambda: rebuild_branch(c, TEST_FILE, CODE_FILE)]  # gleiche Tests, anderer Commit
    with pytest.raises(Stop):
        c.run("K1")
    rework = checked(c, "tests_unchanged_since_test_commit")[1]
    assert rework["ok"] is False and rework["same_commit"] is False and rework["touched"] == []


# ---------- Die Tests am Stand sind die, die am Test-Commit rot waren


@pytest.mark.parametrize("path, content", [
    ("tests/test_a.py", "def test_a():\n    assert True\n"),           # Test abgeschwächt
    ("tests/conftest.py", 'collect_ignore = ["test_a.py"]\n'),           # Test ausgeblendet
    ("tests/test_ä.py", "def test_neu():\n    assert True\n"),          # Name, den git in Anführungszeichen setzt
])
def test_a_first_submission_that_changes_tests_after_the_test_commit_is_not_accepted(c, box, path, content):
    box.agent = [lambda: commit_on_branch(c, path, content)]  # dritter Commit derselben Abgabe
    with pytest.raises(Stop):
        c.run("K1")
    first = checked(c, "tests_unchanged_since_test_commit")[0]
    assert first["ok"] is False and first["touched"] == [path]
    assert "test_commit" not in c.state("K1")  # nicht abgenommen
    assert "gate" not in moves(c) and c.column("K1") == "implementation"
    assert "noch nicht abgenommen" in box.agent_calls[1]["prompt"]


def test_a_rebuild_with_weakened_tests_in_the_second_commit_is_not_accepted(c, box):
    weak = {"tests/test_a.py": "def test_a():\n    assert True\n"}
    box.agent = [
        lambda: rebuild_branch(c, {**TEST_FILE, **CODE_FILE}),   # erst alles in einem Commit
        lambda: rebuild_branch(c, TEST_FILE, {**CODE_FILE, **weak}),  # dann Test-Commit, aber der zweite schwächt ab
    ]
    with pytest.raises(Stop):
        c.run("K1")
    assert [e["ok"] for e in checked(c, "tests_unchanged_since_test_commit")][0] is False
    assert "gate" not in moves(c)


# ---------- Ausfall der Prüf-Sandbox


@pytest.mark.parametrize("answer", [
    Result(124, "", "Zeitlimit nach 900 s"),  # keine Ergebniszeilen
    "sync=91 pytest=-1",                      # Stand kam nicht vollständig an
    "sync=2 pytest=-1",                       # Umgebung ließ sich nicht aufbauen
    "sync=0 pytest=127",                      # Testbefehl nicht gefunden
    "sync=0 pytest=137",                      # Testbefehl abgeschossen
])
def test_tests_that_did_not_run_halt_the_card_instead_of_returning_it(c, box, answer):
    if isinstance(answer, str):
        commits = [c.sh.run(["git", "rev-parse", ref], cwd=c.project).out.strip()
                   for ref in ("karte/k1~1", "karte/k1")]
        answer = Result(0, "".join(f"RESULT {sha} {answer}\n" for sha in commits), "")
    box.tests = [answer]
    with pytest.raises(Stop):
        c.run("K1")
    assert halt(c)["cause"] == "checks_incomplete" and halt(c)["step"] == "verify"
    assert checked(c, "tests_ran")[0]["ok"] is False
    assert c.state("K1")["returns"] == {"gate": 0, "checks": 0}  # keine Runde verbraucht
    assert len(box.agent_calls) == 1

    c.resume("K1")
    assert c.column("K1") == "approval" and len(box.agent_calls) == 1


def test_only_the_first_result_line_of_a_commit_counts(c, box):
    """Die letzte Zeile des Testprotokolls stammt aus dem geprüften Code."""
    commits = [c.sh.run(["git", "rev-parse", ref], cwd=c.project).out.strip() for ref in ("karte/k1~1", "karte/k1")]
    forged = "".join(f"RESULT {sha} sync=2 pytest=-1\nTAIL {sha} x\rRESULT {sha} sync=0 pytest=0\n"
                     f"RESULT {sha} sync=0 pytest=0\n" for sha in commits)
    box.tests = [Result(0, forged, "")]
    with pytest.raises(Stop):
        c.run("K1")
    assert halt(c)["cause"] == "checks_incomplete"
