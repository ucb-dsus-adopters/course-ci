# course-ci

Shared CI for the UCB DSUS adopter notebook courses (Data 8 / FDS, Data 6, …).

Each course repo used to carry its own copy of four GitHub Actions workflows **and** a set
of Python tooling scripts. They had already drifted (v2 had fixes v1 was missing). This
repo holds the single canonical copy: the workflows are **reusable** (`on: workflow_call`)
and the scripts live in [`tooling/`](tooling/). Each course repo keeps only a thin caller
workflow that triggers on the right event and calls the reusable workflow.

## Layout

```
.github/workflows/          reusable workflows (called by course repos)
  notebook-pipeline.yml        PR gate: regenerate + grade artifacts, commit back
  deploy-notebooks.yml         manual: a11y check + sync student_notebooks/ to a public repo
  standalone-grade-check.yml   manual: grade committed pairs in the otter-srv-stdalone image
  validate-candidate-image.yml manual: dry-run a candidate base-user-image before deploy
tooling/                     the scripts the workflows run
  otter_assign_runner.py
  deploy_notebooks.py
  tests/*.py                   graders + staging + checks
examples/callers/            copy these into each course repo's .github/workflows/
```

## How it runs (the dual-checkout model)

A reusable workflow does **two** checkouts:

- the **course content** (or the PR head) → `./course`
- **this repo's tooling** → `./ci`

Steps run with `working-directory: course` and invoke `python "$CI_TOOLING/…"`. Scripts
read the `COURSE_ROOT` env var (set to `./course`) to locate course content, instead of
deriving it from their own file location. Because all writes and `git add -A` happen inside
`./course`, the tooling checkout is never committed into a course PR.

`COURSE_ROOT` defaults to the current directory, so every script still works when run by
hand from inside a course checkout (e.g. `python tooling/tests/fetch_test_notebooks.py --all`).

## What a course repo must provide

Required directory layout (same as materials-fds-private / -v2):

```
raw_notebooks/<type>/<assignment>/<assignment>.ipynb     (type ∈ lab|hw|project)
student_notebooks/<type>/<assignment>/
instructor_notebooks/<type>/<assignment>/
autograder_zips/<type>/<assignment>/<assignment>-autograder.zip
otter_assign config + assign_config.yml, requirement pins   (as today)
```

Required **repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Example | Used by |
|---|---|---|
| `NOTEBOOK_DISTRIBUTOR_APP_ID` | `Iv1.abc123…` (App client id) | all push/PR steps |
| `DEPLOY_TARGET_REPO` | `ucb-dsus-adopters/materials-fds-v2` | notebook-pipeline reminder, deploy default |

Required **secrets** — set once at the **org** level (Settings → Secrets → Actions) and
made available to each course repo, so callers can use `secrets: inherit`:

| Secret | Purpose |
|---|---|
| `GCP_SA_KEY` | SA JSON with read on the GAR images |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook |
| `NOTEBOOK_DISTRIBUTOR_PRIVATE_KEY` | private key of the notebook-distributor GitHub App |

The GitHub App must be installed on both the course repo (push to PR head) and the deploy
target repo (open downstream PR).

## Adopting in a new course (e.g. Data 6)

1. Ensure the repo follows the layout above.
2. Add the two repo variables and confirm the org secrets + App installation reach it.
3. Copy the four files from [`examples/callers/`](examples/callers/) into
   `.github/workflows/` — they are identical for every course; no edits needed.
4. Open a PR touching a `raw_notebooks/**.ipynb` to exercise the pipeline.

## Versioning

Callers pin `@v1` and pass `ci_ref: v1`, so a course always runs a matching workflow +
tooling pair. Cut a release by tagging this repo (`git tag -f v1 && git push -f origin v1`,
or a fresh `v2`); bump both `@vN` and `ci_ref: vN` in the callers when you move a course up.

## Go-live checklist

See [`MIGRATION.md`](MIGRATION.md).
