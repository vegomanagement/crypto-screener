#!/usr/bin/env bash
# Online, consistent copies of every SQLite file of the crypto screener (sqlite3 .backup uses the
# backup API: safe while the app writes, WAL included). Runs on the VPS host, cron-able:
#   40 3 * * * root /opt/crypto-screener/src/deploy/contabo/backup.sh
# Output: /opt/backups/crypto-screener/<YYYY-MM-DD>/<file>.gz, kept KEEP_DAYS (default 14).
set -euo pipefail

DATA="${CS_DATA:-/opt/crypto-screener/data}"
DEST_ROOT="${CS_BACKUP_DIR:-/opt/backups/crypto-screener}"
KEEP_DAYS="${KEEP_DAYS:-14}"
DAY="$(date +%F)"
DEST="$DEST_ROOT/$DAY"

command -v sqlite3 >/dev/null || { echo "sqlite3 not installed" >&2; exit 1; }
mkdir -p "$DEST"
chmod 700 "$DEST_ROOT"

n=0
while IFS= read -r -d '' db; do
  rel="${db#"$DATA"/}"
  out="$DEST/${rel//\//__}"
  sqlite3 "$db" ".timeout 10000" ".backup '$out.tmp'"
  sqlite3 "$out.tmp" "PRAGMA integrity_check;" | grep -qx ok || { echo "integrity FAILED: $rel" >&2; rm -f "$out.tmp"; exit 1; }
  gzip -f "$out.tmp" && mv "$out.tmp.gz" "$out.gz"
  echo "$(date -Is) ok $rel -> $out.gz ($(stat -c %s "$out.gz") bytes)"
  n=$((n + 1))
done < <(find "$DATA" -type f \( -name '*.db' -o -name '*.sqlite3' \) -print0)

echo "$(date -Is) backed up $n file(s) into $DEST"
find "$DEST_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20??-??-??' -mtime +"$KEEP_DAYS" -exec rm -rf {} +
