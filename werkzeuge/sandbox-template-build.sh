#!/usr/bin/env bash
#
# sandbox-template-build.sh — baut die Vorlage für Arbeits-Sandboxes (Docker Sandboxes, sbx).
#
# Aus dem aktuellen Standard-Image von sbx (ab 0.54 ein gehärtetes Debian ohne Node)
# entsteht eine Vorlage mit
#   - Node.js und npm, jeweils die neueste Version, die mindestens COOLDOWN_DAYS alt ist,
#   - der Cooldown-Konfiguration für npm, uv und pip aus werkzeuge/sandbox-home/,
#   - ohne Anmeldedatei und ohne Projektreste.
#
# Usage:  sandbox-template-build.sh [tag]        (Standard-Tag: heutiges Datum)
#         NODE_MAJOR=22 sandbox-template-build.sh  (andere Node-Linie)
# Danach: sbx create --clone --pull never -t baseline-claude:<tag> --name <auftrag> claude <projektpfad>
#
# Das Kreislauf-Skript erwartet einen bestimmten Tag (WORK_TEMPLATE in kreislauf/cycle.py).
# Entweder mit diesem Tag bauen oder KREISLAUF_WORK_TEMPLATE=baseline-claude:<tag> setzen;
# siehe Kreislauf.md, Abschnitt „Vorlagen bauen".
#
set -euo pipefail

REPO="baseline-claude"
TAG="${1:-$(date +%F)}"
NODE_MAJOR="${NODE_MAJOR:-24}"     # LTS-Linie von Node.js
NPM_MAJOR="${NPM_MAJOR:-11}"       # min-release-age gibt es erst ab npm 11.10
COOLDOWN_DAYS="${COOLDOWN_DAYS:-7}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SRC="$HERE/sandbox-home"
BUILD="vorlage-bau-$$"

die() { echo "FEHLER: $*" >&2; exit 1; }

command -v sbx >/dev/null || die "sbx ist nicht installiert."
sbx ls >/dev/null 2>&1 || die "sbx ist nicht angemeldet oder der Daemon läuft nicht (sbx login)."
for f in npmrc uv.toml pip.conf; do [[ -f "$SRC/$f" ]] || die "$SRC/$f fehlt."; done
if sbx template ls 2>/dev/null | awk '{print $1":"$2}' | grep -qx "docker.io/library/$REPO:$TAG"; then
  die "Die Vorlage $REPO:$TAG gibt es schon. Anderen Tag angeben oder sie vorher löschen."
fi

cleanup() { sbx rm --force "$BUILD" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> Bau-Sandbox $BUILD aus dem Standard-Image anlegen (ohne Projektordner)"
sbx create --name "$BUILD" claude >/dev/null

echo "==> Konfiguration kopieren"
sbx exec "$BUILD" sh -c 'mkdir -p "$HOME/.config/uv" "$HOME/.config/pip"'
sbx cp "$SRC/npmrc"    "$BUILD:/home/agent/.npmrc"
sbx cp "$SRC/uv.toml"  "$BUILD:/home/agent/.config/uv/uv.toml"
sbx cp "$SRC/pip.conf" "$BUILD:/home/agent/.config/pip/pip.conf"

echo "==> Werkzeuge einrichten, prüfen, aufräumen"
# Läuft in der Sandbox als Benutzer agent (sudo ohne Passwort).
# shellcheck disable=SC2016
PROVISION='
set -eu
node_major="$1"; npm_major="$2"; days="$3"
fail() { echo "FEHLER in der Sandbox: $*" >&2; exit 1; }
for t in curl jq tar gzip sha256sum sudo uv; do command -v "$t" >/dev/null 2>&1 || fail "$t fehlt im Image"; done

cutoff_day="$(date -u -d "$days days ago" +%F)"
cutoff_ts="$(date -u -d "$days days ago" +%FT%TZ)"
case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=x64 ;; *) fail "unbekannte Architektur $(uname -m)" ;; esac

# Node.js: neueste Version der Linie, die mindestens $days Tage alt ist. Prüfsumme gegen SHASUMS256.txt.
if ! command -v node >/dev/null 2>&1; then
  nv="$(curl -fsSL https://nodejs.org/dist/index.json \
        | jq -r --arg p "v$node_major." --arg d "$cutoff_day" "[.[] | select(.version|startswith(\$p)) | select(.date <= \$d)][0].version // empty")"
  [ -n "$nv" ] || fail "keine Node-Version $node_major.x gefunden, die $days Tage alt ist"
  tmp="$(mktemp -d)"; f="node-$nv-linux-$arch.tar.gz"
  curl -fsSL -o "$tmp/$f" "https://nodejs.org/dist/$nv/$f"
  curl -fsSL -o "$tmp/SHASUMS256.txt" "https://nodejs.org/dist/$nv/SHASUMS256.txt"
  (cd "$tmp" && grep " $f\$" SHASUMS256.txt | sha256sum -c - >/dev/null 2>&1) || fail "Prüfsumme von $f stimmt nicht"
  sudo mkdir -p "/opt/node-$nv"
  sudo tar -xzf "$tmp/$f" -C "/opt/node-$nv" --strip-components=1 --no-same-owner
  for b in node npm npx; do sudo ln -sf "/opt/node-$nv/bin/$b" "/usr/local/bin/$b"; done
  rm -rf "$tmp"
fi

# npm: das mitgelieferte npm kennt min-release-age meist nicht, deshalb hier von Hand
# dieselbe Regel: neueste Version der Linie, die mindestens $days Tage alt ist.
want="$(curl -fsSL https://registry.npmjs.org/npm \
        | jq -r --arg m "^$npm_major\\.[0-9]+\\.[0-9]+\$" --arg t "$cutoff_ts" \
          "[.time | to_entries[] | select(.key|test(\$m)) | select(.value <= \$t)] | sort_by(.key|split(\".\")|map(tonumber)) | last | .key // empty")"
[ -n "$want" ] || fail "keine npm-Version $npm_major.x gefunden, die $days Tage alt ist"
out="$(npm install -g "npm@$want" --no-audit --no-fund 2>&1)" || { echo "$out" >&2; fail "npm ließ sich nicht auf $want heben"; }
hash -r
have="$(npm --version 2>/dev/null)"
[ "$have" = "$want" ] || fail "npm ist $have statt $want (PATH: $(command -v npm))"
[ "$(npm config get min-release-age 2>/dev/null)" = "$days" ] || fail "min-release-age greift nicht"
[ "$(npm config get ignore-scripts 2>/dev/null)" = "true" ] || fail "ignore-scripts greift nicht"
[ -f "$HOME/.config/uv/uv.toml" ] && [ -f "$HOME/.config/pip/pip.conf" ] || fail "uv- oder pip-Konfiguration fehlt"

# Wirkt der Cooldown? boto3 erscheint fast täglich, mit und ohne Konfiguration
# müssen sich die Versionen also unterscheiden. Nur Hinweis, kein Abbruch.
mit="$(echo boto3 | uv pip compile - --no-header --no-deps -q 2>/dev/null | grep -i "^boto3" || true)"
ohne="$(echo boto3 | uv pip compile - --no-header --no-deps -q --no-config 2>/dev/null | grep -i "^boto3" || true)"
if [ -n "$mit" ] && [ "$mit" != "$ohne" ]; then
  echo "    Cooldown wirkt: $mit statt $ohne"
else
  echo "    HINWEIS: Cooldown-Probe ohne Aussage (mit: ${mit:-keine Antwort}, ohne: ${ohne:-keine Antwort})"
fi

# sbx schreibt die Anmeldedatei nur, wenn sie fehlt. Eine eingefrorene Datei
# macht jede Sandbox aus der Vorlage unangemeldet.
rm -f "$HOME/.claude/.credentials.json"
rm -rf "$HOME/venv" "$HOME/.npm/_logs" "$HOME/.npm/_cacache" "$HOME/.cache/uv"
echo "    node $(node --version), npm $have, uv $(uv --version | cut -d" " -f2), python $(python3 --version | cut -d" " -f2)"
'
sbx exec "$BUILD" sh -c "$PROVISION" sh "$NODE_MAJOR" "$NPM_MAJOR" "$COOLDOWN_DAYS"

echo "==> Vorlage $REPO:$TAG speichern"
sbx stop "$BUILD" >/dev/null
sbx template save "$BUILD" "$REPO:$TAG"

echo
echo "Fertig. Neue Sandbox aus der Vorlage:"
echo "  sbx create --clone --pull never -t $REPO:$TAG --name <auftrag> claude <projektpfad>"
