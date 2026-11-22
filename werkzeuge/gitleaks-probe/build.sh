#!/bin/sh
# Baut ein Wegwerf-Repository, an dem sich die Gitleaks-Läufe des Gates erproben lassen.
#
# Aufruf: build.sh <zielordner>   (der Ordner wird neu angelegt; nicht im Projekt ablegen)
#
# Jeder Fall bringt ein erfundenes Token im Format eines GitHub-Tokens in einer Form
# ein, die ein einfacher Scan übersieht. Die Tokens entstehen bei jedem Bau neu aus
# Zufallszeichen; in diesem Skript steht keines.
#
# Branch `karte` ab `main` (die Basis hat keine Regeln für Gitleaks):
#   plain   Token in einem Commit, der nächste entfernt es
#   bin     Token in einer Datei mit NUL-Byte (für git binär), danach gelöscht
#   attr    Token in einer Datei hinter dem Attribut `-diff`, danach entfernt
#   allow   Token in einer Zeile mit `gitleaks:allow`, danach entfernt
#   msg     Token nur in der Commit-Nachricht
#   merge   Token, das nur der Merge-Commit selbst einbringt, danach entfernt
#   keep    Token im Stand; die Karte nimmt die Datei in ihrer `.gitleaks.toml` aus
# Branch `karte2` ab `karte` (die Basis nimmt `src/keep.py` aus):
#   ein weiteres Token in `src/keep.py` (ausgenommen), eines in `src/other.py` und
#   eines in der Commit-Nachricht; die Karte ändert die Regeln auf „alles ausgenommen".
#
# Ausgabe: je Zeile ein Name und ein Commit (`base`, `head`, `msg`, `merge`, `head2`).
set -eu
dest="$1"
rm -rf "$dest"
mkdir -p "$dest"
cd "$dest"
tok() { printf 'ghp_%s' "$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 36)"; }
g() { git -c user.name=probe -c user.email=probe@example.invalid "$@"; }

git init -q -b main .
mkdir src
echo "# probe" > README.md
echo "x = 1" > src/a.py
g add -A; g commit -q -m "Basis"
echo "base $(git rev-parse HEAD)"

g checkout -q -b karte

printf 'TOKEN = "%s"\n' "$(tok)" > src/plain.py
g add -A; g commit -q -m "plain: Token hinein"
echo 'TOKEN = None' > src/plain.py
g add -A; g commit -q -m "plain: Token heraus"

{ printf 'KEY = "%s"\n' "$(tok)"; printf '\000\n'; } > src/bin.dat
g add -A; g commit -q -m "bin: Token in Datei mit NUL"
g rm -q src/bin.dat; g commit -q -m "bin: Datei geloescht"

echo 'src/attr.py -diff' > .gitattributes
printf 'TOKEN = "%s"\n' "$(tok)" > src/attr.py
g add -A; g commit -q -m "attr: Token hinter -diff"
echo 'TOKEN = None' > src/attr.py
g add -A; g commit -q -m "attr: Token heraus"

printf 'TOKEN = "%s"  # gitleaks:allow\n' "$(tok)" > src/allow.py
g add -A; g commit -q -m "allow: Token mit gitleaks:allow"
echo 'TOKEN = None' > src/allow.py
g add -A; g commit -q -m "allow: Token heraus"

echo "y = 2" >> src/a.py
g add -A; g commit -q -m "msg: Token in der Nachricht $(tok)"
echo "msg $(git rev-parse HEAD)"

g checkout -q -b seite
echo "z = 3" > src/seite.py
g add -A; g commit -q -m "seite: harmlos"
g checkout -q karte
echo "w = 4" > src/haupt.py
g add -A; g commit -q -m "haupt: harmlos"
g merge -q --no-ff --no-commit seite > /dev/null
printf 'TOKEN = "%s"\n' "$(tok)" > src/merge.py
g add -A; g commit -q -m "Merge seite (mit eigener Aenderung)"
echo "merge $(git rev-parse HEAD)"
echo 'TOKEN = None' > src/merge.py
g add -A; g commit -q -m "merge: Token heraus"

printf 'TOKEN = "%s"\n' "$(tok)" > src/keep.py
cat > .gitleaks.toml <<'EOF'
[extend]
useDefault = true

[allowlist]
paths = ['''src/keep\.py''']
EOF
g add -A; g commit -q -m "keep: Token im Stand, Ausnahme in .gitleaks.toml"
echo "head $(git rev-parse HEAD)"

g checkout -q -b karte2
printf 'ZWEI = "%s"\n' "$(tok)" >> src/keep.py
printf 'TOKEN = "%s"\n' "$(tok)" > src/other.py
cat > .gitleaks.toml <<'EOF'
[extend]
useDefault = true

[allowlist]
paths = ['''.*''']
EOF
g add -A; g commit -q -m "karte2: lockert die Regeln, Nachricht mit $(tok)"
echo "head2 $(git rev-parse HEAD)"
g checkout -q karte
