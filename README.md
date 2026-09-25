# 🚀 Hubble Exhibitor Platform

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
uvicorn app:app --reload --port 8000