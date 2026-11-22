"""Der Stand kommt vollständig in die Sandbox: mit echtem git und sh, ohne `sbx`."""

import subprocess

import pytest

from helpers import git
from kreislauf.cycle import extract_snippet


@pytest.fixture
def repo(tmp_path):
    """Ein Stand, der Dateien vor `git archive` versteckt und einen Platzhalter ersetzen lässt."""
    project = tmp_path / "quelle"
    (project / "src" / "geheim").mkdir(parents=True)
    (project / "src" / "a.py").write_text("A = 1\n")
    (project / "src" / "evil.py").write_text('TOKEN = "x"\n')
    (project / "src" / "geheim" / "conftest.py").write_text("raise SystemExit\n")
    (project / "src" / "stand.txt").write_text("$Format:%H$\n")
    (project / "src" / ".gitattributes").write_text(
        "evil.py export-ignore\ngeheim export-ignore\nstand.txt export-subst\n")
    (project / "link").symlink_to("src/a.py")
    git(project, "init", "-q", "-b", "main")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "start")
    return project


PATH = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"


def extract(repo, home, sha="HEAD", target="ziel", path=PATH):
    script = extract_snippet(str(repo / ".git")) + 'extract "$1" "$HOME/$2"\n'
    return subprocess.run(["sh", "-c", script, "sh", git(repo, "rev-parse", sha), target],
                          env={"HOME": str(home), "PATH": path}, capture_output=True, text=True)


def fake_tar(tmp_path, afterwards: str) -> str:
    """Ein `tar`, das richtig auspackt und danach etwas am Ziel ändert. PATH dafür."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tar").write_text(f'#!/bin/sh\n/usr/bin/tar "$@" || exit $?\ncd "$3" && {afterwards}\n')
    (bin_dir / "tar").chmod(0o755)
    return f"{bin_dir}:{PATH}"


def test_plain_git_archive_leaves_these_files_out(repo, tmp_path):
    """Die Voraussetzung des Tests: Ohne Gegenmaßnahme fehlen die Dateien."""
    listing = subprocess.run("git archive HEAD | tar -t", shell=True, cwd=repo, capture_output=True, text=True).stdout
    assert "src/a.py" in listing
    assert "src/evil.py" not in listing and "conftest.py" not in listing


def test_extract_brings_the_whole_tree_including_export_ignored_files(repo, tmp_path):
    done = extract(repo, tmp_path)
    assert done.returncode == 0, done.stderr
    target = tmp_path / "ziel"
    assert (target / "src" / "evil.py").read_text() == 'TOKEN = "x"\n'
    assert (target / "src" / "geheim" / "conftest.py").exists()
    assert (target / "src" / "stand.txt").read_text() == "$Format:%H$\n"  # nicht ersetzt
    assert (target / "link").is_symlink()
    tracked = git(repo, "ls-files").split("\n")
    assert sorted(str(p.relative_to(target)) for p in target.rglob("*") if not p.is_dir() or p.is_symlink()) \
        == sorted(tracked)


def test_extract_works_for_a_second_commit_in_the_same_sandbox(repo, tmp_path):
    (repo / "src" / "b.py").write_text("B = 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "b")
    assert extract(repo, tmp_path, "HEAD~1", "alt").returncode == 0
    assert extract(repo, tmp_path, "HEAD", "neu").returncode == 0
    assert not (tmp_path / "alt" / "src" / "b.py").exists()
    assert (tmp_path / "neu" / "src" / "b.py").exists()


def test_extract_reports_a_tree_that_did_not_arrive_complete(repo, tmp_path):
    (tmp_path / "ziel").mkdir()
    (tmp_path / "ziel" / "fremd.txt").write_text("lag schon da\n")  # die Zahl der Dateien stimmt nicht
    assert extract(repo, tmp_path).returncode == 91


def test_extract_fails_for_an_unknown_commit(repo, tmp_path):
    script = extract_snippet(str(repo / ".git")) + 'extract "$1" "$HOME/ziel"\n'
    done = subprocess.run(["sh", "-c", script, "sh", "0" * 40],
                          env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert done.returncode == 90
    assert not (tmp_path / "ziel").exists()


def test_extract_does_not_apply_content_attributes_from_the_commit(repo, tmp_path):
    """`working-tree-encoding`, `eol` und `ident` ändern Bytes; die Scanner sähen dann nicht den Blob."""
    # Im Arbeitsbaum UTF-16, im Repository als UTF-8: so legt git die Datei mit diesem Attribut ab.
    (repo / "src" / "secret.py").write_bytes('TOKEN = "klartext"\n'.encode("utf-16-le"))
    (repo / "src" / "zeilen.txt").write_text("eins\nzwei\n")
    (repo / "src" / "ident.txt").write_text("$Id$\n")
    (repo / "src" / ".gitattributes").write_text(
        "secret.py working-tree-encoding=UTF-16LE\nzeilen.txt text eol=crlf\nident.txt ident\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "attribute")
    plain = subprocess.run("git archive HEAD src/zeilen.txt | tar -xO", shell=True, cwd=repo, capture_output=True)
    assert b"\r\n" in plain.stdout  # Voraussetzung: ohne Gegenmaßnahme ändert git archive die Datei

    done = extract(repo, tmp_path)
    assert done.returncode == 0, done.stderr
    target = tmp_path / "ziel" / "src"
    assert (target / "secret.py").read_bytes() == b'TOKEN = "klartext"\n'
    assert (target / "zeilen.txt").read_bytes() == b"eins\nzwei\n"
    assert (target / "ident.txt").read_bytes() == b"$Id$\n"


@pytest.mark.parametrize("afterwards", [
    "rm -f src/a.py",                    # eine Datei fehlt
    "echo x >> src/a.py",                # eine Datei ist verändert, die Zahl stimmt
    "rm -f src/a.py && echo x > neu",    # eine fehlt, eine ist zu viel: die Zahl stimmt
])
def test_extract_reports_a_missing_or_altered_file(repo, tmp_path, afterwards):
    assert extract(repo, tmp_path, path=fake_tar(tmp_path, afterwards)).returncode == 91


def test_extract_ignores_replace_refs_and_a_prepared_helper_repository(repo, tmp_path):
    """Liegt das Hilfs-Repository schon da, etwa von Code aus einem früheren Stand, gilt es nicht."""
    assert extract(repo, tmp_path, target="erst").returncode == 0
    helper = tmp_path / ".kreislauf-git"
    (helper / "info" / "attributes").write_text("")  # Attribute wieder eingeschaltet
    done = extract(repo, tmp_path, target="dann")
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "dann" / "src" / "evil.py").exists()
    assert (tmp_path / "dann" / "src" / "stand.txt").read_text() == "$Format:%H$\n"
