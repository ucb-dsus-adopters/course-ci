# Data 8 Materials — Adopter Cleanup Checklist

Strip Berkeley-specific course-ops from `raw_notebooks/` so the materials are adopter-neutral.
Ordered roughly by how much each bit them this year.

## Golden rule: edit `raw_notebooks/` only
The **notebook-pipeline** runs on any PR touching `raw_notebooks/*.ipynb`: it regenerates
student/instructor/autograder **inside the base image** and commits them back to the PR, then
runs grader.check + otter grade. **Never hand-edit generated files** — the pipeline overwrites them.
- Public repo (`materials-fds-v2`) updates via the **Deploy notebooks** workflow (rsync from
  `student_notebooks/`) *after* merge. Deploy **does not delete** removed assignments — delete
  those from the public repo with a direct PR.
- Markdown-only edits still trigger a full regenerate/re-seed of the changed notebooks. Fine on the
  pinned base image; expect all changed autograder zips to show as binary diffs.
- Pure deletions (e.g. removing an assignment): commit with `[skip ci]` so the pipeline doesn't try
  to regenerate a notebook that's gone.

## Scripts (starting points — run, then review the diff)
Regex passes over `raw_notebooks/` markdown, one concern each. They take the course-repo root as
the only arg and edit in place. **Always review `git diff` before committing** — content drifts year
to year, and these are aids, not blind automation. Graded-question removals (§6) and branding (§9)
are still manual.

```bash
cd <course-repo>            # e.g. materials-fds-private-v2, on a fresh branch
python .../adopter_cleanup/strip_deadlines.py   "$(pwd)"   # §1 deadlines
python .../adopter_cleanup/strip_project_ops.py "$(pwd)"   # §2 project ## Logistics + platform names
python .../adopter_cleanup/strip_urls.py        "$(pwd)"   # §4/§5 Pensieve + Google-Drive links + pensieve.png
python .../adopter_cleanup/strip_lab_ops.py     "$(pwd)"   # §3 lab logistics (+ lab04 partner Q)
git diff                    # eyeball everything, then PR (pipeline regenerates)
```
Known sharp edges baked into the scripts: match Pensieve **links by URL**, not display text;
protect image filenames (`pensieve.png`) from the bare-word replace; the "expensive" substring is a
false positive for "pensive".

## What to remove

### 1. Deadlines
- hw intro cell: `**Deadline:**` + the paragraph under it
- project `## Logistics`: `**Deadline.**` + its paragraph
- lab intro: bold `**... Submission Deadline: ...**` line (labs 03/04/07)
- **lab01**: inline "released Mondays … due **Fridays at 5 PM** … due on **Friday, 1/23**" — *no bold
  Deadline marker*, easy to miss

### 2. Project `## Logistics` Berkeley-ops (all 6 projects)
Remove the **Checkpoint.**, **Partners.**, **Support.** labeled paragraphs; keep Rules/Tests/Advice/
point-table. They contain: Pensive/Pensieve submission, "assigned lab section", "1 lab score",
"docked 10%", office hours, Ed, TA/tutor, a Google-Drive walkthrough video.

### 3. Lab logistics (labs 01–09)
- "**Getting help on lab**" line (GSI/tutor) — remove
- lab-credit-policy block: "Lab is worth 20%", attendance-based / discussion portion,
  Regular-Labs vs Self-Service
- lab01 "administrative details" block (schedule/attendance) — keep the "Collaborating is
  encouraged" paragraph and "## Today's lab"

### 4. Submission platform (everywhere — hw, lab, project)
- `[Pensieve](https://www.pensieve.co/…)` links **and** bare "Pensieve"/"Pensive" → generic wording
  (we used "your course's submission platform"). **Match links by URL, not display text** — some were
  already partly neutralized, and a bare-word replace corrupts the href.
- Gradescope references
- **pensieve.png** — the "your submission should look like this" screenshot block in lab01 + the
  image file + its entry in the cell-0 `files:` manifest (also orphaned in hw01's manifest)
- Protect image filenames (`pensieve.png`) from the bare-word "Pensi…" replace

### 5. Resource links (Google Drive)
Drop the whole line for any `drive.google.com` link: Arrays Guide, Sampling Methods Guide,
Tabular Thinking Guide, Sampling Notes Document, "watch this video to see how to submit".

### 6. Graded Berkeley-logistics QUESTIONS — removal + regenerate, NOT markdown-only
These are `# BEGIN QUESTION … # END QUESTION` blocks with real tests. Delete the whole question
(the regenerate drops the test); check the assignment's point-total table afterward.
- **hw04 `q0_1`** — "Midterm Accommodations Form" (`secret_phrase`, test requires the survey phrase
  "valentine" via `hw04_check.correctness_check_0` → unpassable for adopters)
- **lab04** — "Project 1 Partner Form" (`submitted = True`, GSI-distributed form)

### 7. Project checkpoints — delete entirely
`project1c / 2c / 3c` are truncated subsets of project1/2/3 (first 9/16/14 questions), packaged only
for Berkeley's early-checkpoint submission. Every question already lives in the full project. Delete
raw + student + instructor + autograder_zips (and the public repo directly). `[skip ci]`.

### 8. Misc residuals
- "Official Project X Partner form" line
- Dated headers: `### Checkpoint (due Friday …)` → `### Checkpoint`
- hw04 section "0. Midterm Accommodations Form" header (goes with `q0_1`)
- `data8.org/sp26/reference/` Python-Reference links (Berkeley course site) — decide keep/generalize
- JupyTutor (Berkeley LLM assistant) mention in lab01
- Berkeley color hex in `<hr>`/`<span>`: `#003262` (blue), `#fdb515` (gold), `#BC412B` — cosmetic

### 9. Branding — deferred (bigger decision)
`data8logo.png` (all 28 notebooks) + "Data 8" name + alt text ("Data 8 Logo"). Rebrand = swap the
`src`, the alt text, and the logo file. (All images already have non-empty alt text — a11y is fine.)

## Infra / config gotchas (these cost real time this year)
- **ipykernel pin**: after a base-image bump, `requirements.txt` must pin the new ipykernel or the
  pipeline's `check_requirements_sync.py` fails. Run it with `--fix`. Root cause of the
  `np.True_` vs `True` grading bug was ipykernel **7.0.1** resetting numpy's print options per cell;
  fixed at **≥ 7.3.0**. Seed env and grade env must be the same ipykernel.
- **notebook-pipeline disk space**: the ~16 GB base image fills the runner ("no space left on
  device") — needs a "Free disk space" step before the pull (in `course-ci` `v1`, mirrors
  `regenerate.yml`).
- **otter-grader** auto-pins to the base image's version (currently **6.1.6**) in each zip's
  `environment.yml` — otter 7 won't leak in. Only moves on a deliberate base-image bump + test.
- **Fragile tests**: hash-of-optimizer-output checks (hw12 `q2_5`) fail across conda-seed vs
  pip-grade environments. Prefer `np.isclose` on the objective, not exact coordinates.
- **`_check.py` files** are hidden answer-validators the autograders import — **keep them**.

## Distribution
All adopter forks were clean fast-forwards (no one had diverged). To push a release: announce in the
upstream repo + email the adopter list with `Sync fork` / `gh repo sync` instructions. You cannot
force forks to sync.

## Reusable tooling
Regex-based strip scripts (operate on `raw_notebooks/` markdown, one concern each) built this year:
deadlines, project-ops, URLs+screenshot, lab-ops. Worth committing to `course-ci/tooling/` as an
`adopter_cleanup/` helper so next year is "run + review diffs" instead of "rediscover the patterns".
