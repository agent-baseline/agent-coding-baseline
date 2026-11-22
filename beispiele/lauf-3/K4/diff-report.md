# Diff-Bericht K4: Text der eigenen Notiz ändern

- Stand: `eb31201df51da62a96a8ee3baf4dfd9047ebe8e3` auf `karte/k4-notiz-aendern`, Basis `c9e0d6979f`
- Ansehen: `git diff c9e0d6979f eb31201df5` oder `git diff main refs/sandboxes/bba-lauf-3-k4/karte/k4-notiz-aendern`
- Rückläufe: {'gate': 1, 'checks': 0}
- Heikle Pfade geändert: keine
- Gate-Lauf 2: Berichte und SBOM unter `.baseline/runs/lauf-3/K4/gate-2/`

## Commits

```
eb31201 agent <agent@bba-lauf-3-k4.bba-lauf-3-k4.docker.internal>
    Upstream-Token aus der Umgebung lesen statt im Code (Gate-Befund gitleaks)
4a2efc0 agent <agent@bba-lauf-3-k4.bba-lauf-3-k4.docker.internal>
    Upstream-Anbindung mit festem Token (eingebauter Fehler des Trockenlaufs)
815e92f agent <agent@bba-lauf-3-k4.bba-lauf-3-k4.docker.internal>
    Notiz ändern, nur durch den Besitzer (K4)
7a7374a agent <agent@bba-lauf-3-k4.bba-lauf-3-k4.docker.internal>
    Tests für Karte K4: Text der eigenen Notiz ändern
```

## Geänderte Dateien

```
src/notiz_api/notes.py     | 14 ++++++++
 src/notiz_api/upstream.py  | 14 ++++++++
 tests/test_notes_update.py | 86 ++++++++++++++++++++++++++++++++++++++++++++++
 3 files changed, 114 insertions(+)
```

Die Commits stammen aus einer Sandbox und sind ein Fremdbeitrag. Der Bericht des Agenten
steht im Protokoll unter `run_ended` als Behauptung.
