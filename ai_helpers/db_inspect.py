#!/usr/bin/env python3
"""Quick read-only DB peeks for AI-chat triage.

Replaces check_db.py / check_scored.py / check_scorers.py / verify_status.py
(one CLI, four subcommands — same queries, same output):

Run from the repo root:
    py ai_helpers/db_inspect.py status      # rows per stage  (was verify_status.py)
    py ai_helpers/db_inspect.py scored      # top-20 scored   (was check_scored.py)
    py ai_helpers/db_inspect.py scorers     # scores per model (was check_scorers.py)
    py ai_helpers/db_inspect.py cv_ready    # cv_ready paths  (was check_db.py)
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from ai_helpers.common import get_conn


def show_status() -> None:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM job_match GROUP BY status"
        ).fetchall()
        print(json.dumps({r[0]: r[1] for r in rows}, ensure_ascii=False))
    finally:
        conn.close()


def show_scored(limit: int = 20) -> None:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT job_id, status, final_weighted_score,
                      c2j_score, j2c_score, is_dealbreaker, dealbreaker_reason
               FROM job_match WHERE status = 'scored'
               ORDER BY final_weighted_score DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        for r in rows:
            print(tuple(r))
    finally:
        conn.close()


def show_scorers() -> None:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT scorer, COUNT(*) FROM job_match
               WHERE final_weighted_score IS NOT NULL
               GROUP BY scorer ORDER BY COUNT(*) DESC"""
        ).fetchall()
        for r in rows:
            print(f"{r[0] or '(none/legacy)'}: {r[1]}")
    finally:
        conn.close()


def show_cv_ready() -> None:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT job_id, status, cv_md_path, cv_pdf_path FROM job_match
               WHERE status = 'cv_ready' ORDER BY job_id"""
        ).fetchall()
        for r in rows:
            print(tuple(r))
    finally:
        conn.close()


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: py ai_helpers/db_inspect.py <status|scored|scorers|cv_ready> [limit]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        show_status()
    elif cmd == "scored":
        show_scored(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    elif cmd == "scorers":
        show_scorers()
    elif cmd == "cv_ready":
        show_cv_ready()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
