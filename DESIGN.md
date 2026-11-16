# Job Pipeline design

This document describes the current implementation. The [README](README.md) covers installation and daily use.

## Architecture

```text
React UI  ⇄  FastAPI  ⇄  db/jobs.db
                 ⇅
          JobSpy fetcher

AI coding agent  ⇄  skills/ + ai_helpers/  ⇄  db/jobs.db + personal_files/
```

The web app has no model client. FastAPI serves the board, handles manual edits, launches background fetches, stores feedback, and serves generated CV files. An agent works on request, reading the shared skill instructions, candidate files, and helper queues. This keeps AI scoring and CV writing separate from UI navigation.

## Data boundaries

- `fetched_jobs` stores scraped or manually entered postings, deduplicated by job URL.
- `job_match` stores score components, explanations, pipeline status, feedback, saved flag, application stage, and generated CV paths.
- `fetch_runs` and `fetch_attempts` record fetch history and errors. The fetcher saves data as pages complete so one failed site does not discard earlier results.
- `personal_files/` contains the current candidate's profile, target criteria, CV source notes, feedback lessons, and generated CVs. It is ignored by Git and mounted into Docker from the host.
- `examples/personal_files/` contains generic setup prompts only. The initializer copies missing files without overwriting user files.

SQLite schema creation and upgrades happen through `server.jobs_db.init_db()`, called by the server and helpers. `db/jobs.db` is the source of truth for the board and agent queues.

## Stage flow

```text
New → Scored → Ready for CV → CV Ready → Applied
         ↘ Score Review ↗      ↘ CV Review ↗
Any relevant stage → Dismissed
```

The UI can also move a job directly to `Ready for CV` or `Applied` when the user chooses. Scoring feedback puts a job in `score_review`; CV feedback puts it in `cv_review`. The agent workflows process these queues and return the job to `scored` or `cv_ready`. `is_saved` is an independent flag. An `applied` job can have an outcome such as phone screen, interview, offer, rejection, ghosted, or withdrawn.

The candidate applies outside the app. AI workflows must not mark an application as submitted.

## AI workflows

The canonical instructions are in `skills/`. `AGENTS.md` lists the workflow for each request. Platform entry points for Claude Code, Antigravity, and OpenCode refer to the same files. Agents must read the user's private files; they must not use generic examples or another candidate's history as facts.

Scoring records two components: candidate-to-job (ability to perform the core work) and job-to-candidate (fit with the user's goals). The current final score is `round(C2J × 0.4 + J2C × 0.6)`, or zero for a verified deal-breaker. A score includes a short job brief, reasoning, fits, and gaps. The `save_score` helper writes the board-compatible fields and advances a `new` job to `scored`.

CV generation writes Markdown under `personal_files/cv_generated/`, renders a PDF with `ai_helpers/generate_pdf.py`, verifies the one-page result, then uses `ai_helpers/cv_helper.py save_cv` to link both files to a tracked job. Untracked one-off CVs stay outside the database.

## Runtime and deployment boundary

- Local run: FastAPI serves the built `ui/dist/` and API on port 8000. A fresh clone needs one `npm ci` and `npm run build` before this works.
- Optional UI development: Vite on port 5173 proxies API and file requests to FastAPI on port 8000 for live updates.
- Docker Compose: the multi-stage image builds the UI and FastAPI serves it on port 8000; `db/` and `personal_files/` are bind mounted.
- There is no authentication or authorization layer. The server is intended for local use, not public hosting.
- The Docker image runs the web app. Agent tools and skill files are used from the host checkout.

`jobsworm_jobs/` is a separate side project. It and dependency directories such as `node_modules/` are outside the published application.
