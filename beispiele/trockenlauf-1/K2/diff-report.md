# Diff-Bericht K2: Benutzerliste nur für admin

- Stand: `ecebc1bf03d024fa6e261dcdcc4b072c42cb8661` auf `karte/k2-benutzerliste`, Basis `5aa118fea9`
- Ansehen: `git diff 5aa118fea9 ecebc1bf03` oder `git diff main refs/sandboxes/bba-trockenlauf-1-k2/karte/k2-benutzerliste`
- Rückläufe: {'gate': 1, 'checks': 0}
- Heikle Pfade geändert: keine
- Gate-Lauf 2: Berichte und SBOM unter `.baseline/runs/trockenlauf-1/K2/gate-2/`

## Commits

```
ecebc1b agent <agent@bba-trockenlauf-1-k2.bba-trockenlauf-1-k2.docker.internal>
    Upstream-Token aus der Umgebung lesen statt im Code (Gate-Befund gitleaks github-pat)
89711bc agent <agent@bba-trockenlauf-1-k2.bba-trockenlauf-1-k2.docker.internal>
    Upstream-Anbindung mit festem Token (eingebauter Fehler des Trockenlaufs)
497cfdc agent <agent@bba-trockenlauf-1-k2.bba-trockenlauf-1-k2.docker.internal>
    Benutzerliste GET /users nur für admin (K2)
742df28 agent <agent@bba-trockenlauf-1-k2.bba-trockenlauf-1-k2.docker.internal>
    Tests: Benutzerliste nur für admin (K2)
```

## Geänderte Dateien

```
src/notiz_api/app.py      |  2 ++
 src/notiz_api/auth.py     |  5 +++++
 src/notiz_api/upstream.py | 11 +++++++++++
 src/notiz_api/users.py    | 13 +++++++++++++
 tests/test_users.py       | 30 ++++++++++++++++++++++++++++++
 5 files changed, 61 insertions(+)
```

Die Commits stammen aus einer Sandbox und sind ein Fremdbeitrag. Der Bericht des Agenten
steht im Protokoll unter `run_ended` als Behauptung.
