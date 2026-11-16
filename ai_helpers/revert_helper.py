#!/usr/bin/env python3
"""
Revert helper — resets specific scoring/CV statuses.
Also optionally cleans up CV files from filesystem.
"""

import shutil
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from ai_helpers.common import CV_GEN_DIR, get_conn, utcnow_iso


def _delete_job_cv_files(job_id: int):
    """Delete CV files for a specific job from filesystem.
    Looks for directories in cv_generated/ matching the job's company name.
    Safe to call even if no files exist.
    """
    conn = get_conn()
    try:
        cur = conn.execute("SELECT company FROM fetched_jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        if not row:
            return
        company = row[0]
    finally:
        conn.close()

    if not company:
        return

    # Build filesystem-safe company name pattern
    safe_name = re.sub(r'[^a-zA-Z0-9\-]', '-', company).strip('-')
    if not safe_name:
        return

    # Find and remove matching directories
    if not CV_GEN_DIR.exists():
        return

    removed = 0
    for d in CV_GEN_DIR.iterdir():
        if d.is_dir():
            # Check if directory name contains the company name (case-insensitive)
            if safe_name.lower() in d.name.lower():
                try:
                    shutil.rmtree(d)
                    removed += 1
                except Exception:
                    pass  # Best effort

    if removed:
        print(f"  Cleaned {removed} CV directory/directories for company '{company}'")


def revert_code_engine_scores(delete_files: bool = False):
    """Revert all jobs scored by code-engine-v1 back to 'new' with cleared scores.

    Args:
        delete_files: If True, also delete CV files for each reverted job from filesystem.
    """
    conn = get_conn()
    try:
        cur = conn.execute("""
            SELECT job_id FROM job_match
            WHERE scorer LIKE '%code-engine%' AND final_weighted_score IS NOT NULL
        """)
        job_ids = [row['job_id'] for row in cur.fetchall()]

        if not job_ids:
            print("No code-engine-v1 scores found")
            return 0

        placeholders = ",".join("?" for _ in job_ids)
        now = utcnow_iso()

        # Clear scores and reset status
        conn.execute(f"""
            UPDATE job_match SET
                match_pct = NULL, explanation = NULL,
                job_brief = NULL, matched_points = NULL, gaps = NULL,
                c2j_score = NULL, c2j_reasoning = NULL,
                j2c_score = NULL, j2c_reasoning = NULL,
                compensable_gaps = NULL, major_gaps = NULL,
                is_dealbreaker = 0, dealbreaker_reason = NULL,
                final_weighted_score = NULL, scorer = NULL, scored_at = NULL,
                status = 'new', status_updated_at = ?
            WHERE job_id IN ({placeholders})
        """, (now, *job_ids))

        conn.commit()
        count = len(job_ids)
        print(f"Reverted {count} code-engine-v1 scored jobs to 'new'")

        # Optional: delete CV files per job
        if delete_files:
            for jid in job_ids:
                _delete_job_cv_files(jid)

        return count
    finally:
        conn.close()


def revert_cv_ready_to_ready_for_cv(delete_files: bool = False):
    """Revert cv_ready jobs back to ready_for_cv, clear CV paths.

    Args:
        delete_files: If True, also delete CV files for each reverted job from filesystem.
    """
    conn = get_conn()
    try:
        cur = conn.execute("""
            SELECT job_id, cv_md_path, cv_pdf_path FROM job_match
            WHERE status = 'cv_ready'
        """)
        rows = cur.fetchall()

        if not rows:
            print("No cv_ready jobs found")
            return 0

        job_ids = [row['job_id'] for row in rows]
        placeholders = ",".join("?" for _ in job_ids)
        now = utcnow_iso()

        # Clear CV paths and reset status
        conn.execute(f"""
            UPDATE job_match SET
                cv_md_path = NULL, cv_pdf_path = NULL,
                status = 'ready_for_cv', status_updated_at = ?
            WHERE job_id IN ({placeholders})
        """, (now, *job_ids))

        conn.commit()
        count = len(job_ids)
        print(f"Reverted {count} cv_ready jobs to 'ready_for_cv', cleared CV paths")

        # Optional: delete CV files per job
        if delete_files:
            for jid in job_ids:
                _delete_job_cv_files(jid)

        return count
    finally:
        conn.close()


def revert_all_scored_to_new(delete_files: bool = False):
    """Nuclear option: revert ALL scored jobs to new (clears all scores).

    Args:
        delete_files: If True, also delete CV files for each reverted job from filesystem.
    """
    conn = get_conn()
    try:
        cur = conn.execute("""
            SELECT f.id, m.final_weighted_score, m.cv_md_path, m.cv_pdf_path
            FROM fetched_jobs f JOIN job_match m ON m.job_id = f.id
            WHERE m.final_weighted_score IS NOT NULL OR m.match_pct IS NOT NULL
        """)
        rows = cur.fetchall()

        if not rows:
            return 0

        job_ids = [row[0] for row in rows]
        now = utcnow_iso()

        # Clear scores and reset status
        conn.execute("""
            UPDATE job_match SET
                match_pct = NULL, explanation = NULL,
                job_brief = NULL, matched_points = NULL, gaps = NULL,
                c2j_score = NULL, c2j_reasoning = NULL,
                j2c_score = NULL, j2c_reasoning = NULL,
                compensable_gaps = NULL, major_gaps = NULL,
                is_dealbreaker = 0, dealbreaker_reason = NULL,
                final_weighted_score = NULL, scorer = NULL, scored_at = NULL,
                status = 'new', status_updated_at = ?
            WHERE final_weighted_score IS NOT NULL OR match_pct IS NOT NULL
        """, (now,))

        conn.commit()
        count = len(job_ids)
        print(f"Reverted ALL {count} scored jobs to 'new'")

        # Optional: delete CV files per job
        if delete_files:
            for jid in job_ids:
                _delete_job_cv_files(jid)

        return count
    finally:
        conn.close()


def show_status_counts():
    conn = get_conn()
    try:
        rows = conn.execute("SELECT status, COUNT(*) as cnt FROM job_match GROUP BY status ORDER BY cnt DESC").fetchall()
        for row in rows:
            print(f"  {row['status']}: {row['cnt']}")
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    if len(sys.argv) < 2:
        print("Usage: py ai_helpers/revert_helper.py <command> [delete_files]")
        print("Commands:")
        print("  revert-code-engine       - Revert code-engine-v1 scores to 'new'")
        print("  revert-cv-ready          - Revert cv_ready to 'ready_for_cv', clear paths")
        print("  revert-all-scored        - Revert ALL scored jobs to 'new' (nuclear)")
        print("  status                   - Show current status counts")
        print()
        print("Optional 2nd argument: delete_files=true/false")
        print("  e.g.: py ai_helpers/revert_helper.py revert-code-engine true")
        sys.exit(1)

    cmd = sys.argv[1]
    delete_files = False
    if len(sys.argv) > 2:
        delete_files = sys.argv[2].lower() == "true"

    print("Before:")
    show_status_counts()
    print()

    if cmd == "revert-code-engine":
        n = revert_code_engine_scores(delete_files=delete_files)
        print(f"Reverted {n} code-engine-v1 scores to 'new'")
    elif cmd == "revert-cv-ready":
        n = revert_cv_ready_to_ready_for_cv(delete_files=delete_files)
        print(f"Reverted {n} cv_ready jobs to 'ready_for_cv', cleared CV paths")
    elif cmd == "revert-all-scored":
        n = revert_all_scored_to_new(delete_files=delete_files)
        print(f"Reverted ALL {n} scored jobs to 'new'")
    elif cmd == "status":
        pass
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

    print("\nAfter:")
    show_status_counts()
