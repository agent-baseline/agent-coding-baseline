# agent-coding-baseline

Ein einheitlicher Sicherheits- und Qualitätsstandard für Projekte, die mit Coding-Agenten entwickelt werden. Das Projekt enthält das Konzept und einen ausführbaren Ablauf dazu: Ein Agent setzt ein Arbeitspaket in einer Sandbox um, ein Skript prüft das Ergebnis von außen, Scanner laufen in einer eigenen Sandbox, und gemergt wird erst nach der Freigabe durch einen Menschen.

Dieses README ist der Einstieg für Mitwirkende. `CLAUDE.md` richtet sich an Coding-Agenten.

## Was wo steht

| Datei oder Ordner | Inhalt |
| --- | --- |
| `Konzept.md` | Bedrohungsmodell, Schutzstufen, Schichten 0 bis 3 |
| `Workflow.md` | Entwurf des Ablaufs; die Einleitung sagt, was davon gebaut ist |
| `Kreislauf.md` | Bedienung des Skripts, Zustände und Übergänge, Protokollformat; der Abschnitt „Grenzen" sagt, was an echten Läufen erprobt ist |
| `Sandbox-Arbeitsweise.md` | Arbeiten mit `sbx`: anlegen, Auftrag, abholen, prüfen, löschen |
| `kreislauf/` | das Skript: `cycle.py` (Ablauf), `machine.py` (Zustandsautomat), `checks.py` (Prüfungen), `events.py` (Protokoll) |
| `tests/` | Tests des Skripts; sie spielen die Sandboxes und brauchen kein `sbx` |
| `werkzeuge/` | Bauskripte der beiden Sandbox-Vorlagen und ihre Konfiguration, Probe für Gitleaks in der Scan-Vorlage |
| `beispiele/` | Ereignisprotokolle und Gate-Berichte aus zwei echten Läufen |

## Voraussetzungen

- macOS oder Linux, `git`, Python ab 3.12 und [uv](https://docs.astral.sh/uv/).
- Für die Tests reicht das. Für einen echten Lauf zusätzlich:
  - `sbx` (Docker Sandboxes) ab 0.54, angemeldet mit `sbx login`,
  - die Netz-Policy `balanced` von `sbx`,
  - in `sbx` das Öffnen von Adressen im Browser des Rechners abgeschaltet: `sbx settings set browser.enabled false` (die Voreinstellung erlaubt es Prozessen in jeder Sandbox),
  - eine eigene Anmeldung bei Claude Code für die Arbeits-Sandbox (einmal `/login` in einer beliebigen Sandbox) oder ein API-Schlüssel im Schlüsselspeicher von `sbx` (`sbx secret set anthropic`),
  - ein eigenes Projekt als Git-Repository mit `AGENTS.md` und `.baseline/project.json` (siehe `Kreislauf.md`). Das Beispielprojekt, an dem die Läufe unter `beispiele/` entstanden sind, ist nicht veröffentlicht.

## Einrichten und Tests

```bash
uv sync --locked
```

```bash
uv run --locked pytest -q
```

Die Umgebung kommt aus `uv.lock`. `uv.toml` setzt den Cooldown: Es wird keine Paketversion genommen, die jünger als sieben Tage ist. Diese Einstellung und das Lockfile bitte nicht übersteuern.

Das Skript selbst braucht nur die Standardbibliothek und läuft auch ohne die Umgebung: `python3 -m kreislauf …`.

## Sandbox-Vorlagen bauen

Beide Vorlagen entstehen lokal aus dem Standard-Image von `sbx`; sie werden nicht verteilt. Die Tags müssen zu dem passen, was das Skript erwartet:

```bash
werkzeuge/sandbox-template-build.sh 2026-11-18-3
```

```bash
werkzeuge/scan-template-build.sh 2026-11-20
```

- **Arbeits-Vorlage** `baseline-claude:<tag>`: Claude Code, Node und npm, Cooldown-Konfiguration für npm, uv und pip aus `werkzeuge/sandbox-home/`. Sie enthält keine Anmeldung.
- **Scan-Vorlage** `baseline-scan:<tag>`: Gitleaks, OSV-Scanner und Syft, kein Modell. Aus ihr entstehen auch die Prüf-Sandboxes für die Tests.

Wer mit anderen Tags baut, nennt sie dem Skript über `KREISLAUF_WORK_TEMPLATE` und `KREISLAUF_SCAN_TEMPLATE`. Einzelheiten stehen in `Kreislauf.md`, Abschnitt „Vorlagen bauen".

## Eine Karte durch den Kreislauf führen

```bash
python3 -m kreislauf run --project <pfad> --job <auftrag> --card <karte>
```

Davor steht die Freigabe der Karte (`release`), danach die Entscheidung in der Freigabe (`decide`) und der Merge (`merge`). Bricht ein Lauf ab, setzt `resume` ihn am selben Schritt fort. Alle Befehle, die Zustände einer Karte und das Format des Protokolls stehen in `Kreislauf.md`.

Was davon an einem echten Lauf erprobt ist und was nur per Test belegt, steht im Abschnitt „Grenzen" von `Kreislauf.md`.

## Mitarbeiten

- **Branch und Pull Request:** nicht auf `main` committen. Ein Pull Request braucht die Freigabe des Code-Owners (`.github/CODEOWNERS`).
- **Fremdbeiträge prüfen:** Commits von anderen und aus einer Sandbox erst lesen, dann mergen, auschecken oder ausführen. Besonders auf `CLAUDE.md`, `.claude/`, `kreislauf/`, `werkzeuge/`, `tests/`, CI und Abhängigkeiten achten. Tests und Skripte aus ungeprüften Ständen laufen in einer Sandbox, nicht auf dem eigenen Rechner.
- **Tests zuerst:** erst der Test, der scheitert, dann die Umsetzung. Eine Korrektur bekommt einen Test, der am alten Stand scheitert.
- **Geprüft heißt ausgeführt:** In den Dokumenten gilt etwas nur als geprüft, wenn es tatsächlich gelaufen ist. Alles andere wird als ungeprüft oder als nur gegen die Doku geprüft gekennzeichnet.
- **Keine Secrets im Projektordner,** auch nicht in nicht eingecheckten Dateien: Eine Sandbox kann den ganzen Ordner lesen.
- **Kein GitHub-Token im Schlüsselspeicher von `sbx`:** Was dort liegt, setzt der Proxy laut Hilfe von `sbx` an die Anfragen aus jeder Sandbox. `sbx secret import` übernimmt Geheimnisse aus der Umgebung des Rechners, also nicht ungeprüft ausführen. Gepusht wird nur vom eigenen Rechner aus, nie aus einer Sandbox.
- **Checks nicht umgehen:** kein `--no-verify`, Cooldown und Registry-Einstellungen bleiben, wie sie sind.
- **Sprache:** Dokumente und Commit-Nachrichten auf Deutsch; Feldnamen und Ereignisarten im Protokoll auf Englisch.

## Stand und Herkunft

Das Repository ist aus einem privaten Arbeits-Repository abgeleitet und beginnt mit einer neuen Historie. Die Dokumente sind Arbeitsstände mit Datum; was gebaut ist und was nur Entwurf, sagen sie jeweils selbst. Namen von Rechnern und anderen Projekten sind durch allgemeine Bezeichnungen ersetzt.

## Lizenz

MIT, siehe `LICENSE`. Die Lizenz gilt für Code und Dokumente. Die Rechte liegen bei den Autoren des Projekts; sie stehen in `AUTHORS`. Wer beiträgt, stellt seinen Beitrag unter dieselbe Lizenz und kann sich dort eintragen.
