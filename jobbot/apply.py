"""Opens a job application in a real browser window, uploads the tailored resume, and fills in every question.

Run as:  python -m jobbot.apply <job_id> [--auto-submit] [--headless] [--no-wait]

By default it fills everything in and leaves the window open so the applicant can check it and press Submit.
With --auto-submit it presses Submit itself, but only when every required question was answered confidently
and no CAPTCHA is showing. Results are written to <JobHelper>/.applications/<job_id>/result.json.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import Frame, Page, sync_playwright

from . import llm, sources, store
from .browser import launch_kwargs

JS = (Path(__file__).parent / "formfill.js").read_text(encoding="utf-8")
CONFIRM_RE = re.compile(
    r"(thank(s| you) for (applying|your (application|interest))|application (has been )?(submitted|received)|"
    r"we('ve| have) received your application|successfully submitted|you('ve| have) applied)", re.I)
AUTO_SUBMIT_ATS = {"greenhouse", "lever", "ashby"}


# ------------------------------------------------------------------ deterministic answers

def contact_answers(profile: dict, resume: dict) -> list[tuple[re.Pattern, str]]:
    c = profile.get("contact", {})
    first, last = c.get("first_name", ""), c.get("last_name", "")
    city_state = ", ".join(x for x in [c.get("city"), c.get("state")] if x)
    latest = (resume.get("experience") or [{}])[0]
    rules = [
        (r"^(legal )?first name|^given name", first),
        (r"^preferred (first )?name", c.get("preferred_name") or first),
        (r"^(legal )?last name|^family name|^surname", last),
        (r"^(full |legal )?name$|^first name and last name|^your name", f"{first} {last}".strip()),
        (r"e-?mail", c.get("email", "")),
        (r"phone|mobile", c.get("phone", "")),
        (r"linkedin", c.get("linkedin", "")),
        (r"github", c.get("github", "")),
        (r"website|portfolio|personal (site|url)|other (website|url|link)", c.get("website") or c.get("github", "")),
        (r"^(current )?(location|city)|where are you (currently )?(located|based)", city_state),
        (r"^current (company|employer)|^(most recent )?employer", latest.get("org", "")),
        (r"^current (title|role|position)", latest.get("title", "")),
    ]
    return [(re.compile(p, re.I), v) for p, v in rules if v]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (s or "").lower()).strip()


def best_option(want: str, options: list[str]) -> int | None:
    if not options or not want:
        return None
    w = norm(want)
    normed = [norm(o) for o in options]
    for i, o in enumerate(normed):
        if o == w:
            return i
    for i, o in enumerate(normed):
        if o.startswith(w) or w.startswith(o) and len(o) > 2:
            return i
    for i, o in enumerate(normed):
        if w in o:
            return i
    m = difflib.get_close_matches(w, normed, n=1, cutoff=0.6)
    return normed.index(m[0]) if m else None


# ------------------------------------------------------------------ form helpers

def pick_frame(page: Page) -> Frame:
    """Some company sites embed the application in an iframe; use whichever frame has the most fields."""
    best, best_n = page.main_frame, -1
    for f in page.frames:
        try:
            n = f.locator("input:not([type=hidden]), textarea, select").count()
        except PWError:
            continue
        if n > best_n:
            best, best_n = f, n
    return best


def open_form(page: Page, url: str) -> Frame:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWError:
        pass
    frame = pick_frame(page)
    if frame.locator("input:not([type=hidden]), textarea").count() < 3:
        # The form may be behind an "Apply" button.
        btn = page.get_by_role("link", name=re.compile(r"^apply( now| for this job)?$", re.I)).or_(
            page.get_by_role("button", name=re.compile(r"^apply( now| for this job)?$", re.I)))
        if btn.count():
            try:
                btn.first.click(timeout=5000)
                page.wait_for_timeout(3000)
            except PWError:
                pass
        frame = pick_frame(page)
    return frame


def options_for_combobox(frame: Frame, el) -> list[str]:
    try:
        el.click(timeout=3000)
        frame.page.wait_for_timeout(500)
        opts = frame.locator("[role=option]:visible").all_inner_texts()
        el.press("Escape")
    except PWError:
        return []
    return [o.strip() for o in opts if o.strip()][:150]


def choose_in_combobox(frame: Frame, el, value: str) -> bool:
    try:
        el.click(timeout=3000)
        el.fill("")
        el.press_sequentially(value[:40], delay=25)
        frame.page.wait_for_timeout(1200)
        opts = frame.locator("[role=option]:visible")
        texts = [t.strip() for t in opts.all_inner_texts()]
        i = best_option(value, texts)
        if i is None and len(texts) == 1 and "no options" not in texts[0].lower():
            i = 0
        if i is None:
            el.fill("")
            el.press("Escape")
            return False
        opts.nth(i).click(timeout=3000)
        frame.page.wait_for_timeout(200)
        return True
    except PWError:
        return False


def fill_question(frame: Frame, q: dict, ans: dict) -> bool:
    loc = frame.locator(f'[data-jh="{q["qid"]}"]')
    kind = q["kind"]
    text = (ans.get("text") or "").strip()
    choices = [c for c in (ans.get("choices") or []) if c] or ([text] if text else [])
    try:
        if kind in ("text", "email", "tel", "number", "url", "date", "textarea", "password"):
            if not text:
                return False
            loc.first.fill(text, timeout=5000)
            return True
        if kind == "select":
            el = loc.first
            labels = [o.strip() for o in el.locator("option").all_inner_texts()]
            done = False
            for c in choices:
                i = best_option(c, labels)
                if i is not None:
                    el.select_option(index=i, timeout=5000)
                    done = True
            return done
        if kind == "combobox":
            ok = False
            for c in choices if q.get("multi") else choices[:1]:
                ok = choose_in_combobox(frame, loc.first, c) or ok
            return ok
        if kind in ("radio", "checkbox", "buttons"):
            opts = q.get("options") or []
            ok = False
            for c in choices if kind == "checkbox" else choices[:1]:
                i = best_option(c, opts)
                if i is None:
                    continue
                target = frame.locator(f'[data-jh="{q["qid"]}"][data-jh-opt="{i}"]').first
                if kind == "checkbox":
                    target.check(force=True, timeout=5000)
                else:
                    target.click(force=True, timeout=5000)
                ok = True
            return ok
        if kind == "checkbox_single":
            want = (text or (choices[0] if choices else "")).lower()
            if want in ("checked", "yes", "true", "check"):
                loc.first.check(force=True, timeout=5000)
            return True
    except PWError:
        return False
    return False


def is_empty(frame: Frame, q: dict) -> bool:
    loc = frame.locator(f'[data-jh="{q["qid"]}"]')
    try:
        if q["kind"] in ("radio", "checkbox"):
            return not any(loc.nth(i).is_checked() for i in range(loc.count()))
        if q["kind"] == "checkbox_single":
            return not loc.first.is_checked()
        if q["kind"] in ("buttons", "file"):
            return False
        if q["kind"] == "combobox":
            # react-select shows the chosen value next to the input, not in it
            return not frame.evaluate(
                """(el) => { let c = el;
                     for (let i = 0; i < 4 && c.parentElement; i++) c = c.parentElement;  // up to the whole select
                     return !!c.querySelector('[class*=singleValue], [class*=multiValue], [class*=single-value], [class*=multi-value]')
                            || !!el.value; }""", loc.first.element_handle())
        return not (loc.first.input_value() or "").strip()
    except PWError:
        return False


def mark(frame: Frame, qids: list[str], color: str) -> None:
    for qid in qids:
        try:
            frame.evaluate(
                """([qid, color]) => document.querySelectorAll(`[data-jh="${qid}"]`).forEach(e => {
                       const t = e.closest('fieldset, [class*=question], [class*=field], div') || e;
                       t.style.outline = `3px solid ${color}`; t.style.outlineOffset = '3px'; })""", [qid, color])
        except PWError:
            pass


def banner(page: Page, html: str, color: str = "#1f6feb") -> None:
    try:
        page.evaluate(
            """([html, color]) => { let b = document.getElementById('jh-banner');
                 if (!b) { b = document.createElement('div'); b.id = 'jh-banner'; document.body.appendChild(b); }
                 b.style.cssText = `position:fixed;top:0;left:0;right:0;z-index:2147483647;background:${color};color:#fff;
                   font:16px/1.4 system-ui,sans-serif;padding:12px 18px;box-shadow:0 2px 8px rgba(0,0,0,.3);`;
                 b.innerHTML = html; }""", [html, color])
    except PWError:
        pass


def captcha_showing(page: Page) -> bool:
    for f in page.frames:
        if re.search(r"recaptcha/api2/bframe|hcaptcha.com/.*challenge|challenges.cloudflare", f.url or ""):
            try:
                if f.frame_element().is_visible():
                    return True
            except PWError:
                continue
    return False


def looks_submitted(page: Page) -> bool:
    try:
        if re.search(r"/(confirmation|thanks|thank-you|submitted|success)", page.url, re.I):
            return True
        return any(CONFIRM_RE.search(f.locator("body").inner_text(timeout=2000) or "") for f in page.frames[:4])
    except PWError:
        return False


def click_submit(frame: Frame) -> bool:
    btn = frame.get_by_role("button", name=re.compile(r"submit( application| my application)?$|^apply$|send application", re.I))
    if not btn.count():
        btn = frame.locator("button[type=submit], input[type=submit]")
    if not btn.count():
        return False
    try:
        btn.last.scroll_into_view_if_needed(timeout=3000)
        btn.last.click(timeout=5000)
        return True
    except PWError:
        return False


# ------------------------------------------------------------------ main flow

def run(job_id: str, auto_submit: bool, headless: bool, wait: bool) -> dict:
    settings, profile, resume = store.load_settings(), store.load_profile(), store.load_resume()
    job = store.get_job(job_id)
    if not job:
        raise SystemExit(f"Unknown job {job_id}")
    tailored_resume = json.loads(job["tailor_notes"] or "{}").get("resume") or resume
    out_dir = store.APPS_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"job_id": job_id, "started_at": time.time(), "submitted": False, "issues": [], "answers": {}}
    pdf = Path(job["resume_path"]) if job.get("resume_path") else None
    cover_pdf = out_dir / "Cover_Letter.pdf" if (out_dir / "Cover_Letter.pdf").exists() else None
    description = job.get("description") or ""

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(store.BROWSER_DIR), headless=headless, viewport=None if not headless else {"width": 1280, "height": 900},
            args=["--start-maximized"], **launch_kwargs())
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        frame = open_form(page, sources.application_url(job))
        banner(page, "⏳ <b>Job Helper</b> is filling in this application… please don't touch anything yet.")

        # 1. Upload resume (and cover letter) first: some sites re-fill fields from the resume.
        qs = frame.evaluate(JS)
        for q in [q for q in qs if q["kind"] == "file"]:
            what = (q["label"] + " " + (q.get("hint") or "")).lower()
            target = None
            if re.search(r"cover", what):
                target = cover_pdf
            elif re.search(r"resume|cv|autofill", what) or q is next(x for x in qs if x["kind"] == "file"):
                target = pdf
            if target and target.exists():
                try:
                    frame.locator(f'[data-jh="{q["qid"]}"]').set_input_files(str(target), timeout=10000)
                    page.wait_for_timeout(2500)
                except PWError:
                    result["issues"].append(f"Couldn't upload file for “{q['label']}”")
        page.wait_for_timeout(2000)

        # 2. Read all the questions again (uploads can add/change fields) and work out answers.
        qs = [q for q in frame.evaluate(JS) if q["kind"] != "file"]
        for q in qs:
            if q["kind"] == "combobox" and not q.get("options"):
                q["options"] = options_for_combobox(frame, frame.locator(f'[data-jh="{q["qid"]}"]').first)
                if not q["options"]:
                    q["kind_note"] = "type-to-search field: answer with the text to type (e.g. a city or school name)"
                    q["hint"] = (q.get("hint") or "") + " (type-to-search)"

        answers: dict[str, dict] = {}
        rules = contact_answers(profile, tailored_resume)
        for q in qs:
            if q["kind"] in ("text", "email", "tel", "url", "textarea"):
                for rx, val in rules:
                    if rx.search(q["label"]):
                        answers[q["qid"]] = {"qid": q["qid"], "status": "answered", "text": val, "choices": []}
                        break
        rest = [q for q in qs if q["qid"] not in answers and q["label"]]
        if rest:
            try:
                answers.update(llm.answer_questions(settings, tailored_resume, profile, job, description, rest))
            except Exception as e:  # noqa: BLE001
                result["issues"].append(f"Couldn't get answers from Claude: {e}")

        # 3. Fill everything in.
        needs_you, failed = [], []
        for q in qs:
            a = answers.get(q["qid"])
            result["answers"][q["label"][:150] or q["qid"]] = a
            if not a or a["status"] == "skip":
                continue
            if a["status"] == "needs_human":
                needs_you.append(q)
                if a.get("text") and q["kind"] in ("text", "textarea"):
                    fill_question(frame, q, a)  # put the suggestion in, but flag it
                continue
            if not fill_question(frame, q, a):
                failed.append(q)
        page.wait_for_timeout(800)

        missing = [q for q in qs if q["required"] and is_empty(frame, q)]
        flagged = {q["qid"]: q for q in needs_you + failed + missing}
        mark(frame, list(flagged), "#e5534b")
        for q in flagged.values():
            result["issues"].append(f"Please check: “{q['label'][:120]}”")

        can_auto = auto_submit and job["ats"] in AUTO_SUBMIT_ATS and not flagged and not captcha_showing(page)
        if can_auto:
            banner(page, "🚀 <b>Job Helper</b> filled everything in and is submitting…", "#2da44e")
            if click_submit(frame):
                for _ in range(20):
                    page.wait_for_timeout(1000)
                    if looks_submitted(page):
                        result["submitted"] = True
                        break
                    if captcha_showing(page):
                        break
            if not result["submitted"]:
                result["issues"].append("Tried to submit but couldn't confirm it went through (maybe a CAPTCHA).")

        if result["submitted"]:
            banner(page, "✅ <b>Submitted!</b> You can close this window.", "#2da44e")
        elif flagged:
            banner(page, f"✋ <b>Job Helper</b> filled in most of this. <b>{len(flagged)} question(s) outlined in red need you.</b> "
                         "Check everything, press <b>Submit</b>, then close this window.", "#bf8700")
        else:
            banner(page, "✅ <b>Job Helper</b> filled in this application. Please look it over, press <b>Submit</b>, "
                         "then close this window.", "#1f6feb")

        page.screenshot(path=str(out_dir / "filled.png"), full_page=True)
        result["filled_at"] = time.time()
        (out_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))

        # 4. Wait for the person to submit and close the window.
        if wait and not headless:
            closed = {"v": False}
            ctx.on("close", lambda *_: closed.update(v=True))
            page.on("close", lambda *_: closed.update(v=True))
            while not closed["v"]:
                try:
                    if not result["submitted"] and looks_submitted(page):
                        result["submitted"] = True
                        banner(page, "✅ <b>Submitted!</b> You can close this window.", "#2da44e")
                    page.wait_for_timeout(1500)
                except PWError:
                    break
        try:
            ctx.close()
        except PWError:
            pass

    result["finished_at"] = time.time()
    (out_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--auto-submit", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()
    res = run(a.job_id, a.auto_submit, a.headless, not a.no_wait)
    print(json.dumps({"submitted": res["submitted"], "issues": res["issues"]}))


if __name__ == "__main__":
    sys.exit(main())
