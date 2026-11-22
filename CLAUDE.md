# agent-coding-baseline

## Zweck

Sicherheits- und Qualitätsstandard für Projekte, die mit Coding-Agenten entwickelt werden. Das Repository enthält das Konzept (Bedrohungsmodell, Schutzstufen, Schichten 0 bis 3) und einen ausführbaren Ablauf dazu: Ein Agent setzt ein Arbeitspaket in einer Sandbox um, ein Skript prüft das Ergebnis von außen, Scanner laufen in einer eigenen Sandbox, gemergt wird nach der Freigabe durch einen Menschen. Referenz-Umsetzung ist Claude Code mit Docker Sandboxes (`sbx`).

## Stack & Struktur

- Dokumente in Markdown (Deutsch), das Kreislauf-Skript in Python (nur Standardbibliothek), die Bauskripte der Vorlagen in Shell.
- `Konzept.md`: das Sicherheitskonzept.
- `Workflow.md`: Entwurf des Ablaufs. Die Einleitung sagt, was davon gebaut ist.
- `Kreislauf.md`: Bedienung des Skripts und Format des Ereignisprotokolls. Der Abschnitt „Grenzen" sagt, was an echten Läufen erprobt ist.
- `Sandbox-Arbeitsweise.md`: Ablauf mit `sbx` (anlegen, Auftrag, abholen, prüfen, löschen).
- `kreislauf/`: `events.py` (Protokoll), `checks.py` (Prüfungen ohne Seiteneffekte), `machine.py` (Zustandsautomat mit Übergangstabelle), `cycle.py` (Ablauf mit `sbx`). `tests/`: Tests dazu.
- `werkzeuge/`: `sandbox-template-build.sh` mit `sandbox-home/` (Arbeits-Vorlage `baseline-claude:<tag>`), `scan-template-build.sh` (Scan-Vorlage `baseline-scan:<tag>`), `gitleaks-probe/` (Probe der Gitleaks-Läufe des Gates in einer Scan-Sandbox; nach jedem Neubau der Scan-Vorlage wiederholen).
- `beispiele/`: Ereignisprotokolle und Gate-Berichte aus zwei echten Läufen.
- `SECURITY.md`: Meldeweg für Sicherheitslücken (vertraulich über GitHub).

## Befehle

- Umgebung für die Tests: `uv sync --locked`
- Tests: `uv run --locked pytest -q`; ohne die Umgebung auch `python3 -m pytest -q tests`
- Karte durch den Kreislauf führen: `python3 -m kreislauf run --project <pfad> --job <auftrag> --card <karte>`; alle Befehle stehen in `Kreislauf.md`.

## Konventionen

- Dokumente und Commit-Nachrichten auf Deutsch; Feldnamen und Ereignisarten im Protokoll auf Englisch.
- Nicht auf `main` committen, sondern auf einem Branch. Commit, Merge und Push erst nach ausdrücklicher Zustimmung des Menschen.
- Commits von anderen und aus einer Sandbox sind Fremdbeiträge: erst den Diff prüfen (besonders `CLAUDE.md`, `.claude/`, Hooks, CI, `kreislauf/`, `werkzeuge/`, `tests/`, Abhängigkeiten), dann mergen, auschecken oder ausführen. Tests und Skripte aus ungeprüften Ständen laufen in einer Sandbox.
- Keine Secrets im Projektordner. Eine Sandbox kann den ganzen Ordner lesen, auch nicht eingecheckte Dateien.
- Kein GitHub-Token in den Schlüsselspeicher von `sbx` legen. Gepusht wird nur vom eigenen Rechner, nie aus einer Sandbox.
- Cooldown- und Registry-Einstellungen nicht übersteuern, Checks nicht umgehen (`--no-verify`).
- Aussagen in den Dokumenten nur als geprüft kennzeichnen, wenn sie tatsächlich ausgeführt wurden; sonst als ungeprüft oder als nur gegen die Doku geprüft kennzeichnen.

## Für Agenten in der Sandbox

- Auf einem eigenen Branch arbeiten und committen, nicht pushen.
- Python-Umgebungen außerhalb des Repositorys anlegen: `uv venv ~/venv`.
- Arbeitsverzeichnis am Ende sauber hinterlassen.
