# 💼 Job Helper

Job Helper finds **data science, analytics and machine-learning internships and jobs**. It writes a version of your resume tailored to each one, then **fills in the application for you**. You check it and press Submit.

---

## For you (the job seeker): how to start

### 1. Download it
Download the ZIP file you were sent, or click the green **Code** button on this page and then **Download ZIP**. Double-click the ZIP to unzip it, then move the **job-helper** folder somewhere easy to find, like your Desktop.

### 2. Open it
| Windows | Mac |
|---|---|
| Double-click **`Start Job Helper (Windows).bat`** | Double-click **`Start Job Helper (Mac).command`** |
| If you see "Windows protected your PC", click **More info**, then **Run anyway**. | If you see "can't be opened because it is from an unidentified developer", **right-click** the file, choose **Open**, then click **Open** again. |

A black window appears. **The first time, it takes about 5 minutes** to set itself up. Then Job Helper opens in your web browser.

Keep the black window open while you use Job Helper. Close it when you're finished. Next time, double-click the same file again.

### 3. Follow the steps in the app
The left side of the app walks you through it:

1. **Set up** (one time):
   - **A.** Get a Claude key: make an account at console.anthropic.com and add about $10 of credit. Each application costs roughly 10–25 cents.
   - **B.** Upload your resume. Include *everything* you've done; Job Helper picks the right parts for each job.
   - **C.** Answer the usual application questions once: work authorization, sponsorship, school, and the optional demographic questions.
   - **D.** Pick what kind of jobs you want.
2. **Find jobs**: click the button. It searches thousands of postings and gives each one a match score from 0–100.
3. **Apply**: click **Apply to the top 5 jobs**. For each job:
   - A browser window opens and fills itself in. **Don't touch it** until the bar at the top changes color.
   - Anything outlined in **red** needs you. Check the rest quickly too.
   - Press the site's **Submit** button, then **close that window**. The next job opens.
4. **My applications**: shows everything you've applied to. You can download it as a spreadsheet.

### Good to know
- **It never makes things up.** The tailored resume only rewords and reorders what's already on your resume. Numbers are never changed and no new skills are added. You can see what changed for each job in the **Apply** page.
- **Your answers are only used as you gave them.** For example, if you chose "Prefer not to say" for a demographic question, that's what it picks on every form.
- **Your files are saved** in a folder called **JobHelper** in your home folder. Tailored resumes are in **JobHelper → Tailored Resumes**. Downloading a newer version of Job Helper won't erase them.
- **Workday and some company websites** make you create an account first. On those sites, Job Helper fills in what it can, and you do the rest.
- **Something broke?** Take a screenshot of the black window and send it to whoever gave you Job Helper.

---

## For the person sharing it: how it works

| Part | How |
|---|---|
| **Job sources** | [SimplifyJobs](https://github.com/SimplifyJobs) internship and new-grad lists. Public Greenhouse, Lever and Ashby job-board feeds for the companies in `companies.yaml`. Optionally Indeed, LinkedIn and Google Jobs via `python-jobspy`, which is off by default because those sites block scrapers and it breaks their terms. |
| **Matching** | Job titles are filtered by keyword, then Claude rates each new posting 0–100 against the resume and flags blockers like sponsorship or required clearances (`jobbot/llm.py: score_job`). |
| **Tailoring** | The master resume is stored as structured data. Claude picks, reorders and rewords bullets, and each bullet must point back to a bullet in the master resume. The code then checks that the IDs exist, that no new numbers appear (otherwise the original wording is used), and that every skill is already on the resume. The result is a one-page PDF, made with an HTML template and Chromium (`jobbot/render.py`). |
| **Applying** | Playwright opens a visible Chromium window (`jobbot/apply.py`). The page script (`jobbot/formfill.js`) finds every question and its label and options: text fields, dropdowns, React-style comboboxes, radio buttons, checkboxes and Yes/No buttons. Contact fields are filled by rules. Claude answers everything else from the questionnaire and resume, choosing exact option text. Required fields left empty or uncertain are outlined in red. Auto-submit is opt-in, only works on Greenhouse, Lever and Ashby, and only runs when every required field is filled and no CAPTCHA is showing. |
| **Storage** | `~/JobHelper/` holds `settings.json` (with the API key), `profile.json`, `master_resume.json`, `jobs.db` (SQLite) and the PDFs. |

Tested against live Greenhouse, Lever and Ashby application forms without submitting.

### Run from source
```bash
uv run python -m jobbot.firstrun
```
```bash
uv run streamlit run app.py
```

### Adding companies
Edit `companies.yaml`. A company's board name is in its careers URL, for example `job-boards.greenhouse.io/<name>`, `jobs.lever.co/<name>` or `jobs.ashbyhq.com/<name>`.
