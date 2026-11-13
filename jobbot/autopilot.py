"""Autopilot: find jobs, tailor resumes, and submit applications with nobody at the keyboard.

Runs once a day from the computer's scheduler (see schedule.py), or from the app's "Run now" button:
    python -m jobbot.autopilot

Only Greenhouse, Lever and Ashby applications are submitted automatically, and only when every required
question had a confident answer and no CAPTCHA appeared. Everything else is left in the queue for the person,
with the exact questions it needs. Answers they give are remembered, so those jobs go through next time.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import traceback
from datetime import datetime

from . import pipeline, store
from .apply import AUTO_SUBMIT_ATS

LOCK = store.DATA_DIR / ".autopilot.lock"


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M}] {msg}"
    print(line, flush=True)
    with open(store.AUTOPILOT_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def blocked_questions(job_id: str) -> list[dict]:
    f = store.APPS_DIR / job_id / "result.json"
    try:
        return json.loads(f.read_text()).get("blocked_questions", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def unblocked(job: dict) -> bool:
    """A job that needed the person earlier, but whose questions now all have remembered answers."""
    qs = blocked_questions(job["id"])
    return bool(qs) and all(store.recall_answer(q["label"]) for q in qs)


def applied_today() -> int:
    start = datetime.now().replace(hour=0, minute=0, second=0).timestamp()
    return sum(1 for j in store.list_jobs(["applied"]) if (j["applied_at"] or 0) >= start)


def run() -> dict:
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 14 * 3600:
        log("Autopilot is already running – skipping.")
        return {}
    LOCK.write_text(str(os.getpid()))
    summary = {"started": time.time(), "submitted": [], "needs_you": [], "errors": []}
    try:
        settings = store.load_settings()
        if not (settings.get("api_key") and store.load_resume() and store.load_profile().get("answers")):
            log("Set-up isn't finished (Claude key, resume, questions). Nothing to do.")
            return summary
        log("Autopilot started. Looking for new jobs…")
        cap_total = int(settings["autopilot_daily_cap"])
        # Rate enough new postings to fill today's limit (roughly half of rated jobs pass the match bar).
        found = pipeline.find_and_score(lambda m, f=None: None,
                                        limit=max(int(settings["max_scored_per_run"]), 2 * cap_total))
        summary.update(found)
        log(f"Checked {found['found']} postings, {found['new']} new, rated {found['scored']}.")

        cap = cap_total - applied_today()
        min_score = int(settings["autopilot_min_score"])
        queue = [j for j in store.list_jobs(["new", "ready"])
                 if (j["fit_score"] or 0) >= min_score and j["ats"] in AUTO_SUBMIT_ATS]
        queue += [j for j in store.list_jobs(["needs you"]) if j["ats"] in AUTO_SUBMIT_ATS and unblocked(j)]
        queue.sort(key=lambda j: -(j["fit_score"] or 0))
        if cap <= 0:
            log("Daily limit reached – no more applications today.")
            queue = []
        for job in queue[:max(0, cap)]:
            name = f"{job['company']} – {job['title']}"
            try:
                if job["status"] not in ("ready", "needs you") or not job.get("resume_path"):
                    log(f"Tailoring resume for {name}")
                    pipeline.prepare(job["id"])
                log(f"Applying to {name}")
                res = pipeline.launch_application(job["id"], auto_submit=True, wait=False)
                if res.get("submitted"):
                    summary["submitted"].append(name)
                    log(f"  ✅ Submitted {name}")
                else:
                    summary["needs_you"].append({"job": name, "why": res.get("issues", [])[:5]})
                    log(f"  ✋ Needs you: {name} – {'; '.join(res.get('issues', [])[:3])}")
            except Exception as e:  # noqa: BLE001
                summary["errors"].append(f"{name}: {e}")
                store.update_job(job["id"], status="failed", notes=str(e)[:500])
                log(f"  ⚠️ {name}: {e}")
            time.sleep(random.uniform(10, 30))  # pace applications instead of firing them back to back

        waiting = [j for j in store.list_jobs(["new", "ready"])
                   if (j["fit_score"] or 0) >= min_score and j["ats"] not in AUTO_SUBMIT_ATS]
        summary["manual_waiting"] = len(waiting)
        log(f"Done: {len(summary['submitted'])} submitted, {len(summary['needs_you'])} need you, "
            f"{len(waiting)} good matches on other sites waiting for you.")
    except Exception as e:  # noqa: BLE001
        summary["errors"].append(str(e))
        log(f"Autopilot stopped with an error: {e}\n{traceback.format_exc()}")
    finally:
        summary["finished"] = time.time()
        store.AUTOPILOT_LAST.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        LOCK.unlink(missing_ok=True)
    return summary


if __name__ == "__main__":
    run()
    sys.exit(0)
