import json

from helpers import CARD
from kreislauf.checks import (
    above_threshold,
    branch_problem,
    card_problems,
    first_commit_is_test_only,
    has_lockfile,
    outside_allowed,
    parse_gitleaks,
    parse_osv,
    path_matches,
    route_problem,
    scan_consistent,
    severity_from_score,
)
from kreislauf.checks import tests_ran as ran  # nicht unter einem Namen, den pytest als Test einsammelt

ALLOWED = ["src/notiz_api/**", "tests/**"]


def test_double_star_matches_everything_below_a_directory():
    assert path_matches("src/notiz_api/notes.py", ALLOWED)
    assert path_matches("tests/unit/deep/test_x.py", ALLOWED)
    assert not path_matches("src/other/x.py", ALLOWED)
    assert not path_matches("tests_extra/x.py", ALLOWED)


def test_exact_names_and_single_star():
    assert path_matches("uv.lock", ["uv.lock"])
    assert not path_matches("sub/uv.lock", ["uv.lock"])
    assert path_matches("docs/a.md", ["docs/*.md"])
    assert not path_matches("docs/sub/a.md", ["docs/*.md"])


def test_outside_allowed_lists_offenders_and_sensitive_paths_win():
    changed = ["src/notiz_api/notes.py", "tests/test_notes.py", ".claude/settings.json", "uv.lock"]
    assert outside_allowed(changed, ALLOWED) == [".claude/settings.json", "uv.lock"]
    # Ein heikler Pfad bleibt gesperrt, auch wenn ein erlaubtes Muster ihn träfe.
    assert outside_allowed(["src/notiz_api/x.py"], ["**"], sensitive=["src/notiz_api/x.py"]) == [
        "src/notiz_api/x.py"
    ]


def test_first_commit_must_touch_only_tests():
    assert first_commit_is_test_only([["tests/test_notes.py"], ["src/notiz_api/notes.py"]], ["tests/**"])
    assert not first_commit_is_test_only([["tests/test_notes.py", "src/a.py"], ["src/b.py"]], ["tests/**"])
    assert not first_commit_is_test_only([["tests/test_notes.py"]], ["tests/**"])  # nur ein Commit
    assert not first_commit_is_test_only([[], ["src/b.py"]], ["tests/**"])  # leerer erster Commit


def test_route_kurz_only_without_sensitive_paths_below_public():
    sensitive = [".baseline/**", "uv.lock"]
    tracked = ["src/notiz_api/app.py", "tests/test_app.py", ".baseline/project.json", "uv.lock"]
    assert route_problem("kurz", "internal", ALLOWED, sensitive, tracked) is None
    assert route_problem("kurz", "hobby", ALLOWED, sensitive, tracked) is None
    assert "public" in route_problem("kurz", "public", ALLOWED, sensitive, tracked)
    assert "uv.lock" in route_problem("kurz", "internal", ["**"], sensitive, tracked)
    # voll ist nicht gebaut; die Karte liefe sonst ohne Review durch.
    assert "not built" in route_problem("voll", "internal", ALLOWED, sensitive, tracked)
    assert "not built" in route_problem("", "internal", ALLOWED, sensitive, tracked)


def test_lockfiles_are_found_at_any_depth():
    assert has_lockfile(["src/a.py", "uv.lock"])
    assert has_lockfile(["frontend/package-lock.json"])
    assert not has_lockfile(["src/a.py", "docs/uv.lock.md", ""])


def test_exit_code_and_report_of_a_scanner_must_agree():
    assert scan_consistent(0, [])
    assert not scan_consistent(0, None)          # sauber gemeldet, aber kein lesbarer Bericht
    assert scan_consistent(1, [{"rule": "x"}])
    assert not scan_consistent(1, [])            # Funde gemeldet, Bericht leer
    assert not scan_consistent(1, None)          # Funde gemeldet, Bericht fehlt
    assert not scan_consistent(2, [])            # unbekannter Exit-Code
    assert not scan_consistent(None, [])         # Scanner hat kein Ergebnis gemeldet
    assert not scan_consistent(128, None)        # 128 gilt nur für den Scanner, der es kennt
    assert scan_consistent(128, None, no_packages_code=128, packages_expected=False)
    assert not scan_consistent(128, None, no_packages_code=128, packages_expected=True)


def test_threshold_depends_on_tier_not_on_scanner():
    assert above_threshold("critical", "hobby")
    assert not above_threshold("high", "hobby")
    assert above_threshold("high", "internal")
    assert not above_threshold("medium", "internal")
    assert above_threshold("high", "public")
    # Unbekannte Schwere zählt vorsichtshalber wie hoch.
    assert above_threshold("unknown", "internal")
    assert not above_threshold("unknown", "hobby")


def test_severity_from_cvss_score():
    assert severity_from_score("9.8") == "critical"
    assert severity_from_score("7.5") == "high"
    assert severity_from_score("5.0") == "medium"
    assert severity_from_score("2.1") == "low"
    assert severity_from_score("") == "unknown"
    assert severity_from_score(None) == "unknown"


def test_gitleaks_findings_are_critical_and_carry_no_secret():
    report = json.dumps([
        {
            "RuleID": "github-pat",
            "Description": "Uncovered a GitHub Personal Access Token",
            "File": "src/notiz_api/settings.py",
            "StartLine": 4,
            "Secret": "REDACTED",
            "Match": "TOKEN = REDACTED",
        }
    ])
    (f,) = parse_gitleaks(report)
    assert f["tool"] == "gitleaks"
    assert f["rule"] == "github-pat"
    assert f["file"] == "src/notiz_api/settings.py"
    assert f["line"] == 4
    assert f["severity"] == "critical"
    assert "Secret" not in f and "Match" not in f
    assert f["demand"]


def test_gitleaks_empty_report():
    assert parse_gitleaks("[]") == []
    assert parse_gitleaks("") == []
    assert parse_gitleaks("null") == []


def test_osv_groups_become_findings_and_malware_is_critical():
    report = json.dumps({
        "results": [
            {
                "source": {"path": "/home/agent/scan/uv.lock", "type": "lockfile"},
                "packages": [
                    {
                        "package": {"name": "jinja2", "version": "3.1.2", "ecosystem": "PyPI"},
                        "vulnerabilities": [
                            {"id": "GHSA-h5c8-rqwp-cp95", "summary": "Sandbox breakout"},
                            {"id": "MAL-2026-1", "summary": "Malicious code"},
                        ],
                        "groups": [
                            {"ids": ["GHSA-h5c8-rqwp-cp95"], "max_severity": "8.4"},
                            {"ids": ["MAL-2026-1"], "max_severity": ""},
                        ],
                    }
                ],
            }
        ]
    })
    high, mal = parse_osv(report)
    assert high["tool"] == "osv-scanner"
    assert high["rule"] == "GHSA-h5c8-rqwp-cp95"
    assert high["severity"] == "high"
    assert high["file"] == "uv.lock"
    assert "jinja2" in high["risk"] and "3.1.2" in high["risk"]
    assert mal["severity"] == "critical"


def test_osv_without_results():
    assert parse_osv(json.dumps({"results": []})) == []
    assert parse_osv("") == []


def test_tests_ran_only_with_a_verdict_of_the_test_command():
    assert ran({"sync": 0, "pytest": 0})
    assert ran({"sync": 0, "pytest": 1})
    assert ran({"sync": 0, "pytest": 5})       # keine Tests gefunden: ein Urteil, nicht grün
    assert not ran({})                          # kein Ergebnis
    assert not ran({"sync": 91, "pytest": -1})  # Stand unvollständig
    assert not ran({"sync": 2, "pytest": -1})   # Umgebung nicht aufgebaut
    assert not ran({"sync": 0, "pytest": 126})  # nicht ausführbar
    assert not ran({"sync": 0, "pytest": 127})  # nicht gefunden
    assert not ran({"sync": 0, "pytest": 137})  # von einem Signal beendet


# ---------- Karte


def test_a_complete_card_has_no_problems():
    assert card_problems(CARD, "K1") == []


def test_plain_branch_names_pass():
    for name in ("karte/k1", "karte/k3-notiz-loeschen", "feature_1", "a/b/c", "v1.2"):
        assert branch_problem(name) is None, name


def test_branch_names_with_shell_or_ref_syntax_are_refused():
    for name in ("karte/k1; touch x", "karte/$(id)", "karte/`id`", "karte/k1 x", "karte/k1\nid",
                 "-q", "--upload-pack=x", "karte/-x", ".hidden", "karte//k1", "/karte", "karte/",
                 "a..b", "karte/k1.lock", "karte/k1.", "refs/heads/x:main", "karte/k1~1", "karte/ä"):
        assert branch_problem(name) is not None, name


def test_a_card_with_an_unsafe_branch_is_incomplete():
    problems = card_problems({**CARD, "branch": "karte/k1; touch x"}, "K1")
    assert len(problems) == 1 and "branch" in problems[0]
    # Ein fehlender Branch wird einmal gemeldet, nicht zweimal.
    assert card_problems({**CARD, "branch": ""}, "K1") == ["branch missing or empty"]
