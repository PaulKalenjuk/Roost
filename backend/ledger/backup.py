"""Read and manage the Roost backups from inside the app.

The backup itself runs on the **host** (`deploy/backup/roost-backup.sh`, driven by
a systemd timer) so it keeps working even when this container is down. The
container only *sees* the results through bind mounts, and this module exposes
them to the UI:

* the restore points (locally, and on the encrypted remote),
* the backup log,
* the Google Drive account (which is configurable) and its quota,
* a "run now" request — dropping a sentinel file the host's systemd *path* unit
  watches, which then runs the backup.

Nothing here performs the backup; it is a thin, read-mostly view plus the two
write actions the UI needs (request a run, change the Drive account).
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

from django.conf import settings

# roost-db-YYYYmmdd-HHMMSS.dump / roost-media-YYYYmmdd-HHMMSS.tar.gz
_DB_PREFIX = "roost-db-"
_MEDIA_PREFIX = "roost-media-"
_DB_SUFFIX = ".dump"
_MEDIA_SUFFIX = ".tar.gz"


def _run(cmd, timeout=60):
    """Run a command, returning (rc, stdout, stderr) without ever raising."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]} timed out"
    except OSError as exc:  # pragma: no cover - defensive
        return 1, "", str(exc)


def _rclone(*args, timeout=60):
    return _run(
        [settings.ROOST_RCLONE, "--config", settings.ROOST_RCLONE_CONFIG, *args],
        timeout=timeout,
    )


def _stamp_from(name, prefix, suffix):
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix) : -len(suffix)] or None
    return None


def _iso_utc(stamp):
    try:
        return (
            datetime.strptime(stamp, "%Y%m%d-%H%M%S")
            .replace(tzinfo=timezone.utc)
            .isoformat()
        )
    except (TypeError, ValueError):
        return None


# --- restore points ---------------------------------------------------------
def local_restore_points():
    """The restore points present in the (bind-mounted) backup directory."""
    d = settings.ROOST_BACKUP_DIR
    if not os.path.isdir(d):
        return []
    points: dict[str, dict] = {}
    for name in os.listdir(d):
        stamp = _stamp_from(name, _DB_PREFIX, _DB_SUFFIX) or _stamp_from(
            name, _MEDIA_PREFIX, _MEDIA_SUFFIX
        )
        if not stamp:
            continue
        rec = points.setdefault(
            stamp, {"stamp": stamp, "db_bytes": 0, "media_bytes": 0}
        )
        size = os.path.getsize(os.path.join(d, name))
        if name.endswith(_DB_SUFFIX):
            rec["db_bytes"] = size
        else:
            rec["media_bytes"] = size
    return _decorate(points)


def remote_restore_points():
    """The restore points on the remote, or None if the remote is unreachable."""
    rc, out, err = _rclone(
        "lsjson", f"{settings.ROOST_BACKUP_REMOTE}:", "--files-only"
    )
    if rc != 0:
        return None
    try:
        items = json.loads(out or "[]")
    except json.JSONDecodeError:
        return None
    points: dict[str, dict] = {}
    for item in items:
        name = item.get("Name", "")
        stamp = _stamp_from(name, _DB_PREFIX, _DB_SUFFIX)
        key = "db_bytes"
        if not stamp:
            stamp = _stamp_from(name, _MEDIA_PREFIX, _MEDIA_SUFFIX)
            key = "media_bytes"
        if not stamp:
            continue
        rec = points.setdefault(
            stamp, {"stamp": stamp, "db_bytes": 0, "media_bytes": 0}
        )
        rec[key] = item.get("Size", 0)
    return _decorate(points)


def _decorate(points: dict[str, dict]):
    out = []
    for rec in points.values():
        rec["created"] = _iso_utc(rec["stamp"])
        rec["complete"] = bool(rec["db_bytes"] and rec["media_bytes"])
        out.append(rec)
    out.sort(key=lambda r: r["stamp"], reverse=True)
    return out


# --- account / remote -------------------------------------------------------
def account():
    try:
        with open(settings.ROOST_RCLONE_ACCOUNT) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _save_account(data):
    path = settings.ROOST_RCLONE_ACCOUNT
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def drive_about():
    rc, out, err = _rclone("about", f"{settings.ROOST_DRIVE_REMOTE}:", "--json")
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _fetch_email(access_token):
    """Best-effort: the Drive account's email/name for the given access token."""
    if not access_token:
        return None, None
    import urllib.request

    try:
        req = urllib.request.Request(
            "https://www.googleapis.com/drive/v3/about"
            "?fields=user(emailAddress,displayName)",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            user = json.load(resp).get("user", {})
        return user.get("emailAddress"), user.get("displayName")
    except Exception:  # noqa: BLE001 - best effort only
        return None, None


# --- log / run state --------------------------------------------------------
def log_tail(lines=200):
    try:
        with open(settings.ROOST_BACKUP_LOG, errors="replace") as fh:
            data = fh.read().splitlines()
    except OSError:
        return []
    return data[-lines:]


def is_running():
    return os.path.exists(os.path.join(settings.ROOST_BACKUP_DIR, ".running"))


def request_run():
    """Drop the sentinel the host's systemd path unit watches."""
    if is_running():
        return False, "A backup is already running."
    try:
        os.makedirs(settings.ROOST_BACKUP_DIR, exist_ok=True)
        open(os.path.join(settings.ROOST_BACKUP_DIR, ".run-now"), "w").close()
    except OSError as exc:
        return False, f"Could not request a backup: {exc}"
    return True, "Backup requested — the host will pick it up within a few seconds."


def change_account(token_json):
    """Point the Drive remote at a different account using a pasted rclone token."""
    try:
        token = json.loads(token_json)
    except (TypeError, json.JSONDecodeError):
        return False, "That doesn't look like the JSON token rclone prints."
    if not token.get("refresh_token"):
        return False, "The token is missing a refresh_token."

    remote = settings.ROOST_DRIVE_REMOTE
    conf_path = settings.ROOST_RCLONE_CONFIG
    try:
        with open(conf_path) as fh:
            original = fh.read()
        with open(f"{conf_path}.bak", "w") as fh:
            fh.write(original)
    except OSError:
        original = None

    rc, out, err = _rclone(
        "config", "update", remote, "token", json.dumps(token), "--non-interactive"
    )
    if rc != 0:
        if original is not None:
            with open(conf_path, "w") as fh:
                fh.write(original)
        return False, f"rclone couldn't save the token: {(err or out).strip()}"

    rc, out, err = _rclone("about", f"{remote}:", "--json")
    if rc != 0:
        if original is not None:
            with open(conf_path, "w") as fh:
                fh.write(original)
        return False, f"The new account didn't respond: {(err or out).strip()}"

    email, display = _fetch_email(token.get("access_token"))
    _save_account(
        {"email": email, "displayName": display, "changed_at": datetime.now(timezone.utc).isoformat()}
    )
    return True, email or "Account updated."


def status():
    return {
        "running": is_running(),
        "remote": f"{settings.ROOST_BACKUP_REMOTE}:",
        "drive_remote": f"{settings.ROOST_DRIVE_REMOTE}:",
        "keep": settings.ROOST_BACKUP_KEEP,
        "schedule": settings.ROOST_BACKUP_SCHEDULE,
        "account": account(),
        "quota": drive_about(),
        "local": local_restore_points(),
        "remote_points": remote_restore_points(),
    }
