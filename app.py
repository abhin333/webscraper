from fastapi import FastAPI, HTTPException
import sqlite3
import os
import re
import time
from typing import List, Dict, Any
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

DB_NAME = "exhibitors.db"
TARGET_BASE_URL = "https://mmiconnect.in/app/catalogue/exhibitors/ep-blr-2026"

app = FastAPI(title="Exhibitor Scraper API")

# --- 1. DATABASE SETUP (3NF Normalized Schema) ---

def init_db():
    print(f"[DB INIT] Using database path: {os.path.abspath(DB_NAME)}")
    conn = sqlite3.connect(DB_NAME)
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
    conn.commit()
    conn.close()

init_db()

# --- 2. UPSERT IMPLEMENTATION ---

def save_to_db_normalized(exhibitor_data: List[Dict[str, Any]], event_slug: str = "ep-blr-2026"):
    print(f"[DB SAVE] Saving {len(exhibitor_data)} records to SQLite...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    # Ensure event exists
    cursor.execute("INSERT OR IGNORE INTO events (slug) VALUES (?)", (event_slug,))
    cursor.execute("SELECT id FROM events WHERE slug = ?", (event_slug,))
    event_id = cursor.fetchone()[0]

    upserted_count = 0

    for record in exhibitor_data:
        country_name = record.get("country", "").strip()
        country_id = None
        
        # 1. Upsert Country
        if country_name:
            cursor.execute("INSERT OR IGNORE INTO countries (name) VALUES (?)", (country_name,))
            cursor.execute("SELECT id FROM countries WHERE name = ?", (country_name,))
            res = cursor.fetchone()
            if res:
                country_id = res[0]

        # 2. Upsert Exhibitor Core Entity
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

        # 3. Upsert Event Location Detail
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
    conn.close()
    print(f"[DB SAVE SUCCESS] {upserted_count} records upserted successfully.")
    return upserted_count

# --- 3. SCRAPING LOGIC WITH EXPLICIT WAITS ---

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

            # Wait for elements to load
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
                except Exception as e:
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

# --- 4. API ENDPOINT ---

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
    except Exception as e:
        print(f"[API ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))