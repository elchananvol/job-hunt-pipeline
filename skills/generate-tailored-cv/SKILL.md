---
name: generate-tailored-cv
description: Create a truthful, ATS-friendly tailored CV in Markdown and PDF from a job description and the candidate's private career notes.
---

# Generate a tailored CV

Use this workflow when the user asks for a CV or resume for a job posting, or for the `ready_for_cv` queue. Run commands from the repository root. Generate Markdown first, then render and check the PDF.

## Candidate source of truth

Read every Markdown file in `personal_files/cv_sections/`, beginning with `00-INDEX.md` if present. Also read `personal_files/feedback_cv.md` for reusable corrections. These are private, user-owned inputs. Apply any explicit writing rules in those files. The generic examples under `examples/` are setup prompts, not facts to put in a CV.

If the directory is missing, only contains starter prompts, or lacks the candidate's name, contact details, and documented experience or education, ask the user to complete it before drafting. Never use another candidate's employers, dates, metrics, skills, contact details, or life story. Do not invent accomplishments. If the source files conflict, clarify the conflict or use the more conservative supported statement.

## Job input

For a tracked job, get the posting from `python ai_helpers/cv_helper.py extract` or the relevant helper queue. For a one-off request, use the job description the user provides. Identify title, company, responsibilities, required skills, preferred skills, and seniority. Verify claims against the candidate files.

## Drafting

- Write in the candidate's preferred language if specified; otherwise use the posting's language when practical.
- Keep a single-column, ATS-friendly Markdown layout with conventional headings. Avoid tables, columns, images, icons, and unsupported formatting.
- Prioritize relevant, evidenced achievements. Keep dates, employers, role order, technical depth, and wording consistent with the user's source notes and CV lessons.
- Include exact job keywords only when the candidate has the corresponding documented skill or experience.
- Aim for a readable one-page CV. Trim less relevant content if the PDF overflows; do not pad to fill the page.

Write the files under `personal_files/cv_generated/<date-time>-<company-slug>/`, using safe filenames based on the candidate, role, and company. This directory is local and ignored by Git.

## Render and verify

```text
python ai_helpers/generate_pdf.py "personal_files/cv_generated/<folder>/<name>.md" "personal_files/cv_generated/<folder>/<name>.pdf"
```

Read the Markdown before rendering. Check that the PDF exists and is one page. If the renderer warns of overflow, shorten the Markdown and render again. Report both paths and any unresolved source uncertainty.

For a tracked pipeline job, record the verified files:

```text
python ai_helpers/cv_helper.py save_cv <job_id> "<md_path>" "<pdf_path>"
```

This updates the job record and moves `ready_for_cv` or `cv_review` to `cv_ready`. For an `applied` job, it records the files without changing that stage. For an untracked posting, do not update the database.

## Batch mode

`python ai_helpers/cv_helper.py extract` writes the `ready_for_cv` queue to `temp/ready_for_cv_jobs.json`. Process every returned job unless the user limits the batch. Finish verification and `save_cv` for one job before moving to the next. Use `review_queue` for CV feedback and `applied_missing` for applied jobs without a PDF.
