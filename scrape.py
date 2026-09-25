from math import log
import re
import sqlite3
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Target URL
TARGET_URL = "https://mmiconnect.in/app/catalogue/exhibitors/ep-blr-2026?first=100"
DB_NAME = "exhibitors.db"

# Regex patterns to pull hall/booth numbers out of free text.
# Covers formats like "Hall No: 3", "Hall 3A", "Booth No. B-102", "Stall: 45", etc.
HALL_PATTERN = re.compile(r"Hall\s*(?:No\.?)?\s*[:\-]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
BOOTH_PATTERN = re.compile(r"(?:Booth|Stall)\s*(?:No\.?)?\s*[:\-]?\s*([A-Za-z0-9\-]+)", re.IGNORECASE)


def setup_database():
    """Initialize SQLite Database and Create Exhibitors Table."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exhibitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            country TEXT,
            hallNo TEXT,
            boothNo TEXT,
            event TEXT,
            profile_url TEXT UNIQUE
        )
    """)
    conn.commit()
    conn.close()

def save_to_db(exhibitor_data):
    print(f"dbdtaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa{exhibitor_data}")
    """Save extracted exhibitor records into SQLite database."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    inserted_count = 0
    for record in exhibitor_data:
        try:
            # Map dictionary keys explicitly so hall -> hallNo and booth -> boothNo
            payload = {
                "name": record.get("name", ""),
                "country": record.get("country", ""),
                "hallNo": record.get("hall") or record.get("hallNo", ""),
                "boothNo": record.get("booth") or record.get("boothNo", ""),
                "event": record.get("event", "ep-blr-2026"),
                "profile_url": record.get("profile_url", "")
            }

            cursor.execute("""
                INSERT OR IGNORE INTO exhibitors (name, country, hallNo, boothNo, event, profile_url)
                VALUES (:name, :country, :hallNo, :boothNo, :event, :profile_url)
            """, payload)

            if cursor.rowcount > 0:
                inserted_count += 1
        except sqlite3.Error as e:
            print(f"Database error: {e}")

    conn.commit()
    conn.close()
    return inserted_count   


def extract_hall_booth(text_block, link_element):
    """
    Dynamically pull hallNo/boothNo instead of hardcoding them.

    Strategy 1: search the link's own visible text for "Hall"/"Booth" patterns.
    Strategy 2 (fallback): if not found in the link text, walk up to the
    surrounding card container and search any elements whose class name
    hints at hall/booth/stall, since some sites put these in separate
    sibling spans rather than inside the anchor text.
    """
    hall_no, booth_no = "", ""

    hall_match = HALL_PATTERN.search(text_block)
    if hall_match:
        hall_no = hall_match.group(1).strip()

    booth_match = BOOTH_PATTERN.search(text_block)
    if booth_match:
        booth_no = booth_match.group(1).strip()

    # Fallback: look at the parent card container for dedicated hall/booth fields
    if not hall_no or not booth_no:
        try:
            # Go up a couple of levels to the likely card wrapper
            container = link_element.find_element(By.XPATH, "./ancestor::*[contains(@class,'card')][1]")
        except Exception:
            container = None

        if container is not None:
            if not hall_no:
                hall_candidates = container.find_elements(
                    By.XPATH, ".//*[contains(translate(@class,'HALL','hall'),'hall')]"
                )
                for el in hall_candidates:
                    m = HALL_PATTERN.search(el.text) or re.search(r"[A-Za-z0-9\-]+", el.text.strip())
                    if m:
                        hall_no = m.group(1) if m.groups() else m.group(0)
                        break

            if not booth_no:
                booth_candidates = container.find_elements(
                    By.XPATH,
                    ".//*[contains(translate(@class,'BOOTHSTALL','boothstall'),'booth') "
                    "or contains(translate(@class,'BOOTHSTALL','boothstall'),'stall')]"
                )
                for el in booth_candidates:
                    m = BOOTH_PATTERN.search(el.text) or re.search(r"[A-Za-z0-9\-]+", el.text.strip())
                    if m:
                        booth_no = m.group(1) if m.groups() else m.group(0)
                        break

    return hall_no, booth_no


def scrape_exhibitors():
    """Scrape exhibitor records using Selenium headless browser."""
    print("Setting up Headless Browser...")
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # Setting user agent to prevent bot rejection
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)

    try:
        print(f"Loading page: {TARGET_URL}")
        driver.get(TARGET_URL)

        # Wait up to 15 seconds for card items or exhibitor listing containers to load
        wait = WebDriverWait(driver, 15)

        # Scroll down to ensure dynamic content finishes rendering
        time.sleep(5)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)

        # Locate exhibitor elements (adapt selector based on rendered structure)
        cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'card') or contains(@class, 'exhibitor')] | //a[contains(@href, 'exhibitor-detail')]")

        exhibitors_list = []

        print(f"Found {len(cards)} exhibitor entry candidates. Processing...")

        all_links = driver.find_elements(By.TAG_NAME, "a")

        for link in all_links:
            href = link.get_attribute("href")
            if href and "exhibitor-detail" in href:
                text = link.text.strip()
                lines = [line.strip() for line in text.split("\n") if line.strip()]

                name = lines[0] if lines else "Unknown"
                country = lines[1] if len(lines) > 1 else ""

                # Dynamically resolve hall/booth instead of hardcoding ""
                hall_no, booth_no = extract_hall_booth(text, link)

                print(f"Parsed exhibitor: {name!r} | hall={hall_no!r} booth={booth_no!r}")

                exhibitor_data = {
                    "name": name,
                    "country": country,
                    "hallNo": hall_no,
                    "boothNo": booth_no,
                    "event": "ep-blr-2026",
                    "profile_url": href
                }
                exhibitors_list.append(exhibitor_data)

        print(f"Successfully scraped {len(exhibitors_list)} items.")
        return exhibitors_list

    finally:
        driver.quit()

if __name__ == "__main__":
    setup_database()
    scraped_data = scrape_exhibitors()
 
    if scraped_data:
        saved = save_to_db(scraped_data)
        print(f"Done! {saved} new exhibitor records stored in '{DB_NAME}'.")
    else:
        print("No data extracted. Verify network connectivity or inspect element selectors.")