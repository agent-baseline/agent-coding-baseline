# Sicherheitslücken melden

*Security issues: please report them privately via "Report a vulnerability" in the Security tab of this repository. German or English is fine.*

Bitte kein öffentliches Issue für eine Sicherheitslücke öffnen.

## Meldeweg

Im Reiter „Security" dieses Repositorys auf „Report a vulnerability" gehen:
<https://github.com/agent-baseline/agent-coding-baseline/security/advisories/new>

Die Meldung sehen nur die Maintainer. Hilfreich sind:

- die betroffene Datei oder der betroffene Befehl,
- der Stand (Commit), an dem das Problem auftritt,
- Schritte zum Nachstellen,
- die Auswirkung: Was kann ein Agent, ein Fremdbeitrag oder ein Paket damit erreichen?

## Was zu erwarten ist

Das Projekt wird in der Freizeit gepflegt. Eine Antwortfrist sagen wir nicht zu; wir melden uns, sobald wir können. Über Zeitpunkt und Form einer Veröffentlichung stimmen wir uns mit der meldenden Person ab.

## Geltungsbereich

- Das Kreislauf-Skript (`kreislauf/`) und die Bauskripte der Sandbox-Vorlagen (`werkzeuge/`), jeweils am aktuellen Stand von `main`. Releases gibt es nicht.
- Besonders wichtig sind Wege, auf denen eine Karte ohne grünes Gate oder ohne Entscheidung des Menschen nach `main` käme, und Wege, auf denen Code aus einer Sandbox auf dem Rechner ausgeführt würde.
- Die bekannten Grenzen stehen in `Kreislauf.md` unter „Grenzen". Hinweise dazu sind willkommen, müssen aber nicht vertraulich sein.

Lücken in fremder Software (Docker Sandboxes, Claude Code, Gitleaks, OSV-Scanner, Syft, npm, uv, pip) gehören an deren Hersteller. Setzt unser Skript ein Verhalten dieser Werkzeuge falsch voraus, ist das ein Fall für dieses Repository.
