"""The steps the app runs: find + score jobs, tailor a resume, and launch the auto-fill window."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from . import llm, render, sources, store

ROOT = Path(__file__).resolve().parent.parent


def find_and_score(progress=lambda msg, frac=None: None) -> dict:
    settings, profile, resume = store.load_settings(), store.load_profile(), store.load_resume()
    found = sources.find_jobs(settings, lambda m: progress(m, None))
    new = store.upsert_jobs(found)
    to_score = [j for j in store.list_jobs(["new"]) if j["fit_score"] is None][: int(settings["max_scored_per_run"])]
    errors = 0
    for i, job in enumerate(to_score):
        progress(f"Reading and rating job {i + 1} of {len(to_score)}: {job['title']} at {job['company']}",
                 (i + 1) / max(1, len(to_score)))
        desc = job["description"] or sources.fetch_description(job)
        try:
            fit = llm.score_job(settings, resume, profile, job, desc or job["title"])
        except Exception as e:  # noqa: BLE001
            errors += 1
            if errors >= 3:
                raise RuntimeError(f"Rating jobs keeps failing: {e}") from e
            continue
        store.update_job(job["id"], description=desc, fit_score=fit["score"], fit_reason=fit["reason"],
                         status="new" if fit["score"] >= int(settings["min_fit_score"]) else "skipped")
    return {"found": len(found), "new": new, "scored": len(to_score)}


def prepare(job_id: str) -> dict:
    """Tailor the resume for one job and save the PDF. Returns the job row."""
    settings, profile, resume = store.load_settings(), store.load_profile(), store.load_resume()
    job = store.get_job(job_id)
    desc = job["description"] or sources.fetch_description(job)
    tailored, info = llm.tailor(settings, resume, profile, job, desc or job["title"])
    name = render.safe_filename(f"{resume['name']} - {job['company']} - {job['title']}")
    pdf = render.render_pdf(tailored, store.RESUMES_DIR / f"{name}.pdf")
    out_dir = store.APPS_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    if info.get("cover_letter"):
        render.render_cover_letter(resume, job, info["cover_letter"], out_dir / "Cover_Letter.pdf")
    store.update_job(job_id, description=desc, resume_path=str(pdf), cover_letter=info.get("cover_letter", ""),
                     tailor_notes=json.dumps({"resume": tailored, "changes": info["changes"],
                                              "missing_keywords": info["missing_keywords"]}),
                     status="ready")
    return store.get_job(job_id)


def launch_application(job_id: str, auto_submit: bool) -> dict:
    """Opens the browser window and waits until the person closes it."""
    cmd = [sys.executable, "-m", "jobbot.apply", job_id] + (["--auto-submit"] if auto_submit else [])
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    res_file = store.APPS_DIR / job_id / "result.json"
    result = json.loads(res_file.read_text()) if res_file.exists() else {"submitted": False, "issues": []}
    if proc.returncode != 0 and not res_file.exists():
        result["issues"] = [f"The application window crashed: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'unknown error'}"]
        store.update_job(job_id, status="failed", notes=result["issues"][0])
        return result
    if result.get("submitted"):
        store.update_job(job_id, status="applied", applied_at=time.time())
    else:
        store.update_job(job_id, status="needs you", notes="; ".join(result.get("issues", []))[:1000])
    return result
