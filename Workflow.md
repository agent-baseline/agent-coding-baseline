# Workflow für agentische Entwicklung: Entwurf

Entwurf vom 2026-11-19, Stand 2026-11-20. **Gebaut und einmal ausgeführt ist nur der Durchstich ohne Oberfläche:** Umsetzung, Prüfung von außen, Gate, Rücklauf und Freigabe für die Route `kurz` (Skript `kreislauf/`, Bedienung in `Kreislauf.md`). Analyse, Review, Board und alles Übrige sind Entwurf. Was an echten Läufen erprobt ist, steht in `Kreislauf.md` unter „Grenzen".

Das Sicherheitskonzept steht in `Konzept.md`. Dieses Dokument beschreibt, wie daraus ein Ablauf wird, der Qualität und Sicherheit bei jedem Arbeitspaket prüft, und wie ein Kanban-Board ihn sichtbar macht.

## Grundsätze

1. **Getrennte Kontexte.** Jede Spalte hat einen eigenen Akteur mit eigenem Prompt. Zwischen den Spalten wandern nur festgelegte Artefakte (Dateien, Commits, strukturierte Befunde), keine Gesprächsverläufe.
2. **Geprüft wird von außen.** Die Meldung eines Agenten, er sei fertig, ist eine Behauptung. Eine Karte bewegt sich erst, wenn ein Skript auf dem Mac, ein anderer Akteur oder der Mensch die Bedingungen bestätigt hat. Ein Check darf von der Änderung, die er prüft, nicht änderbar sein.
3. **Der Mensch entscheidet an drei Stellen:** Briefing, Zerlegung, Freigabe. Merge und Push bleiben auf dem Mac.
4. **Jede Spalte muss sich rechtfertigen.** Jeder Baustein kodiert eine Annahme darüber, was ein Modell allein nicht zuverlässig schafft. Die Messwerte des Boards zeigen, ob die Annahme noch trägt; mit jeder Modellgeneration wird das geprüft.

## Rollen

| Rolle | Wo | Aufgabe |
| --- | --- | --- |
| Mensch | überall | Briefing, Freigabe der Zerlegung, Freigabe zum Merge, Ausnahmen |
| Koordinator | Claude Code auf dem Mac | legt Sandboxes an, übergibt Aufträge, holt ab, prüft Bedingungen, schreibt das Protokoll |
| Umsetzer | Arbeits-Sandbox | analysiert und setzt um, committet im Klon |
| Prüfer | frische Arbeits-Sandbox | liest den abgeholten Stand ohne den Verlauf des Umsetzers |
| Scanner | Scan-Sandbox | deterministische Prüfungen, kein Modell, keine Anmeldung |

## Spalten

| Spalte | Akteur | Ready, wenn | Done, wenn | Prüft |
| --- | --- | --- | --- | --- |
| Briefing | Mensch, Koordinator | Repository committet, Schutzstufe gesetzt, Sicherheitsmodell in `AGENTS.md` | Ziel, Grenzen und Abnahmekriterien schriftlich | Mensch |
| Analyse | Umsetzer, nur lesend | Sandbox steht, Ausgangswerte gemessen (Tests, Abdeckung) | Vorschläge am Code belegt, mit Angabe, was geprüft wurde und was nicht; Repository unverändert | Skript, Koordinator liest die Stellen nach |
| Zerlegung | Koordinator, Mensch | Analyse abgeschlossen oder Route ohne Analyse | je Paket: Abnahmekriterium, erlaubte Pfade, Modell und Effort, Route | Mensch |
| Umsetzung | Umsetzer | Paket freigegeben, WIP frei, Budget frei | Test-Commit rot, Umsetzungs-Commit grün, ganze Suite grün, nur erlaubte Pfade geändert | Skript, Tests in frischer Umgebung |
| Review | Prüfer | Commits abgeholt; Repository am Commit, Abnahmekriterien und Sicherheitsmodell liegen bei | kein offener Befund über der Schwelle der Schutzstufe | Koordinator in Stichproben |
| Gate | Scanner | Review abgeschlossen oder Route ohne Review | alle Scanner gelaufen, nichts über der Schwelle | Skript, Exit-Codes |
| Freigabe | Mensch | Gate grün, Diff-Bericht liegt vor | Entscheidung mit Zeit und Begründung im Protokoll | – |
| Gemergt | Koordinator | Entscheidung liegt vor | gemergt, Tests auf dem Mac grün | Skript |

Dazu kommt eine **Ablage** für Zurückgestelltes und für strittige Befunde.

### Definition of Ready und Definition of Done

- **Ready ist der Eingangsvertrag des Prompts.** Die Bedingungen legen fest, was der Akteur der Spalte mitbekommt. Fehlt etwas, startet das Skript die Sandbox nicht.
- **Done ist der Ausgangsvertrag.** Der Akteur der Spalte prüft ihn nie selbst.
- Eine nicht erfüllte Done-Bedingung löst den Rücklauf aus; die verletzte Bedingung steht als Grund im Protokoll.
- Die Schutzstufe ändert die Schwelle, nicht die Liste (Grundprinzip 4 im Konzept).
- Die Bedingungen stehen versioniert im Repository und gelten als heikler Pfad wie `.claude/`.
- Höchstens fünf Bedingungen je Spalte. Jede Bedingung, die sich nicht maschinell prüfen lässt, kostet Zeit des Menschen oder des Koordinators.

Schwächstes Glied: Die Done-Bedingung im Review lässt sich nicht per Skript belegen.

## Karten

- **Eine Karte ist ein Arbeitspaket** mit eigenem Abnahmekriterium und eigenen Commits, wie V1 bis V8 im Pilot. Der Auftrag ist die Klammer darüber.
- **Eine Unteraufgabe wird nur dann zur Karte,** wenn sie eigene Commits bekommt oder eine Entscheidung des Menschen braucht. Subagenten innerhalb eines Laufs erscheinen als Aktivitätsspur auf der Karte (Anzahl, Modelle, Token).
- **Zwei Commits je Paket:** erst der Test, dann die Umsetzung. So prüft das Skript von außen, dass der Test ohne Umsetzung scheitert und mit ihr besteht.
- **Farbe:** wer am Zug ist (Agent arbeitet, Scanner läuft, wartet auf den Menschen, blockiert oder Limit erreicht).
- **Tags:** Modell und Effort, Sandbox und Rechner, Schutzstufe, heikle Pfade, Quelle und Schwere eines Befunds, Schleifenzähler, Route.

### Route

Nicht jede Karte braucht jede Spalte. Die Route wird in der Zerlegung festgelegt und vom Menschen freigegeben.

| Route | Spalten | Für |
| --- | --- | --- |
| `voll` | alle | Pakete an heiklen Pfaden, unklare Pakete, alles bei `public` |
| `kurz` | ohne Analyse und ohne Review | kleine, klar umrissene Pakete ohne heikle Pfade bei `hobby` und `internal` |

Das Gate läuft immer. Das entspricht dem Konzept, das ein unabhängiges Review erst bei `public` zur Pflicht macht.

## Schleife und Zähler

Im klassischen Kanban wandert eine Karte nicht zurück. Hier ist der Rücklauf gewollt, weil die Nachbesserung einen anderen Kontext und einen anderen Prompt hat als die Prüfung.

- Zurück gehen nur strukturierte Befunde: Datei, Zeile, Risiko, verlangtes Verhalten.
- Der Zähler wird je Quelle geführt (Review, Gate, Freigabe) und auf der Karte als Summe gezeigt.
- **Erster Rücklauf:** derselbe Umsetzer, Fortsetzung der Sitzung (`--resume`).
- **Zweiter Rücklauf:** frischer Kontext, stärkeres Modell.
- **Danach:** Die Karte wartet auf den Menschen oder geht zurück in die Zerlegung, weil das Paket falsch geschnitten war.
- In der Nachbesserung sind die Tests des Pakets schreibgeschützt; das Skript prüft, dass der Test-Commit unverändert ist.
- Jede Nachbesserung ist ein neuer Commit. Review und Gate laufen danach vollständig neu.
- Nur Befunde über der Schwelle der Schutzstufe lösen einen Rücklauf aus. Der Rest wird Notiz auf der Karte; Stilfragen bleiben draußen.
- **Kein Rücklauf in zwei Fällen:** Ein strittiger Befund wird als Test mit `xfail(strict=True)` festgehalten und bekommt eine Karte in der Ablage. Stellt ein Befund das Briefing in Frage, entscheidet der Mensch.

Der Anteil der Karten ohne Rücklauf, je Modell und Paketart, ist die Messgröße für die Modellwahl.

### WIP-Grenzen

- Parallele Sandboxes: begrenzt durch das Nutzungsfenster des Abos. Startwert 2.
- Spalte Freigabe: begrenzt durch den Menschen. Startwert 3. Die Umsetzung zieht kein neues Paket, solange die Grenze erreicht ist.

Beide Startwerte sind geschätzt, nicht gemessen.

## Modellwahl

Starthypothese, am Trockenlauf zu messen:

| Aufgabe | Modell | Effort |
| --- | --- | --- |
| Koordination | Opus 5.5 | `high` |
| Briefing und Zerlegung schwieriger Aufträge | Fable 5.2 | `high` |
| Analyse | Opus 5.5 | `high` |
| Umsetzung, klar umrissen | Sonnet 5.5 | `medium` |
| Umsetzung, unklar oder an heiklen Pfaden | Opus 5.5 | `high` |
| Review | mindestens so stark wie der Umsetzer, frischer Kontext | `high` |

Je Paket werden Token, Runden, Rückläufe und Befunde festgehalten. Daraus soll eine belegte Regel werden.

## Ereignisprotokoll

Das Protokoll ist der Vertrag zwischen Workflow und Board. Der Workflow läuft ohne Oberfläche und schreibt je Auftrag eine Datei; das Board liest sie nur.

- **Format:** JSON Lines, nur anhängend, eine Zeile je Ereignis. Feldnamen englisch, weil das Format die Schnittstelle zum Board ist.
- **Geschrieben nur auf der Mac-Seite,** aus beobachteten Tatsachen: git, Exit-Codes, Dateivergleiche. Berichte der Agenten hängen als Behauptung an der Karte.
- **Entscheidungen des Menschen** stehen als Ereignis im Protokoll. Das Board schreibt sie dorthin und hat keine Schreibrechte auf git. Der Koordinator mergt nur, wenn die Entscheidung vorliegt.

Ereignisarten im ersten Wurf: `card_created`, `card_moved`, `card_returned`, `condition_checked`, `run_started`, `run_ended`, `subagent`, `finding`, `decision`, `limit_reached`.

Felder im ersten Wurf: `ts`, `project`, `host`, `job`, `card`, `event`, `column`, `actor`, `model`, `effort`, `sandbox`, `commit`, `source`, `reason`, `evidence`, `tokens`.

Beim Bau am 2026-11-20 festgelegt: Die Spalten heißen im Protokoll englisch (`decomposition`, `implementation`, `gate`, `approval`, `merged`). Der Bericht des Agenten steht als `evidence.claim` am Ereignis `run_ended`. Neu ist die Ereignisart `fault_injected` für den Trockenlauf. `subagent` wird noch nicht geschrieben. Am 2026-11-21 kamen `card_halted` und `card_resumed` für den Zustandsautomaten dazu. Die Tabelle der Felder und Ereignisse steht in `Kreislauf.md`, ein Beispiel unter `beispiele/trockenlauf-1/`.

Am 2026-11-21 ergänzt: die Ereignisart `network_observed`. Nach jedem Lauf in einer Sandbox schreibt das Skript die angesprochenen Ziele im Netz ins Protokoll, erlaubte und gesperrte, aus `sbx policy log`. Stand: seit dem 2026-11-21 in echten Läufen des Kreislaufs entstanden, auch mit gesperrten Zielen (Läufe `lauf-2` bis `lauf-4`, davon `lauf-3` unter `beispiele/lauf-3/`; auch auf einem zweiten Rechner). Das Beispielprotokoll unter `beispiele/trockenlauf-1/` enthält es nicht.

## Sandbox-Vorlagen

| Vorlage | Inhalt | Zweck |
| --- | --- | --- |
| Arbeits-Vorlage | wie `baseline-claude` heute, dazu Compiler und `gitleaks` für den Hook | Analyse, Umsetzung, Review |
| Scan-Vorlage | Gitleaks, OSV-Scanner, Semgrep, Syft; kein Claude, keine Anmeldung | Gate |

Gebaut ist die Scan-Vorlage `baseline-scan:2026-11-20` aus dem Standard-Image des Agenten `shell`, mit Gitleaks 8.30.1, OSV-Scanner 2.7.1 und Syft 1.57.1 (`werkzeuge/scan-template-build.sh`). Semgrep fehlt noch. Aus derselben Vorlage entsteht die Prüf-Sandbox, in der die Tests laufen; sie bringt uv und Python mit, aber kein Modell. Die Arbeits-Vorlage ist unverändert.

Das Gate läuft auf dem abgeholten Commit in einer frischen Scan-Sandbox, die der Umsetzer nie berührt hat. Fremder Code wird dadurch nie auf dem Mac ausgeführt, und die Scanner sind selbst isoliert.

Vorerst ist dieses lokale Gate das verbindliche. Dasselbe Skript soll später der GitHub-Workflow aus Schicht 3 aufrufen.

## Board

- Kleine Web-App: FastAPI liest die Protokolldateien, die Oberfläche kommt ohne Build-Schritt aus. Jeder Lauf lässt sich als eigenständige HTML-Seite exportieren.
- Erst ein Projekt je Board; das Protokoll trägt Projekt und Rechner schon mit.
- Ein Klick auf Karte oder Tag öffnet ein Kontextmenü mit den Funktionen der Spalte und der Checkliste aus Ready und Done.
- „Freigeben" schreibt eine Entscheidung ins Protokoll, mehr nicht.
- Das Board ruft kein Modell auf.

Entschieden (2026-11-22):

- **Grundlage wird ein Server aus der Standardbibliothek,** der nur das Ereignisprotokoll eines Auftrags liest. FastAPI entfällt damit; das Board bleibt ohne Abhängigkeiten.
- **Das Board bleibt vorerst nur lesend.** Entscheidungen des Menschen hält weiter `kreislauf decide` fest. Ein schreibender Endpunkt macht den Browser zum Teil der Freigabe und braucht eigenen Schutz; das Schreiben ins Protokoll ist zurückgestellt, nicht verworfen.

Stand: Das Board ist nicht gebaut und nicht Teil dieses Repositorys.

## Ausblick: auslieferbare und native Software

Stand 2026-11-19. Nichts davon ist gebaut.

Der Workflow erreicht heute nur, was die Arbeits-Vorlage bauen kann: Python und Node. Im Bestand liegen drei Fälle, die er nicht erreicht: ein Rust-Workspace, eine Xcode-App mit Swift-Paketen und eine PlatformIO-Firmware. Gesehen sind nur die Build-Dateien, nicht der Code.

### Grundsatz: Bauen ist Ausführen

`build.rs`, Proc-Makros, Gradle-Skripte und SwiftPM-Plugins laufen beim Kompilieren. Anders als Install-Skripte bei npm (`ignore-scripts`) lassen sie sich nicht abschalten. Ein abgeholter Branch wird deshalb vor der Freigabe nie auf dem Mac gebaut, auch nicht für Tests.

### Ausbaustufen

| Stufe | Inhalt | Erschließt |
| --- | --- | --- |
| 1 Toolchain-Vorlagen | Das Bauskript der Vorlage bekommt einen Parameter und erzeugt Varianten mit Rust, JVM oder PlatformIO. Jede bringt Compiler, Cooldown und Netzregel mit. | Rust-Workspace, Firmware-Build |
| 2 Kern-Pakete auf Linux | Swift-Pakete ohne Apple-Frameworks bauen und testen in der Linux-Sandbox. Regel in `AGENTS.md`: Logik ins Paket, dünne Plattform-Hülle. | den Paket-Anteil der Xcode-App |
| 3 macOS-Bau-VM als Prüfer | Eine Wegwerf-VM mit Xcode baut und testet nach dem Muster der Scan-Sandbox: Commit hinein, Exit-Codes heraus, keine Apple-ID und keine Signierschlüssel darin. | Xcode-Projekte |
| 4 Gerätetest und Release | Zwei Spalten des Menschen: Gerätetest (Flashen, Bluetooth, USB, Metal-Leistung) und Release (Signieren, Notarisieren, SBOM aus dem gebauten Artefakt). | auslieferbare Produkte |

Reihenfolge wie nummeriert. Stufe 1 ist als offener Punkt aus dem Pilot bekannt, Stufe 4 braucht erst ein Produkt.

- **Zu Stufe 1:** `crates.io` ist in der Policy `balanced` gesperrt und braucht eine eigene Regel. Cargo übernimmt wie npm Versionen, die schon im Lockfile stehen; die Schadpaket-Prüfung im Gate bleibt nötig.
- **Zu Stufe 3:** Apple erlaubt zwei macOS-VMs je Mac gleichzeitig. Das wird die WIP-Grenze für diese Spalte. Die Abbilder mit Xcode sind groß und gehören nicht ins Repository.
- **Zu Stufe 4:** Der wöchentliche Scan prüft dann die SBOMs der ausgelieferten Versionen, nicht nur `main`, weil beim Kunden alte Stände laufen.

### Produkt-Merkmal

Ob ein Projekt unter den CRA fällt, hängt nicht an der Schutzstufe. Ein eigenes Merkmal im Briefing hält es fest: Wird die Software als installierbares Produkt gegen Entgelt ausgeliefert?

| Projektart | CRA | Grund |
| --- | --- | --- |
| Hobby, Demo | nein | keine gewerbliche Tätigkeit |
| Interne Tools | nein | nicht am Markt bereitgestellt (eigene Ableitung aus Art. 3 Nr. 22) |
| Websites, Web-APIs, SaaS | nein | Erwägungsgrund 12 |
| Nicht monetarisierte Open-Source-Software | nein | Erwägungsgrund 18 |
| Gegen Entgelt ausgelieferte Software, auch maßgeschneiderte | grundsätzlich ja | Anhang I Teil I Nr. 2 b, Erwägungsgrund 64 |

- Ist das Merkmal gesetzt, wird die Release-Spalte Pflicht, die SBOM je Release aufbewahrt und ein Meldeweg benannt.
- Die Meldepflicht (Art. 14) gilt seit dem 11. September 2026, auch für Produkte, die vor dem 11. Dezember 2027 in Verkehr gebracht wurden (Art. 69 Abs. 3). Die übrigen Pflichten gelten ab dem 11. Dezember 2027.
- Das ist eine Lesart des Verordnungstexts, keine Rechtsberatung.

## Offene Entscheidungen

- Ob das Protokoll eingecheckt wird. Es liegt jetzt unter `.baseline/runs/<auftrag>/events.jsonl` im Projekt und ist dort von Git ausgenommen.
- Entschieden (2026-11-21): Das Gate prüft mit Gitleaks auch die Commits der Karte zwischen Basis und Stand. Ein Fund dort führt zum Rücklauf, und die Nachbesserung ersetzt die Commits. Anlass: Im Trockenlauf und in `lauf-3` blieb das eingebaute Token nach der Nachbesserung in einem früheren Commit des Branches. Verworfen ist, beim Merge zusammenzufassen (Squash): Das Gate bliebe grün, das Secret unbemerkt, und der freigegebene Commit wäre nicht mehr der gemergte. Ergänzt (2026-11-22): Der Lauf deckt auch Merge-Commits, Dateien, die git für binär hält, und die Köpfe der Commits (Nachricht, Autor, Committer) ab. Regeln und Ausnahmen für Gitleaks gelten so, wie sie in der Basis stehen; `gitleaks:allow` im geprüften Code gilt nicht.
- Fernzugriff: SSH-Sitzung der Desktop-App oder Remote Control. Nicht getestet.
- Zusatznutzung des Abos ein- oder ausgeschaltet.
- Wie der Koordinator ein erreichtes Limit in einem Headless-Lauf erkennt; Exit-Code und Feld sind nicht dokumentiert. Das Skript nimmt vorerst einen Fehlerlauf, dessen Text „limit" enthält; der Fall ist nicht eingetreten.
- Semgrep braucht Regeln aus dem Netz; die Netz-Policy dafür ist offen. Für den OSV-Scanner ist die Frage geklärt: Die Policy `balanced` sperrt `api.osv.dev`, lässt aber den Download der Offline-Datenbank zu.
- Mutationstests als zusätzliches Kriterium neben der Abdeckung.
- Sprache der Oberfläche.

## Belege

Am 2026-11-19 gegen Anthropic-Quellen und sonstige Praxis geprüft. Die Recherche lief über ein zusammenfassendes Werkzeug; **die Zahlen sind nicht am Original geprüft.**

Gestützt:

- Trennung von Erzeuger und Prüfer, Review in frischem Kontext ([Anthropic, Harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps); [Cognition](https://cognition.com/blog/multi-agents-working))
- Prüfung von außen, Abnahmekriterien vor dem Code, Schleifengrenze mit Eskalation ([Claude Code best practices](https://code.claude.com/docs/en/best-practices))
- Günstigere Modelle für die Umsetzung; Fable als Orchestrator war in Anthropics Messung teurer bei gleicher Wertung ([Optimizing for cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence))

Daraus geändert gegenüber dem ersten Zuschnitt:

- Opus statt Fable als Koordinator.
- Der Prüfer liest das ganze Repository, nicht nur den Diff: In einer Studie zu KI-Review wurde über die Hälfte der Kommentare abgelehnt, meist wegen fehlenden Kontexts ([arXiv 2607.03316](https://www.alphaxiv.org/abs/2607.03316v1)).
- Schleifen begrenzt und Tests in der Nachbesserung schreibgeschützt: mehr kritische Schwachstellen über Iterationen ([Shukla et al.](https://arxiv.org/abs/2506.11022)), mehr Test-Manipulation unter wiederholtem Feedback ([ImpossibleBench](https://arxiv.org/abs/2510.20270)).
- Route je Karte statt aller Spalten für alle: Bei leichten Aufgaben ist ein Prüfer reiner Aufwand.
- Zwei Commits je Paket, damit Test-first von außen prüfbar ist.

Ohne Beleg:

- Review durch ein anderes Modell gegenüber demselben Modell.
- Schleifenzähler, Anteil ohne Rücklauf und maschinell geprüfte Ready- und Done-Bedingungen für Agenten.
- Obergrenzen für Schleifen und Nacharbeitskosten bei gemischter Modellwahl.

Nicht abrufbar waren der OpenAI-Beitrag zu Harness Engineering und der DORA-Report 2025.
