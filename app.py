from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import os
import re
import time
from typing import List, Dict, Any, Optional
from contextlib import contextmanager
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from pydantic import BaseModel, EmailStr

DB_NAME = "exhibitors.db"
TARGET_BASE_URL = "https://mmiconnect.in/app/catalogue/exhibitors/ep-blr-2026"

app = FastAPI(title="Exhibitor Scraper API")

# --- 0. CORS ---
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


# --- 1. DATABASE CONNECTION HELPER ---
# A single place that opens/closes SQLite connections safely, with
# row_factory set so query results come back as dict-like rows.

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# --- 2. DATABASE SETUP (3NF Normalized Schema) ---

def init_db():
    print(f"[DB INIT] Using database path: {os.path.abspath(DB_NAME)}")
    with get_db() as conn:
        cursor = conn.cursor()

        # Countries Lookup
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS countries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)

        # Events Lookup
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL
            )
        """)

        # Exhibitors Core Entity
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS exhibitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                profile_url TEXT UNIQUE NOT NULL,
                country_id INTEGER,
                FOREIGN KEY (country_id) REFERENCES countries(id)
            )
        """)

        # Junction Table for Event Locations (3NF Mapping)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS exhibitor_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exhibitor_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                hall_no TEXT,
                booth_no TEXT,
                FOREIGN KEY (exhibitor_id) REFERENCES exhibitors(id),
                FOREIGN KEY (event_id) REFERENCES events(id),
                UNIQUE(exhibitor_id, event_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consultations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                company TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()


init_db()


# --- 3. UPSERT IMPLEMENTATION ---

def save_to_db_normalized(exhibitor_data: List[Dict[str, Any]], event_slug: str = "ep-blr-2026"):
    print(f"[DB SAVE] Saving {len(exhibitor_data)} records to SQLite...")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute("INSERT OR IGNORE INTO events (slug) VALUES (?)", (event_slug,))
        cursor.execute("SELECT id FROM events WHERE slug = ?", (event_slug,))
        event_id = cursor.fetchone()[0]

        upserted_count = 0

        for record in exhibitor_data:
            country_name = record.get("country", "").strip()
            country_id = None

            if country_name:
                cursor.execute("INSERT OR IGNORE INTO countries (name) VALUES (?)", (country_name,))
                cursor.execute("SELECT id FROM countries WHERE name = ?", (country_name,))
                res = cursor.fetchone()
                if res:
                    country_id = res[0]

            cursor.execute("""
                INSERT INTO exhibitors (name, profile_url, country_id)
                VALUES (:name, :profile_url, :country_id)
                ON CONFLICT(profile_url) DO UPDATE SET
                    name = excluded.name,
                    country_id = COALESCE(excluded.country_id, exhibitors.country_id)
                RETURNING id;
            """, {
                "name": record["name"],
                "profile_url": record["profile_url"],
                "country_id": country_id
            })
            exhibitor_id = cursor.fetchone()[0]

            cursor.execute("""
                INSERT INTO exhibitor_locations (exhibitor_id, event_id, hall_no, booth_no)
                VALUES (:exhibitor_id, :event_id, :hall_no, :booth_no)
                ON CONFLICT(exhibitor_id, event_id) DO UPDATE SET
                    hall_no = excluded.hall_no,
                    booth_no = excluded.booth_no;
            """, {
                "exhibitor_id": exhibitor_id,
                "event_id": event_id,
                "hall_no": record.get("hallNo", ""),
                "booth_no": record.get("boothNo", "")
            })
            upserted_count += 1

        conn.commit()

    print(f"[DB SAVE SUCCESS] {upserted_count} records upserted successfully.")
    return upserted_count


# --- 4. SCRAPING LOGIC WITH EXPLICIT WAITS ---

HALL_PATTERN = re.compile(r"Hall\s*(?:No\.?)?\s*[:\-]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
BOOTH_PATTERN = re.compile(r"(?:Booth|Stall)\s*(?:No\.?)?\s*[:\-]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)


def extract_hall_booth(text_block):
    hall_no, booth_no = "", ""
    hall_match = HALL_PATTERN.search(text_block)
    if hall_match:
        hall_no = hall_match.group(1).strip()
    booth_match = BOOTH_PATTERN.search(text_block)
    if booth_match:
        booth_no = booth_match.group(1).strip()
    return hall_no, booth_no


def run_scraper(page_size: int = 50, max_pages: int = 2):
    print("--- [STARTING SCRAPER ENGINE] ---")
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/118.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)
    all_exhibitors = []
    seen_urls = set()

    try:
        page = 0
        while page < max_pages:
            offset = page * page_size
            url = f"{TARGET_BASE_URL}?first={page_size}&skip={offset}"
            print(f"[SCRAPER] Navigating to page {page + 1}: {url}")
            driver.get(url)

            wait = WebDriverWait(driver, 15)
            try:
                wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'exhibitor-detail')]")))
                print("[SCRAPER] Page loaded successfully with exhibitor links.")
            except Exception:
                print(f"[SCRAPER WARNING] Timeout waiting for exhibitor elements on page {page + 1}.")

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

            links = driver.find_elements(By.TAG_NAME, "a")
            page_extracted = 0

            for link in links:
                try:
                    href = link.get_attribute("href")
                    if href and "exhibitor-detail" in href and href not in seen_urls:
                        seen_urls.add(href)
                        text = link.text.strip()
                        lines = [l.strip() for l in text.split("\n") if l.strip()]

                        name = lines[0] if lines else "Unknown"
                        country = lines[1] if len(lines) > 1 else ""
                        hall_no, booth_no = extract_hall_booth(text)

                        all_exhibitors.append({
                            "name": name,
                            "country": country,
                            "hallNo": hall_no,
                            "boothNo": booth_no,
                            "profile_url": href
                        })
                        page_extracted += 1
                except Exception:
                    continue

            print(f"[SCRAPER] Extracted {page_extracted} new items on page {page + 1}.")
            if page_extracted == 0:
                break

            page += 1

        if all_exhibitors:
            save_to_db_normalized(all_exhibitors)
            return len(all_exhibitors)
        else:
            print("[SCRAPER ERROR] No exhibitors extracted across any page.")
            return 0

    finally:
        driver.quit()
        print("--- [SCRAPER ENGINE CLOSED] ---")


# --- 5. API ENDPOINTS ---

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


class ConsultRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str
    company: Optional[str] = None
    message: Optional[str] = None


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