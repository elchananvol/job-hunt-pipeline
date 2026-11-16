#!/usr/bin/env python3
"""CLI for the CV skills (generate-tailored-cv, rework-cv).

Reads:
- `extract` — dump every `ready_for_cv` job to temp/ready_for_cv_jobs.json
- `review_queue` — `cv_review` rows + user CV feedback, as JSON
- `applied_missing` — `applied` rows with no PDF yet, as JSON

Writes:
- `save_cv <job_id> <md_path> <pdf_path>` — record the generated files and
  advance the row to `cv_ready` (from `ready_for_cv`/`cv_review`; for
  `applied` rows only the paths are recorded, status stays). Other stages
  are refused. Prints the resulting status.

Run from the repo root:
    py ai_helpers/cv_helper.py extract
    py ai_helpers/cv_helper.py save_cv 106 personal_files/cv_generated/.../cv.md personal_files/cv_generated/.../cv.pdf
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from ai_helpers.common import READY_FOR_CV_JSON, TEMP_DIR, get_conn, utcnow_iso

# Stages the AI may record CV paths for (DESIGN.md 3.3). Only the first two
# advance to cv_ready; `applied` rows keep their status (batch-CV flow).
CV_ADVANCE_FROM = ("ready_for_cv", "cv_review")


def extract_ready_for_cv(output: Path | None = None) -> list[dict]:
    """Dump all `ready_for_cv` jobs to a temp JSON file. Returns the jobs."""
    out = Path(output) if output else READY_FOR_CV_JSON
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT f.id, f.title, f.company, f.job_url, f.description,
                      f.location, f.date_posted
               FROM fetched_jobs f JOIN job_match m ON m.job_id = f.id
               WHERE m.status = 'ready_for_cv'
               ORDER BY f.id"""
        ).fetchall()
        jobs = [
            {
                "id": r[0],
                "title": r[1] or "",
                "company": r[2] or "",
                "job_url": r[3] or "",
                "description": r[4] or "",
                "location": r[5] or "",
                "date_posted": r[6] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(jobs, fh, ensure_ascii=False, indent=2)
    print(f"Extracted {len(jobs)} jobs to {out}")
    return jobs


def get_cv_review_queue() -> list[dict]:
    """`cv_review` rows with the user's CV feedback (rework skill input)."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT f.id, f.title, f.company, f.job_url, f.description,
                      m.cv_feedback, m.cv_feedback_at,
                      m.cv_md_path, m.cv_pdf_path
               FROM fetched_jobs f JOIN job_match m ON m.job_id = f.id
               WHERE m.status = 'cv_review'
               ORDER BY m.cv_feedback_at"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_applied_missing_cv() -> list[dict]:
    """`applied` rows with no PDF yet (batch-CV skill input)."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT f.id, f.title, f.company, f.job_url, f.description
               FROM fetched_jobs f JOIN job_match m ON m.job_id = f.id
               WHERE m.status = 'applied' AND m.cv_pdf_path IS NULL
               ORDER BY f.id"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_cv_paths(job_id: int, md_path: str, pdf_path: str) -> str:
    """Record CV files for a job.

    - `ready_for_cv` / `cv_review` rows advance to `cv_ready`.
    - `applied` rows keep their status (batch-CV flow) — only paths recorded.
    Returns the (possibly unchanged) status. Raises ValueError for any other
    stage — the AI must never touch scored/new/dismissed/review rows here.
    Feedback text is always kept as history (never blanked).
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT status FROM job_match WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown job_id: {job_id}")
        status = row["status"]
        now = utcnow_iso()
        if status in CV_ADVANCE_FROM:
            conn.execute(
                """UPDATE job_match SET cv_md_path = ?, cv_pdf_path = ?,
                          status = 'cv_ready', status_updated_at = ?
                   WHERE job_id = ?""",
                (md_path, pdf_path, now, job_id),
            )
            conn.commit()
            return "cv_ready"
        if status == "applied":
            conn.execute(
                """UPDATE job_match SET cv_md_path = ?, cv_pdf_path = ?,
                          status_updated_at = ?
                   WHERE job_id = ?""",
                (md_path, pdf_path, now, job_id),
            )
            conn.commit()
            return "applied"
        raise ValueError(
            f"job #{job_id} is '{status}' — save_cv only accepts "
            f"{list(CV_ADVANCE_FROM)} (advance to cv_ready) and 'applied' "
            f"(paths only, status stays)"
        )
    finally:
        conn.close()


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: py ai_helpers/cv_helper.py <command> [args]")
        print("Commands:")
        print("  extract [out.json]               - dump ready_for_cv queue to temp JSON")
        print("  review_queue                     - cv_review rows + feedback, as JSON")
        print("  applied_missing                  - applied rows with no PDF, as JSON")
        print("  save_cv <job_id> <md> <pdf>      - record CV files, advance to cv_ready")
        print(f"(default extract output: {READY_FOR_CV_JSON}, temp dir: {TEMP_DIR})")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "extract":
        out = Path(sys.argv[2]) if len(sys.argv) > 2 else None
        extract_ready_for_cv(out)
    elif cmd == "review_queue":
        _print_json(get_cv_review_queue())
    elif cmd == "applied_missing":
        _print_json(get_applied_missing_cv())
    elif cmd == "save_cv":
        if len(sys.argv) != 5:
            print("Usage: py ai_helpers/cv_helper.py save_cv <job_id> <md_path> <pdf_path>")
            sys.exit(1)
        try:
            status = save_cv_paths(int(sys.argv[2]), sys.argv[3], sys.argv[4])
        except ValueError as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)
        print(f"job #{sys.argv[2]} -> {status}")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
