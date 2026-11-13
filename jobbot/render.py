"""Turning a resume (structured data) into a nicely formatted one-page PDF."""
from __future__ import annotations

import copy
import io
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pypdf import PdfReader

from .browser import launch_kwargs

_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape())


def _html(resume: dict, fs: float) -> str:
    return _env.get_template("resume.html").render(r=resume, fs=fs)


def _pdf_bytes(html: str) -> bytes:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(**launch_kwargs())
        page = b.new_page()
        page.set_content(html, wait_until="load")
        data = page.pdf(format="Letter", print_background=True, prefer_css_page_size=True)
        b.close()
    return data


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _trim_once(r: dict) -> bool:
    """Remove the least important line. Returns False if nothing left to trim."""
    for section in ("projects", "experience"):
        items = r.get(section) or []
        # longest entry list first, from the end
        cands = [(len(it["bullets"]), i) for i, it in enumerate(items) if len(it["bullets"]) > 1]
        if cands:
            _, i = max(cands, key=lambda c: (c[0], c[1]))
            items[i]["bullets"].pop()
            return True
    if len(r.get("projects") or []) > 1:
        r["projects"].pop()
        return True
    for s in r.get("extras") or []:
        if s["items"]:
            s["items"].pop()
            return True
    return False


def render_pdf(resume: dict, out_path: Path, one_page: bool = True) -> Path:
    """Render to PDF, shrinking the font slightly and then trimming bullets until it fits on one page."""
    r = copy.deepcopy(resume)
    r = {k: v for k, v in r.items()}
    for x in r.get("experience", []) + r.get("projects", []):
        x.pop("_order", None)

    def run():
        fs = 10.5
        pdf = _pdf_bytes(_html(r, fs))
        while one_page and _pages(pdf) > 1:
            if fs > 9.6:
                fs -= 0.3
            elif not _trim_once(r):
                break
            pdf = _pdf_bytes(_html(r, fs))
        return pdf

    # Playwright's sync API must not run inside an asyncio loop; a fresh thread is always safe.
    with ThreadPoolExecutor(1) as ex:
        pdf = ex.submit(run).result()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(pdf)
    return out_path


def safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _.-]+", "", s).strip().replace(" ", "_")[:80]


def render_cover_letter(resume: dict, job: dict, text: str, out_path: Path) -> Path:
    from datetime import date
    from markupsafe import escape
    paras = "".join(f"<p>{escape(p.strip())}</p>" for p in text.split("\n") if p.strip())
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
      @page {{ size: Letter; margin: 1in; }} body {{ font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.45; }}
      .h {{ margin-bottom: 24px; }} p {{ margin: 0 0 12px; }}</style></head><body>
      <div class="h"><b>{escape(resume['name'])}</b><br>{escape(resume['email'])} · {escape(resume['phone'])}<br><br>
      {date.today():%B %d, %Y}<br>{escape(job['company'])}</div>{paras}</body></html>"""
    with ThreadPoolExecutor(1) as ex:
        pdf = ex.submit(_pdf_bytes, html).result()
    out_path.write_bytes(pdf)
    return out_path
