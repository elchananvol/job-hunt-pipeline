"""SQLite store: raw fetched jobs + AI match data (source of truth for UI/API/AI).

Tables (see DESIGN.md section 3):
- fetched_jobs: raw job cache, dedup by job_url (UNIQUE). `id` is the
  surrogate key used in API paths/UI links; provider-side id lives in id_ext.
- job_match: one row per job — C2J/J2C breakdown + final weighted score,
  status machine, per-job feedback, CV paths. Fetching never writes it;
  scoring never writes fetched_jobs.
  Legacy columns (match_pct, explanation, gaps) are kept synced to the new
  fields for sorting/backward compat; final_weighted_score is the source
  of truth for the v7 rubric.

Auto timestamps: created_at fills itself on INSERT (column DEFAULT);
updated_at is bumped by AFTER UPDATE triggers (SQLite has no ON UPDATE
clause). App code NEVER writes created_at/updated_at. Domain timestamps
(fetched_at, last_seen_at, scored_at, status_updated_at, *_feedback_at)
are app-written and keep their distinct meanings.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "jobs.db"

# Columns mirrored from jobspy.util.desired_order (JobSpy output schema).
# NOTE: JobSpy's own "id" (provider-side, e.g. li-123) is stored in id_ext;
# the table PK `id` is ours (INTEGER AUTOINCREMENT).
JOB_COLUMNS = [
    "id",
    "site",
    "job_url",
    "job_url_direct",
    "title",
    "company",
    "location",
    "date_posted",
    "job_type",
    "salary_source",
    "interval",
    "min_amount",
    "max_amount",
    "currency",
    "is_remote",
    "job_level",
    "job_function",
    "listing_type",
    "emails",
    "description",
    "company_industry",
    "company_url",
    "company_logo",
    "company_url_direct",
    "company_addresses",
    "company_num_employees",
    "company_revenue",
    "company_description",
    "skills",
    "experience_range",
    "company_rating",
    "company_reviews_count",
    "vacancy_count",
    "work_from_home_type",
]

META_COLUMNS = ["search_term", "search_terms", "fetched_at", "last_seen_at"]

NOW_EXPR = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"

VALID_STATUSES = {"new", "scored", "score_review", "ready_for_cv",
                    "cv_ready", "cv_review", "applied", "dismissed"}

# Post-application tracking: sub-stage of an `applied` job. Stored in
# job_match.applied_stage (NULL = just applied, waiting to hear). Only
# meaningful when status = 'applied'; cleared when the job leaves applied.
# The main stage machine is untouched — AI queues key on status only.
APPLIED_STAGES = (
    "phone_screen",
    "interview",
    "offer",
    "rejected",
    "rejected_fit",
    "rejected_better",
    "rejected_chemistry",
    "ghosted",
    "withdrawn",
)

APPLIED_STAGE_LABELS = {
    "phone_screen": "Phone screen",
    "interview": "Interviewing",
    "offer": "Offer",
    "rejected": "Rejected",
    "rejected_fit": "Rejected — not fit for position",
    "rejected_better": "Rejected — better candidates",
    "rejected_chemistry": "Rejected — they didn't go with me",
    "ghosted": "Ghosted — process died",
    "withdrawn": "Withdrawn — I pulled out",
}

# In-process = score_review or later (computed in the API, never stored).
IN_PROCESS_STATUSES = frozenset({"score_review", "ready_for_cv", "cv_ready",
                                 "cv_review", "applied"})

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS fetched_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_url TEXT NOT NULL UNIQUE,
    site TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    date_posted TEXT,
    job_type TEXT,
    salary_source TEXT,
    "interval" TEXT,
    min_amount REAL,
    max_amount REAL,
    currency TEXT,
    is_remote INTEGER,
    job_level TEXT,
    job_function TEXT,
    listing_type TEXT,
    emails TEXT,
    description TEXT,
    company_industry TEXT,
    company_url TEXT,
    company_logo TEXT,
    company_url_direct TEXT,
    company_addresses TEXT,
    company_num_employees TEXT,
    company_revenue TEXT,
    company_description TEXT,
    job_url_direct TEXT,
    id_ext TEXT,
    skills TEXT,
    experience_range TEXT,
    company_rating REAL,
    company_reviews_count INTEGER,
    vacancy_count INTEGER,
    work_from_home_type TEXT,
    search_term TEXT NOT NULL,
    search_terms TEXT,
    fetched_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT ({NOW_EXPR}),
    updated_at TEXT NOT NULL DEFAULT ({NOW_EXPR})
);
CREATE INDEX IF NOT EXISTS idx_fetched_jobs_site ON fetched_jobs(site);
CREATE INDEX IF NOT EXISTS idx_fetched_jobs_fetched_at ON fetched_jobs(fetched_at);
CREATE INDEX IF NOT EXISTS idx_fetched_jobs_search_term ON fetched_jobs(search_term);
CREATE TABLE IF NOT EXISTS job_match (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE REFERENCES fetched_jobs(id) ON DELETE CASCADE,
    match_pct INTEGER,
    explanation TEXT,
    job_brief TEXT,
    matched_points TEXT,
    gaps TEXT,
    c2j_score INTEGER,
    c2j_reasoning TEXT,
    j2c_score INTEGER,
    j2c_reasoning TEXT,
    compensable_gaps TEXT,
    major_gaps TEXT,
    is_dealbreaker INTEGER NOT NULL DEFAULT 0,
    dealbreaker_reason TEXT,
    final_weighted_score INTEGER,
    scorer TEXT,
    scored_at TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    status_updated_at TEXT NOT NULL,
    applied_stage TEXT,
    is_saved INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    match_feedback TEXT,
    match_feedback_at TEXT,
    cv_feedback TEXT,
    cv_feedback_at TEXT,
    cv_md_path TEXT,
    cv_pdf_path TEXT,
    created_at TEXT NOT NULL DEFAULT ({NOW_EXPR}),
    updated_at TEXT NOT NULL DEFAULT ({NOW_EXPR})
);
CREATE INDEX IF NOT EXISTS idx_job_match_status ON job_match(status);
CREATE INDEX IF NOT EXISTS idx_job_match_pct ON job_match(match_pct);
-- NOTE: idx_job_match_final is created in _migrate_job_match (after the
-- v7 columns exist) so init_db works on pre-v7 tables too.
CREATE TRIGGER IF NOT EXISTS trg_fetched_jobs_updated AFTER UPDATE ON fetched_jobs
BEGIN
    UPDATE fetched_jobs SET updated_at = {NOW_EXPR} WHERE id = NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_job_match_updated AFTER UPDATE ON job_match
BEGIN
    UPDATE job_match SET updated_at = {NOW_EXPR} WHERE id = NEW.id;
END;
CREATE TABLE IF NOT EXISTS fetch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL DEFAULT 'running',
    search_term TEXT NOT NULL,
    sites TEXT NOT NULL,
    target_new INTEGER,
    params TEXT,
    passes INTEGER NOT NULL DEFAULT 0,
    total_rows INTEGER NOT NULL DEFAULT 0,
    total_inserted INTEGER NOT NULL DEFAULT 0,
    total_updated INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT ({NOW_EXPR}),
    updated_at TEXT NOT NULL DEFAULT ({NOW_EXPR})
);
CREATE TABLE IF NOT EXISTS fetch_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES fetch_runs(id) ON DELETE CASCADE,
    pass_no INTEGER NOT NULL,
    site TEXT NOT NULL,
    strategy TEXT,
    pages INTEGER NOT NULL DEFAULT 0,
    failed_pages INTEGER NOT NULL DEFAULT 0,
    rows INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT ({NOW_EXPR}),
    updated_at TEXT NOT NULL DEFAULT ({NOW_EXPR})
);
CREATE INDEX IF NOT EXISTS idx_fetch_attempts_run ON fetch_attempts(run_id);
CREATE TRIGGER IF NOT EXISTS trg_fetch_runs_updated AFTER UPDATE ON fetch_runs
BEGIN
    UPDATE fetch_runs SET updated_at = {NOW_EXPR} WHERE id = NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_fetch_attempts_updated AFTER UPDATE ON fetch_attempts
BEGIN
    UPDATE fetch_attempts SET updated_at = {NOW_EXPR} WHERE id = NEW.id;
END;
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path = DB_PATH) -> Path:
    db_path = Path(db_path)
    conn = get_conn(db_path)
    try:
        conn.executescript(SCHEMA)
        _migrate_job_match(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _migrate_job_match(conn: sqlite3.Connection) -> None:
    """Idempotent v6+v7 upgrades for existing job_match tables.

    v6:
    - Adds job_brief / is_saved columns to pre-v6 tables.
    - Backfills stage 'scored' for rows that already carry a score.
    - Folds retired statuses into the v6 model (match_review -> score_review;
      saved -> is_saved flag, keeping scored/new stage by score presence).
    v7 (C2J/J2C rubric):
    - Adds c2j_score / c2j_reasoning / j2c_score / j2c_reasoning /
      compensable_gaps / major_gaps / is_dealbreaker / dealbreaker_reason /
      final_weighted_score columns. Old scores are NOT migrated — they stay
      under the legacy match_pct/explanation/gaps columns until rescored
      (or wiped via clear_scores()).
    Post-application tracking:
    - Adds applied_stage (sub-stage of status='applied', NULL = just applied).
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_match)")}
    if "job_brief" not in cols:
        conn.execute("ALTER TABLE job_match ADD COLUMN job_brief TEXT")
    if "applied_stage" not in cols:
        conn.execute("ALTER TABLE job_match ADD COLUMN applied_stage TEXT")
    if "is_saved" not in cols:
        conn.execute("ALTER TABLE job_match ADD COLUMN is_saved INTEGER NOT NULL DEFAULT 0")
    for col, ddl in (
        ("c2j_score", "INTEGER"),
        ("c2j_reasoning", "TEXT"),
        ("j2c_score", "INTEGER"),
        ("j2c_reasoning", "TEXT"),
        ("compensable_gaps", "TEXT"),
        ("major_gaps", "TEXT"),
        ("is_dealbreaker", "INTEGER NOT NULL DEFAULT 0"),
        ("dealbreaker_reason", "TEXT"),
        ("final_weighted_score", "INTEGER"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE job_match ADD COLUMN {col} {ddl}")
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_match_final "
            "ON job_match(final_weighted_score)")
    except Exception:
        pass
    now = utcnow_iso()
    conn.execute(
        "UPDATE job_match SET status = 'scored', status_updated_at = ? "
        "WHERE match_pct IS NOT NULL AND status = 'new'", (now,))
    conn.execute(
        "UPDATE job_match SET status = 'score_review', status_updated_at = ? "
        "WHERE status = 'match_review'", (now,))
    conn.execute(
        "UPDATE job_match SET is_saved = 1, status_updated_at = ? "
        "WHERE status = 'saved'", (now,))
    conn.execute(
        "UPDATE job_match SET status = 'scored' "
        "WHERE status = 'saved' AND match_pct IS NOT NULL")
    conn.execute(
        "UPDATE job_match SET status = 'new' "
        "WHERE status = 'saved' AND match_pct IS NULL")


def _norm(value):
    """Convert pandas/NaN-ish values to SQLite-friendly ones."""
    try:
        import pandas as pd  # local import so module works without pandas for DDL

        if value is None:
            return None
        if isinstance(value, float) and pd.isna(value):
            return None
        if value is pd.NaT or (isinstance(value, type(pd.NaT))):
            return None
    except ImportError:
        pass
    if value is None:
        return None
    # pandas Timestamp / datetime -> ISO date string
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()  # type: ignore
        except Exception:
            return str(value)
    # numpy bool/int/float -> python natives
    try:
        import numpy as np  # type: ignore

        if isinstance(value, (np.bool_,)):
            return int(value)
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            f = float(value)
            return None if f != f else f  # NaN -> None
    except ImportError:
        pass
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def save_jobs(df, search_term: str, db_path: str | Path = DB_PATH) -> dict:
    """Upsert a JobSpy DataFrame into fetched_jobs.

    - Dedup by job_url (UNIQUE): existing rows are NOT duplicated.
    - On conflict: refresh last_seen_at, merge search_terms history,
      and update mutable job fields (keeps cache fresh) but preserves
      the original fetched_at + original search_term.
    - Commits after the batch; each call is atomic for its own batch.
    - Never touches job_match or created_at/updated_at (DB-owned).

    Returns {"inserted": int, "updated": int, "skipped": int}.
    """
    import pandas as pd

    init_db(db_path)
    if df is None or len(df) == 0:
        return {"inserted": 0, "updated": 0, "skipped": 0}

    now = utcnow_iso()
    inserted = updated = skipped = 0
    conn = get_conn(db_path)
    try:
        for _, row in df.iterrows():
            job_url = row.get("job_url") if "job_url" in df.columns else None
            if job_url is None or (isinstance(job_url, float) and pd.isna(job_url)):
                skipped += 1
                continue
            job_url = str(job_url).strip()
            if not job_url:
                skipped += 1
                continue

            record = {col: _norm(row.get(col)) for col in JOB_COLUMNS if col in df.columns}
            # ensure all JOB_COLUMNS keys exist
            for col in JOB_COLUMNS:
                record.setdefault(col, None)
            record["job_url"] = job_url

            cur = conn.execute(
                "SELECT fetched_at, search_term, search_terms FROM fetched_jobs WHERE job_url = ?",
                (job_url,),
            )
            existing = cur.fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO fetched_jobs (
                        job_url, site, title, company, location, date_posted, job_type,
                        salary_source, "interval", min_amount, max_amount, currency,
                        is_remote, job_level, job_function, listing_type, emails,
                        description, company_industry, company_url, company_logo,
                        company_url_direct, company_addresses, company_num_employees,
                        company_revenue, company_description, job_url_direct,
                        id_ext, skills, experience_range, company_rating,
                        company_reviews_count, vacancy_count, work_from_home_type,
                        search_term, search_terms, fetched_at, last_seen_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record.get("job_url"), record.get("site"), record.get("title"),
                        record.get("company"), record.get("location"), record.get("date_posted"),
                        record.get("job_type"), record.get("salary_source"), record.get("interval"),
                        record.get("min_amount"), record.get("max_amount"), record.get("currency"),
                        record.get("is_remote"), record.get("job_level"), record.get("job_function"),
                        record.get("listing_type"), record.get("emails"), record.get("description"),
                        record.get("company_industry"), record.get("company_url"), record.get("company_logo"),
                        record.get("company_url_direct"), record.get("company_addresses"),
                        record.get("company_num_employees"), record.get("company_revenue"),
                        record.get("company_description"), record.get("job_url_direct"),
                        record.get("id"), record.get("skills"), record.get("experience_range"),
                        record.get("company_rating"), record.get("company_reviews_count"),
                        record.get("vacancy_count"), record.get("work_from_home_type"),
                        search_term, search_term, now, now,
                    ),
                )
                inserted += 1
            else:
                prior_terms = (existing["search_terms"] or existing["search_term"] or "")
                term_set = [t for t in prior_terms.split(" | ") if t] if prior_terms else []
                if search_term not in term_set:
                    term_set.append(search_term)
                merged_terms = " | ".join(term_set) if term_set else search_term
                conn.execute(
                    """UPDATE fetched_jobs SET
                        site=COALESCE(?,site), title=COALESCE(?,title),
                        company=COALESCE(?,company), location=COALESCE(?,location),
                        date_posted=COALESCE(?,date_posted), job_type=COALESCE(?,job_type),
                        description=COALESCE(?,description),
                        company_url=COALESCE(?,company_url),
                        job_url_direct=COALESCE(?,job_url_direct),
                        search_terms=?, last_seen_at=?
                       WHERE job_url=?""",
                    (
                        record.get("site"), record.get("title"), record.get("company"),
                        record.get("location"), record.get("date_posted"), record.get("job_type"),
                        record.get("description"), record.get("company_url"),
                        record.get("job_url_direct"), merged_terms, now, job_url,
                    ),
                )
                updated += 1
        conn.commit()
    finally:
        conn.close()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def count_jobs(db_path: str | Path = DB_PATH) -> int:
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM fetched_jobs").fetchone()["n"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# job_match helpers (AI lane + future backend; fetching never calls these)
# ---------------------------------------------------------------------------

def ensure_match_row(job_id: int, db_path: str | Path = DB_PATH) -> int:
    """Create the job_match row for a job if missing. Returns job_match.id."""
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        row = conn.execute("SELECT id FROM job_match WHERE job_id = ?", (job_id,)).fetchone()
        if row is not None:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO job_match (job_id, status, status_updated_at) VALUES (?, 'new', ?)",
            (job_id, utcnow_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def ensure_all_match_rows(db_path: str | Path = DB_PATH) -> int:
    """Backfill job_match rows for jobs that lack one (e.g. after migration)."""
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            """INSERT INTO job_match (job_id, status, status_updated_at)
               SELECT id, 'new', ? FROM fetched_jobs
               WHERE id NOT IN (SELECT job_id FROM job_match)""",
            (utcnow_iso(),),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_unscored_jobs(limit: int = 50, db_path: str | Path = DB_PATH) -> list[dict]:
    """Jobs awaiting scoring: no final score yet and status 'new'.

    Checks both the v7 column (final_weighted_score) and the legacy
    match_pct so old-rubric rows are not silently treated as unscored
    (they must be wiped via clear_scores() first, then they qualify).
    """
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT f.* FROM fetched_jobs f
               JOIN job_match m ON m.job_id = f.id
               WHERE m.final_weighted_score IS NULL AND m.match_pct IS NULL
               AND m.status = 'new'
               ORDER BY f.fetched_at LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_score(
    job_id: int,
    match_pct: int | None = None,
    explanation: str = "",
    matched_points: str = "",
    gaps: str = "",
    scorer: str = "",
    job_brief: str = "",
    c2j_score: int | None = None,
    c2j_reasoning: str = "",
    j2c_score: int | None = None,
    j2c_reasoning: str = "",
    compensable_gaps: str = "",
    major_gaps: str = "",
    is_dealbreaker: bool = False,
    dealbreaker_reason: str | None = None,
    final_weighted_score: int | None = None,
    db_path: str | Path = DB_PATH,
) -> None:
    """Write a v7 C2J/J2C score for a job (creates the match row if missing).

    New callers pass the C2J/J2C breakdown + final_weighted_score.
    Legacy columns are synced for backward compat:
    - match_pct mirrors final_weighted_score (board sorting / old queries).
    - explanation mirrors "C2J … / J2C …" when not given explicitly.
    - gaps mirrors major_gaps (+ dealbreaker reason) when not given.

    Legacy positional calls save_score(job_id, pct, expl, fits, gaps,
    scorer, brief) still work: pct is treated as the final score.

    First-time scoring (row still 'new') auto-advances the stage to
    'scored'. Rescoring from review queues is handled by the skill's own
    SQL (this helper never moves score_review -> scored).
    """
    if final_weighted_score is None and match_pct is not None:
        final_weighted_score = match_pct
    if final_weighted_score is not None and match_pct is None:
        match_pct = final_weighted_score
    if not explanation and (c2j_reasoning or j2c_reasoning):
        explanation = (
            f"C2J {c2j_score if c2j_score is not None else '?'}: "
            f"{c2j_reasoning} J2C "
            f"{j2c_score if j2c_score is not None else '?'}: {j2c_reasoning}"
        ).strip()
    if not gaps and (major_gaps or dealbreaker_reason):
        parts = [p for p in [major_gaps, dealbreaker_reason if is_dealbreaker else ""] if p]
        gaps = "\n".join(parts)
    ensure_match_row(job_id, db_path)
    conn = get_conn(db_path)
    try:
        now = utcnow_iso()
        conn.execute(
            """UPDATE job_match SET match_pct = ?, explanation = ?,
               job_brief = ?, matched_points = ?, gaps = ?,
               c2j_score = ?, c2j_reasoning = ?,
               j2c_score = ?, j2c_reasoning = ?,
               compensable_gaps = ?, major_gaps = ?,
               is_dealbreaker = ?, dealbreaker_reason = ?,
               final_weighted_score = ?, scorer = ?,
               scored_at = ? WHERE job_id = ?""",
            (match_pct, explanation, job_brief, matched_points, gaps,
             c2j_score, c2j_reasoning, j2c_score, j2c_reasoning,
             compensable_gaps, major_gaps,
             1 if is_dealbreaker else 0, dealbreaker_reason,
             final_weighted_score, scorer, now, job_id),
        )
        conn.execute(
            "UPDATE job_match SET status = 'scored', status_updated_at = ? "
            "WHERE job_id = ? AND status = 'new'", (now, job_id))
        conn.commit()
    finally:
        conn.close()


def clear_scores(db_path: str | Path = DB_PATH,
                 statuses: tuple[str, ...] = ("scored",)) -> int:
    """Wipe scores so jobs are rescored under the current rubric.

    Default: only rows in stage 'scored' are reset to 'new' (in-flight
    rows — score_review / ready_for_cv / cv_ready / cv_review / applied /
    dismissed — are left untouched to preserve user work and CVs).
    Returns the number of rows reset.
    """
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        placeholders = ",".join("?" for _ in statuses)
        cur = conn.execute(
            f"""UPDATE job_match SET match_pct = NULL, explanation = NULL,
                job_brief = NULL, matched_points = NULL, gaps = NULL,
                c2j_score = NULL, c2j_reasoning = NULL,
                j2c_score = NULL, j2c_reasoning = NULL,
                compensable_gaps = NULL, major_gaps = NULL,
                is_dealbreaker = 0, dealbreaker_reason = NULL,
                final_weighted_score = NULL, scorer = NULL, scored_at = NULL,
                status = 'new', status_updated_at = ?
                WHERE status IN ({placeholders})""",
            (utcnow_iso(), *statuses),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def set_saved(job_id: int, saved: bool, db_path: str | Path = DB_PATH) -> None:
    """Toggle the tracking flag (independent of pipeline stage)."""
    ensure_match_row(job_id, db_path)
    conn = get_conn(db_path)
    try:
        conn.execute("UPDATE job_match SET is_saved = ? WHERE job_id = ?",
                     (1 if saved else 0, job_id))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# fetch history (one run = one user request; one attempt = one site-pass)
# ---------------------------------------------------------------------------

def create_fetch_run(search_term: str, sites: list[str],
                     target_new: int | None = None, params: str = "",
                     db_path: str | Path = DB_PATH) -> int:
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO fetch_runs (started_at, state, search_term, sites, "
            "target_new, params) VALUES (?, 'running', ?, ?, ?, ?)",
            (utcnow_iso(), search_term, ",".join(sites), target_new, params))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def start_attempt(run_id: int, pass_no: int, site: str,
                  db_path: str | Path = DB_PATH) -> int:
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO fetch_attempts (run_id, pass_no, site, started_at) "
            "VALUES (?, ?, ?, ?)", (run_id, pass_no, site, utcnow_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def finish_attempt(attempt_id: int, strategy: str | None, pages: int,
                   failed_pages: int, rows: int, inserted: int, updated: int,
                   skipped: int, error: str | None,
                   db_path: str | Path = DB_PATH) -> None:
    conn = get_conn(db_path)
    try:
        conn.execute(
            """UPDATE fetch_attempts SET strategy = ?, pages = ?,
               failed_pages = ?, rows = ?, inserted = ?, updated = ?,
               skipped = ?, error = ?, finished_at = ? WHERE id = ?""",
            (strategy, pages, failed_pages, rows, inserted, updated,
             skipped, error, utcnow_iso(), attempt_id))
        conn.commit()
    finally:
        conn.close()


def finish_fetch_run(run_id: int, state: str, passes: int, total_rows: int,
                     total_inserted: int, total_updated: int,
                     error: str | None = None,
                     db_path: str | Path = DB_PATH) -> None:
    conn = get_conn(db_path)
    try:
        conn.execute(
            """UPDATE fetch_runs SET finished_at = ?, state = ?, passes = ?,
               total_rows = ?, total_inserted = ?, total_updated = ?,
               error = ? WHERE id = ?""",
            (utcnow_iso(), state, passes, total_rows, total_inserted,
             total_updated, error, run_id))
        conn.commit()
    finally:
        conn.close()


def list_fetch_runs(limit: int = 20,
                    db_path: str | Path = DB_PATH) -> list[dict]:
    """Newest runs with attempt counts (for the UI history panel)."""
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT r.*, COUNT(a.id) AS attempts FROM fetch_runs r
               LEFT JOIN fetch_attempts a ON a.run_id = r.id
               GROUP BY r.id ORDER BY r.id DESC LIMIT ?""",
            (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_fetch_run(run_id: int, db_path: str | Path = DB_PATH) -> dict | None:
    """One run with its attempts in execution order."""
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        run = conn.execute("SELECT * FROM fetch_runs WHERE id = ?",
                           (run_id,)).fetchone()
        if run is None:
            return None
        attempts = conn.execute(
            "SELECT * FROM fetch_attempts WHERE run_id = ? "
            "ORDER BY pass_no, id", (run_id,)).fetchall()
        payload = dict(run)
        payload["attempts"] = [dict(a) for a in attempts]
        return payload
    finally:
        conn.close()


def get_top_matches(limit: int = 10, db_path: str | Path = DB_PATH) -> list[dict]:
    """Highest-scored jobs with their match rows (v7 final score first)."""
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT f.id, f.title, f.company, f.location, f.site, f.job_url,
                      COALESCE(m.final_weighted_score, m.match_pct) AS match_pct,
                      m.final_weighted_score, m.c2j_score, m.c2j_reasoning,
                      m.j2c_score, m.j2c_reasoning,
                      m.explanation, m.job_brief, m.matched_points,
                      m.compensable_gaps, m.major_gaps, m.gaps,
                      m.is_dealbreaker, m.dealbreaker_reason,
                      m.status, m.is_saved
               FROM fetched_jobs f JOIN job_match m ON m.job_id = f.id
               WHERE m.final_weighted_score IS NOT NULL OR m.match_pct IS NOT NULL
               ORDER BY COALESCE(m.final_weighted_score, m.match_pct) DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
