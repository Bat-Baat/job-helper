"""Where everything is saved: settings, profile, master resume, and the jobs database.

All personal data lives in a folder called "JobHelper" in the user's home folder,
so downloading a new version of the app never wipes it.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("JOBHELPER_HOME", Path.home() / "JobHelper"))
RESUMES_DIR = DATA_DIR / "Tailored Resumes"
APPS_DIR = DATA_DIR / ".applications"
BROWSER_DIR = DATA_DIR / ".browser-profile"
for d in (DATA_DIR, RESUMES_DIR, APPS_DIR):
    d.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = DATA_DIR / "settings.json"
PROFILE_FILE = DATA_DIR / "profile.json"
RESUME_FILE = DATA_DIR / "master_resume.json"
DB_FILE = DATA_DIR / "jobs.db"
MEMORY_FILE = DATA_DIR / "learned_answers.json"
AUTOPILOT_LOG = DATA_DIR / "autopilot_log.txt"
AUTOPILOT_LAST = DATA_DIR / "autopilot_last_run.json"

DEFAULT_SETTINGS = {
    "api_key": "",
    "job_types": ["Internship"],          # Internship / New grad / Full-time
    "role_keywords": [
        "data scien", "machine learning", "ml engineer", "data analy", "analytics",
        "applied scien", "ai engineer", "decision scien", "statistic", "quantitative research",
        "business intelligence", "data engineer", "research scien", "ai/ml", "ml/ai", "deep learning",
        "nlp", "computer vision", "data intern", "quant research", "quant analyst", "quant modeling", "quant modelling",
    ],
    "exclude_keywords": ["senior", "sr.", "staff", "principal", "lead", "manager", "director", "phd"],
    "locations": [],                      # empty = anywhere in the US
    "remote_ok": True,
    "us_only": True,
    "sources": {"simplify": True, "company_boards": True, "indeed_linkedin": False},
    "max_scored_per_run": 40,
    "min_fit_score": 60,
    "auto_submit": False,
    "autopilot_enabled": False,
    "autopilot_time": "09:00",
    "autopilot_daily_cap": 50,
    "autopilot_min_score": 70,
    "write_cover_letters": False,
}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(_read(SETTINGS_FILE, {}))
    return s


def save_settings(s: dict) -> None:
    _write(SETTINGS_FILE, s)


def load_profile() -> dict:
    return _read(PROFILE_FILE, {})


def save_profile(p: dict) -> None:
    _write(PROFILE_FILE, p)


def load_resume() -> dict | None:
    return _read(RESUME_FILE, None)


def save_resume(r: dict) -> None:
    _write(RESUME_FILE, r)


# ---------------------------------------------------------------- remembered answers
# Every question the applicant answers once (in a browser window or in the app) is remembered,
# so the next application that asks it can be submitted without them.

def _qkey(label: str) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]+", "", (label or "").lower()).strip()[:300]


def load_memory() -> dict:
    return _read(MEMORY_FILE, {})


def remember_answer(label: str, text: str = "", choices: list[str] | None = None, options: list[str] | None = None) -> None:
    if not label or not (text or choices):
        return
    mem = load_memory()
    mem[_qkey(label)] = {"label": label, "text": text, "choices": choices or [], "options": options or [],
                         "updated": time.time()}
    _write(MEMORY_FILE, mem)


def forget_answer(key: str) -> None:
    mem = load_memory()
    mem.pop(key, None)
    _write(MEMORY_FILE, mem)


def recall_answer(label: str) -> dict | None:
    return load_memory().get(_qkey(label))


# ---------------------------------------------------------------- jobs database

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT, company TEXT, title TEXT, location TEXT,
    url TEXT, apply_url TEXT, ats TEXT,
    description TEXT, posted_at REAL,
    fit_score INTEGER, fit_reason TEXT,
    status TEXT DEFAULT 'new',
    resume_path TEXT, cover_letter TEXT, tailor_notes TEXT,
    applied_at REAL, created_at REAL, notes TEXT, applied_by TEXT
);
"""

STATUSES = ["new", "ready", "applied", "needs you", "skipped", "failed"]


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    if "applied_by" not in cols:  # databases created by older versions
        con.execute("ALTER TABLE jobs ADD COLUMN applied_by TEXT")
    return con


def upsert_jobs(jobs: list[dict]) -> int:
    """Insert jobs we haven't seen before. Returns how many were new."""
    con = db()
    new = 0
    with con:
        for j in jobs:
            # The same job is often listed under more than one link; never keep (or apply to) it twice.
            dup = con.execute("SELECT 1 FROM jobs WHERE lower(trim(company))=? AND lower(trim(title))=? AND id<>?",
                              (j["company"].strip().lower(), j["title"].strip().lower(), j["id"])).fetchone()
            if dup:
                continue
            cur = con.execute(
                "INSERT OR IGNORE INTO jobs (id, source, company, title, location, url, apply_url, ats,"
                " description, posted_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (j["id"], j["source"], j["company"], j["title"], j.get("location", ""), j["url"],
                 j.get("apply_url") or j["url"], j.get("ats", "other"), j.get("description", ""),
                 j.get("posted_at"), time.time()),
            )
            new += cur.rowcount
    con.close()
    return new


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    con = db()
    with con:
        cols = ", ".join(f"{k}=?" for k in fields)
        con.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
    con.close()


def get_job(job_id: str) -> dict | None:
    con = db()
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def list_jobs(statuses: list[str] | None = None) -> list[dict]:
    con = db()
    if statuses:
        q = f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(statuses))})"
        rows = con.execute(q + " ORDER BY COALESCE(fit_score,-1) DESC, posted_at DESC", statuses).fetchall()
    else:
        rows = con.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]
