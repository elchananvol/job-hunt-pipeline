#!/usr/bin/env python3
"""Shared paths + DB helpers for the AI-chat helper scripts.

Single source of truth for where the database, temp extracts, and generated
CVs live. Every script in `ai_helpers/` imports from here instead of
hardcoding paths, so a future move only touches this file.

Run from the repo root (all helpers assume that):
    py ai_helpers/<script>.py <command>
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "db" / "jobs.db"
TEMP_DIR = REPO_ROOT / "temp"
READY_FOR_CV_JSON = TEMP_DIR / "ready_for_cv_jobs.json"
CV_GEN_DIR = REPO_ROOT / "personal_files" / "cv_generated"

# Allow `from server.jobs_db import ...` no matter how the helper was launched
# (as `py ai_helpers/x.py`, where sys.path[0] is ai_helpers/).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
