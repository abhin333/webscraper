"""SQLite connection handling, schema creation, and upsert logic."""

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List
from dotenv import load_dotenv
import os

load_dotenv()

DB_NAME = os.getenv("DB_NAME")




# --- Connection helper ---

@contextmanager
def get_db():
    """A single place that opens/closes SQLite connections safely, with
    row_factory set so query results come back as dict-like rows."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# --- Schema setup (3NF normalized) ---

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


# --- Upsert logic ---

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


# --- Read queries ---

def get_exhibitor_locations(event_slug: str = None, limit: int = 100, offset: int = 0):
    """
    Returns exhibitor_locations rows joined with exhibitor name/profile_url,
    country name, and event slug. Optionally filtered to a single event.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        base_query = """
            SELECT
                exhibitor_locations.id AS location_id,
                exhibitors.id AS exhibitor_id,
                exhibitors.name AS exhibitor_name,
                exhibitors.profile_url AS profile_url,
                countries.name AS country,
                events.slug AS event_slug,
                exhibitor_locations.hall_no AS hall_no,
                exhibitor_locations.booth_no AS booth_no
            FROM exhibitor_locations
            JOIN exhibitors ON exhibitor_locations.exhibitor_id = exhibitors.id
            JOIN events ON exhibitor_locations.event_id = events.id
            LEFT JOIN countries ON exhibitors.country_id = countries.id
        """

        params = []
        if event_slug:
            base_query += " WHERE events.slug = ?"
            params.append(event_slug)

        base_query += " ORDER BY exhibitor_locations.id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(base_query, params)
        rows = cursor.fetchall()

        # Total count for pagination, respecting the same event filter
        if event_slug:
            cursor.execute("""
                SELECT COUNT(*) FROM exhibitor_locations
                JOIN events ON exhibitor_locations.event_id = events.id
                WHERE events.slug = ?
            """, (event_slug,))
        else:
            cursor.execute("SELECT COUNT(*) FROM exhibitor_locations")
        total_count = cursor.fetchone()[0]

    return total_count, [dict(row) for row in rows]