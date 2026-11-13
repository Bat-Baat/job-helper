"""Job Helper - the app window. Started by the "Start Job Helper" file."""
from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from jobbot import llm, pipeline, render, store

st.set_page_config(page_title="Job Helper", page_icon="💼", layout="wide")

settings = store.load_settings()
profile = store.load_profile()
resume = store.load_resume()

DECLINE = "Prefer not to say"

# ------------------------------------------------------------------ sidebar / navigation

setup_done = {
    "key": bool(settings.get("api_key")),
    "resume": bool(resume),
    "questions": bool(profile.get("answers", {}).get("work_authorization")),
}
st.sidebar.title("💼 Job Helper")
st.sidebar.caption("Finds data science jobs, tailors your resume, and fills in applications.")
pages = ["🏠 Start here", "1️⃣ Set up", "2️⃣ Find jobs", "3️⃣ Apply", "📊 My applications"]
if "page" not in st.session_state:
    st.session_state.page = pages[0] if all(setup_done.values()) else pages[1]
page = st.sidebar.radio("Go to", pages, key="page", label_visibility="collapsed")
st.sidebar.divider()
st.sidebar.markdown(
    "\n".join(f"{'✅' if ok else '⬜'} {label}" for ok, label in [
        (setup_done["key"], "Claude key added"), (setup_done["resume"], "Resume added"),
        (setup_done["questions"], "Questions answered")]))
st.sidebar.caption(f"Your files are saved in:\n\n`{store.DATA_DIR}`")


def need_setup() -> bool:
    if not all(setup_done.values()):
        st.warning("Finish **1️⃣ Set up** first (left side).")
        return True
    return False


def friendly_error(e: Exception) -> str:
    s = str(e)
    if "authentication" in s.lower() or "api key" in s.lower() or "401" in s:
        return "Your Claude key didn't work. Go to 1️⃣ Set up and paste it again."
    if "credit" in s.lower() or "billing" in s.lower():
        return "Your Claude account is out of credit. Add credit at console.anthropic.com → Billing."
    return f"Something went wrong: {s}"


# ------------------------------------------------------------------ home

if page == pages[0]:
    st.title("Welcome to Job Helper 👋")
    st.markdown("""
**How it works**

1. **Set up** (one time, ~15 minutes): add your Claude key, upload your resume, and answer the standard
   application questions (work authorization, demographics, etc.) once.
2. **Find jobs**: one click checks hundreds of companies for data science / analytics / ML roles and rates
   how good a match each one is for you.
3. **Apply**: for each job, Job Helper writes a version of your resume tailored to that job (never adding
   anything untrue), opens the application, and fills in every question for you. You look it over and
   press **Submit**.
4. **My applications** keeps track of everything you've applied to.
""")
    jobs = store.list_jobs()
    c1, c2, c3 = st.columns(3)
    c1.metric("Jobs found", len(jobs))
    c2.metric("Good matches waiting", sum(1 for j in jobs if j["status"] in ("new", "ready") and (j["fit_score"] or 0) >= settings["min_fit_score"]))
    c3.metric("Applied", sum(1 for j in jobs if j["status"] == "applied"))

# ------------------------------------------------------------------ setup

elif page == pages[1]:
    st.title("1️⃣ Set up")
    t_key, t_resume, t_q, t_pref = st.tabs(["A. Claude key", "B. Your resume", "C. Application questions", "D. What jobs you want"])

    with t_key:
        st.subheader("Connect Claude (the AI that tailors resumes and answers questions)")
        st.markdown("""
1. Go to **[console.anthropic.com](https://console.anthropic.com)** and make an account.
2. Click **Billing** and add some credit ($10 is plenty to start; each application costs roughly 10–25 cents).
3. Click **API Keys** → **Create Key**, then copy the key (it starts with `sk-ant-`).
4. Paste it below and click **Save**.
""")
        key = st.text_input("Your Claude key", value=settings.get("api_key", ""), type="password")
        if st.button("Save key", type="primary"):
            settings["api_key"] = key.strip()
            store.save_settings(settings)
            try:
                llm.client(settings).models.retrieve(llm.MODEL)
                st.success("Key works! ✅ Now go to tab **B. Your resume**.")
            except Exception as e:  # noqa: BLE001
                st.error(friendly_error(e))

    with t_resume:
        st.subheader("Your master resume")
        st.markdown("Upload your current resume (PDF or Word). Include **everything** – every job, project, "
                    "and skill. Job Helper picks the most relevant parts for each job, so more is better here.")
        up = st.file_uploader("Resume file", type=["pdf", "docx", "txt"])
        notes = st.text_area("Optional: anything that's NOT on your resume but could be (extra projects, "
                             "skills, coursework, clubs). Be specific and truthful.", height=100)
        if up and st.button("Read my resume", type="primary", disabled=not settings.get("api_key")):
            with st.spinner("Reading your resume… (about 30 seconds)"):
                try:
                    with tempfile.TemporaryDirectory() as td:
                        path = Path(td) / up.name
                        path.write_bytes(up.getvalue())
                        resume = llm.resume_from_file(settings, path, notes)
                    store.save_resume(resume)
                    p = profile.setdefault("contact", {})
                    first, _, last = resume["name"].partition(" ")
                    p.setdefault("first_name", first)
                    p.setdefault("last_name", last)
                    p.setdefault("email", resume["email"])
                    p.setdefault("phone", resume["phone"])
                    for l in resume["links"]:
                        if "linkedin" in l.lower():
                            p.setdefault("linkedin", l)
                        elif "github" in l.lower():
                            p.setdefault("github", l)
                    store.save_profile(profile)
                    st.success("Got it! Check the preview below, then go to tab **C**.")
                except Exception as e:  # noqa: BLE001
                    st.error(friendly_error(e))
        if resume:
            st.divider()
            st.markdown("**Preview of your master resume** (tailored versions will be shorter, one page).")
            if st.button("Show preview PDF"):
                with st.spinner("Making PDF…"):
                    pdf = render.render_pdf(resume, store.DATA_DIR / "Master_Resume_Preview.pdf", one_page=False)
                st.download_button("⬇️ Download preview", pdf.read_bytes(), "Master_Resume.pdf", "application/pdf")
            with st.expander("Fix a mistake (advanced: edit the text directly)"):
                txt = st.text_area("Resume data", json.dumps(resume, indent=2, ensure_ascii=False), height=400)
                if st.button("Save changes"):
                    try:
                        store.save_resume(json.loads(txt))
                        st.success("Saved.")
                    except json.JSONDecodeError as e:
                        st.error(f"There's a typo in the text (line {e.lineno}). Nothing was saved.")

    with t_q:
        st.subheader("Answer these once – Job Helper uses them on every application")
        st.caption("These are the same questions job applications ask. Answer truthfully; for the voluntary "
                   "demographic questions you can always choose “Prefer not to say”. Your answers stay on this computer "
                   "and are only sent to Claude to fill in forms.")
        c = profile.get("contact", {})
        a = profile.get("answers", {})

        def sel(label, options, key, help=None, src=a):
            cur = src.get(key)
            return st.selectbox(label, options, index=options.index(cur) if cur in options else 0, help=help)

        with st.form("questions"):
            st.markdown("##### Contact")
            col1, col2 = st.columns(2)
            first = col1.text_input("First name", c.get("first_name", ""))
            last = col2.text_input("Last name", c.get("last_name", ""))
            preferred = col1.text_input("Preferred name (optional)", c.get("preferred_name", ""))
            pronouns = col2.text_input("Pronouns (optional)", c.get("pronouns", ""))
            email = col1.text_input("Email", c.get("email", ""))
            phone = col2.text_input("Phone", c.get("phone", ""))
            address = st.text_input("Street address (some applications require it)", c.get("address", ""))
            col1, col2, col3, col4 = st.columns(4)
            city = col1.text_input("City", c.get("city", ""))
            state = col2.text_input("State", c.get("state", ""))
            zipc = col3.text_input("ZIP code", c.get("zip", ""))
            country = col4.text_input("Country", c.get("country", "United States"))
            col1, col2, col3 = st.columns(3)
            linkedin = col1.text_input("LinkedIn URL", c.get("linkedin", ""))
            github = col2.text_input("GitHub URL", c.get("github", ""))
            website = col3.text_input("Portfolio / website", c.get("website", ""))

            st.markdown("##### Work authorization")
            auth = sel("What is your work authorization in the US?", [
                "", "US citizen", "US permanent resident (green card)", "F-1 student visa (eligible for CPT/OPT)",
                "Other work visa (H-1B, etc.)", "Not authorized to work in the US"], "work_authorization")
            spons = sel("Will you now or in the future need visa sponsorship to work in the US?",
                        ["", "No", "Yes"], "needs_sponsorship",
                        help="F-1 students usually answer Yes (OPT/STEM OPT eventually ends).")
            citizenship = st.text_input("Country (or countries) of citizenship", a.get("citizenship", ""))
            clearance = sel("Security clearance", ["None", "Have an active clearance", "Eligible / willing to obtain"], "clearance")

            st.markdown("##### School & availability")
            col1, col2, col3 = st.columns(3)
            school = col1.text_input("Current/most recent school", a.get("school", ""))
            degree = col2.text_input("Degree (e.g. B.S. Data Science)", a.get("degree", ""))
            grad = col3.text_input("Graduation month & year", a.get("graduation_date", ""))
            col1, col2, col3 = st.columns(3)
            gpa = col1.text_input("GPA (leave blank to not share)", a.get("gpa", ""))
            year = col2.selectbox("Year in school", ["", "Freshman", "Sophomore", "Junior", "Senior", "Master's", "PhD", "Graduated"],
                                  index=["", "Freshman", "Sophomore", "Junior", "Senior", "Master's", "PhD", "Graduated"].index(a.get("year_in_school", "")) if a.get("year_in_school", "") in ["", "Freshman", "Sophomore", "Junior", "Senior", "Master's", "PhD", "Graduated"] else 0)
            start = col3.text_input("When can you start? (e.g. May 2027)", a.get("start_date", ""))
            terms = st.multiselect("Which terms are you looking for?", ["Summer 2027", "Fall 2027", "Spring 2027", "Winter 2026",
                                                                          "Full-time after graduation"], a.get("terms", ["Summer 2027"]))
            col1, col2 = st.columns(2)
            relocate = col1.selectbox("Willing to relocate?", ["Yes", "No", "Depends on location"],
                                      index=["Yes", "No", "Depends on location"].index(a.get("relocate", "Yes")))
            onsite = col2.selectbox("Open to in-office / hybrid work?", ["Yes", "Remote only", "Hybrid or remote only"],
                                    index=["Yes", "Remote only", "Hybrid or remote only"].index(a.get("onsite", "Yes")))
            col1, col2 = st.columns(2)
            salary = col1.text_input("Pay expectation (e.g. “$30/hour” or “Open”)", a.get("salary", "Open / negotiable"))
            heard = col2.text_input("Default answer for “How did you hear about us?”", a.get("how_heard", "Company website"))
            noncompete = st.selectbox("Are you bound by a non-compete or similar agreement?", ["No", "Yes"],
                                      index=["No", "Yes"].index(a.get("non_compete", "No")))
            relatives = st.text_input("Previously worked at / have relatives at any companies? (list them, or leave blank)",
                                      a.get("prior_employers_or_relatives", ""))

            st.markdown("##### Voluntary self-identification (equal opportunity questions)")
            st.caption("Employers ask these to measure diversity; answering is optional and doesn't affect your application.")
            col1, col2 = st.columns(2)
            gender = col1.selectbox("Gender", [DECLINE, "Woman", "Man", "Non-binary", "Other"],
                                    index=[DECLINE, "Woman", "Man", "Non-binary", "Other"].index(a.get("gender", DECLINE)))
            hispanic = col2.selectbox("Hispanic or Latino?", [DECLINE, "Yes", "No"],
                                      index=[DECLINE, "Yes", "No"].index(a.get("hispanic_latino", DECLINE)))
            races = ["American Indian or Alaska Native", "Asian", "Black or African American", "Hispanic or Latino",
                     "Middle Eastern or North African", "Native Hawaiian or Other Pacific Islander", "White", "Two or more races"]
            race = st.multiselect("Race / ethnicity (choose all that apply, or leave empty for “Prefer not to say”)",
                                  races, [r for r in a.get("race", []) if r in races])
            col1, col2, col3 = st.columns(3)
            veteran = col1.selectbox("Veteran status", [DECLINE, "I am not a protected veteran", "I am a protected veteran"],
                                     index=[DECLINE, "I am not a protected veteran", "I am a protected veteran"].index(a.get("veteran", DECLINE)))
            disability = col2.selectbox("Disability", [DECLINE, "No, I do not have a disability", "Yes, I have a disability"],
                                        index=[DECLINE, "No, I do not have a disability", "Yes, I have a disability"].index(a.get("disability", DECLINE)))
            lgbtq = col3.selectbox("LGBTQ+?", [DECLINE, "Yes", "No"], index=[DECLINE, "Yes", "No"].index(a.get("lgbtq", DECLINE)))
            col1, col2 = st.columns(2)
            trans = col1.selectbox("Transgender?", [DECLINE, "Yes", "No"], index=[DECLINE, "Yes", "No"].index(a.get("transgender", DECLINE)))
            orientation = col2.selectbox("Sexual orientation", [DECLINE, "Heterosexual / straight", "Gay or lesbian", "Bisexual", "Other"],
                                         index=[DECLINE, "Heterosexual / straight", "Gay or lesbian", "Bisexual", "Other"].index(a.get("sexual_orientation", DECLINE)))

            st.markdown("##### Anything else")
            extra = st.text_area("Other facts applications might ask about (languages you speak, willingness to travel, "
                                 "certifications, etc.)", a.get("extra", ""), height=80)
            if st.form_submit_button("💾 Save my answers", type="primary"):
                if not auth or not spons:
                    st.error("Please answer the two work authorization questions – applications almost always require them.")
                else:
                    profile["contact"] = dict(first_name=first, last_name=last, preferred_name=preferred, pronouns=pronouns,
                                              email=email, phone=phone, address=address, city=city, state=state, zip=zipc,
                                              country=country, linkedin=linkedin, github=github, website=website)
                    profile["answers"] = dict(
                        work_authorization=auth, needs_sponsorship=spons, citizenship=citizenship, clearance=clearance,
                        school=school, degree=degree, graduation_date=grad, gpa=gpa, year_in_school=year, start_date=start,
                        terms=terms, relocate=relocate, onsite=onsite, salary=salary, how_heard=heard, non_compete=noncompete,
                        prior_employers_or_relatives=relatives, gender=gender, hispanic_latino=hispanic,
                        race=race or [DECLINE], veteran=veteran, disability=disability, lgbtq=lgbtq, transgender=trans,
                        sexual_orientation=orientation, extra=extra)
                    store.save_profile(profile)
                    st.success("Saved ✅  Last step: tab **D**, then go to **2️⃣ Find jobs**.")

    with t_pref:
        st.subheader("What jobs should Job Helper look for?")
        with st.form("prefs"):
            types = st.multiselect("Job types", ["Internship", "New grad", "Full-time"], settings["job_types"])
            locs = st.text_input("Preferred locations, separated by commas (leave blank for anywhere in the US)",
                                 ", ".join(settings["locations"]))
            remote = st.checkbox("Include remote jobs", settings["remote_ok"])
            st.markdown("**Where to look**")
            src_s = st.checkbox("SimplifyJobs internship & new-grad lists (recommended – thousands of up-to-date jobs)",
                                settings["sources"]["simplify"])
            src_c = st.checkbox("Company job boards listed in companies.yaml (recommended)", settings["sources"]["company_boards"])
            src_i = st.checkbox("Indeed / LinkedIn / Google Jobs (slower and less reliable)", settings["sources"]["indeed_linkedin"])
            st.markdown("**Matching**")
            kw = st.text_area("Job title keywords (one per line)", "\n".join(settings["role_keywords"]), height=150)
            ex = st.text_input("Skip titles containing (comma separated)", ", ".join(settings["exclude_keywords"]))
            col1, col2 = st.columns(2)
            min_score = col1.slider("Only show jobs rated at least…", 0, 100, int(settings["min_fit_score"]))
            max_scored = col2.number_input("How many new jobs to rate per search (each costs ~1–2 cents)", 5, 300,
                                           int(settings["max_scored_per_run"]))
            st.markdown("**Applying**")
            auto = st.checkbox("Submit automatically when every question was answered confidently "
                               "(Greenhouse / Lever / Ashby sites only). Leave OFF to check each application yourself first.",
                               settings["auto_submit"])
            cl = st.checkbox("Also write a cover letter for each job", settings["write_cover_letters"])
            if st.form_submit_button("💾 Save", type="primary"):
                settings.update(job_types=types or ["Internship"], locations=[l.strip() for l in locs.split(",") if l.strip()],
                                remote_ok=remote, sources={"simplify": src_s, "company_boards": src_c, "indeed_linkedin": src_i},
                                role_keywords=[k.strip().lower() for k in kw.splitlines() if k.strip()],
                                exclude_keywords=[k.strip().lower() for k in ex.split(",") if k.strip()],
                                min_fit_score=min_score, max_scored_per_run=int(max_scored), auto_submit=auto,
                                write_cover_letters=cl)
                store.save_settings(settings)
                st.success("Saved ✅  Now go to **2️⃣ Find jobs**.")

# ------------------------------------------------------------------ find jobs

elif page == pages[2]:
    st.title("2️⃣ Find jobs")
    if not need_setup():
        st.markdown("Click the button to search for new jobs. Job Helper reads each new posting and rates how well it "
                    "fits you (0–100). This takes a few minutes – you can leave it running.")
        if st.button("🔎 Find new jobs", type="primary"):
            bar = st.progress(0.0, "Starting…")
            try:
                r = pipeline.find_and_score(lambda m, f=None: bar.progress(f if f is not None else 0.0, m))
                bar.progress(1.0, "Done!")
                st.success(f"Checked {r['found']} matching postings – {r['new']} were new, and {r['scored']} were rated. "
                           "Go to **3️⃣ Apply** to start applying.")
            except Exception as e:  # noqa: BLE001
                st.error(friendly_error(e))
        jobs = [j for j in store.list_jobs(["new", "ready"]) if j["fit_score"] is not None]
        unrated = len([j for j in store.list_jobs(["new"]) if j["fit_score"] is None])
        if unrated:
            st.info(f"{unrated} more jobs are waiting to be rated – click **Find new jobs** again to rate the next batch.")
        if jobs:
            df = pd.DataFrame([{"Match": j["fit_score"], "Company": j["company"], "Job": j["title"], "Where": j["location"][:60],
                                "Why": j["fit_reason"], "Link": j["url"]} for j in jobs])
            st.dataframe(df, hide_index=True, use_container_width=True,
                         column_config={"Match": st.column_config.ProgressColumn("Match", min_value=0, max_value=100, format="%d"),
                                        "Link": st.column_config.LinkColumn("Link", display_text="open")})

# ------------------------------------------------------------------ apply

elif page == pages[3]:
    st.title("3️⃣ Apply")
    if not need_setup():
        jobs = [j for j in store.list_jobs(["ready", "new"]) if (j["fit_score"] or 0) >= settings["min_fit_score"]]
        if not jobs:
            st.info("No jobs waiting. Go to **2️⃣ Find jobs** first.")
        else:
            mode = "submit automatically when it's safe" if settings["auto_submit"] else "fill everything in, then **you** press Submit"
            st.markdown(f"For each job, Job Helper tailors your resume, opens the application in a new browser window, "
                        f"and fills it in. Current setting: **{mode}** (change in 1️⃣ Set up → D).")
            st.info("When the browser window opens: wait until the colored bar at the top says it's done, check the "
                    "answers (anything outlined in **red** needs you), press **Submit**, then **close that window** "
                    "to move on.")
            colA, colB = st.columns([1, 2])
            n = colA.number_input("How many in a row?", 1, 50, 5)
            if colB.button(f"▶️ Apply to the top {n} jobs, one after another", type="primary"):
                status = st.status("Working…", expanded=True)
                for j in jobs[: int(n)]:
                    status.write(f"**{j['company']} – {j['title']}**: tailoring resume…")
                    try:
                        if j["status"] != "ready":
                            pipeline.prepare(j["id"])
                        status.write("Opening the application window…")
                        res = pipeline.launch_application(j["id"], settings["auto_submit"])
                        status.write("✅ Submitted" if res.get("submitted") else
                                     "↪️ Not confirmed as submitted – it's in **My applications** to double check.")
                    except Exception as e:  # noqa: BLE001
                        status.write(f"⚠️ {friendly_error(e)}")
                        store.update_job(j["id"], status="failed", notes=str(e)[:500])
                status.update(label="All done!", state="complete")
            st.divider()
            st.subheader("Or pick one job at a time")
            for j in jobs[:40]:
                with st.expander(f"{j['fit_score']}  ·  {j['company']} – {j['title']}  ·  {j['location'][:50]}"):
                    st.caption(j["fit_reason"] or "")
                    st.markdown(f"[Open the job posting]({j['url']})")
                    c1, c2, c3 = st.columns(3)
                    if c1.button("✍️ Tailor my resume", key="t" + j["id"]):
                        with st.spinner("Tailoring… (about a minute)"):
                            try:
                                j = pipeline.prepare(j["id"])
                            except Exception as e:  # noqa: BLE001
                                st.error(friendly_error(e))
                    if j.get("resume_path") and Path(j["resume_path"]).exists():
                        notes = json.loads(j["tailor_notes"] or "{}")
                        st.markdown("**What was changed for this job:**\n" + "\n".join(f"- {x}" for x in notes.get("changes", [])))
                        if notes.get("missing_keywords"):
                            st.markdown("**Things the job wants that your resume doesn't show:** " + ", ".join(notes["missing_keywords"]))
                        st.download_button("⬇️ Tailored resume (PDF)", Path(j["resume_path"]).read_bytes(),
                                           Path(j["resume_path"]).name, "application/pdf", key="d" + j["id"])
                        if c2.button("🚀 Open & fill application", key="a" + j["id"], type="primary"):
                            with st.spinner("A browser window is open – finish there, then close it."):
                                res = pipeline.launch_application(j["id"], settings["auto_submit"])
                            if res.get("submitted"):
                                st.success("Submitted! 🎉")
                            else:
                                st.warning("Not confirmed as submitted. " + " ".join(res.get("issues", [])[:5]))
                    if c3.button("🙅 Not interested", key="s" + j["id"]):
                        store.update_job(j["id"], status="skipped")
                        st.rerun()

# ------------------------------------------------------------------ tracker

elif page == pages[4]:
    st.title("📊 My applications")
    jobs = [j for j in store.list_jobs() if j["status"] in ("applied", "needs you", "failed", "ready")]
    if not jobs:
        st.info("Nothing here yet.")
    else:
        df = pd.DataFrame([{
            "Status": j["status"], "Company": j["company"], "Job": j["title"],
            "Applied": datetime.fromtimestamp(j["applied_at"]).strftime("%b %d") if j["applied_at"] else "",
            "Notes": j["notes"] or "", "Link": j["url"]} for j in jobs])
        st.dataframe(df, hide_index=True, use_container_width=True,
                     column_config={"Link": st.column_config.LinkColumn("Link", display_text="open")})
        st.download_button("⬇️ Download as spreadsheet (CSV)", df.to_csv(index=False), "my_applications.csv", "text/csv")
        st.subheader("Fix a status")
        st.caption("Submitted something yourself, or Job Helper wasn't sure? Update it here.")
        options = {f"{j['company']} – {j['title']} ({j['status']})": j["id"] for j in jobs}
        pick = st.selectbox("Job", list(options))
        c1, c2, c3 = st.columns(3)
        if c1.button("✅ I applied"):
            store.update_job(options[pick], status="applied", applied_at=time.time())
            st.rerun()
        if c2.button("🔁 Try the application again"):
            res = pipeline.launch_application(options[pick], False)
            st.rerun()
        if c3.button("🙅 Remove"):
            store.update_job(options[pick], status="skipped")
            st.rerun()
