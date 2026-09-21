#!/bin/sh
set -e

echo "==> Waiting for database + applying migrations"
i=0
until python manage.py migrate --noinput; do
    i=$((i + 1))
    if [ "$i" -ge 40 ]; then
        echo "database not reachable after $i attempts" >&2
        exit 1
    fi
    echo "  database not ready yet (attempt $i/40)…"
    sleep 2
done
echo "==> Migrations applied"

# Optional: create the first superuser non-interactively on first boot.
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    python manage.py createsuperuser --noinput || true
fi

echo "==> Starting gunicorn"
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile -
