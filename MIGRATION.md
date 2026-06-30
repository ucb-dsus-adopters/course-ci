# Go-live checklist

These are the **outward-facing** steps — they create an org repo, change org/repo settings,
and rewire live course CI. Do them in order. Nothing here has been done automatically.

> Reusable-workflow + script behavior can only be fully verified by an actual CI run.
> Migrate one repo first (materials-fds-private-v2), confirm a green pipeline, then do the rest.

## 1. Create and push the course-ci repo

```bash
cd course-ci
git add -A
git commit -m "Initial course-ci: reusable workflows + shared tooling"
gh repo create ucb-dsus-adopters/course-ci --private --source=. --remote=origin --push
git tag v1 && git push origin v1
```

## 2. Org-level secrets (once)

Set these as **organization** secrets visible to the course repos (or copy them to each
course repo if you prefer per-repo secrets):

```bash
gh secret set GCP_SA_KEY                     --org ucb-dsus-adopters --body "$(cat sa-key.json)"
gh secret set SLACK_WEBHOOK_URL              --org ucb-dsus-adopters --body "https://hooks.slack.com/…"
gh secret set NOTEBOOK_DISTRIBUTOR_PRIVATE_KEY --org ucb-dsus-adopters < distributor-app.pem
```

(They already exist on materials-fds-private and -v2; promoting them to org scope lets
every course inherit them via `secrets: inherit`. If you keep them per-repo, just make sure
each new course repo has all three.)

## 3. Per-course repo variables

For each course repo (materials-fds-private, materials-fds-private-v2, future Data 6):

```bash
R=ucb-dsus-adopters/materials-fds-private-v2
gh variable set NOTEBOOK_DISTRIBUTOR_APP_ID --repo "$R" --body "<app client id>"
gh variable set DEPLOY_TARGET_REPO          --repo "$R" --body "ucb-dsus-adopters/materials-fds-v2"
# materials-fds-private deploys to:          ucb-dsus-adopters/materials-fds
```

The App client id is currently in `vars.NOTEBOOK_DISTRIBUTOR_APP_ID` on each repo already —
reuse the same value.

## 4. Swap each course repo to the callers

In each course repo, on a branch:

```bash
cp <course-ci>/examples/callers/*.yml .github/workflows/
git rm .github/scripts/deploy_notebooks.py
git rm otter_assign_runner.py
git rm tests/check_requirements_sync.py tests/fetch_test_notebooks.py \
       tests/run_grader_check_tests.py tests/run_otter_grade_tests.py \
       tests/run_standalone_grade_check.py tests/report_image_env.py \
       tests/check_student_invariance.py tests/otter_grade_common.py
git add -A && git commit -m "ci: use shared ucb-dsus-adopters/course-ci reusable workflows"
```

The caller filenames match the originals, so they overwrite them cleanly. Keep any course
content under `tests/` that isn't tooling (there is none today — the dir only held scripts
and the runtime-generated `test_files/`).

## 5. Update branch protection (required status check name changes)

With reusable workflows the required check is reported as **`pipeline / pipeline`**
(caller job `pipeline` → reusable job `pipeline`), not the old `pipeline` name. Update the
branch protection rule on `main` (and `fix-*` working branches if protected) to require the
new check, or the gate will appear "pending forever."

```bash
gh api repos/ucb-dsus-adopters/materials-fds-private-v2/branches/main/protection \
  --jq '.required_status_checks.contexts'   # inspect current required checks
```

## 6. Verify, then roll out

- Open a PR in materials-fds-private-v2 that edits a `raw_notebooks/**.ipynb`.
- Confirm: pipeline regenerates artifacts, commits them back, comments the deploy reminder,
  Slack pings green, and the required check passes.
- Manually dispatch **Deploy notebooks** (apply_changes off) to confirm the a11y + validate
  path works against the shared tooling.
- Then repeat steps 3–5 for materials-fds-private and onboard Data 6 via the README.

## Rollback

Each course repo still has the old workflows + scripts in its git history; `git revert` the
swap commit to return to self-contained CI. course-ci can stay in place unused.
