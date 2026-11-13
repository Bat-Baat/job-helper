"""Finding jobs.

Sources, most reliable first:
  1. SimplifyJobs' community-maintained internship / new-grad lists on GitHub.
  2. Company job boards on Greenhouse, Lever, and Ashby (public JSON feeds; see companies.yaml).
  3. Optional: Indeed / LinkedIn / Google Jobs via the python-jobspy library (less reliable).
"""
from __future__ import annotations

import hashlib
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"User-Agent": "Mozilla/5.0 (JobHelper personal job search)"}
SIMPLIFY_LISTS = {
    "Internship": [
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    ],
    "New grad": [
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    ],
}

INTERN_RE = re.compile(r"\b(intern|internship|co-?op|fellow(ship)?|apprentice)\b", re.I)
NEWGRAD_RE = re.compile(r"\b(new ?grad|university|graduate|early career|entry[- ]level|junior|associate|I)\b|\b20(26|27)\b", re.I)
NON_US = re.compile(
    r"\b(canada|toronto|vancouver|montreal|ontario|uk|united kingdom|london|england|ireland|dublin|india|"
    r"bangalore|bengaluru|hyderabad|pune|chennai|mumbai|delhi|germany|berlin|munich|france|paris|"
    r"netherlands|amsterdam|spain|madrid|barcelona|poland|warsaw|israel|tel aviv|singapore|japan|tokyo|"
    r"australia|sydney|melbourne|china|shanghai|beijing|hong kong|korea|seoul|switzerland|zurich|sweden|"
    r"stockholm|denmark|copenhagen|mexico|brazil|são paulo|sao paulo|argentina|colombia|chile|costa rica|"
    r"philippines|emea|apac|latam|europe|portugal|lisbon|italy|milan|romania|czech|prague|serbia|"
    r"estonia|finland|norway|oslo|belgium|austria|vienna|uae|dubai|taiwan|vietnam|indonesia|malaysia|"
    r"thailand|nigeria|kenya|south africa)\b",
    re.I,
)


def job_id(url: str) -> str:
    return hashlib.sha1(url.split("?")[0].rstrip("/").lower().encode()).hexdigest()[:16]


def detect_ats(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "greenhouse.io" in host or "gh_jid=" in url:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "myworkdayjobs" in host:
        return "workday"
    return "other"


def html_to_text(s: str) -> str:
    if not s:
        return ""
    soup = BeautifulSoup(html.unescape(s), "html.parser")
    text = soup.get_text("\n")
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# ------------------------------------------------------------------ filtering

def title_matches(title: str, settings: dict) -> bool:
    t = title.lower()
    if not any(k.lower() in t for k in settings["role_keywords"]):
        return False
    types = settings["job_types"]
    is_intern = bool(INTERN_RE.search(title))
    if any(re.search(rf"\b{re.escape(k.strip('.'))}\b", t) for k in settings["exclude_keywords"]):
        # "senior" etc. are fine to exclude, but don't throw away interns for "PhD" if they asked for it
        return False
    if "Full-time" in types:
        return True if not is_intern or "Internship" in types else False
    ok = False
    if "Internship" in types and is_intern:
        ok = True
    if "New grad" in types and not is_intern and NEWGRAD_RE.search(title):
        ok = True
    return ok


def location_matches(loc: str, settings: dict) -> bool:
    loc = loc or ""
    if settings.get("us_only") and NON_US.search(loc) and not re.search(r"\b(us|usa|united states|remote - us)\b", loc, re.I):
        return False
    wanted = [w.strip().lower() for w in settings.get("locations", []) if w.strip()]
    if not wanted:
        return True
    l = loc.lower()
    if settings.get("remote_ok") and "remote" in l:
        return True
    return any(w in l for w in wanted)


# ------------------------------------------------------------------ sources

def _get_json(client: httpx.Client, url: str):
    try:
        r = client.get(url, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


def fetch_simplify(client: httpx.Client, settings: dict) -> list[dict]:
    jobs = []
    cutoff = datetime.now().timestamp() - 120 * 86400  # last ~4 months
    for jtype in settings["job_types"]:
        for url in SIMPLIFY_LISTS.get(jtype, []):
            data = _get_json(client, url) or []
            for x in data:
                if not x.get("active") or not x.get("is_visible", True):
                    continue
                if (x.get("date_posted") or 0) < cutoff:
                    continue
                title = x.get("title", "")
                if not any(k.lower() in title.lower() for k in settings["role_keywords"]):
                    continue
                if any(re.search(rf"\b{re.escape(k.strip('.'))}\b", title.lower()) for k in settings["exclude_keywords"]):
                    continue
                loc = "; ".join(x.get("locations") or [])
                if not location_matches(loc, settings):
                    continue
                link = x["url"].replace("?embed=true", "").replace("&embed=true", "")
                jobs.append({
                    "id": job_id(link), "source": f"Simplify ({jtype})", "company": x.get("company_name", ""),
                    "title": title, "location": loc, "url": link, "ats": detect_ats(link),
                    "posted_at": x.get("date_posted"),
                    "sponsorship_note": x.get("sponsorship"),
                })
    return jobs


def _greenhouse_board(client, token, settings):
    data = _get_json(client, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs") or {}
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        if title_matches(j["title"], settings) and location_matches(loc, settings):
            out.append({
                "id": job_id(j["absolute_url"]), "source": "Greenhouse", "company": j.get("company_name") or token,
                "title": j["title"], "location": loc, "url": j["absolute_url"], "ats": "greenhouse",
                "posted_at": _ts(j.get("first_published") or j.get("updated_at")),
                "_gh": (token, j["id"]),
            })
    return out


def _lever_board(client, token, settings):
    data = _get_json(client, f"https://api.lever.co/v0/postings/{token}?mode=json") or []
    out = []
    for j in data if isinstance(data, list) else []:
        loc = (j.get("categories") or {}).get("location", "") or ""
        if title_matches(j["text"], settings) and location_matches(loc, settings):
            desc = "\n\n".join(filter(None, [j.get("descriptionPlain"),
                                             *[f"{l['text']}\n{html_to_text(l['content'])}" for l in j.get("lists", [])],
                                             j.get("additionalPlain")]))
            out.append({
                "id": job_id(j["hostedUrl"]), "source": "Lever", "company": token.title(), "title": j["text"],
                "location": loc, "url": j["hostedUrl"], "apply_url": j.get("applyUrl"), "ats": "lever",
                "posted_at": (j.get("createdAt") or 0) / 1000, "description": desc,
            })
    return out


def _ashby_board(client, token, settings):
    data = _get_json(client, f"https://api.ashbyhq.com/posting-api/job-board/{token}") or {}
    out = []
    for j in data.get("jobs", []):
        if not j.get("isListed", True):
            continue
        loc = j.get("location", "") or ""
        if j.get("isRemote") and "remote" not in loc.lower():
            loc += " (Remote)"
        if title_matches(j["title"], settings) and location_matches(loc, settings):
            out.append({
                "id": job_id(j["jobUrl"]), "source": "Ashby", "company": token.title(), "title": j["title"].strip(),
                "location": loc, "url": j["jobUrl"], "apply_url": j.get("applyUrl"), "ats": "ashby",
                "posted_at": _ts(j.get("publishedAt")), "description": j.get("descriptionPlain", ""),
            })
    return out


def _ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def fetch_company_boards(client: httpx.Client, settings: dict) -> list[dict]:
    companies = yaml.safe_load((ROOT / "companies.yaml").read_text()) or {}
    tasks = [(_greenhouse_board, t) for t in companies.get("greenhouse") or []]
    tasks += [(_lever_board, t) for t in companies.get("lever") or []]
    tasks += [(_ashby_board, t) for t in companies.get("ashby") or []]
    jobs = []
    with ThreadPoolExecutor(8) as ex:
        for res in ex.map(lambda ft: ft[0](client, ft[1], settings), tasks):
            jobs.extend(res)
    return jobs


def fetch_jobspy(settings: dict) -> list[dict]:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        return []
    terms = []
    if "Internship" in settings["job_types"]:
        terms.append("data science intern")
    if "New grad" in settings["job_types"]:
        terms.append("entry level data scientist")
    if "Full-time" in settings["job_types"]:
        terms.append("data scientist")
    jobs = []
    locs = settings.get("locations") or ["United States"]
    for term in terms:
        for loc in locs[:3]:
            try:
                df = scrape_jobs(site_name=["indeed", "linkedin", "google"], search_term=term,
                                 google_search_term=f"{term} jobs near {loc} since last week",
                                 location=loc, results_wanted=40, hours_old=24 * 14, country_indeed="USA")
            except Exception:  # noqa: BLE001 - these sites break often; never crash the run
                continue
            for _, r in df.iterrows():
                url = str(r.get("job_url_direct") or r.get("job_url") or "")
                title = str(r.get("title") or "")
                if not url or not title_matches(title, settings):
                    continue
                jobs.append({
                    "id": job_id(url), "source": str(r.get("site", "web")).title(), "company": str(r.get("company") or ""),
                    "title": title, "location": str(r.get("location") or ""), "url": url, "ats": detect_ats(url),
                    "description": str(r.get("description") or ""), "posted_at": None,
                })
    return jobs


def find_jobs(settings: dict, progress=lambda msg: None) -> list[dict]:
    jobs: list[dict] = []
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        if settings["sources"].get("simplify"):
            progress("Checking the SimplifyJobs internship / new-grad lists…")
            jobs += fetch_simplify(client, settings)
        if settings["sources"].get("company_boards"):
            progress("Checking company job boards (Greenhouse, Lever, Ashby)…")
            jobs += fetch_company_boards(client, settings)
    if settings["sources"].get("indeed_linkedin"):
        progress("Searching Indeed / LinkedIn / Google Jobs (this one is slow)…")
        jobs += fetch_jobspy(settings)
    seen, unique = set(), []
    for j in jobs:
        key = (j["company"].strip().lower(), j["title"].strip().lower())
        if j["id"] not in seen and key not in seen:
            seen.update([j["id"], key])
            unique.append(j)
    return unique


# ------------------------------------------------------------------ descriptions

_ashby_cache: dict[str, dict] = {}


def fetch_description(job: dict) -> str:
    """Get the full job description text for one posting."""
    url = job["url"]
    p = urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        try:
            if "greenhouse.io" in p.netloc:
                token = parse_qs(p.query).get("for", [None])[0] or (parts[0] if parts else None)
                jid = parse_qs(p.query).get("token", [None])[0] or (parts[2] if len(parts) > 2 else None)
                if token and jid:
                    d = _get_json(client, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}")
                    if d and d.get("content"):
                        return html_to_text(d["content"])
            elif "lever.co" in p.netloc and len(parts) >= 2:
                d = _get_json(client, f"https://api.lever.co/v0/postings/{parts[0]}/{parts[1]}")
                if d:
                    return "\n\n".join(filter(None, [d.get("descriptionPlain"),
                                                     *[f"{l['text']}\n{html_to_text(l['content'])}" for l in d.get("lists", [])],
                                                     d.get("additionalPlain")]))
            elif "ashbyhq.com" in p.netloc and len(parts) >= 2:
                org, jid = parts[0], parts[1]
                if org not in _ashby_cache:
                    board = _get_json(client, f"https://api.ashbyhq.com/posting-api/job-board/{org}") or {}
                    _ashby_cache[org] = {j["id"]: j for j in board.get("jobs", [])}
                if jid in _ashby_cache[org]:
                    return _ashby_cache[org][jid].get("descriptionPlain", "")
            # Anything else: grab the page text.
            r = client.get(url)
            soup = BeautifulSoup(r.text, "html.parser")
            for t in soup(["script", "style", "nav", "header", "footer"]):
                t.decompose()
            return re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n")).strip()[:15000]
        except httpx.HTTPError:
            return ""


def application_url(job: dict) -> str:
    url = job.get("apply_url") or job["url"]
    if job["ats"] == "lever" and not url.rstrip("/").endswith("/apply"):
        url = url.rstrip("/") + "/apply"
    if job["ats"] == "ashby" and not url.rstrip("/").endswith("/application"):
        url = url.rstrip("/") + "/application"
    return url
