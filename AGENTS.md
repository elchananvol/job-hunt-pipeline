# Project guidance for coding agents

This repository has a local web app and a separate AI-assisted workflow. The web app never calls a model. Read `README.md` for setup and `DESIGN.md` for architecture.

Before AI work, read the matching platform-neutral workflow in `skills/`:

| User request | Workflow |
|---|---|
| Score new jobs | `skills/score-job-match/SKILL.md` |
| Apply corrections to job scores | `skills/apply-match-feedback/SKILL.md` |
| Generate a CV for a job | `skills/generate-tailored-cv/SKILL.md` |
| Rework CVs from feedback | `skills/rework-cv/SKILL.md` |

Run helper commands from the repository root. Treat `personal_files/` as the current user's private data; `examples/personal_files/` contains generic setup prompts only. Never use examples as candidate facts. Do not copy, publish, or commit personal data, generated CVs, or the SQLite database. Confirm that real candidate files are complete before scoring or generating a CV.

The UI controls fetches and manual stage changes. AI scoring and CV generation happen only when the user asks the agent to do them. Applications are submitted by the user outside this app.
