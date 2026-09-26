"""FastAPI app entrypoint: CORS setup and route definitions."""

import sqlite3

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from database import get_db, init_db
from models import ConsultRequest
from scraper import run_scraper

app = FastAPI(title="Exhibitor Scraper API")

# --- CORS ---
# Needed if the browser ever calls this API directly (Swagger UI, Postman,
# or a client-side fetch). When Next.js proxies through its own Route
# Handlers (server calling server), CORS doesn't apply, but this keeps
# every other calling path working without extra config.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


@app.post("/api/scrape/exhibitors")
def trigger_scrape():
    """Triggers scraper synchronously and returns real execution feedback."""
    try:
        extracted_count = run_scraper(page_size=50, max_pages=2)
        if extracted_count > 0:
            return {
                "status": "success",
                "extracted_count": extracted_count,
                "message": f"Successfully scraped and upserted {extracted_count} exhibitors."
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Scraper ran but failed to extract any data. Check element selectors or site availability."
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[API ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/live-stats")
def get_live_stats():
    """Returns live exhibitor counts from the database for header/footer display."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM exhibitors")
            total_exhibitors = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT country_id) FROM exhibitors WHERE country_id IS NOT NULL")
            total_countries = cursor.fetchone()[0]

        return {
            "total_exhibitors": total_exhibitors,
            "total_countries": total_countries
        }
    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")


@app.post("/api/consult")
def submit_consult(payload: ConsultRequest):
    """Saves consultation form responses into the shared database."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO consultations (name, email, phone, company, message)
                VALUES (?, ?, ?, ?, ?)
            """, (payload.name, payload.email, payload.phone, payload.company, payload.message))
            conn.commit()
        return {"status": "success", "message": "Consultation request submitted successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.get("/api/exhibitors")
def get_exhibitors(limit: int = Query(default=6, ge=1, le=100)):
    """
    Returns { count, results } — `count` is the TOTAL number of exhibitor
    rows in the table (ignoring `limit`), so the frontend can show something
    like "6 of 42" instead of only ever seeing the page size back.

    NOTE: this previously used `Depends(init_db)` as if init_db() returned a
    connection — it doesn't (it returns None), which is what caused the
    500/404s. Each request now opens its own short-lived connection instead.
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM exhibitors")
            total_count = cursor.fetchone()[0]

            cursor.execute("""
                SELECT
                    exhibitors.id AS id,
                    exhibitors.name AS name,
                    exhibitors.profile_url AS profile_url,
                    countries.name AS country
                FROM exhibitors
                LEFT JOIN countries ON exhibitors.country_id = countries.id
                ORDER BY exhibitors.id DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            exhibitors = [dict(row) for row in rows]

        return {
            "count": total_count,
            "results": exhibitors
        }
    except sqlite3.OperationalError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}. Make sure the 'exhibitors' table exists."
        )