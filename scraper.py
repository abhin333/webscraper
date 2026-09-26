"""Selenium-based scraper for exhibitor listings, with explicit waits."""

import re
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import TARGET_BASE_URL
from database import save_to_db_normalized

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