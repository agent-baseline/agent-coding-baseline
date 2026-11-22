#!/usr/bin/env bash
#
# scan-template-build.sh — baut die Scan-Vorlage für das Gate (Docker Sandboxes, sbx).
#
# Aus dem Standard-Image des Agenten "shell" (gehärtetes Debian, kein Claude Code)
# entsteht eine Vorlage mit Gitleaks, OSV-Scanner und Syft. Je Werkzeug wird das
# neueste Release genommen, das mindestens COOLDOWN_DAYS alt ist; die Prüfsumme
# wird gegen die Prüfsummen-Datei desselben Release geprüft. Semgrep fehlt noch.
#
# Usage:  scan-template-build.sh [tag]            (Standard-Tag: heutiges Datum)
#         GITLEAKS_VERSION=v8.x.y scan-template-build.sh   (Version festlegen;
#         ebenso OSV_SCANNER_VERSION, SYFT_VERSION)
#         BUILD_SANDBOX=<name> ...                (vorhandene Bau-Sandbox weiterverwenden)
#         CLEANUP=1 ...                           (Bau-Sandbox am Ende löschen)
# Danach: sbx create --clone --pull never -t baseline-scan:<tag> --name <name> shell <projektpfad>
#
# Hinweis: Das Bauskript der Arbeits-Vorlage liegt daneben (sandbox-template-build.sh).
#
set -euo pipefail

REPO="baseline-scan"
TAG="${1:-$(date +%F)}"
COOLDOWN_DAYS="${COOLDOWN_DAYS:-7}"
BUILD="${BUILD_SANDBOX:-scan-vorlage-bau-$$}"
CLEANUP="${CLEANUP:-0}"

die() { echo "FEHLER: $*" >&2; exit 1; }

command -v sbx >/dev/null || die "sbx ist nicht installiert."
sbx ls >/dev/null 2>&1 || die "sbx ist nicht angemeldet oder der Daemon läuft nicht (sbx login)."
if sbx template ls 2>/dev/null | awk '{print $1":"$2}' | grep -qx "docker.io/library/$REPO:$TAG"; then
  die "Die Vorlage $REPO:$TAG gibt es schon. Anderen Tag angeben oder sie vorher löschen."
fi

cleanup() {
  if [[ "$CLEANUP" == "1" ]]; then
    sbx rm --force "$BUILD" >/dev/null 2>&1 || true
  else
    echo "Bau-Sandbox $BUILD bleibt stehen. Löschen: sbx rm --force $BUILD"
  fi
}
trap cleanup EXIT

if sbx ls 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$BUILD"; then
  echo "==> Bau-Sandbox $BUILD weiterverwenden"
else
  echo "==> Bau-Sandbox $BUILD aus dem Standard-Image anlegen (Agent shell, ohne Projektordner)"
  sbx create --name "$BUILD" shell >/dev/null
fi

echo "==> Scanner einrichten und prüfen"
# Läuft in der Sandbox als Benutzer agent (sudo ohne Passwort).
# shellcheck disable=SC2016
PROVISION='
set -eu
days="$1"; want_gitleaks="$2"; want_osv="$3"; want_syft="$4"
fail() { echo "FEHLER in der Sandbox: $*" >&2; exit 1; }
for t in curl jq tar gzip sha256sum sudo git; do command -v "$t" >/dev/null 2>&1 || fail "$t fehlt im Image"; done
command -v claude >/dev/null 2>&1 && fail "claude liegt im Image; die Scan-Vorlage soll kein Modell enthalten"

cutoff="$(date -u -d "$days days ago" +%FT%TZ)"
case "$(uname -m)" in
  aarch64) a_gitleaks=arm64; a_std=arm64 ;;
  x86_64)  a_gitleaks=x64;   a_std=amd64 ;;
  *) fail "unbekannte Architektur $(uname -m)" ;;
esac
tmp="$(mktemp -d)"
sudo mkdir -p /usr/local/share/baseline-scan
: > "$tmp/versions.txt"

# install <name> <owner/repo> <gewünschte Version oder leer> <Regex Asset> <Regex Prüfsummen-Datei> <tar|bin>
install() {
  name="$1"; repo="$2"; want="$3"; asset_re="$4"; sums_re="$5"; kind="$6"
  rel="$tmp/$name.releases.json"
  curl -fsSL "https://api.github.com/repos/$repo/releases?per_page=40" -o "$rel" || fail "$name: Release-Liste nicht abrufbar"
  if [ -n "$want" ]; then
    tag="$want"
  else
    tag="$(jq -r --arg t "$cutoff" "[.[] | select(.draft|not) | select(.prerelease|not) | select(.published_at <= \$t)][0].tag_name // empty" "$rel")"
  fi
  [ -n "$tag" ] || fail "$name: kein Release gefunden, das $days Tage alt ist"
  published="$(jq -r --arg v "$tag" ".[] | select(.tag_name == \$v) | .published_at" "$rel")"
  [ -n "$published" ] || fail "$name: Release $tag nicht in der Liste"
  [ "$published" \< "$cutoff" ] || [ "$published" = "$cutoff" ] || fail "$name: Release $tag ist jünger als $days Tage ($published)"
  url="$(jq -r --arg v "$tag" --arg re "$asset_re" ".[] | select(.tag_name == \$v) | .assets[] | select(.name|test(\$re)) | .browser_download_url" "$rel" | head -n1)"
  sums="$(jq -r --arg v "$tag" --arg re "$sums_re" ".[] | select(.tag_name == \$v) | .assets[] | select(.name|test(\$re)) | .browser_download_url" "$rel" | head -n1)"
  [ -n "$url" ] && [ -n "$sums" ] || fail "$name: Asset oder Prüfsummen-Datei im Release $tag nicht gefunden"
  file="$(basename "$url")"
  curl -fsSL -o "$tmp/$file" "$url"
  curl -fsSL -o "$tmp/$name.sums" "$sums"
  expected="$(awk -v f="$file" "\$2 == f || \$2 == \"*\" f || \$2 == \"./\" f {print \$1}" "$tmp/$name.sums" | head -n1)"
  actual="$(sha256sum "$tmp/$file" | cut -d" " -f1)"
  [ -n "$expected" ] && [ "$expected" = "$actual" ] || fail "$name: Prüfsumme von $file stimmt nicht"
  if [ "$kind" = tar ]; then
    mkdir -p "$tmp/$name.x"
    tar -xzf "$tmp/$file" -C "$tmp/$name.x" --no-same-owner
    sudo install -m 0755 "$tmp/$name.x/$name" "/usr/local/bin/$name"
  else
    sudo install -m 0755 "$tmp/$file" "/usr/local/bin/$name"
  fi
  echo "$name $tag published=$published sha256=$actual asset=$file" >> "$tmp/versions.txt"
}

install gitleaks    gitleaks/gitleaks  "$want_gitleaks" "^gitleaks_.*_linux_${a_gitleaks}\\.tar\\.gz\$" "checksums\\.txt\$"  tar
install osv-scanner google/osv-scanner "$want_osv"      "^osv-scanner_linux_${a_std}\$"                 "SHA256SUMS"         bin
install syft        anchore/syft       "$want_syft"     "^syft_.*_linux_${a_std}\\.tar\\.gz\$"          "checksums\\.txt\$"  tar

gitleaks version >/dev/null 2>&1    || fail "gitleaks startet nicht"
osv-scanner --version >/dev/null 2>&1 || fail "osv-scanner startet nicht"
syft version >/dev/null 2>&1        || fail "syft startet nicht"

sudo install -m 0644 "$tmp/versions.txt" /usr/local/share/baseline-scan/versions.txt
rm -rf "$tmp" "$HOME/.cache"
cat /usr/local/share/baseline-scan/versions.txt | sed "s/^/    /"
'
sbx exec "$BUILD" sh -c "$PROVISION" sh "$COOLDOWN_DAYS" \
  "${GITLEAKS_VERSION:-}" "${OSV_SCANNER_VERSION:-}" "${SYFT_VERSION:-}"

echo "==> Vorlage $REPO:$TAG speichern"
sbx stop "$BUILD" >/dev/null
sbx template save "$BUILD" "$REPO:$TAG"

echo
echo "Fertig. Scan-Sandbox aus der Vorlage:"
echo "  sbx create --clone --pull never -t $REPO:$TAG --name <name> shell <projektpfad>"
