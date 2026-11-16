#!/usr/bin/env python3
"""CLI for the score-job-match / apply-match-feedback skills.

Thin wrapper over `server.jobs_db` (the single source of truth for scoring
SQL) plus the `score_review` queue reader the rescore skill needs. The AI
makes all scoring decisions; this file only moves rows in and out.

Run from the repo root:
    py ai_helpers/ai_scoring_helper.py get_unscored [limit]
    py ai_helpers/ai_scoring_helper.py counts
    py ai_helpers/ai_scoring_helper.py score_review_queue
    py ai_helpers/ai_scoring_helper.py get_rescore_above 70 [min_id] [max_id]
    py ai_helpers/ai_scoring_helper.py get_top [limit]
    py ai_helpers/ai_scoring_helper.py clear_scores [status1,status2...]

Writes (save_score) have no CLI on purpose — scores are computed by the AI
and written via `from server.jobs_db import save_score` in-session.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from ai_helpers.common import get_conn

from server.jobs_db import (  # noqa: E402
    clear_scores,
    get_top_matches,
    get_unscored_jobs,
)


def get_score_review_queue() -> list[dict]:
    """Rows awaiting rescoring: status='score_review' with user feedback."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT f.id, f.title, f.company, f.job_url, f.location, f.site,
                      f.description,
                      m.match_pct, m.final_weighted_score,
                      m.c2j_score, m.c2j_reasoning, m.j2c_score, m.j2c_reasoning,
                      m.job_brief, m.matched_points, m.compensable_gaps,
                      m.major_gaps, m.gaps, m.is_dealbreaker,
                      m.dealbreaker_reason, m.match_feedback, m.match_feedback_at
               FROM fetched_jobs f JOIN job_match m ON m.job_id = f.id
               WHERE m.status = 'score_review'
               ORDER BY m.match_feedback_at"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_status_counts() -> list[dict]:
    """Rows per pipeline stage (cheapest sanity check before any run)."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM job_match "
            "GROUP BY status ORDER BY cnt DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_rescore_above(
    min_score: int = 70,
    min_id: int | None = None,
    max_id: int | None = None,
) -> list[dict]:
    """Full scored rows above a threshold, optionally bounded by job ID."""
    clauses = ["m.status = 'scored'", "m.final_weighted_score > ?"]
    args: list[int] = [min_score]
    if min_id is not None:
        clauses.append("f.id >= ?")
        args.append(min_id)
    if max_id is not None:
        clauses.append("f.id <= ?")
        args.append(max_id)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""SELECT f.id, f.title, f.company, f.job_url, f.location, f.site,
                       f.description, f.experience_range,
                       m.match_pct, m.final_weighted_score,
                       m.c2j_score, m.c2j_reasoning, m.j2c_score,
                       m.j2c_reasoning, m.job_brief, m.matched_points,
                       m.compensable_gaps, m.major_gaps, m.is_dealbreaker,
                       m.dealbreaker_reason
                FROM fetched_jobs f JOIN job_match m ON m.job_id = f.id
                WHERE {' AND '.join(clauses)} ORDER BY f.id""",
            args,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: py ai_helpers/ai_scoring_helper.py <command> [args]")
        print("Commands:")
        print("  get_unscored [limit]       - unscored jobs (status='new'), as JSON")
        print("  score_review_queue         - jobs awaiting rescore + feedback, as JSON")
        print("  get_rescore_above 70 [min_id] [max_id] - scored rows with descriptions")
        print("  counts                     - rows per stage, as JSON")
        print("  get_top [limit]            - highest-scored jobs, as JSON")
        print("  clear_scores [s1,s2...]    - wipe scores so rows go back to 'new'")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "get_unscored":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        _print_json(get_unscored_jobs(limit))
    elif cmd == "score_review_queue":
        _print_json(get_score_review_queue())
    elif cmd == "get_rescore_above":
        min_score = int(sys.argv[2]) if len(sys.argv) > 2 else 70
        min_id = int(sys.argv[3]) if len(sys.argv) > 3 else None
        max_id = int(sys.argv[4]) if len(sys.argv) > 4 else None
        _print_json(get_rescore_above(min_score, min_id, max_id))
    elif cmd == "counts":
        _print_json(get_status_counts())
    elif cmd == "get_top":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        _print_json(get_top_matches(limit))
    elif cmd == "clear_scores":
        statuses = tuple(sys.argv[2].split(",")) if len(sys.argv) > 2 else ("scored",)
        n = clear_scores(statuses=statuses)
        print(f"Cleared {n} rows")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
