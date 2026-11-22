# Kreislauf-Skript: Bedienung und Protokollformat

Stand 2026-11-21. Das Skript `kreislauf/` führt eine Karte ohne Oberfläche durch Umsetzung, Prüfung von außen, Gate und Freigabe und schreibt dabei das Ereignisprotokoll. Der Entwurf dahinter steht in `Workflow.md`; was davon gebaut ist, sagt dort die Einleitung. Was an echten Läufen erprobt ist, steht unten unter „Grenzen".

## Voraussetzungen

- Mac oder Linux mit `sbx` ab 0.54 (angemeldet mit `sbx login`), Arbeits-Vorlage `baseline-claude:2026-11-18-3` und Scan-Vorlage `baseline-scan:2026-11-20`. Beide baut man selbst, siehe „Vorlagen bauen".
- In `sbx` ist `browser.enabled` abgeschaltet (`sbx settings set browser.enabled false`). Die Voreinstellung erlaubt Prozessen in jeder Sandbox, eine Adresse im Browser des Rechners zu öffnen.
- Für die Arbeits-Sandbox eine eigene Anmeldung bei Claude Code: einmal `/login` in einer beliebigen Sandbox, das gilt dann für alle. Stattdessen geht ein API-Schlüssel im Schlüsselspeicher von `sbx` (`sbx secret set anthropic`); so auf einem zweiten Rechner genutzt, auf dem ersten nicht erprobt.
- Python ab 3.12 für das Skript, ohne Zusatzpakete. Die Tests brauchen `pytest`; die Umgebung dafür kommt aus dem Lockfile (`uv sync --locked`), die Tests laufen mit `uv run --locked pytest -q` und brauchen kein `sbx`.
- Das Projekt ist ein Git-Repository mit sauberem Arbeitsverzeichnis auf `main`, mit `AGENTS.md` (Abschnitt „Sicherheitsmodell") und `.baseline/project.json`.

## Vorlagen bauen

Beide Vorlagen entstehen lokal aus dem Standard-Image von `sbx`; sie werden nicht verteilt. Die Tags müssen zu dem passen, was das Skript erwartet:

```bash
werkzeuge/sandbox-template-build.sh 2026-11-18-3
```

```bash
werkzeuge/scan-template-build.sh 2026-11-20
```

Ohne Tag nehmen beide Skripte das heutige Datum. Wer so baut, nennt dem Kreislauf die Vorlagen über die Umgebung:

| Variable | Bedeutung | Standard |
| --- | --- | --- |
| `KREISLAUF_WORK_TEMPLATE` | Vorlage der Arbeits-Sandbox | `baseline-claude:2026-11-18-3` |
| `KREISLAUF_SCAN_TEMPLATE` | Vorlage der Prüf- und Scan-Sandbox | `baseline-scan:2026-11-20` |
| `KREISLAUF_WIP` | parallele Karten in der Umsetzung | `2` |
| `KREISLAUF_RUN_TIMEOUT` | Zeitlimit eines Agentenlaufs in Sekunden | `2400` |

Die Arbeits-Vorlage (`werkzeuge/sandbox-template-build.sh` mit der Konfiguration aus `werkzeuge/sandbox-home/`) bringt Node und npm mit, jeweils die neueste Version, die mindestens sieben Tage alt ist, dazu den Cooldown für npm, uv und pip. Eine Anmeldedatei enthält sie nicht.

Beide Skripte nehmen die neuesten Versionen, die alt genug sind. Zwei Vorlagen mit demselben Tag, an verschiedenen Tagen gebaut, können sich also in den Versionen unterscheiden. Die Scan-Vorlage hält ihre Versionen in `/usr/local/share/baseline-scan/versions.txt` fest; das Gate schreibt sie ins Protokoll.

Zur Scan-Vorlage:

Das Skript nimmt je Scanner das neueste Release, das mindestens sieben Tage alt ist, und prüft die Prüfsumme gegen die Prüfsummen-Datei des Release. Die Vorlage enthält Gitleaks, OSV-Scanner und Syft, aber kein Claude Code. Semgrep fehlt noch.

Nach jedem Neubau der Scan-Vorlage die Probe für Gitleaks wiederholen:

```bash
python3 werkzeuge/gitleaks-probe/probe.py
```

Das Skript baut außerhalb des Projekts ein Wegwerf-Repository mit erfundenen Tokens (`werkzeuge/gitleaks-probe/build.sh`, bei jedem Bau neue Zufallswerte), legt daraus eine Scan-Sandbox an, führt dort die Shell-Zeilen des Gates aus und vergleicht die drei Berichte mit der Erwartung. Die Tests des Projekts spielen Gitleaks mit einer Attrappe; nur diese Probe zeigt, ob das Gitleaks und das git der Vorlage die Aufrufe kennen und alles lesen. Exit 0 heißt: alle Fälle wie erwartet. `sbx create` braucht den entsperrten Schlüsselbund, das Skript läuft also im Terminal. Ohne `--rm` bleiben Sandbox und Repository stehen, und das Skript nennt die Befehle zum Aufräumen.

## Dateien im Projekt

| Datei | Inhalt |
| --- | --- |
| `.baseline/project.json` | Schutzstufe (`tier`), heikle Pfade, Setup- und Testbefehl, Präfix für Sandbox-Namen |
| `.baseline/jobs/<auftrag>/briefing.md` | Ziel, Grenzen, Abnahme des Auftrags |
| `.baseline/jobs/<auftrag>/cards/<karte>.json` | Arbeitspaket: Titel, Route, Branch, Modell, Effort, erlaubte Pfade, Abnahmekriterien, Aufgabe. Der Branch ist ein schlichter Name: Buchstaben, Ziffern, `.`, `_`, `-`, Teile mit `/` getrennt, jeder Teil beginnt mit Buchstabe oder Ziffer. |
| `.baseline/runs/<auftrag>/events.jsonl` | Ereignisprotokoll, nur anhängend |
| `.baseline/runs/<auftrag>/<karte>/` | Arbeitsstand des Skripts (`state.json`), Ausgabe der Agentenläufe, Gate-Berichte und SBOM je Gate-Lauf, Diff-Bericht |

`.baseline/runs/` ist im Beispielprojekt nicht eingecheckt. Ob das Protokoll ins Repository gehört, ist offen.

## Befehle

Alle Befehle laufen im Ordner dieses Projekts. `P` ist der Projektpfad, zum Beispiel `~/Projects/mein-projekt`.

| Befehl | Wirkung |
| --- | --- |
| `python3 -m kreislauf init --project P --job J` | nimmt die Karten des Auftrags ins Protokoll auf |
| `python3 -m kreislauf release --project P --job J --card K1 --reason "…"` | hält die Freigabe des Menschen aus der Zerlegung fest |
| `python3 -m kreislauf run --project P --job J --card K1` | startet eine Karte aus der Zerlegung und führt sie bis in die Spalte Freigabe |
| `python3 -m kreislauf run … --inject-fault` | Trockenlauf: baut den in der Karte genannten Fehler ein (`dry_run_fault`: `secret` einmal nach dem ersten Agentenlauf, `secret-every-round` nach jedem Agentenlauf, damit die Schleifengrenze erreichbar ist) |
| `python3 -m kreislauf decide --project P --job J --card K1 --verdict approved --reason "…"` | hält die Entscheidung des Menschen fest, in der Freigabe oder an einer angehaltenen Karte; bei `rejected` geht die Karte in die Ablage (`parked`) |
| `python3 -m kreislauf resume --project P --job J --card K1` | setzt eine angehaltene Karte an dem Schritt fort, den der Halt nennt |
| `python3 -m kreislauf merge --project P --job J --card K1` | mergt nach `main` und führt die Tests auf dem Mac aus; sind sie rot, nimmt es den Merge zurück |
| `python3 -m kreislauf cleanup --project P --job J --card K1` | löscht die Prüf- und Scan-Sandboxes der Karte |
| `python3 -m kreislauf status --project P --job J` | zeigt Spalte, Stand, Rückläufe und einen offenen Halt je Karte |

`release` und `decide` halten Entscheidungen des Menschen fest. Der Koordinator ruft sie nur mit den Worten des Menschen als Begründung auf. Die Arbeits-Sandbox einer Karte löscht das Skript nie; darüber entscheidet der Mensch.

## Zustände und Übergänge

Der Zustand einer Karte ist ihre Spalte und, wenn sie angehalten hat, der Halt. Beides ergibt sich allein aus dem Protokoll (`kreislauf/machine.py`). Einen Spaltenwechsel gibt es nur, wenn er in dieser Tabelle steht; das Skript schreibt jeden Wechsel über dieselbe Stelle und weist alles andere ab.

| Von | Nach | Wodurch |
| --- | --- | --- |
| – | `decomposition` | `init`: Karte angelegt |
| `decomposition` | `implementation` | `run`: Ready erfüllt, Arbeits-Sandbox steht |
| `implementation` | `implementation` | Rücklauf aus der Prüfung von außen |
| `implementation` | `gate` | Done der Umsetzung erfüllt |
| `gate` | `implementation` | Rücklauf aus dem Gate |
| `gate` | `approval` | Gate ohne Befund |
| `approval` | `merged` | `merge`: gemergt, Tests auf dem Mac grün |
| `approval` | `approval` | `merge`: Tests auf dem Mac rot, Merge zurückgenommen |
| `approval` | `parked` | `decide`: in der Freigabe abgelehnt |
| `decomposition`, `implementation`, `gate` | `parked` | `decide`: angehaltene Karte abgelehnt |

Aus `merged` und `parked` führt nichts heraus.

Welcher Befehl in welchem Zustand gilt:

| Befehl | Gilt, wenn |
| --- | --- |
| `run` | die Karte in `decomposition` steht und nicht angehalten ist |
| `resume` | die Karte angehalten ist; wartet sie auf den Menschen, erst nach dessen Zustimmung |
| `decide` | die Karte in `approval` steht oder angehalten ist; zustimmen kann der Mensch an einer angehaltenen Karte nur, wenn sie auf ihn wartet |
| `merge` | die Karte in `approval` steht (weitere Bedingungen unter „Was `merge` tut") |

Ein Befehl, der nicht gilt, bewegt nichts; die Abweisung steht als `condition_checked` im Protokoll. Eine Karte bearbeitet immer nur ein Prozess: `run`, `resume`, `decide`, `merge` und `cleanup` halten für ihre Dauer eine Sperre des Betriebssystems (`flock`) auf `.baseline/runs/<auftrag>/<karte>/lock`. Stirbt der Prozess, ist die Sperre frei; die Datei bleibt liegen und nennt den letzten Besitzer.

## Halt und Fortsetzung

`run` und `resume` arbeiten eine Schrittfolge ab: `start` (Ready prüfen, Arbeits-Sandbox anlegen), `agent` (anstehenden Agentenlauf ausführen), `verify` (abholen, Done von außen prüfen), `gate` (Scanner), danach die Freigabe. Ein Befund führt zurück zu `agent`. Bricht ein Schritt ab, schreibt das Skript `card_halted` mit Grund und Schritt; die Spalte bleibt. `resume` schreibt `card_resumed` und macht an diesem Schritt weiter.

| Grund (`cause`) | Was passiert ist | `resume` macht weiter bei | Am Zug |
| --- | --- | --- | --- |
| `agent_failed`, `agent_timeout`, `limit_reached` | Agentenlauf gescheitert, am Zeitlimit oder am Abo-Limit | `agent`: derselbe Auftrag noch einmal | Koordinator |
| `fetch_failed` | Branch ließ sich nicht abholen | `verify` | Koordinator |
| `sandbox_failed` | Prüf- oder Scan-Sandbox ließ sich nicht anlegen | dem Schritt, der lief | Koordinator |
| `fault_injection_failed` | Trockenlauf: Fehler ließ sich nicht einbauen | `verify` | Koordinator |
| `checks_incomplete` | die Tests sind in der Prüf-Sandbox nicht gelaufen | `verify` | Koordinator |
| `gate_incomplete`, `approval_not_ready` | ein Scanner ist nicht gelaufen oder der Stand kam nicht vollständig an | `gate`: nur das Gate, auf demselben Commit | Koordinator |
| `loop_limit` | Schleifengrenze erreicht | `agent`: eine weitere Runde, frischer Kontext, Opus 5.5 | Mensch |
| `error`, `illegal_transition` | unerwarteter Fehler im Skript | dem Schritt, der lief | Koordinator |
| `interrupted` | Lauf abgebrochen (Strg-C oder abgeschossener Prozess) | dem Schritt, der lief | Koordinator |

- **Kein Halt ohne Bewegung:** Sind Ready-Bedingungen nicht erfüllt, ist die Karte unvollständig oder lässt sich die Arbeits-Sandbox nicht anlegen, bleibt die Karte unbewegt in `decomposition`. Die Fortsetzung ist ein neues `run`.
- **Schleifengrenze:** `decide --verdict approved` gilt für genau eine weitere Runde; findet sie wieder etwas, hält die Karte erneut an. `decide --verdict rejected` stellt die Karte in die Ablage.
- **Ablehnen geht immer:** Jede angehaltene Karte lässt sich mit `decide --verdict rejected` in die Ablage stellen.
- **Abgeschossener Prozess:** Er schreibt keinen Halt mehr. `resume` erkennt den Fall daran, dass die Karte in `implementation` oder `gate` steht, kein Halt offen ist und die Sperre frei ist, und trägt den Halt `interrupted` nach. Der Schritt ergibt sich aus der Spalte und daraus, ob ein Agentenlauf ansteht. Fiel der Abbruch zwischen den Rücklauf an der Schleifengrenze und dessen Halt, wartet die Karte weiter auf den Menschen.
- **Ein Halt nennt immer einen Schritt, der in der Spalte der Karte läuft.** Steht die Karte schon in der Freigabe, gibt es keinen Halt mehr. Prüf- und Scan-Sandboxes bekommen bei jedem Versuch einen neuen Namen, auch nach einem Abbruch.
- **Angehaltene Karten zählen nicht gegen die WIP-Grenze.** Beim Fortsetzen an `start` oder `agent` wird die Grenze neu geprüft.
- **Merge:** In der Freigabe gibt es keinen Halt. Nach einem Konflikt bleibt die Karte dort stehen; nach roten oder abgebrochenen Tests steht sie neu dort und braucht eine neue Entscheidung.

Der Arbeitsstand `state.json` hält fest, was `resume` braucht: den laufenden Schritt, den anstehenden Agentenlauf (`pending` mit Auftrag, Modell, Effort), den zuletzt abgewiesenen Stand und die Zähler.

## Was `run` tut

1. **Ready der Umsetzung prüfen:** Vorab: Die Karte ist vollständig und ihr Branch ein schlichter Name (`card_valid`), und `run` gilt in ihrem Zustand (`command_allowed`). Dann: Route erlaubt, Repository committet, Schutzstufe gesetzt, Sicherheitsmodell in `AGENTS.md`, Karte freigegeben, WIP frei. Fehlt etwas, startet keine Sandbox. Mit den beiden Vorab-Bedingungen und `work_sandbox_created` sind das neun Bedingungen, mehr als die Obergrenze von fünf aus `Workflow.md`. Erlaubt ist nur die Route `kurz`, und die nur unterhalb von `public` und wenn die erlaubten Pfade keine eingecheckte Datei unter den heiklen Pfaden treffen.
2. **Arbeits-Sandbox** im Klon-Modus anlegen und den Auftrag an Claude Code übergeben (`--model`, `--effort`, `--output-format json`). Der Bericht des Agenten steht im Protokoll als Behauptung.
3. **Abholen** mit `git fetch --no-tags --force sandbox-<name>`. Tags aus der Sandbox kommen nicht ins Projekt: Ein Tag namens `main` oder mit der Kurzform eines Commits hätte beim Review Vorrang vor Branch und Commit. Scheitert das Abholen, hält die Karte an; ein Ref aus einem früheren Abholen gilt nicht als aktueller Stand. Mit `--force` überschreibt das Abholen den Ref der Sandbox, wenn die Nachbesserung Commits ersetzt hat.
4. **Done der Umsetzung von außen prüfen:** erster Commit nur Tests, nur erlaubte Pfade geändert, Tests am Test-Commit rot, ganze Suite am Stand grün. Die Tests laufen ohne Modell, je Commit in einer eigenen frischen Prüf-Sandbox: Der Code eines Commits kann das Ergebnis des anderen nicht beeinflussen. Auf dem Mac läuft der abgeholte Code nicht.
   - **Pfadvergleich ohne Umbenennungen:** Eine Umbenennung zählt als Löschen am alten und Anlegen am neuen Pfad. Eine heikle Datei lässt sich so nicht unbemerkt aus dem Baum schieben. Die Pfadlisten kommen unverändert von git (`-z`), auch bei Namen mit Umlauten oder Sonderzeichen.
   - **Tests nur im ersten Commit:** Nach dem Test-Commit ändert kein Commit mehr etwas unter den Test-Pfaden (`tests_unchanged_since_test_commit`), auch nicht durch Verschieben oder eine neue `conftest.py`. So sind die Tests, die am Stand grün sein müssen, dieselben, die am Test-Commit rot waren. Das gilt schon für die erste Abgabe.
   - **Abnahme der Tests:** Abgenommen sind die Tests, sobald eine Abgabe das erfüllt (erster Commit nur Tests, ohne Umsetzung rot, danach unverändert). Von da an ist der Test-Commit festgeschrieben, und jede spätere Abgabe muss ihn tragen. Vorher darf die Nachbesserung den Branch neu aufbauen; der Auftrag der Nachbesserung sagt, welcher Fall gilt.
   - **Tests nicht gelaufen:** Fehlt das Ergebnis, kam der Stand nicht vollständig an, ließ sich die Umgebung nicht aufbauen oder endete der Testbefehl mit einem Exit-Code der Shell (126, 127, ab 128), hält die Karte an (`checks_incomplete`). Das ist kein Befund für den Umsetzer und verbraucht keine Runde. Je Commit zählt nur die erste Ergebniszeile.
   - **Vollständiger und unveränderter Stand:** Prüf- und Scan-Sandbox packen den Commit so aus, dass die Attribute aus dem Stand nicht gelten, die Dateien auslassen oder ihren Inhalt ändern (`export-ignore`, `export-subst`, `ident`, `text`, `eol`, `working-tree-encoding`, `filter`). Danach prüfen sie unabhängig davon die Zahl der Einträge und den Objekt-Hash jeder Datei gegen den Baum des Commits. Tests und Scanner sehen die Bytes, die im Repository stehen. Ausgepackt wird, bevor in der Sandbox Code aus dem Stand läuft.
5. **Gate** in einer frischen Scan-Sandbox: Gitleaks auf dem Stand, über die Commits der Karte und über deren Köpfe, OSV-Scanner auf den Lockfiles (Datenbank offline geladen), Syft erzeugt die SBOM (CycloneDX). Berichte und SBOM landen neben dem Protokoll. Exit-Code und Bericht müssen zusammenpassen: Meldet ein Scanner Funde und der Bericht fehlt, ist nicht lesbar oder enthält keine, gilt das Gate als unvollständig und die Karte hält an. Dasselbe gilt, wenn der OSV-Scanner keine Pakete findet, obwohl im Stand ein Lockfile liegt.
   - **Commits der Karte:** Gitleaks läuft ein zweites Mal über die Commits zwischen Basis und Stand (`gitleaks git` mit `--log-opts`, Bericht `gitleaks-history.json`). Ein Fund dort zählt wie ein Fund im Stand, auch wenn der Stand das Secret nicht mehr enthält: Mit dem Merge käme der Commit sonst nach `main`. Die Scan-Sandbox braucht dafür kein eigenes Repository mit Historie. Das Hilfs-Repository, das beim Auspacken entsteht, liest die Objekte des Projekts mit, und dort liegen nach dem Abholen alle Commits der Karte. Die Optionen für `git log` zeigen auch, was es von sich aus auslässt: `--text` Dateien, die git für binär hält oder die ein Attribut vom Diff ausnimmt, `--diff-merges=first-parent` das, was ein Merge-Commit gegenüber seinem ersten Elternteil ändert; `--no-textconv` und `--no-ext-diff` lassen keinen Filter dazwischen. Der Lauf gilt nur als gelaufen (`gitleaks-history_ran`), wenn Exit-Code und Bericht zusammenpassen und die Sandbox zwischen Basis und Stand so viele Commits sieht wie das Projekt.
   - **Köpfe der Commits:** `git log -p` zeigt Gitleaks nur Dateiinhalte. Ein dritter Lauf (`gitleaks dir`, Bericht `gitleaks-messages.json`) liest deshalb je Commit das rohe Commit-Objekt: Autor, Committer, weitere Kopfzeilen und die Nachricht. Ein Fund nennt den Commit und zählt wie ein Fund in den Commits. Der Lauf gilt nur als gelaufen (`gitleaks-messages_ran`), wenn Exit-Code und Bericht zusammenpassen und für jeden Commit der Karte ein Kopf geschrieben ist.
   - **Regeln und Ausnahmen für Gitleaks:** Für alle drei Läufe gelten `.gitleaks.toml` und `.gitleaksignore` so, wie sie in der Basis stehen. Was die Karte daran ändert, löscht oder neu mitbringt, wirkt erst nach dem Merge, also nach der Freigabe. Eine Zeile mit `gitleaks:allow` nimmt nichts aus (`--ignore-gitleaks-allow`): Eine begründete Ausnahme steht in `.gitleaksignore` oder `.gitleaks.toml` auf `main`, nicht im geprüften Code. Lässt sich eine der beiden Dateien der Basis nicht lesen, läuft Gitleaks nicht, und die Karte hält mit `gate_incomplete` an.
6. **Rücklauf**, wenn ein Befund über der Schwelle der Schutzstufe liegt: erster Rücklauf mit `--resume` an denselben Umsetzer, zweiter mit frischem Kontext und Opus 5.5, danach wartet die Karte auf den Menschen. Sind die Tests abgenommen, hängt die Nachbesserung einen Commit an; sind sie es nicht, baut sie den Branch neu auf. Bei einem Secret in einem Commit der Karte, auch in dessen Nachricht oder Kopf, genügt ein weiterer Commit nicht: Der Test-Commit bleibt, die Nachbesserung ersetzt die Commits danach. Steht das Secret im abgenommenen Test-Commit selbst, fällt die Abnahme der Tests weg (`test_commit_withdrawn` am Rücklauf), und der Branch entsteht neu. Prüfung und Gate laufen nach jeder Nachbesserung vollständig neu.
7. **Freigabe:** Diff-Bericht schreiben, Karte in die Spalte Freigabe stellen, anhalten. Der Diff-Bericht nennt volle Commit-Ids und `refs/heads/main`, keine Kurzformen.

## Was `merge` tut

1. **Ready prüfen, alles aus dem Protokoll:** Die Karte steht mit genau diesem Commit in der Freigabe, der letzte Gate-Lauf galt diesem Commit und blieb ohne Befund, die Entscheidung des Menschen ist danach gefallen und lautet `approved`, `main` ist ausgecheckt und sauber und trägt den Commit noch nicht. Fehlt etwas, wird nicht gemergt.
2. **Mergen** mit `git merge --no-ff`. Bei einem Konflikt bricht das Skript den Merge ab.
3. **Tests auf dem Mac.** Sind sie grün, wandert die Karte nach `merged`. Sind sie rot, laufen sie ins Zeitlimit oder werden sie abgebrochen (Strg-C, Fehler), setzt das Skript `main` auf den Stand vor dem Merge zurück (`git reset --hard`) und stellt die Karte neu in die Freigabe. Die bisherige Entscheidung gilt dann nicht weiter; für einen neuen Versuch braucht es eine neue. Wird der Prozess in den Tests abgeschossen, bleibt der Merge auf `main` stehen; der nächste `merge` erkennt das (`commit_not_yet_on_main`) und hält an. Der Stand davor steht im Protokoll an der Bedingung `merged` als `main_before`; über das Zurücksetzen entscheidet der Mensch.

## Protokollformat

JSON Lines, eine Zeile je Ereignis, Felder in fester Reihenfolge. Felder ohne Wert fehlen.

| Feld | Bedeutung |
| --- | --- |
| `ts` | Zeitpunkt in UTC |
| `project`, `host`, `job`, `card` | Projekt, Rechner, Auftrag, Karte |
| `event` | Ereignisart, siehe unten |
| `column` | Spalte: `briefing`, `analysis`, `decomposition`, `implementation`, `review`, `gate`, `approval`, `merged`, `parked` |
| `actor` | `human`, `coordinator`, `script`, `implementer`, `reviewer`, `scanner` |
| `model`, `effort` | Modell und Effort eines Agentenlaufs |
| `sandbox` | Name der Sandbox |
| `commit` | Commit, auf den sich das Ereignis bezieht |
| `source` | Quelle eines Befunds oder Rücklaufs: `checks`, `review`, `gate`, `approval` |
| `reason` | Begründung in einem Satz |
| `evidence` | Einzelheiten als Objekt, je Ereignisart verschieden |
| `tokens` | Verbrauch eines Agentenlaufs: `input`, `output`, `cache_read`, `cache_creation` |

| Ereignis | Wann | Wichtig in `evidence` |
| --- | --- | --- |
| `card_created` | Karte in der Zerlegung angelegt | `title`, `route`, `branch`, `allowed_paths`, `acceptance`, `tier` |
| `decision` | Entscheidung des Menschen | `verdict` (`approved`, `rejected`); in der Freigabe zusätzlich `commit`, an einer angehaltenen Karte `halt` |
| `condition_checked` | eine Ready- oder Done-Bedingung geprüft | `phase` (`ready`, `done`), `condition`, `ok`, Einzelheiten |
| `card_moved` | Karte wechselt die Spalte | je nach Spalte, etwa `template`, `waiting_for` |
| `card_halted` | Karte hält mitten im Ablauf an; die Spalte bleibt | `cause`, `step`, `waiting_for` (`coordinator`, `human`); Einzelheiten in `reason` |
| `card_resumed` | `resume` setzt die Karte fort | `cause`, `step` |
| `run_started`, `run_ended` | Lauf eines Agenten oder der Scanner | `round`, `exit_code`, `num_turns`, `session_id`, `claim` (Bericht des Agenten, eine Behauptung) |
| `finding` | Befund eines Scanners | `tool`, `rule`, `file`, `line`, `severity`, `above_threshold`, `demand`; bei einem Fund in den Commits der Karte (`tool`: `gitleaks-history`) oder in ihren Köpfen (`tool`: `gitleaks-messages`) dazu `found_in`, der Commit mit dem Secret |
| `card_returned` | Rücklauf in die Umsetzung | `returns` je Quelle, `total`, Kurzform der Befunde; `test_commit_withdrawn`, wenn die Abnahme der Tests wegfällt |
| `fault_injected` | Trockenlauf: Fehler vom Skript eingebaut | `kind`, `file`, `note` |
| `limit_reached` | Agentenlauf am Limit gescheitert (Heuristik) | `exit_code`, `excerpt` |
| `network_observed` | nach jedem Lauf in einer Sandbox (Agentenlauf, Prüfung von außen, Gate) | `ok`, `allowed` und `blocked` (je Ziel `host` mit Port, `count`, `first_seen`, `last_seen`, bei gesperrten Zielen `rule` und `reason`, soweit `sbx` sie liefert), dazu die Summen `allowed_hosts`, `blocked_hosts`, `allowed_requests`, `blocked_requests` |
| `subagent` | vorgesehen, noch nicht geschrieben | – |

Der Wert eines gefundenen Secrets steht weder im Protokoll noch im Gitleaks-Bericht. `--redact` schwärzt im Bericht nur Fundstelle und Wert; zu einem Fund in den Commits schreibt Gitleaks auch Nachricht und Autor des Commits hinein, ungeschwärzt. Der Koordinator holt die Berichte deshalb zuerst in einen Ordner außerhalb des Projekts und übernimmt aus den Gitleaks-Berichten nur Regel, Datei, Zeile, Commit, Entropie und Fingerprint. Ein Gitleaks-Bericht, der sich nicht lesen lässt, kommt nicht in den Projektordner und gilt als fehlend.

## Grenzen

- Gitleaks prüft den Stand, die Commits der Karte zwischen Basis und Stand und deren Köpfe. Der Lauf über die Commits liest die hinzugefügten Zeilen, bei einem Merge-Commit die gegenüber dem ersten Elternteil. Die Namen von Dateien und Ordnern prüft keiner der Läufe als Inhalt.
- Ausnahmen für Gitleaks kommen nur aus der Basis. Ein Projekt, das sie bisher mit `gitleaks:allow` im Code setzt, bekommt dafür Befunde, bis sie in `.gitleaksignore` oder `.gitleaks.toml` auf `main` stehen. Eine Karte, die diese beiden Dateien ändert, wird noch mit den Regeln der Basis geprüft; ob die Dateien zu den heiklen Pfaden gehören, legt das Projekt in `sensitive_paths` fest.
- Ein gemeldetes Secret bleibt auf dem Mac in den abgeholten Refs der Sandbox, auch wenn der Branch danach neu aufgebaut wurde. Nach `main` kommt es nicht. Ein echtes Secret gilt als offengelegt.
- Die drei Gitleaks-Läufe sind in einer Scan-Sandbox an einem Wegwerf-Repository ausgeführt (Gitleaks 8.30.1, git 2.47.3), nicht in einem ganzen Lauf des Kreislaufs. Mit einer neuen Fassung von Gitleaks oder git in der Scan-Vorlage ist die Probe zu wiederholen (`werkzeuge/gitleaks-probe/probe.py`): Kennt Gitleaks oder git einen Aufruf nicht mehr, hält die Karte mit `gate_incomplete` an; liest Gitleaks einen Teil der Ausgabe von `git log` nicht mehr, fiele das im Lauf nicht auf.
- Ein Token im Namen einer Datei stünde im Feld `File` des Berichts und damit im Projektordner und im Protokoll.
- Der OSV-Scanner arbeitet mit der offline geladenen Datenbank, weil die Netz-Policy `balanced` `api.osv.dev` sperrt.
- `github.com` und `api.github.com` sind aus jeder Sandbox erreichbar. Die dort gesetzte Variable `GH_TOKEN` ist ein Platzhalter und gibt keinen Zugriff (geprüft in einer Scan-Sandbox). Liegt im Schlüsselspeicher von `sbx` ein GitHub-Token, setzt der Proxy es laut Hilfe von `sbx` an die Anfragen aus jeder Sandbox; Code dort könnte dann auf GitHub handeln. Deshalb dort keines ablegen und `sbx secret import` nicht ungeprüft ausführen.
- `network_observed` ist eine Beobachtung und keine Bedingung: Das Skript hält daran nicht an, auch nicht bei gesperrten Zielen. Die Quelle ist `sbx policy log <sandbox> --json`. Die Zähler sind Summen seit dem Anlegen der Sandbox; läuft in einer Sandbox mehr als ein Lauf (Nachbesserung), gilt je Sandbox der letzte Eintrag. Zu sehen sind nur Ziel und Anzahl, nicht Pfad oder Inhalt der Anfragen. Scheitert der Aufruf oder ist die Ausgabe unlesbar, steht `ok: false` im Ereignis. Für eine Sandbox, die `sbx` nicht kennt, liefert `sbx` leere Listen; das ist von „kein Verkehr" nicht zu unterscheiden.
- Ein erreichtes Abo-Limit erkennt das Skript nur an einem Fehlerlauf, dessen Text „limit" enthält. Der Fall ist nicht erprobt. Das eigene Zeitlimit zählt nicht dazu.
- Subagenten, Analyse, Review und die Route `voll` sind nicht gebaut. Die WIP-Grenze zählt nur Karten desselben Auftrags.
- Kommen zwei Karten in dieselbe Datei, kann der zweite Merge einen Konflikt haben; das Skript bricht den Merge dann ab.
- Eine Karte in der Ablage lässt sich nicht erneut entscheiden; ein Weg zurück aus der Ablage ist nicht gebaut.
- Der Zustand kommt aus dem Protokoll, die Arbeitsdaten (Commit, Zähler, anstehender Agentenlauf) weiter aus `state.json`. Fehlt die Datei oder ist sie beschädigt, lässt sich die Karte nicht fortsetzen.
- `resume` prüft nicht, ob in der Sandbox noch ein Agent läuft. Nach Strg-C lief der Agent im echten Lauf noch etwa drei Sekunden weiter und hat committet; nach einem abgeschossenen Skript war drei Sekunden später keiner mehr zu sehen. Nach einem Abbruch deshalb einige Sekunden warten, bevor `resume` läuft.
- Scheitert `run`, nachdem die Arbeits-Sandbox angelegt war und bevor die Karte in `implementation` stand, scheitert der nächste Versuch am vergebenen Namen. Über das Löschen der Sandbox entscheidet der Mensch.
- An einem echten Lauf erprobt sind `run`, ein Halt in der Prüfung von außen, `resume`, Gate, Freigabe und Merge (2026-11-21). Ebenso Rückläufe aus dem Gate mit Nachbesserung in derselben Sitzung und danach mit stärkerem Modell, die Schleifengrenze mit einer vom Menschen gewährten Runde und die Ablehnung in die Ablage. Ebenso ein Abbruch mitten im Agentenlauf, mit Strg-C und durch einen abgeschossenen Prozess. Ein Rücklauf aus der Prüfung von außen ist seit dem Zustandsautomaten nur per Test belegt.
- Der Code am geprüften Stand bestimmt den Exit-Code seines eigenen Testlaufs. Gegen eine Umsetzung, die den Testlauf absichtlich fälscht (etwa den Prozess mit Erfolg beendet), schützt die Prüfung nicht; das bleibt Sache des Diff-Reviews in der Freigabe.
- Exit-Code 2 des Testbefehls gilt am Test-Commit als rot (pytest: Abbruch beim Sammeln, etwa fehlendes Modul). `uv run` meldet eigene Fehler mit demselben Code.
- Die Scanner sehen die Bytes aus dem Repository. Ein Checkout auf dem Mac wendet die Attribute des Stands an (Zeilenenden, Encoding); die Dateien im Arbeitsverzeichnis können sich davon unterscheiden.
- Ersetzt die Nachbesserung Commits (vor der Abnahme der Tests oder nach einem Secret in einem Commit), überschreibt `git fetch --force` den Ref der Sandbox. Per Test belegt, mit einem echten git-Repository als Sandbox, dessen Remote das Überschreiben nicht von sich aus erlaubt; nicht an einem Lauf mit `sbx` erprobt. Scheitert das Abholen, hält die Karte an (`fetch_failed`).
- Lässt sich die Umgebung in der Prüf-Sandbox nicht aufbauen, hält die Karte an, auch wenn die Änderung des Agenten der Grund ist. Der Koordinator muss dann nachsehen; einen Rücklauf an den Agenten gibt es dafür nicht.
- Karte, `project.json` und `AGENTS.md` sind nicht festgeschrieben: `resume` liest die Karte neu. Dass der Branch von der Basis abstammt und die Nachbesserung nur anhängt, prüft das Skript nicht.
- Ob ein Lockfile im Stand liegt, entscheidet eine feste Namensliste (`LOCKFILES` in `kreislauf/checks.py`). Bei einem Lockfile, das dort fehlt, geht „keine Pakete gefunden" weiter durch.
- Die Route prüft nur eingecheckte Dateien gegen die heiklen Pfade. Neue Dateien unter einem heiklen Pfad weist erst die Done-Bedingung `only_allowed_paths` ab.
