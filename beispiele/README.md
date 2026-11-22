# Beispiele

## `trockenlauf-1/`

Erster Lauf des Kreislauf-Skripts am 2026-11-20, an einem Beispielprojekt (`baseline-beispiel-api`, nicht veröffentlicht; Web-API mit zwei Rollen, Schutzstufe `internal`). Gedacht als Vorlage für das Board: Es liest `events.jsonl` und sonst nichts.

| Datei | Inhalt |
| --- | --- |
| `events.jsonl` | Ereignisprotokoll des Auftrags, 86 Zeilen. Format in `../Kreislauf.md`. |
| `K1/card.json`, `K2/card.json` | die beiden Arbeitspakete |
| `K1/diff-report.md`, `K2/diff-report.md` | Diff-Bericht für die Freigabe |
| `K2/gate-1/` | Gate-Lauf, der die Karte abgewiesen hat: Gitleaks-Bericht mit einem Fund, OSV-Bericht ohne Fund |
| `K2/gate-2/` | Gate-Lauf nach der Nachbesserung: beide Berichte leer, dazu die SBOM (CycloneDX 1.7, 23 Komponenten) |

Was im Protokoll zu sehen ist:

- **K1** läuft ohne Rücklauf von der Zerlegung bis in die Freigabe. Der erste Versuch scheitert an der Ready-Bedingung `card_released`, weil die Freigabe des Menschen noch fehlt.
- **K2** bekommt nach dem Agentenlauf einen Fehler eingebaut (`fault_injected`), das Gate meldet ihn (`finding`), die Karte geht zurück (`card_returned`), der Umsetzer bessert nach, und der zweite Gate-Lauf ist grün.
- Beide Karten stehen danach in der Spalte `approval` und warten auf den Menschen.
- **K1** wird vom Menschen freigegeben (`decision` mit dem geprüften Commit), gemergt und auf dem Rechner getestet; die Karte endet in `merged`.
- **K2** wird vom Menschen abgelehnt, weil die Historie des Branches das eingebaute Token enthält, und geht in die Ablage (`parked`). Der anschließende Merge-Versuch scheitert an der Bedingung `human_decision_for_this_commit`.

Gegenüber dem Original sind der Rechnername im Feld `host` und der Vorname in den Begründungen der Entscheidungen ersetzt. Das eingebaute Token steht in keiner dieser Dateien; der Gitleaks-Bericht ist geschwärzt.

## `lauf-3/`

Erster Lauf mit Rücklauf und Nachbesserung seit dem Zustandsautomaten, am 2026-11-21 am selben Beispielprojekt. Die Karte endet in der Ablage.

| Datei | Inhalt |
| --- | --- |
| `events.jsonl` | Ereignisprotokoll des Auftrags, 72 Zeilen. Format in `../Kreislauf.md`. |
| `K4/card.json` | das Arbeitspaket: den Text der eigenen Notiz ändern; mit `dry_run_fault` |
| `K4/diff-report.md` | Diff-Bericht für die Freigabe, mit einem Rücklauf |
| `K4/gate-1/` | Gate-Lauf, der die Karte abgewiesen hat: Gitleaks-Bericht mit einem Fund (geschwärzt), OSV-Bericht ohne Fund |
| `K4/gate-2/` | Gate-Lauf nach der Nachbesserung: beide Berichte leer, dazu die SBOM |

Was im Protokoll zu sehen ist:

- **Eingebauter Fehler:** Nach dem Agentenlauf committet das Skript ein erfundenes Token (`fault_injected`).
- **Erste Runde:** Die Prüfung von außen besteht (Test-Commit rot, Stand grün), das Gate meldet das Token (`finding`, über der Schwelle), die Karte geht zurück (`card_returned` mit `source: gate`).
- **Nachbesserung:** derselbe Umsetzer, dieselbe Sitzung (`run_started` mit `resume: true`), ein neuer Commit.
- **Zweite Runde:** `tests_unchanged_since_test_commit` nennt den abgenommenen Test-Commit; die Tests laufen in zwei neuen Prüf-Sandboxes, das zweite Gate ist grün, die Karte steht in `approval`.
- **Ablehnung:** Der Mensch lehnt ab, weil die Historie des Branches das Token enthält; die Karte geht nach `parked`. Ein Merge-Versuch und eine weitere Entscheidung danach werden abgewiesen (`condition_checked` mit `ok: false`).

Gegenüber dem Original sind der Rechnername im Feld `host` und ein lokaler Pfad in einer Meldung von `sbx` ersetzt. Das eingebaute Token steht in keiner dieser Dateien; der Gitleaks-Bericht ist geschwärzt.
