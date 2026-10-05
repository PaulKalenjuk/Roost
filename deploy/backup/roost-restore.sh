#!/usr/bin/env bash
#
# Roost restore.
#
# Restores a restore point produced by roost-backup.sh: rebuilds the Postgres
# database from the dump and replaces the media volume from the tarball. The app
# is stopped during the restore and started again afterwards.
#
#   roost-restore.sh --list                 # list local restore points
#   roost-restore.sh --list --remote        # also list the (encrypted) Drive ones
#   roost-restore.sh                        # restore the newest local point
#   roost-restore.sh --stamp 20261005-065800
#   roost-restore.sh --remote --stamp ...   # fetch that point from Drive first
#   roost-restore.sh ... --yes             # skip the confirmation prompt
#
# DESTRUCTIVE: this overwrites the current database and media. Use with care.
#
set -euo pipefail

CONFIG=${ROOST_BACKUP_CONF:-$HOME/roost/config/backup.env}
# shellcheck disable=SC1090
[ -r "$CONFIG" ] && . "$CONFIG"

: "${ROOST_BACKUP_REMOTE:=gdrive-crypt:}"
: "${ROOST_BACKUP_SUBDIR:=}"
: "${ROOST_BACKUP_DIR:=$HOME/roost/backups}"
: "${ROOST_DB_CONTAINER:=roost-db}"
: "${ROOST_DB_USER:=roost}"
: "${ROOST_DB_NAME:=roost}"
: "${ROOST_MEDIA_DIR:=$HOME/.local/share/containers/storage/volumes/roost-media/_data}"
: "${ROOST_APP_SERVICE:=roost-app.service}"
: "${RCLONE:=$HOME/bin/rclone}"
: "${RCLONE_CONFIG:=$HOME/.config/rclone/rclone.conf}"

REMOTE_DIR="${ROOST_BACKUP_REMOTE}${ROOST_BACKUP_SUBDIR}"

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

LIST_ONLY=0; FROM_REMOTE=0; STAMP=""; ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --list) LIST_ONLY=1 ;;
    --remote) FROM_REMOTE=1 ;;
    --stamp) STAMP="${2:-}"; shift ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

if [ "$LIST_ONLY" = 1 ]; then
  echo "Local restore points in $ROOST_BACKUP_DIR:"
  ls -1t "$ROOST_BACKUP_DIR"/roost-db-*.dump 2>/dev/null | sed -E 's#.*/roost-db-(.*)\.dump#  \1#' || true
  if [ "$FROM_REMOTE" = 1 ]; then
    echo "Remote (encrypted) restore points at $REMOTE_DIR:"
    "$RCLONE" --config "$RCLONE_CONFIG" lsf "$REMOTE_DIR" --files-only 2>/dev/null \
      | grep -E '^roost-db-' | LC_ALL=C sort \
      | sed -E 's#^roost-db-(.*)\.dump#  \1#' || true
  fi
  exit 0
fi

if [ -z "$STAMP" ]; then
  STAMP="$(ls -1t "$ROOST_BACKUP_DIR"/roost-db-*.dump 2>/dev/null | head -1 | sed -E 's#.*/roost-db-(.*)\.dump#\1#')"
fi
[ -n "$STAMP" ] || { echo "No local restore points found (use --list --remote to see Drive copies)." >&2; exit 1; }

DB="$ROOST_BACKUP_DIR/roost-db-$STAMP.dump"
MEDIA="$ROOST_BACKUP_DIR/roost-media-$STAMP.tar.gz"

if [ "$FROM_REMOTE" = 1 ]; then
  echo "Fetching $STAMP from $REMOTE_DIR ..."
  "$RCLONE" --config "$RCLONE_CONFIG" copy "$REMOTE_DIR/roost-db-$STAMP.dump" "$ROOST_BACKUP_DIR" --no-traverse
  "$RCLONE" --config "$RCLONE_CONFIG" copy "$REMOTE_DIR/roost-media-$STAMP.tar.gz" "$ROOST_BACKUP_DIR" --no-traverse
fi

[ -f "$DB" ] || { echo "Missing $DB" >&2; exit 1; }
[ -f "$MEDIA" ] || { echo "Missing $MEDIA" >&2; exit 1; }

echo "About to RESTORE Roost from restore point:"
echo "  DB dump : $DB"
echo "  Media   : $MEDIA"
echo
echo "This OVERWRITES the current database and media. The app will be stopped while it runs."
if [ "$ASSUME_YES" != 1 ]; then
  printf "Type 'restore' to continue: "
  read -r ans
  [ "$ans" = "restore" ] || { echo "aborted."; exit 1; }
fi

echo "==> Stopping $ROOST_APP_SERVICE"
systemctl --user stop "$ROOST_APP_SERVICE" || true

echo "==> Restoring database"
set +e
podman exec -i "$ROOST_DB_CONTAINER" pg_restore -U "$ROOST_DB_USER" -d "$ROOST_DB_NAME" \
  --clean --if-exists --no-owner --no-privileges < "$DB"
RC=$?
set -e
if [ "$RC" -ne 0 ]; then
  echo "WARNING: pg_restore reported issues (exit $RC) — check the output above."
fi

echo "==> Restoring media"
find "$ROOST_MEDIA_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
tar xzf "$MEDIA" -C "$ROOST_MEDIA_DIR"

echo "==> Starting $ROOST_APP_SERVICE"
systemctl --user start "$ROOST_APP_SERVICE"
sleep 3
systemctl --user is-active "$ROOST_APP_SERVICE"
echo "Restore complete."
