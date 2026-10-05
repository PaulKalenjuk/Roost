#!/usr/bin/env bash
#
# Roost backup.
#
# Makes one restore point = a Postgres custom-format dump (pg_restore-able) plus
# a gzip tarball of the media volume. Keeps the last $ROOST_BACKUP_KEEP on the
# host and mirrors them to an encrypted rclone remote (Google Drive by default).
#
# It is *change aware*: before doing anything it fingerprints the database
# (logical data, not the volatile dump header) and the media volume. If nothing
# has changed since the last successful backup it exits without creating a new
# copy or uploading anything — so quiet days cost nothing.
#
# Config: ~/roost/config/backup.env (see backup.env.example).
#
# Usage: roost-backup.sh [--force]   (--force ignores the change check)
#
set -euo pipefail

CONFIG=${ROOST_BACKUP_CONF:-$HOME/roost/config/backup.env}
# shellcheck disable=SC1090
[ -r "$CONFIG" ] && . "$CONFIG"

: "${ROOST_BACKUP_REMOTE:=gdrive-crypt:}"
: "${ROOST_BACKUP_SUBDIR:=}"
: "${ROOST_BACKUP_KEEP:=3}"
: "${ROOST_BACKUP_DIR:=$HOME/roost/backups}"
: "${ROOST_DB_CONTAINER:=roost-db}"
: "${ROOST_DB_USER:=roost}"
: "${ROOST_DB_NAME:=roost}"
: "${ROOST_MEDIA_DIR:=$HOME/.local/share/containers/storage/volumes/roost-media/_data}"
: "${RCLONE:=$HOME/bin/rclone}"
: "${RCLONE_CONFIG:=$HOME/.config/rclone/rclone.conf}"

mkdir -p "$ROOST_BACKUP_DIR"
LOG="$ROOST_BACKUP_DIR/backup.log"
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$*" | tee -a "$LOG"; }

# A manual run (from the UI, or `--force`) skips the change check; the scheduled
# run only backs up when something has changed.
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# `.running` lets the app show progress; `.run-now` is the sentinel the host's
# systemd path unit watches — remove it here so a later request triggers again.
RUNNING="$ROOST_BACKUP_DIR/.running"
touch "$RUNNING"
rm -f "$ROOST_BACKUP_DIR/.run-now"
cleanup() { rm -f "$RUNNING"; [ -n "${TMP:-}" ] && rm -rf "$TMP"; }
trap cleanup EXIT

REMOTE_DIR="${ROOST_BACKUP_REMOTE}${ROOST_BACKUP_SUBDIR}"

# --- change detection -------------------------------------------------------
# Media: names + sizes + mtimes. DB: a data-only plain dump with the volatile
# comment/psql meta lines stripped, so the hash reflects the data, not the dump.
media_fp() {
  find "$ROOST_MEDIA_DIR" -type f -printf '%P %s %T@\n' 2>/dev/null \
    | LC_ALL=C sort | sha256sum | cut -d' ' -f1
}
db_fp() {
  # Exclude the volatile Django tables (logins / admin activity): a session change
  # isn't an app-data change, and shouldn't trigger a backup on its own.
  podman exec "$ROOST_DB_CONTAINER" \
    pg_dump -U "$ROOST_DB_USER" --data-only --no-owner --no-privileges -Fp \
      -T django_session -T django_admin_log "$ROOST_DB_NAME" 2>/dev/null \
    | grep -vE '^(--|\\|$)' | sha256sum | cut -d' ' -f1
}

if [ "$FORCE" = 1 ]; then log "backup run starting (forced)"; else log "backup run starting"; fi
FP="$(media_fp):$(db_fp)"
STATE="$ROOST_BACKUP_DIR/.last-fingerprint"
if [ "$FORCE" != 1 ] && [ -f "$STATE" ] && [ "$(cat "$STATE")" = "$FP" ]; then
  log "no changes since last backup — nothing to do"
  exit 0
fi

# --- build the restore point ------------------------------------------------
STAMP="$(date +%Y%m%d-%H%M%S)"
DB_FILE="roost-db-$STAMP.dump"
MEDIA_FILE="roost-media-$STAMP.tar.gz"
TMP="$(mktemp -d)"

log "changes detected — building restore point $STAMP"
podman exec "$ROOST_DB_CONTAINER" pg_dump -U "$ROOST_DB_USER" -Fc "$ROOST_DB_NAME" > "$TMP/$DB_FILE"
tar czf "$TMP/$MEDIA_FILE" -C "$ROOST_MEDIA_DIR" .
mv "$TMP/$DB_FILE" "$ROOST_BACKUP_DIR/$DB_FILE"
mv "$TMP/$MEDIA_FILE" "$ROOST_BACKUP_DIR/$MEDIA_FILE"
log "wrote $DB_FILE ($(du -h "$ROOST_BACKUP_DIR/$DB_FILE" | cut -f1)) and $MEDIA_FILE ($(du -h "$ROOST_BACKUP_DIR/$MEDIA_FILE" | cut -f1))"

# --- prune local ------------------------------------------------------------
prune_local() {
  ls -1t "$ROOST_BACKUP_DIR"/$1 2>/dev/null \
    | tail -n +$((ROOST_BACKUP_KEEP + 1)) \
    | while read -r f; do rm -f "$f"; log "pruned local $(basename "$f")"; done || true
}
prune_local 'roost-db-*.dump'
prune_local 'roost-media-*.tar.gz'

# --- off-site (encrypted) ---------------------------------------------------
if "$RCLONE" --config "$RCLONE_CONFIG" lsf "$ROOST_BACKUP_REMOTE" >/dev/null 2>&1; then
  "$RCLONE" --config "$RCLONE_CONFIG" mkdir "$REMOTE_DIR" 2>/dev/null || true
  "$RCLONE" --config "$RCLONE_CONFIG" copy "$ROOST_BACKUP_DIR/$DB_FILE" "$REMOTE_DIR" --no-traverse -q
  "$RCLONE" --config "$RCLONE_CONFIG" copy "$ROOST_BACKUP_DIR/$MEDIA_FILE" "$REMOTE_DIR" --no-traverse -q
  # keep only the newest $ROOST_BACKUP_KEEP of each on the remote too
  for prefix in roost-db- roost-media-; do
    "$RCLONE" --config "$RCLONE_CONFIG" lsf "$REMOTE_DIR" --files-only 2>/dev/null \
      | grep -E "^${prefix}" | LC_ALL=C sort | head -n -"$ROOST_BACKUP_KEEP" \
      | while read -r f; do
          "$RCLONE" --config "$RCLONE_CONFIG" deletefile "$REMOTE_DIR/$f" 2>/dev/null \
            && log "pruned remote $f"
        done || true
  done
  log "off-site copy up to date at $REMOTE_DIR"
else
  log "WARNING: remote $ROOST_BACKUP_REMOTE not reachable — kept locally only"
fi

echo "$FP" > "$STATE"
log "backup run finished"
