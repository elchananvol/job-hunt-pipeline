---
name: score-job-match
description: Score new jobs against the candidate's own profile and target criteria; save evidence, two-way scores, fits, and gaps to the local database.
---

# Score job matches

Run from the repository root when the user asks to score new jobs. The UI and backend do not call an AI model. You are the scoring worker.

## Required inputs

Read these private files on every run:

1. `personal_files/profile_brief.md`: factual candidate background.
2. `personal_files/target_job.md`: the candidate's desired roles, preferences, and deal-breakers.
3. `personal_files/feedback_scoring.md`: later user corrections and reusable lessons.
4. The complete job description and metadata from the queue.

If the profile or target file is missing, empty, or still contains starter prompts, ask the user to complete it before scoring. Do not substitute facts from the example files or another person's CV. If a specific claim is missing or uncertain, say so in the reasoning rather than guessing.

## Queue and database tools

Use the helper from the repository root (`python` may be `py` on Windows):

```text
python ai_helpers/ai_scoring_helper.py counts
python ai_helpers/ai_scoring_helper.py get_unscored 20
python ai_helpers/ai_scoring_helper.py get_top 10
```

The queue contains unscored jobs in the `new` stage. Use `server.jobs_db.save_score(...)` for writes. Avoid manual SQL for scoring.

## Scoring method

Evaluate two questions separately on a 0–100 scale:

- **C2J:** Can this candidate perform the job's core responsibilities? Start with the duties, then compare required experience, skills, domain knowledge, and evidence in the profile. A missing keyword alone is not a gap if equivalent experience is documented. Distinguish bridgeable gaps from gaps that block the core work.
- **J2C:** Does the job fit the candidate's stated goals and constraints? Use the target file for role, seniority, location, work model, and other explicit preferences. Do not invent salary expectations or infer deal-breakers from silence.

Only set `is_dealbreaker=True` when the job verifiably triggers a stated exclusion or an unbridgeable requirement. Explain the evidence. There is no universal years-of-experience penalty or employer blacklist. The candidate's own files decide those judgments.

For compatibility with the current board, calculate `final_weighted_score = 0` for a deal-breaker; otherwise use `round(c2j_score * 0.4 + j2c_score * 0.6)`. The two component scores and their reasoning must make the result understandable.

Save each job using the existing API:

```python
from server.jobs_db import save_score

save_score(
    job_id=job["id"],
    job_brief="Factual two-to-four-sentence summary of the role",
    c2j_score=...,                 # integer 0–100
    c2j_reasoning="Evidence for ability to do the core work and gaps",
    j2c_score=...,                 # integer 0–100
    j2c_reasoning="Evidence for fit with the candidate's goals",
    matched_points="Newline-separated, supported fits",
    compensable_gaps="Newline-separated bridgeable gaps, or empty string",
    major_gaps="Newline-separated substantial gaps, or empty string",
    is_dealbreaker=False,
    dealbreaker_reason=None,
    final_weighted_score=...,
    scorer="model or agent identifier",
)
```

`save_score` mirrors the final score into the legacy board column and advances `new` to `scored`. Do not move jobs into other stages while scoring.

Reply with the top matches, component scores, a short explanation, and counts scored this run and still unscored.
