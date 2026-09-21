# Roost 🪺

Self-hosted income & expense tracker for short-stay rentals (Airbnb).
**Completely separate** from FestivalApp — own repo, database, containers and
config.

## What it does

- **Income** — pull Airbnb earnings into the ledger from the **earnings-report
  PDF** (monthly totals) and/or the **transaction-history CSV** (per reservation).
- **Expenses** — operating costs with **receipt attachments**.
- **Utilities** — the claimable portion is worked out from the **percentage of
  the dwelling that is let** (floor-area based, or a manual override).
- **Depreciating assets** — asset register with prime-cost / diminishing-value
  schedules, effective life and business-use %.
- **Owners** — any number of owners, each with a share that must total 100%.
- **Reporting** — per financial year (AU FY, 1 Jul – 30 Jun) *and per owner*,
  breaking the calculation down.

## Airbnb data — the important bit

Airbnb does **not** offer a public, self-serve API for hosts. Official access is
only via the partner **API Program** (property-management systems). So Roost is
built around a pluggable **importer layer**:

| Importer | Status | Input |
| --- | --- | --- |
| `airbnb_pdf` | **works** | *Payments → Earnings → Download report* **PDF** → monthly totals (overwrites on re-import) |
| `airbnb_csv` | **works** | *Payments → Transaction history* **CSV** → per-reservation rows |
| `airbnb_browser` | later | Playwright auto-download of the same files |

Everything downstream is source-agnostic, so adding/replacing an importer never
touches the ledger model.

### Importing

```bash
# earnings report PDF (monthly totals; re-import overwrites the month)
python manage.py import_airbnb_pdf "/path/report.pdf" --listing "My Listing"

# transaction history CSV (per reservation; idempotent on confirmation code)
python manage.py import_airbnb_csv "/path/transactions.csv" --listing "My Listing"
```

## Reporting

```bash
# property + FY (use 'current' for today's FY); --owners adds the split
python manage.py fy_report --property "My Property" --fy FY2025-26 --owners
python manage.py fy_report --property "My Property" --fy FY2025-26 --recompute-depreciation
```

Output shows income by month, claimable expenses by category (with the
apportionment used), depreciation per asset, the net rental result, and — with
`--owners` — each owner's share of every line.

## Tax notes

- Let share defaults to `rental_floor_area / total_floor_area`, overridable with
  an explicit `let_percentage`.
- Depreciation: prime cost or diminishing value, pro-rated for days held.
  ATO reference is shown next to the setting:
  <https://www.ato.gov.au/individuals-and-families/investments-and-assets/property-and-land/residential-rental-properties>
- `gst_registered` is a per-property flag; GST amounts can be recorded on
  expenses.

## Stack

Django 5 · Postgres 16 · Gunicorn · WhiteNoise. Django admin is the MVP UI.

## Local dev

```bash
cp .env.example .env          # set SECRET_KEY + POSTGRES_PASSWORD
docker compose up --build     # db + app on http://localhost:8686
docker compose exec app python manage.py createsuperuser
```

SQLite fallback (no Docker):

```bash
cd backend
pip install -r requirements.txt
DB_ENGINE=sqlite python manage.py migrate
DB_ENGINE=sqlite python manage.py createsuperuser
DB_ENGINE=sqlite python manage.py runserver 8686
```

Tests: `DB_ENGINE=sqlite python manage.py test ledger`

## Deploy (homeserver)

Runs as an **isolated group** on the homeserver: rootless Podman with Quadlet
units, its own network (`roost.network`) and volumes, port **8686**. See
`deploy/README.md`.

## Front-end (TypeScript SPA)

`frontend/` is a **Vite + React + TypeScript** app with five sections:

| Section | What it does |
| --- | --- |
| **Property setup** | dwelling, floor areas / let %, GST flag, depreciation-method default (ATO link), owners + shares, listings |
| **Income** | import the Airbnb earnings **PDF** (monthly totals, overwrites), view monthly earnings + reservations |
| **Expenses** | two tabs — **Ad hoc** (with receipt upload) and **Utilities** |
| **Depreciating assets** | asset register + built schedules, receipt upload, rebuild schedules |
| **Reporting** | per-FY report with the maths shown, optional per-owner split |

Utilities: create a **utility type** (name, category, frequency — monthly/quarterly/
half-yearly/yearly), then upload each **bill**. The claimable portion is
calculated automatically from the let share.

### Reading bills with DeepSeek (optional)

Set `DEEPSEEK_KEY` in the environment and each bill can be parsed: the PDF text
is extracted with `pdfplumber`, then DeepSeek returns the amount, GST, dates and
supplier as validated JSON. Scanned/photographed bills have no text layer — the
UI reports that and you enter the amount manually. Without a key, the feature is
simply unavailable and everything else works.

## API

Session-authenticated (the same login as `/admin/`) with DRF:

```
/api/auth/{csrf,login,logout,me}/
/api/properties/ /api/owners/ /api/ownerships/ /api/listings/ /api/categories/
/api/expenses/?kind=adhoc|utility  /api/utility-types/ /api/utility-bills/
/api/assets/ (+ /recompute/)  /api/reservations/ /api/monthly-earnings/
/api/receipts/ /api/imports/
/api/income/import-pdf/        # multipart: file + listing
/api/utilities/extract/        # multipart: file -> parsed bill fields (DeepSeek)
/api/reports/fy/?property=&fy=&owners=1&recompute=1
```

## Layout

```
roost/
  docker-compose.yml          # dev/local
  deploy/                     # Quadlet units + notes for the homeserver
  frontend/                   # Vite + React + TS SPA (builds into backend/spa)
  backend/
    config/                   # settings, urls, wsgi/asgi
    spa/                      # built SPA (generated; served by WhiteNoise)
    ledger/
      models.py               # Property, Listing, Owner, PropertyOwnership,
                              # Reservation, MonthlyEarnings, Category, Expense,
                              # Receipt, Asset, DepreciationEntry, UtilityType,
                              # UtilityBill, ImportBatch
      serializers.py          # DRF serializers
      api.py / api_urls.py    # REST API
      reports.py              # per-FY + per-owner reporting
      apportionment.py        # let-share / deductibility maths
      depreciation.py         # prime-cost & diminishing-value schedules
      fiscal.py               # AU financial-year helpers
      constants.py            # ATO links
      services/bill_extract.py# PDF text + DeepSeek -> validated bill fields
      importers/
        airbnb_pdf.py         # earnings-report PDF → monthly totals
        airbnb_csv.py         # transaction CSV → reservations
      tests.py                # regression tests
```
