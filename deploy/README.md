# Roost — homeserver deployment

Runs as an **isolated group** on the homeserver: its own rootless Podman
network, container names and volumes, kept separate from sonarr/radarr/jellyfin
etc. Managed by **Quadlet** user units (like the other services on this host).

## Files

| File | Purpose |
| --- | --- |
| `roost.network` | Dedicated Podman network `roost` |
| `roost-db.container` | Postgres 16, volume `roost-db-data` |
| `roost-app.container` | Django app, published on host port **8686** |
| `roost.env.example` | Template for `/home/paulkalenjuk/roost/config/roost.env` |

## First deploy

On the homeserver (as `paulkalenjuk`, rootless Podman):

```bash
# 1. dirs
mkdir -p ~/roost/{app,config,media}

# 2. source (rsync/scp the repo's backend/ into ~/roost/app/backend)

# 3. secrets — one shared env file, 0600
install -m 600 /dev/null ~/roost/config/roost.env
# fill in SECRET_KEY, POSTGRES_PASSWORD, etc. (see roost.env.example)

# 4. build the image (context = repo root, so the SPA gets built too)
podman build -f ~/roost/app/backend/Dockerfile -t localhost/roost:latest ~/roost/app

# 5. install the units
cp ~/roost/app/deploy/roost.network ~/.config/containers/systemd/
cp ~/roost/app/deploy/roost-db.container ~/.config/containers/systemd/
cp ~/roost/app/deploy/roost-app.container ~/.config/containers/systemd/

# 6. start
systemctl --user daemon-reload
systemctl --user start roost-db.service
systemctl --user start roost-app.service

# 7. status / logs
systemctl --user status roost-app.service
podman logs -f roost-app
```

Whatever triggers the first run also runs migrations (`entrypoint.sh`) and, if
`DJANGO_SUPERUSER_USERNAME`/`_PASSWORD` are set, creates the admin user.

## Isolation notes

- Network `roost` is user-defined, so these two containers *cannot* be reached by
  the other rootless containers except via published ports.
- Data lives in Podman volumes `roost-db-data` and `roost-media`
  (`~/.local/share/containers/storage/volumes/`). `roost-media` holds uploaded
  receipts/bills, asset images **and the retained Airbnb earnings-report PDFs**
  (`income_reports/…`) — back this volume up along with the database.
- Nothing is bind-mounted from `/Media` or the (currently degraded) `/Backups`
  array — DB backups should be added once `/Backups` is healthy again.

## Updating

```bash
rsync -a --delete --exclude node_modules --exclude backend/spa \
      --exclude __pycache__ --exclude .env ./ homeserver:~/roost/app/
ssh homeserver 'podman build -f ~/roost/app/backend/Dockerfile -t localhost/roost:latest ~/roost/app && systemctl --user restart roost-app.service'
```

(`roost-db` has `AutoUpdate=registry` so the Postgres image tracks upstream.)

## Backups

Nightly, **change-aware** backup: a Postgres dump (custom format, `pg_restore`-able)
plus a gzip tarball of the media volume, kept locally (last `ROOST_BACKUP_KEEP`,
default 3) and mirrored to an **encrypted** rclone remote (Google Drive by default).
If nothing has changed since the last successful backup the job does nothing — no
new copy, no upload.

Config lives in `~/roost/config/backup.env` (see `deploy/backup/backup.env.example`);
the Google Drive credentials live in the rclone config, not in the repo.

### One-time setup

```bash
# rclone (user-local, no root)
mkdir -p ~/bin && cd /tmp
curl -fsSL -o rclone.zip https://downloads.rclone.org/rclone-current-linux-amd64.zip
python3 - <<'PY'
import zipfile, glob, os, shutil
zipfile.ZipFile('/tmp/rclone.zip').extractall('/tmp/rclone-x')
shutil.copy2(glob.glob('/tmp/rclone-x/rclone-*/rclone')[0], os.path.expanduser('~/bin/rclone'))
PY
chmod +x ~/bin/rclone

# Google Drive remote + encrypted crypt remote. On a machine with a browser run
#   rclone authorize drive
# and paste the printed token into ~/.config/rclone/rclone.conf:
#   [gdrive]       type = drive  scope = drive  token = {...}
#   [gdrive-crypt] type = crypt  remote = gdrive:RoostBackups
#                  filename_encryption = standard  directory_name_encryption = true
#                  password = <obscured>  password2 = <obscured>

# config + systemd user units
install -m 600 deploy/backup/backup.env.example ~/roost/config/backup.env   # then edit
cp deploy/backup/roost-backup.service deploy/backup/roost-backup-manual.service \
   deploy/backup/roost-backup.timer deploy/backup/roost-backup.path ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now roost-backup.timer roost-backup.path
```

The timer fires at **02:30 Australia/Adelaide** daily; the script decides whether
there is anything to do.

### In the app

The **Backups** page (left menu) shows the log, the local and Drive restore points,
and the connected Drive account + quota. **Back up now** forces a run: it drops
`~/roost/backups/.run-now`, which the `roost-backup.path` unit watches and turns
into `roost-backup-manual.service`. **Change account** takes the token printed by
`rclone authorize "drive"`, tests it and saves it. The container reaches all this
through the bind mounts added to `roost-app.container` (the backups dir and the
rclone config).

### Restore

```bash
bash deploy/backup/roost-restore.sh --list --remote      # what's available
bash deploy/backup/roost-restore.sh --remote --stamp 20261005-065800
```

This stops `roost-app`, replaces the database and media, and starts the app again.

⚠️ **Keep the crypt password/salt safe** (they're in `rclone.conf`): without them
the encrypted Drive copies cannot be decrypted. Logs: `~/roost/backups/backup.log`
and `journalctl --user -u roost-backup.service`.
