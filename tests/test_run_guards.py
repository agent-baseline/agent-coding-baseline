"""`run` lässt nichts durch: Route, Abholen und Gate halten an, wenn etwas nicht stimmt."""


import pytest

from helpers import CARD, CLEAN, LEAK, VULN, git, make_cycle, release, set_card
from kreislauf.cycle import Result, Stop


@pytest.fixture
def cycle(tmp_path):
    return make_cycle(tmp_path)


def conditions(cycle):
    return {e["evidence"]["condition"]: e["evidence"] for e in cycle.events("K1", "condition_checked")}


# ---------- Route


@pytest.mark.parametrize("changes, tier", [
    ({"route": "voll"}, "internal"),          # nicht gebaut: liefe sonst ohne Review durch
    ({"route": "irgendwas"}, "internal"),
    ({}, "public"),                           # kurz ist bei public nicht erlaubt
    ({"allowed_paths": ["**"]}, "internal"),  # erlaubte Pfade decken heikle Pfade ab
])
def test_run_does_not_start_a_sandbox_on_a_route_that_is_not_allowed(cycle, changes, tier):
    set_card(cycle, **changes)
    cycle.tier = tier
    release(cycle)
    with pytest.raises(Stop):
        cycle.run("K1")
    assert conditions(cycle)["route_allowed"]["ok"] is False
    assert not cycle.sh.called("sbx")
    assert cycle.column("K1") == "decomposition"


def test_run_starts_the_sandbox_on_an_allowed_route(cycle):
    release(cycle)
    with pytest.raises(Stop):  # hält später an: Aus der gespielten Sandbox gibt es nichts abzuholen
        cycle.run("K1")
    assert conditions(cycle)["route_allowed"]["ok"] is True
    assert cycle.sh.called("sbx", "create")


# ---------- Abholen


def stale_ref(cycle):
    """Ein Ref aus einem früheren Abholen, wie ihn `git fetch` hinterlässt."""
    head = cycle.state("K1")["head"]
    git(cycle.project, "update-ref", "refs/remotes/sandbox-x/karte/k1", head)
    return head


def test_a_failed_fetch_does_not_fall_back_to_the_ref_of_an_earlier_fetch(cycle):
    stale_ref(cycle)
    cycle.sh.on(("git", "fetch"), Result(1, "", "fatal: 'sandbox-x' does not appear to be a git repository"))
    sha, origin = cycle._fetch(CARD, cycle.state("K1"))
    assert sha is None
    assert "failed" in origin


def test_a_failed_fetch_stops_the_card(cycle):
    stale_ref(cycle)
    cycle.sh.on(("git", "fetch"), Result(1, "", "fatal: no remote"))
    with pytest.raises(Stop):
        cycle._advance(CARD, cycle.state("K1"), "verify")
    assert conditions(cycle)["branch_fetched"]["ok"] is False
    assert not cycle.sh.called("sbx", "create")  # keine Prüfung auf dem alten Stand


def test_a_successful_fetch_returns_the_fetched_commit(cycle):
    head = stale_ref(cycle)
    cycle.sh.on(("git", "fetch"), Result(0, "", ""))
    assert cycle._fetch(CARD, cycle.state("K1")) == (head, "refs/remotes/sandbox-x/karte/k1")


# ---------- Gate

def scanners(cycle, codes, files):
    """Spielt die Scan-Sandbox: Exit-Codes der Scanner und die Berichte, die `sbx cp` herausholt."""
    codes = {"gitleaks-history": 0, "gitleaks-messages": 0, **codes}
    st = cycle.state("K1")
    commits = git(cycle.project, "rev-list", "--count", f"{st['base']}..{st['head']}")
    out = ("".join(f"RESULT {tool} {code}\n" for tool, code in codes.items())
           + f"HISTORY commits {commits}\nHISTORY messages {commits}\nVERSION gitleaks v8 x\n")
    cycle.sh.on(("sbx", "exec"), Result(0, out, ""))

    def cp(argv):
        name = argv[-1].rsplit("/", 1)[-1]
        if name not in files:
            return Result(1, "", "no such file")
        with open(argv[-1], "w") as fh:
            fh.write(files[name])
        return Result(0, "", "")

    cycle.sh.on(("sbx", "cp"), cp)


def gate(cycle):
    return cycle._gate(CARD, cycle.state("K1"))


def test_gate_is_green_when_exit_codes_and_reports_agree(cycle):
    scanners(cycle, {"gitleaks": 0, "osv-scanner": 0, "syft": 0}, CLEAN)
    assert gate(cycle) == []
    assert cycle._gate_green("K1", cycle.state("K1")["head"])


def test_gate_returns_the_findings_the_scanners_report(cycle):
    scanners(cycle, {"gitleaks": 1, "osv-scanner": 1, "syft": 0},
             {**CLEAN, "gitleaks.json": LEAK, "osv.json": VULN})
    assert [f["tool"] for f in gate(cycle)] == ["gitleaks", "osv-scanner"]
    assert not cycle._gate_green("K1", cycle.state("K1")["head"])


@pytest.mark.parametrize("tool, fname", [("osv-scanner", "osv.json"), ("gitleaks", "gitleaks.json")])
def test_gate_stops_when_a_scanner_reports_findings_but_the_report_is_missing(cycle, tool, fname):
    # Ein sauberer Bericht aus einem früheren Lauf liegt noch im Zielordner.
    earlier = cycle.run_dir / "K1" / "gate-1"
    earlier.mkdir(parents=True)
    (earlier / fname).write_text(CLEAN[fname])
    files = {k: v for k, v in CLEAN.items() if k != fname}
    scanners(cycle, {"gitleaks": 0, "osv-scanner": 0, "syft": 0, tool: 1}, files)
    with pytest.raises(Stop):
        gate(cycle)
    assert conditions(cycle)[f"{tool}_ran"]["ok"] is False
    assert not cycle._gate_green("K1", cycle.state("K1")["head"])


@pytest.mark.parametrize("tool, fname", [("osv-scanner", "osv.json"), ("gitleaks", "gitleaks.json")])
def test_a_clean_report_of_an_earlier_run_does_not_stand_in_for_a_missing_one(cycle, tool, fname):
    """Der Scanner meldet nichts, sein Bericht lässt sich aber nicht herausholen."""
    earlier = cycle.run_dir / "K1" / "gate-1"
    earlier.mkdir(parents=True)
    (earlier / fname).write_text(CLEAN[fname])
    scanners(cycle, {"gitleaks": 0, "osv-scanner": 0, "syft": 0}, {k: v for k, v in CLEAN.items() if k != fname})
    with pytest.raises(Stop):
        gate(cycle)
    assert conditions(cycle)[f"{tool}_ran"]["ok"] is False
    assert not (earlier / fname).exists()


@pytest.mark.parametrize("tool", ["osv-scanner", "gitleaks"])
def test_gate_stops_when_a_scanner_reports_findings_but_the_report_has_none(cycle, tool):
    scanners(cycle, {"gitleaks": 0, "osv-scanner": 0, "syft": 0, tool: 1}, CLEAN)
    with pytest.raises(Stop):
        gate(cycle)
    assert conditions(cycle)[f"{tool}_ran"]["ok"] is False


def test_gate_stops_when_a_report_is_not_readable(cycle):
    scanners(cycle, {"gitleaks": 0, "osv-scanner": 0, "syft": 0}, {**CLEAN, "osv.json": "{kaputt"})
    with pytest.raises(Stop):
        gate(cycle)
    assert conditions(cycle)["osv-scanner_ran"]["report_readable"] is False


def test_gate_stops_when_osv_finds_no_packages_although_a_lockfile_is_there(cycle):
    files = {k: v for k, v in CLEAN.items() if k != "osv.json"}
    scanners(cycle, {"gitleaks": 0, "osv-scanner": 128, "syft": 0}, files)
    with pytest.raises(Stop):
        gate(cycle)
    assert conditions(cycle)["osv-scanner_ran"]["lockfile_present"] is True


# ---------- Tags aus der Sandbox


def sandbox_remote(cycle, tmp_path):
    """Ein echtes git-Repository als Sandbox: derselbe Branch, dazu Tags, die sich als
    `main` und als Kurzform eines Commits ausgeben."""
    remote = tmp_path / "sandbox"
    git(tmp_path, "clone", "-q", str(cycle.project), str(remote))
    git(remote, "checkout", "-q", "karte/k1")
    head = git(remote, "rev-parse", "HEAD")
    base = git(remote, "rev-parse", "origin/main")
    git(remote, "tag", "main", head)        # zeigt auf den Stand der Karte, nicht auf main
    git(remote, "tag", base[:10], head)     # sieht aus wie die Kurzform der Basis
    git(cycle.project, "remote", "add", "sandbox-x", str(remote))
    return head


def test_fetch_does_not_bring_tags_from_the_sandbox(cycle, tmp_path):
    """Ein Tag namens `main` hätte Vorrang vor dem Branch: `git diff main …` im Review
    zeigte dann auf einen anderen Stand."""
    head = sandbox_remote(cycle, tmp_path)
    sha, origin = cycle._fetch(CARD, cycle.state("K1"))
    assert sha == head and origin == "refs/remotes/sandbox-x/karte/k1"
    assert git(cycle.project, "tag", "--list") == ""
    assert git(cycle.project, "rev-parse", "main") == git(cycle.project, "rev-parse", "refs/heads/main")
