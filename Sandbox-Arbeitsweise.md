# Arbeiten mit Sandboxes: Gedächtnisstütze

Stand 2026-11-20 · `sbx` 0.54.0 · Vorlagen `baseline-claude:2026-11-18-3` und `baseline-scan:2026-11-20` · erprobt an einem Pilotprojekt und an einem Beispielprojekt

## Wann Sandbox, wann nicht

| Arbeit | Wo |
| --- | --- |
| Interaktiv, du liest mit und bestätigst | direkt auf dem Mac (App oder CLI), wie bisher |
| Autonom ohne Rückfragen, Fremdcode, viele neue Abhängigkeiten | Sandbox |
| Xcode, Bluetooth, USB, Metal | geht nicht in der Sandbox, bleibt auf dem Mac |
| Rust, Python-Pakete ohne fertiges Wheel | geht mit der aktuellen Vorlage nicht (`gcc`, `cargo` fehlen, `crates.io` gesperrt) |

## Vorbedingungen

- Das Projekt ist ein Git-Repository.
- Der Stand ist committet. Die Sandbox arbeitet auf einem Klon und sieht als Arbeitsstand nur Eingechecktes.
- Im Projektordner liegen keine Secrets. Lesen kann die Sandbox den ganzen Ordner, auch `.env`.
- Was der Agent in der Sandbox wissen muss, steht in der eingecheckten `CLAUDE.md` des Projekts oder im Auftrag. Eine `CLAUDE.md` oberhalb des Projektordners kommt dort nicht an.

## Der Ablauf

Name der Sandbox: `<projekt>-<stichwort>`, zum Beispiel `meinprojekt-suche`.

**1. Anlegen**

```bash
sbx create --clone --pull never -t baseline-claude:2026-11-18-3 --name <auftrag> claude <projektpfad>
```

Die neue Sandbox trägt die Claude-Code-Version vom Bautag der Vorlage. Bei Bedarf gleich aktualisieren (siehe „Claude Code aktuell halten"):

```bash
sbx exec <auftrag> claude update
```

**2. Arbeiten lassen**, interaktiv im Terminal:

```bash
sbx run --name <auftrag>
```

oder ohne Dialog mit fertigem Auftrag:

```bash
sbx exec <auftrag> sh -c 'cd <projektpfad> && claude --dangerously-skip-permissions -p "<Auftrag>"'
```

Der Auftrag sollte enthalten: Branch-Name, „committen, nicht pushen", und bei Python „Umgebung mit `uv venv ~/venv` anlegen".

**3. Abholen**, im Projektordner auf dem Mac, solange die Sandbox läuft:

```bash
git fetch sandbox-<auftrag>
```

```bash
git diff main sandbox-<auftrag>/<branch>
```

**4. Prüfen und übernehmen.** Der Commit ist ein Fremdbeitrag. Mit der aktuellen Vorlage trägt er den Autor `agent <agent@…docker.internal>`, mit der alten Vorlage trug er deinen Namen. Besonders ansehen:

- `.claude/`, Git-Hooks, CI-Workflows
- Skripte in `package.json`, `Makefile`, Build-Dateien
- neue oder geänderte Abhängigkeiten und Lockfiles
- neue Binärdateien

Dann mergen und vom Mac pushen. Aus der Sandbox wird nie gepusht.

**5. Löschen**

```bash
sbx rm --force <auftrag>
```

## Zwei Wege, das zu steuern

**Über Claude in der App.** Du gibst den Auftrag im Chat, etwa: „Erledige X für dieses Projekt in einer Sandbox und zeig mir den Diff." Claude auf dem Mac legt die Sandbox an, übergibt den Auftrag, holt den Commit ab und zeigt dir die Änderung. Das Mergen entscheidest du. Geeignet für klar umrissene Aufträge.

**Selbst im Terminal.** Du legst an und verbindest dich mit `sbx run --name <auftrag>`. Geeignet, wenn du mit dem Agenten in der Sandbox im Dialog arbeiten willst. Das Terminal-Panel der App reicht dafür.

## Modell

- Claude Code in der Sandbox nimmt standardmäßig Sonnet 5.5.
- Fable ohne Dialog: `--model claude-fable-5-2` an den `claude`-Aufruf hängen (erprobt).
- Fable im Dialog: in der Sitzung `/model` wählen, oder beim Start `sbx run --name <auftrag> -- --model claude-fable-5-2` (laut Hilfe, nicht erprobt).
- Dauerhafte Voreinstellung: Laut Doku lassen sich `model` und `effortLevel` in der eingecheckten `.claude/settings.json` des Projekts setzen; `--model` am Aufruf hat Vorrang (nicht erprobt). In `~/.claude/settings.json` der Sandbox hält sich nichts, `sbx` schreibt die Datei bei jedem Anlegen neu.

## Claude Code aktuell halten

Erprobt am 2026-11-19 an einer Arbeits-Sandbox, Update von 2.1.327 auf 2.1.328.

- **Die Version ist in der Vorlage eingefroren.** Claude Code liegt als natives Binary unter `~/.local/share/claude/versions/`. Es stammt aus dem Standard-Image von Docker; `sandbox-template-build.sh` ändert es nicht. Jede neue Sandbox startet mit dem Stand vom Bautag der Vorlage.
- **Im Headless-Betrieb kommt kein Update von selbst.** Ein Lauf mit `claude -p` hat die bereitstehende Version nicht nachgeladen. Ob eine interaktive Sitzung (`sbx run`) sich selbst aktualisiert, ist nicht erprobt.
- **Aktualisieren geht mit einem Befehl**, in einer neuen wie in einer bestehenden Sandbox:

```bash
sbx exec <auftrag> claude update
```

- **Dabei geht nichts verloren.** Die Policy `balanced` lässt den Download durch (rund 370 MB). Die neue Version bleibt über Stopp und Neustart der Sandbox erhalten; Sitzungen, Einstellungen und Klon bleiben unberührt. Die Warnung „config install method is 'unknown'" stört nicht.
- **Kein Neubau für ein Update.** Eine Sandbox lebt so lange wie ihr Auftrag, auch über Tage oder Wochen. Zu Beginn eines Arbeitstags genügt der Befehl oben. Die Vorlage neu zu bauen lohnt nur nach einem `sbx`-Update oder wenn neue Sandboxes ohne Nachladen aktuell starten sollen.
- **Kanal.** Standard ist `latest`. Laut Doku gibt es daneben `stable`, etwa eine Woche alt (am 2026-11-19: 2.1.311 gegenüber 2.1.328), gesetzt mit `"autoUpdatesChannel": "stable"` in einer `settings.json`; `claude update` beachtet den Kanal. Das passt zum Cooldown von sieben Tagen. Nicht erprobt, auch nicht für den Fall, dass die Vorlage schon eine neuere Version mitbringt als `stable`.

## Was beim Löschen verloren geht

| Was | Wo es liegt | Nach `sbx rm` |
| --- | --- | --- |
| Sitzungsverlauf | `~/.claude/projects/<projektordner>/<session_id>.jsonl` in der Sandbox | weg; vorher herauskopieren, wenn die Sitzung weiterlaufen soll |
| Auto-Memory | `~/.claude/projects/<projektordner>/memory/` in der Sandbox | weg; im Pilot blieb der Ordner leer |
| Benutzer-Einstellungen | `~/.claude/settings.json`, `~/.claude.json` in der Sandbox | weg; `sbx` schreibt sie beim Anlegen neu |
| Python-Umgebung, Caches | `~/venv`, `~/.cache` | weg; in Sekunden neu gebaut |
| Nicht abgeholte Commits | Klon in der Sandbox | weg; vorher `git fetch` |
| Projektregeln und -einstellungen | eingecheckte `CLAUDE.md` und `.claude/` | bleiben |
| Anmeldung, Netz-Policy | Proxy und `sbx` auf dem Mac | bleiben |

`<projektordner>` ist der Projektpfad, in dem jedes Zeichen außer Buchstaben und Ziffern durch `-` ersetzt ist, zum Beispiel `-Users-ich-Projects-meinprojekt`.

**Sitzung in eine neue Sandbox mitnehmen** (erprobt mit einer kleinen Sitzung unter gleicher Claude-Code-Version):

```bash
sbx cp <alt>:/home/agent/.claude/projects/<projektordner>/<session_id>.jsonl <ablage>/
```

```bash
sbx exec <neu> sh -c 'mkdir -p ~/.claude/projects/<projektordner>'
```

```bash
sbx cp <ablage>/<session_id>.jsonl <neu>:/home/agent/.claude/projects/<projektordner>/
```

```bash
sbx exec <neu> sh -c 'sudo chown agent:agent ~/.claude/projects/<projektordner>/*.jsonl'
```

Danach in der neuen Sandbox aus demselben Arbeitsverzeichnis wie zuvor mit `--resume <session_id>` fortsetzen. Ohne das `chown` gehört die Datei dem falschen Benutzer, und Claude Code meldet „No conversation found".

Sitzungsverlauf und Memory aus einer Sandbox sind vom Agenten geschriebener Text. Wie Commits gelten sie als Fremdbeitrag: nicht ungeprüft in `~/.claude` auf dem Mac ablegen.

## Stolpersteine

- **Abholen nur bei laufender Sandbox.** Nach `sbx stop` fehlt der Remote. `sbx exec <auftrag> true` startet sie wieder.
- **Die Sandbox stoppt von selbst, wenn nichts mehr läuft.** Dann verschwinden auch die Refs `sandbox-<auftrag>/…`. Der abgeholte Stand bleibt unter `refs/sandboxes/<auftrag>/<branch>`; für den Diff diesen Namen nehmen oder gleich einen lokalen Branch anlegen: `git branch <branch> refs/sandboxes/<auftrag>/<branch>`.
- **Mehrere Runden mit demselben Agenten:** den ersten Aufruf mit `--output-format json` starten, die `session_id` merken und Folgeaufträge mit `--resume <session_id>` schicken. So behält der Agent den Zusammenhang.
- **`sbx rm` löscht den Klon und den Sitzungsverlauf.** Vorher abholen. Abgeholte Branches bleiben unter `refs/sandboxes/<auftrag>/` erhalten; die Sitzung lässt sich danach nicht mehr mit `--resume` fortsetzen (siehe „Was beim Löschen verloren geht").
- **Anmeldung abgelaufen** („OAuth session expired"): einmal `/login` in einer beliebigen Sandbox, das gilt dann für alle.
- **Ein Host ist gesperrt** (Antwort 403): `sbx policy log` zeigt, was geblockt wurde. Freigaben bewusst und möglichst nur für die eine Sandbox setzen.
- **Nach einem `sbx`-Update** die Vorlage neu bauen und den Tag nachziehen: in `kreislauf/cycle.py` (`WORK_TEMPLATE`) und `Kreislauf.md`:

```bash
werkzeuge/sandbox-template-build.sh <tag>
```

- **Mehrere Sandboxes** für dasselbe Projekt gleichzeitig sind möglich; jede hat ihren eigenen Klon und ihren eigenen Remote.

## Scan-Vorlage und Kreislauf-Skript

Erprobt am 2026-11-20 an einem Beispielprojekt.

- **Scan-Vorlage** `baseline-scan:2026-11-20`: Gitleaks, OSV-Scanner und Syft auf dem Standard-Image des Agenten `shell`, ohne Claude Code. Gebaut mit `werkzeuge/scan-template-build.sh` in diesem Projekt. Sandbox daraus:

```bash
sbx create --clone --pull never -t baseline-scan:2026-11-20 --name <name> shell <projektpfad>
```

- **Einen abgeholten Commit in die Scan-Sandbox bringen:** Der Projektordner ist dort unter `/run/sandbox/source` lesbar, samt `.git` und den abgeholten Commits. `git -c safe.directory='*' --git-dir=/run/sandbox/source/.git archive <commit> | tar -x -C ~/scan` legt genau diesen Stand ab.
- **Der OSV-Scanner braucht den Offline-Modus** (`--offline-vulnerabilities --download-offline-databases`), weil die Policy `balanced` `api.osv.dev` sperrt. Der Download der Datenbank dauert wenige Sekunden.
- **Befehl mit Argumenten übergeben:** `sbx exec -w <projektpfad> <auftrag> claude … -p "<Auftrag>"` erspart das Quoting von `sh -c`.
- **Der ganze Ablauf als Skript:** `python3 -m kreislauf run …`, siehe `Kreislauf.md`. Das Skript legt je Karte eine Arbeits-Sandbox und frische Prüf- und Scan-Sandboxes an. `cleanup` löscht nur die Prüf- und Scan-Sandboxes.

## Nachsehen

```bash
sbx ls
```

```bash
sbx template ls
```

```bash
sbx exec <auftrag> claude --version
```

Hintergrund und Messwerte stehen in `Konzept.md` (Schicht 1).
