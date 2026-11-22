# Diff-Bericht K1: Notizen anlegen und lesen, mit Besitzerprüfung

- Stand: `971eacc4266e5415883afe745acc3388ad3e7a4a` auf `karte/k1-notizen`, Basis `5aa118fea9`
- Ansehen: `git diff 5aa118fea9 971eacc426` oder `git diff main refs/sandboxes/bba-trockenlauf-1-k1/karte/k1-notizen`
- Rückläufe: {'gate': 0, 'checks': 0}
- Heikle Pfade geändert: keine
- Gate-Lauf 1: Berichte und SBOM unter `.baseline/runs/trockenlauf-1/K1/gate-1/`

## Commits

```
971eacc agent <agent@bba-trockenlauf-1-k1.bba-trockenlauf-1-k1.docker.internal>
    Notizen anlegen und lesen mit Besitzerprüfung (K1)
1572ac1 agent <agent@bba-trockenlauf-1-k1.bba-trockenlauf-1-k1.docker.internal>
    Tests: Notizen anlegen und lesen mit Besitzerprüfung (K1)
```

## Geänderte Dateien

```
src/notiz_api/app.py   |  2 +
 src/notiz_api/notes.py | 55 ++++++++++++++++++++++++++++
 tests/test_notes.py    | 99 ++++++++++++++++++++++++++++++++++++++++++++++++++
 3 files changed, 156 insertions(+)
```

Die Commits stammen aus einer Sandbox und sind ein Fremdbeitrag. Der Bericht des Agenten
steht im Protokoll unter `run_ended` als Behauptung.
