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
