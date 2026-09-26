<!-- # 🚀 Hubble Exhibitor Platform

A full-stack web application built with **Next.js (App Router)** and **FastAPI (Python/SQLite)**. The platform dynamically fetches live scraper statistics and exhibitor records from a Python SQLite database and renders them in an interactive Next.js frontend with live dropdown metrics and real-time status updates.

---

## ⚡ Quick Start (TL;DR)

If you already have Python and Node.js installed, run these commands in two separate terminal windows:

### Terminal 1: Backend (FastAPI)
```bash
cd exhibitor-scraper
python -m venv venv
# On Windows: venv\Scripts\activate | On Mac/Linux: source venv/bin/activate
pip install fastapi uvicorn "pydantic[email]" email-validator
uvicorn main:app --reload --port 8000 -->


# Backend — Exhibitor Scraper API

FastAPI service that scrapes exhibitor listings with Selenium, stores them in
a normalized SQLite database, and serves them to the frontend.

## 1. Project setup

### Docker Compose (preferred, from repo root)

```bash
cp backend/.env.example backend/.env
docker compose up --build backend
```

The backend will be available at `http://localhost:8000`
(Swagger UI at `http://localhost:8000/docs`).

### Manual setup (without Docker)

Requires Python 3.11+ and Google Chrome installed locally (Selenium drives a
real headless Chrome instance).

```bash
cd EXHIBITOR-SCRAPER
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # adjust values if needed
uvicorn main:app --reload
```

### Environment variables (`.env`)

| Variable          | Default                                                          | Description                          |
|-------------------|-------------------------------------------------------------------|---------------------------------------|
| `DB_NAME`         | `exhibitors.db`                                                   | SQLite file path                     |
| `TARGET_BASE_URL` | `https://mmiconnect.in/app/catalogue/exhibitors/ep-blr-2026`       | Exhibitor listing page to scrape     |

**Troubleshooting:** `.env` must sit directly next to `config.py` (i.e. in
`backend/`), be named exactly `.env` (not `.env.txt`), use `KEY=value` with
no quotes, and the server must be restarted after any change — `.env` is
only read once at startup, so `--reload` won't pick up edits to it.

## 2. Table structure

The schema is normalized to 3NF: no repeated text (country names, event
slugs) is stored redundantly on every exhibitor row, and each table holds
data about exactly one kind of entity.

### `countries`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT, UNIQUE | e.g. `"India"`, `"Germany"` |

A lookup table. Exhibitors reference a country by ID instead of storing the
country string on every row, so the same country name is never duplicated
and can be renamed/corrected in one place.

### `events`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `slug` | TEXT, UNIQUE | e.g. `"ep-blr-2026"` |

A lookup table for the exhibition/event itself. Kept separate because an
exhibitor can appear at more than one event over time, and hall/booth
numbers are *per event*, not a fixed property of the exhibitor.

### `exhibitors`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | |
| `profile_url` | TEXT, UNIQUE | Used as the natural key for upserts |
| `country_id` | INTEGER, FK → `countries.id` | Nullable |

The core entity: one row per unique exhibitor, identified by their profile
URL (stable across scrape runs, so re-scraping upserts instead of
duplicating).

### `exhibitor_locations` (junction table)
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `exhibitor_id` | INTEGER, FK → `exhibitors.id` | |
| `event_id` | INTEGER, FK → `events.id` | |
| `hall_no` | TEXT | |
| `booth_no` | TEXT | |
| — | UNIQUE(`exhibitor_id`, `event_id`) | One location per exhibitor per event |

This resolves the many-to-many relationship between exhibitors and events:
the same exhibitor can attend multiple events with a *different* hall/booth
each time, and this table is the only place hall/booth numbers live — they
are not duplicated onto the `exhibitors` table itself.

### `consultations`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | |
| `email` | TEXT | |
| `phone` | TEXT | |
| `company` | TEXT | Nullable |
| `message` | TEXT | Nullable |
| `created_at` | TIMESTAMP | Defaults to insert time |

Standalone table for the site's "Consult Now" form submissions; unrelated to
the exhibitor scraping data.

**Why normalize this way:** without the `countries`/`events` lookup tables
and the `exhibitor_locations` junction table, every exhibitor row would
repeat its country name as free text and could only ever hold one
hall/booth pair — making it impossible to correctly track the same
exhibitor across multiple events, and prone to inconsistent spelling of
country/event names.

## 3. How to trigger the scraper

Trigger a scrape synchronously via:

```bash
curl -X POST http://localhost:8000/api/scrape/exhibitors
```

Response:
```json
{
  "status": "success",
  "extracted_count": 84,
  "message": "Successfully scraped and upserted 84 exhibitors."
}
```

This is a synchronous, blocking call — it runs headless Chrome, scrapes up
to `max_pages` pages of the listing, and upserts into SQLite before
responding. There's no auth on this endpoint currently, so lock it down
(API key, admin-only route, or a scheduled job instead of a public endpoint)
before deploying it publicly.

## 4. Other endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/exhibitors?limit=` | GET | Paginated exhibitor list with country |
| `/api/exhibitor-locations?event_slug=&limit=&offset=` | GET | Exhibitors joined with hall/booth/event |
| `/api/live-stats` | GET | Total exhibitor and country counts |
| `/api/consult` | POST | Submit a consultation form entry |