"""Everything that talks to Claude: reading the resume, scoring jobs, tailoring, and answering form questions."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5-5"


def client(settings: dict) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.get("api_key") or None, max_retries=4)


def _parse(settings: dict, *, system, messages, output_format, effort="medium", max_tokens=16000):
    """One structured call. Falls back to another model automatically if a request is declined."""
    resp = client(settings).beta.messages.parse(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        output_format=output_format,
        output_config={"effort": effort},
        cache_control={"type": "ephemeral"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("Claude could not complete this request. Try again, or check the input.")
    return resp.parsed_output


# ------------------------------------------------------------------ master resume

class Education(BaseModel):
    school: str
    degree: str
    field: str
    location: str
    start: str
    end: str
    gpa: str
    details: list[str]


class Role(BaseModel):
    org: str
    title: str
    location: str
    start: str
    end: str
    bullets: list[str]


class Project(BaseModel):
    name: str
    tech: str
    link: str
    date: str
    bullets: list[str]


class SkillGroup(BaseModel):
    category: str
    items: list[str]


class Section(BaseModel):
    title: str
    items: list[str]


class MasterResume(BaseModel):
    name: str
    email: str
    phone: str
    location: str
    links: list[str]
    summary: str
    education: list[Education]
    experience: list[Role]
    projects: list[Project]
    skills: list[SkillGroup]
    extras: list[Section]


def strip_latex_comments(tex: str) -> str:
    """Remove commented-out lines so old/hidden resume content doesn't sneak into the master resume."""
    tex = re.sub(r"\\begin\{comment\}.*?\\end\{comment\}", "", tex, flags=re.S)
    tex = re.sub(r"\\iffalse\b.*?\\fi\b", "", tex, flags=re.S)
    lines = []
    for line in tex.splitlines():
        line = re.sub(r"(?<!\\)%.*", "", line)  # an unescaped % starts a comment
        if line.strip() or (lines and lines[-1].strip()):
            lines.append(line.rstrip())
    return "\n".join(lines)


def resume_from_file(settings: dict, path: Path, extra_notes: str = "") -> dict:
    """Turn an uploaded PDF / Word / text resume into structured data, word for word."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        content = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                    "data": base64.standard_b64encode(path.read_bytes()).decode()}}]
    elif suffix == ".docx":
        import docx
        d = docx.Document(str(path))
        text = "\n".join(p.text for p in d.paragraphs)
        for t in d.tables:
            for row in t.rows:
                text += "\n" + " | ".join(c.text for c in row.cells)
        content = [{"type": "text", "text": f"<resume>\n{text}\n</resume>"}]
    elif suffix == ".tex":
        tex = strip_latex_comments(path.read_text(encoding="utf-8", errors="ignore"))
        content = [{"type": "text", "text": (
            "This resume is LaTeX source code. Read it as the rendered resume would appear: use only the visible "
            "text (ignore formatting commands, macro definitions and the preamble), convert LaTeX escapes and accents "
            "to normal characters (e.g. \\% -> %, \\& -> &, --- -> an em dash), and expand custom macros like "
            "\\resumeItem{...} or \\resumeSubheading{...}{...} into their text.\n"
            f"<resume_latex>\n{tex}\n</resume_latex>")}]
    else:
        content = [{"type": "text", "text": f"<resume>\n{path.read_text(errors='ignore')}\n</resume>"}]
    content.append({"type": "text", "text": (
        "Convert this resume into the structured format. Copy every bullet point word for word - do not "
        "shorten, merge, improve, or invent anything. Use empty strings for anything missing. Put every skill "
        "listed into skill groups. Put awards, leadership, certifications, publications, activities, etc. in extras."
        + (f"\n\nThe applicant also wrote these extra notes about experience that is not on the resume. Add them as "
           f"bullets or entries where they fit, keeping their facts exactly:\n{extra_notes}" if extra_notes.strip() else "")
    )})
    out = _parse(settings, system="You convert resumes into structured data with perfect accuracy.",
                 messages=[{"role": "user", "content": content}], output_format=MasterResume, effort="low")
    return out.model_dump()


def resume_as_text(r: dict, with_ids: bool = False) -> str:
    lines = [f"{r['name']} | {r['email']} | {r['phone']} | {r['location']} | {' | '.join(r['links'])}"]
    if r.get("summary"):
        lines.append(f"SUMMARY: {r['summary']}")
    lines.append("EDUCATION:")
    for e in r["education"]:
        lines.append(f"- {e['degree']} {e['field']}, {e['school']} ({e['start']}-{e['end']}) GPA {e['gpa']}")
        lines += [f"    * {d}" for d in e["details"]]
    lines.append("EXPERIENCE:")
    for i, x in enumerate(r["experience"]):
        lines.append(f"- [{'E%d' % i if with_ids else ''}] {x['title']}, {x['org']} ({x['start']}-{x['end']})")
        lines += [f"    * {'[E%d.B%d] ' % (i, b) if with_ids else ''}{t}" for b, t in enumerate(x["bullets"])]
    lines.append("PROJECTS:")
    for i, x in enumerate(r["projects"]):
        lines.append(f"- [{'P%d' % i if with_ids else ''}] {x['name']} ({x['tech']}) {x['date']}")
        lines += [f"    * {'[P%d.B%d] ' % (i, b) if with_ids else ''}{t}" for b, t in enumerate(x["bullets"])]
    lines.append("SKILLS:")
    lines += [f"- {g['category']}: {', '.join(g['items'])}" for g in r["skills"]]
    for s in r.get("extras", []):
        lines.append(f"{s['title'].upper()}:")
        lines += [f"- {i}" for i in s["items"]]
    return "\n".join(lines)


# ------------------------------------------------------------------ fit scoring

class Fit(BaseModel):
    score: int
    reason: str


def score_job(settings: dict, resume: dict, profile: dict, job: dict, description: str) -> dict:
    answers = profile.get("answers", {})
    system = [{"type": "text", "text": (
        "You screen job postings for a student/early-career candidate looking for data science roles. "
        "Score 0-100 how worthwhile it is for THIS candidate to apply: role relevance to data science / ML / "
        "analytics, whether they meet the requirements (degree level, graduation date, skills), and hard "
        "blockers. Hard blockers (score under 20): requires sponsorship-free status when the candidate needs "
        "sponsorship, requires US citizenship/clearance the candidate lacks, requires a PhD the candidate "
        "doesn't have, or is clearly senior. Reason: one short plain-English sentence.\n\n"
        f"<candidate_resume>\n{resume_as_text(resume)}\n</candidate_resume>\n"
        f"<candidate_facts>\nWork authorization: {answers.get('work_authorization','')}\n"
        f"Needs sponsorship: {answers.get('needs_sponsorship','')}\n"
        f"Citizenship: {answers.get('citizenship','')}\nGraduation: {answers.get('graduation_date','')}\n"
        f"</candidate_facts>"
    )}]
    msg = f"<job>\n{job['title']} at {job['company']} ({job['location']})\n\n{description[:12000]}\n</job>"
    out = _parse(settings, system=system, messages=[{"role": "user", "content": msg}],
                 output_format=Fit, effort="low", max_tokens=2000)
    return {"score": max(0, min(100, out.score)), "reason": out.reason}


# ------------------------------------------------------------------ tailoring

class TBullet(BaseModel):
    source: str  # e.g. "E0.B2"
    text: str


class TItem(BaseModel):
    source: str  # e.g. "E0" or "P1"
    bullets: list[TBullet]


class Tailored(BaseModel):
    summary: str
    experience: list[TItem]
    projects: list[TItem]
    skills: list[SkillGroup]
    changes: list[str]
    missing_keywords: list[str]
    cover_letter: str


TAILOR_RULES = """You tailor a master resume to one specific job posting for a data science applicant.

Hard rules - the resume must stay 100% truthful:
- Every bullet you output must come from one master bullet, referenced by its id (e.g. "E0.B2"). You may reword it
  to use the job's vocabulary, lead with the most relevant part, and tighten it. You must NOT add tools, methods,
  numbers, results, scope, or responsibilities that the source bullet doesn't state. Keep every number exactly as written.
- Experience/project entries are referenced by id ("E0", "P1"). Put experience in its original (reverse-chronological)
  order. Order projects by relevance. Drop entries and bullets that don't help for this job.
- Skills: only skills that appear in the master resume. Reorder and regroup so the most relevant come first.
- Summary: 1-2 lines, only if the master resume has one or it clearly helps; built only from facts in the resume.
  Empty string is fine.
- Must fit on ONE page: about 12-16 bullets in total, each under ~200 characters.
- changes: 3-6 short plain-English notes on what you changed and why (shown to the applicant).
- missing_keywords: important requirements from the posting the applicant genuinely does not show (so they know).
"""


def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", s))


def tailor(settings: dict, resume: dict, profile: dict, job: dict, description: str) -> tuple[dict, dict]:
    """Returns (tailored_resume_dict, info) where info has changes / missing keywords / cover letter."""
    want_cl = settings.get("write_cover_letters")
    system = [{"type": "text", "text": TAILOR_RULES + (
        "\ncover_letter: a short (≤250 words), specific, sincere cover letter in first person, only using facts from "
        "the resume. No clichés." if want_cl else "\ncover_letter: return an empty string."
    ) + f"\n\n<master_resume>\n{resume_as_text(resume, with_ids=True)}\n</master_resume>"}]
    msg = f"<job>\n{job['title']} at {job['company']} ({job['location']})\n\n{description[:15000]}\n</job>"
    out: Tailored = _parse(settings, system=system, messages=[{"role": "user", "content": msg}],
                           output_format=Tailored, effort="medium")

    # ---- verify everything against the master resume; anything that doesn't check out is reverted / dropped
    def build(items: list[TItem], master_list: list[dict], prefix: str) -> list[dict]:
        built, used = [], set()
        for it in items:
            m = re.fullmatch(rf"{prefix}(\d+)", it.source.strip())
            if not m or int(m.group(1)) >= len(master_list) or it.source in used:
                continue
            idx = int(m.group(1))
            used.add(it.source)
            entry = dict(master_list[idx])
            bullets = []
            for b in it.bullets:
                bm = re.fullmatch(rf"{prefix}{idx}\.B(\d+)", b.source.strip())
                if not bm or int(bm.group(1)) >= len(master_list[idx]["bullets"]):
                    continue
                original = master_list[idx]["bullets"][int(bm.group(1))]
                text = b.text.strip()
                if not text or not _numbers(text) <= _numbers(original):
                    text = original  # a number changed or appeared: use the original wording
                bullets.append(text)
            entry["bullets"] = bullets or master_list[idx]["bullets"][:2]
            entry["_order"] = idx
            built.append(entry)
        return built

    exp = sorted(build(out.experience, resume["experience"], "E"), key=lambda e: e["_order"])
    projs = build(out.projects, resume["projects"], "P")
    master_blob = resume_as_text(resume).lower()
    skills = []
    for g in out.skills:
        items = [s for s in g.items if s.strip() and s.lower() in master_blob]
        if items:
            skills.append({"category": g.category, "items": items})
    tailored = dict(resume, summary=out.summary if resume.get("summary") or out.summary else "",
                    experience=exp or resume["experience"], projects=projs, skills=skills or resume["skills"])
    info = {"changes": out.changes, "missing_keywords": out.missing_keywords,
            "cover_letter": out.cover_letter if want_cl else ""}
    return tailored, info


# ------------------------------------------------------------------ application questions

class Answer(BaseModel):
    qid: str
    status: Literal["answered", "skip", "needs_human"]
    text: str            # for text / textarea / single choice: the value or the EXACT option text
    choices: list[str]   # for multi-select checkboxes: EXACT option texts


class Answers(BaseModel):
    answers: list[Answer]


ANSWER_RULES = """You fill out a job application form on behalf of the applicant, exactly as they would themselves.

Rules:
- Use ONLY the applicant's profile answers and resume. Never invent facts. Never misstate work authorization,
  sponsorship needs, citizenship, criminal history, or demographic information.
- Demographic / EEO / voluntary self-identification questions: use the applicant's stated answer. If they chose
  to decline, pick the option that declines (e.g. "Decline to self-identify", "I don't wish to answer").
- Choice questions: answer with the option text copied EXACTLY from the given options. For checkbox groups put every
  applicable option in `choices`. Consent/acknowledgement checkboxes ("I agree", "I certify the information is true",
  privacy policy) should be checked.
- "How did you hear about us": use the applicant's preferred answer if listed, else the closest to "Job board" /
  "Company website" / "Other".
- Free-text questions (e.g. "Why do you want to work here?"): write a genuine, specific 2-4 sentence answer in the
  first person using only real facts from the resume and the job posting. No clichés, no exaggeration.
- `previously_answered_questions` in the profile are the applicant's own answers to past application questions.
  Reuse them for the same or clearly equivalent questions (adapting to this form's options).
- "Have you worked for <company> before?": answer from the resume. "Do you have relatives/friends at <company>?" or
  conflicts of interest: answer from prior_employers_or_relatives (blank means No).
- Consent questions (interview recording, AI note-takers, background checks, data processing, text messages about the
  application): follow the applicant's consent preferences in the profile.
- Salary questions asking for a number: use the applicant's numeric expectation if given.
- Prefer answering over flagging: use needs_human only when the answer truly cannot be determined from the profile,
  previous answers, or resume, because every needs_human stops the application from being submitted automatically.
- If a REQUIRED question needs information the profile doesn't contain (e.g. a referral's name, a specific
  date not given, a document), use status "needs_human" with your best suggestion in `text` if you have one.
  If an OPTIONAL question needs missing info, use "skip".
- Dates: use the format the field suggests (e.g. MM/DD/YYYY). Salary: use the applicant's expectation or
  "Open / negotiable" for free text.
"""


def answer_questions(settings: dict, resume: dict, profile: dict, job: dict, description: str,
                     questions: list[dict]) -> dict[str, dict]:
    if not questions:
        return {}
    system = [{"type": "text", "text": ANSWER_RULES +
               f"\n<applicant_profile>\n{json.dumps(profile, indent=1, ensure_ascii=False)}\n</applicant_profile>\n"
               f"<applicant_resume>\n{resume_as_text(resume)}\n</applicant_resume>"}]
    qtext = json.dumps([{k: q[k] for k in ("qid", "label", "kind", "required", "options", "hint") if q.get(k) not in (None, [], "")}
                        for q in questions], indent=1, ensure_ascii=False)
    msg = (f"<job>\n{job['title']} at {job['company']} ({job['location']})\n\n{description[:6000]}\n</job>\n\n"
           f"Answer every one of these form questions:\n<questions>\n{qtext}\n</questions>")
    out: Answers = _parse(settings, system=system, messages=[{"role": "user", "content": msg}],
                          output_format=Answers, effort="medium")
    return {a.qid: a.model_dump() for a in out.answers}
