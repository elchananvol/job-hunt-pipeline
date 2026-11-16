---
name: rework-cv
description: Regenerate tailored CVs from user feedback, or batch-generate CVs for applied jobs. Use when the user says "rework the CVs", "fix the CVs in cv_review", "make CVs for everything marked apply", or after they save CV feedback in the UI (stage cv_review). Uses generate-tailored-cv per job, distills rules into feedback_cv.md.
---

# CV Rework + Batch CVs

Two flows, same engine (`generate-tailored-cv` skill + `generate_pdf.py`).
Flow A ends back at `cv_ready`; Flow B leaves `applied` untouched.
All queue reads and bookkeeping go through `ai_helpers/cv_helper.py`
(run from the repo root) — no manual SQL.

## Flow A — rework queue (status = 'cv_review')

Targets (prints id, title, company, description, `cv_feedback`, and the
current `cv_md/pdf_path` rows, oldest feedback first):

```
py ai_helpers/cv_helper.py review_queue
```

Per job:

1. Read `cv_feedback` + `personal_files/feedback_cv.md` (re-read the lesson
   file first) + the job description + `cv_sections/`.
2. Regenerate that job's CV via `generate-tailored-cv`, writing over the
   SAME filenames unless the feedback demands a rename — then update
   `cv_md_path` / `cv_pdf_path` to the new locations.
3. Record the result: `py ai_helpers/cv_helper.py save_cv <job_id> <md> <pdf>`
   (advances the row to `cv_ready`). **Keep `cv_feedback` as history.**
   AI moves `cv_review → cv_ready` only; never set applied/dismissed.
4. Distill at most one reusable rule into `personal_files/feedback_cv.md`
   (file's format). Skip job-specific remarks that generalize to nothing.

## Flow B — batch CVs (status = 'applied', no PDF yet)

Targets:

```
py ai_helpers/cv_helper.py applied_missing
```

Per job: run `generate-tailored-cv`, then record the files with
`py ai_helpers/cv_helper.py save_cv <job_id> <md> <pdf>` (for `applied`
rows it stores the paths and leaves the status alone). Status stays
`applied`.

## Reply

Per job: what was regenerated (paths) or why skipped. Then: rules added to
`feedback_cv.md`. Keep it short.
