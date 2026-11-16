---
name: apply-match-feedback
description: Rescore jobs the user left scoring feedback on with the two-way C2J/J2C engine. Use when the user says "rescore", "apply my feedback", "rescore according to notes", or after they save scoring feedback in the UI (stage score_review). Reads each job's match_feedback, adjusts C2J/J2C/final score, distills reusable rules into feedback_scoring.md, returns rows to scored.
---

# Apply Match Feedback (rescore queue)

Automatic over the `score_review` queue. The UI sets this stage when the
user saves scoring feedback; this skill drains the queue.

## Targets

Run from the repo root (no manual SQL — the helper prints the queue as JSON):

```
py ai_helpers/ai_scoring_helper.py score_review_queue
```

Columns per row: `id, title, company, job_url, description` + current
`match_pct / final_weighted_score / c2j_* / j2c_* / job_brief / fits / gaps /
dealbreaker` + `match_feedback / match_feedback_at` (oldest feedback first).

## Inputs per job

1. The job row + its current score and reasoning.
2. `job_match.match_feedback` — the user's correction. It wins over prior
   judgment on the points it covers.
3. `personal_files/profile_brief.md`, `personal_files/target_job.md`,
   `personal_files/feedback_scoring.md` — re-read the lessons file before
   starting; the new feedback may extend or override an old rule.

## Per job

1. Re-score honestly with the `score-job-match` C2J/J2C engine in light
   of the feedback (the user can be wrong too — if the old score was
   right, say so and keep it, but still explain). Recompute C2J, J2C,
   dealbreaker flag, and `final = round(C2J*0.4 + J2C*0.6)` (0 when
   dealbreaker). Refresh `job_brief` if the feedback shows the brief was
   wrong.
2. Update via `server.jobs_db.save_score(...)` with the full C2J/J2C kwargs (same
   call shape as `score-job-match`), then set `status = 'scored'`,
   `status_updated_at = now` via SQL. **Keep `match_feedback`
   as history — never blank it.** AI moves `score_review → scored` only;
   never set applied/dismissed/ready_for_cv/cv_ready/cv_review.
3. Distill at most one reusable rule per job into
   `personal_files/feedback_scoring.md`, appended in the file's format
   (`YYYY-MM-DD — job #<id> (<title> @ <company>): thought → correction.
   Rule: ...`). Skip job-specific remarks that generalize to nothing.

## Reply

Per job: old final% → new final% (with C2J/J2C split) + one-line what changed. Then: rules added to
`feedback_scoring.md`. Keep it short.
