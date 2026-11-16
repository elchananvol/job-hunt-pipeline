"""FastAPI backend for the job pipeline (DESIGN.md section 5).

Pure mechanics — CRUD, background fetcher runs, static file serving, lesson
reads/appends. No model calls, no scoring logic. Jobs are addressed by
integer `id`; `job_url` travels only inside bodies.
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from server import fetch_jobs
from server import jobs_db
from server.jobs_db import (
    APPLIED_STAGES,
    IN_PROCESS_STATUSES,
    VALID_STATUSES,
    ensure_all_match_rows,
    ensure_match_row,
    get_conn,
    init_db,
    utcnow_iso,
)

BASE_DIR = Path(__file__).resolve().parent.parent
CV_DIR = BASE_DIR / "personal_files" / "cv_generated"
UI_DIR = BASE_DIR / "ui" / "dist"
LESSON_FILES = {
    "scoring": BASE_DIR / "personal_files" / "feedback_scoring.md",
    "cv": BASE_DIR / "personal_files" / "feedback_cv.md",
}

# Manual (UI) stage moves per DESIGN.md 10. Feedback-driven moves to
# score_review/cv_review are applied automatically (match_feedback wins).
# is_saved is a flag, not a stage — toggled any time, never validated here.
# AI moves review->scored/cv_ready directly in the DB, never via this API.
MANUAL_OK: dict[str, set[str]] = {
    "new": {"ready_for_cv", "dismissed", "applied"},
    "scored": {"ready_for_cv", "dismissed", "applied"},
    "score_review": {"dismissed", "applied"},
    "ready_for_cv": {"dismissed", "applied"},
    "cv_ready": {"applied", "dismissed"},
    "cv_review": {"applied", "dismissed"},
    "applied": {"dismissed"},
    # reopen: dismissed -> scored, coerced to 'new' when the job is unscored
    "dismissed": {"scored"},
}

# Feedback is only meaningful at these stages (target review in parens).
MATCH_FB_STAGES = {"new": "score_review", "scored": "score_review",
                   "score_review": "score_review"}
CV_FB_STAGES = {"cv_ready": "cv_review", "cv_review": "cv_review"}

LIST_COLUMNS = (
    "f.id, f.job_url, f.site, f.title, f.company, f.location, f.date_posted, "
    "f.job_type, f.is_remote, f.fetched_at, f.last_seen_at, f.search_term, "
    "COALESCE(m.final_weighted_score, m.match_pct) AS match_pct, "
    "m.final_weighted_score, m.c2j_score, m.j2c_score, m.is_dealbreaker, "
    "m.job_brief, m.status, m.is_saved, m.status_updated_at, "
    "m.scored_at, m.cv_md_path, m.cv_pdf_path, m.applied_stage"
)


def _with_process_flag(row: dict) -> dict:
    row["in_process"] = row.get("status") in IN_PROCESS_STATUSES
    return row

app = FastAPI(title="Job Hunt Pipeline")
app.add_middleware(
    CORSMiddleware,
    # Local-only tool: accept any localhost origin on any port (covers
    # localhost vs 127.0.0.1 vs [::1], vite dev/preview port changes, etc.).
    # No credentials are used, so a local regex is safe and kills the whole
    # class of "OPTIONS 400 Disallowed CORS origin" preflight failures.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- models

class SearchRequest(BaseModel):
    search_term: str
    sites: list[str] = Field(default_factory=lambda: list(fetch_jobs.DEFAULT_SITES))
    location: str = fetch_jobs.DEFAULTS["location"]
    results_wanted: int = fetch_jobs.DEFAULTS["results_wanted"]
    hours_old: int | None = fetch_jobs.DEFAULTS["hours_old"]
    job_type: str | None = fetch_jobs.DEFAULTS["job_type"]
    page_size: int = 25
    target_new: int | None = None
    max_passes: int = 6


class PatchJob(BaseModel):
    status: str | None = None
    is_saved: bool | None = None
    notes: str | None = None
    match_feedback: str | None = None
    cv_feedback: str | None = None
    # Post-application tracking (only on status='applied' rows).
    # One of APPLIED_STAGES; "" clears back to NULL (just applied).
    applied_stage: str | None = None


class ManualJob(BaseModel):
    title: str
    company: str | None = None
    location: str | None = None
    job_url: str
    description: str | None = None
    site: str = "manual"


class NoteRequest(BaseModel):
    topic: Literal["scoring", "cv"]
    text: str
    job_id: int | None = None


# ---------------------------------------------------------------- search runs

_runs: dict[str, dict[str, Any]] = {}
_runs_lock = threading.Lock()


def _run_fetch(run_id: str, params: dict[str, Any]) -> None:
    try:
        summary = fetch_jobs.fetch_all_sites(
            params["search_term"],
            sites=params["sites"],
            location=params["location"],
            results_wanted=params["results_wanted"],
            hours_old=params["hours_old"],
            job_type=params["job_type"],
            page_size=params["page_size"],
            target_new=params.get("target_new"),
            max_passes=params.get("max_passes", 6),
        )
        ensure_all_match_rows()  # new jobs need match rows (status='new')
        per_site = {
            site: {k: v for k, v in info.items() if k != "checkpoints"}
            for site, info in summary["per_site"].items()
        }
        with _runs_lock:
            _runs[run_id].update(
                state="done",
                per_site=per_site,
                total_rows=summary["total_rows"],
                total_inserted=summary["total_inserted"],
                total_updated=summary["total_updated"],
                warnings=summary["warnings"],
                finished_at=summary["finished_at"],
                fetch_run_id=summary.get("run_id"),
                passes=summary.get("passes", []),
            )
    except Exception as exc:  # never leave a run stuck in 'running'
        with _runs_lock:
            _runs[run_id].update(
                state="error", error=str(exc),
                finished_at=utcnow_iso(),
            )


@app.post("/api/search")
def start_search(req: SearchRequest) -> dict[str, str]:
    unknown = [s for s in req.sites if s not in fetch_jobs.ALL_SITES]
    if unknown:
        raise HTTPException(400, f"unknown sites: {unknown}")
    run_id = uuid.uuid4().hex[:12]
    with _runs_lock:
        _runs[run_id] = {
            "run_id": run_id, "state": "running",
            "params": req.model_dump(), "per_site": {},
            "started_at": utcnow_iso(),
        }
    threading.Thread(target=_run_fetch, args=(run_id, req.model_dump()),
                     daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/search/{run_id}")
def poll_search(run_id: str) -> dict[str, Any]:
    with _runs_lock:
        run = _runs.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run_id")
    return run


@app.get("/api/runs")
def run_history(limit: int = Query(20, le=100)) -> dict[str, Any]:
    """Persistent fetch history: newest runs with attempt counts."""
    return {"runs": jobs_db.list_fetch_runs(limit)}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int) -> dict[str, Any]:
    """One recorded run with its per-site passes (attempts)."""
    run = jobs_db.get_fetch_run(run_id)
    if run is None:
        raise HTTPException(404, "unknown fetch run id")
    return run


# ---------------------------------------------------------------- job reads

def _list_where(status: str | None, site: str | None, has_score: bool | None,
                saved: bool | None, q: str | None) -> tuple[str, list[Any]]:
    clauses = ["1=1"]
    args: list[Any] = []
    if status:
        # Single stage or comma-separated set (board multi-select).
        stages = [s.strip() for s in status.split(",") if s.strip()]
        unknown = [s for s in stages if s not in VALID_STATUSES]
        if unknown:
            raise HTTPException(400, f"unknown status: {unknown}")
        placeholders = ",".join("?" for _ in stages)
        clauses.append(f"m.status IN ({placeholders})")
        args.extend(stages)
    if site:
        clauses.append("f.site = ?")
        args.append(site)
    if saved is True:
        clauses.append("m.is_saved = 1")
    elif saved is False:
        clauses.append("m.is_saved = 0")
    if has_score is True:
        clauses.append("(m.final_weighted_score IS NOT NULL OR m.match_pct IS NOT NULL)")
    elif has_score is False:
        clauses.append("(m.final_weighted_score IS NULL AND m.match_pct IS NULL)")
    if q:
        clauses.append("(f.title LIKE ? OR f.company LIKE ? OR f.location LIKE ?)")
        args.extend([f"%{q}%"] * 3)
    return " AND ".join(clauses), args


@app.get("/api/jobs")
def list_jobs(
    status: str | None = None,
    site: str | None = None,
    has_score: bool | None = None,
    saved: bool | None = None,
    sort: str = "-match_pct",
    q: str | None = None,
    limit: int = Query(200, le=5000),
    offset: int = 0,
) -> dict[str, Any]:
    sorts = {
        "-match_pct": "COALESCE(m.final_weighted_score, m.match_pct) DESC",
        "match_pct": "COALESCE(m.final_weighted_score, m.match_pct) ASC",
        "-final_weighted_score": "COALESCE(m.final_weighted_score, m.match_pct) DESC",
        "final_weighted_score": "COALESCE(m.final_weighted_score, m.match_pct) ASC",
        "-fetched_at": "f.fetched_at DESC",
        "fetched_at": "f.fetched_at ASC",
    }
    if sort not in sorts:
        raise HTTPException(400, f"unknown sort: {sort}")
    where, args = _list_where(status, site, has_score, saved, q)
    init_db()
    conn = get_conn()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM fetched_jobs f JOIN job_match m "
            f"ON m.job_id = f.id WHERE {where}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT {LIST_COLUMNS} FROM fetched_jobs f "
            f"JOIN job_match m ON m.job_id = f.id "
            f"WHERE {where} ORDER BY {sorts[sort]} LIMIT ? OFFSET ?",
            (*args, limit, offset)).fetchall()
        return {"total": total,
                "jobs": [_with_process_flag(dict(r)) for r in rows]}
    finally:
        conn.close()


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    """Live counts per stage + saved + unscored (drives the UI filter boxes)."""
    init_db()
    conn = get_conn()
    try:
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM job_match GROUP BY status")}
        unscored = conn.execute(
            "SELECT COUNT(*) FROM job_match "
            "WHERE final_weighted_score IS NULL AND match_pct IS NULL").fetchone()[0]
        saved = conn.execute(
            "SELECT COUNT(*) FROM job_match WHERE is_saved = 1").fetchone()[0]
        by_applied_stage = {r["applied_stage"]: r["n"] for r in conn.execute(
            "SELECT applied_stage, COUNT(*) AS n FROM job_match "
            "WHERE status = 'applied' AND applied_stage IS NOT NULL "
            "GROUP BY applied_stage")}
        return {"by_status": by_status, "unscored": unscored, "saved": saved,
                "total": sum(by_status.values()),
                "by_applied_stage": by_applied_stage}
    finally:
        conn.close()


def _lesson_entries_for(job_url: str) -> list[dict[str, str]]:
    entries = []
    for topic, path in LESSON_FILES.items():
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if job_url in line:
                entries.append({"topic": topic, "text": line.strip()})
    return entries


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: int) -> dict[str, Any]:
    init_db()
    ensure_match_row(job_id)
    conn = get_conn()
    try:
        job = conn.execute("SELECT * FROM fetched_jobs WHERE id = ?",
                           (job_id,)).fetchone()
        if job is None:
            raise HTTPException(404, "unknown job id")
        match = conn.execute("SELECT * FROM job_match WHERE job_id = ?",
                             (job_id,)).fetchone()
        payload = {"job": dict(job),
                   "match": _with_process_flag(dict(match))}
        payload["lesson_entries"] = _lesson_entries_for(job["job_url"])
        return payload
    finally:
        conn.close()


# ---------------------------------------------------------------- job writes

@app.patch("/api/jobs/{job_id}")
def update_job(job_id: int, patch: PatchJob) -> dict[str, Any]:
    init_db()
    ensure_match_row(job_id)
    conn = get_conn()
    try:
        row = conn.execute("SELECT status FROM job_match WHERE job_id = ?",
                           (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown job id")
        cur_status = row["status"]
        scored = conn.execute(
            "SELECT final_weighted_score, match_pct FROM job_match WHERE job_id = ?",
            (job_id,)).fetchone()
        scored = scored is not None and (
            scored["final_weighted_score"] is not None or scored["match_pct"] is not None)
        now = utcnow_iso()
        sets: list[str] = []
        args: list[Any] = []
        new_status = patch.status
        # Feedback auto-drives its review stage; match_feedback wins on ties.
        # Re-saving feedback while already in that review stage just updates text.
        if patch.match_feedback is not None:
            if cur_status not in MATCH_FB_STAGES:
                raise HTTPException(
                    422, f"scoring feedback only from new/scored "
                    f"(current: {cur_status})")
            new_status = "score_review"
            sets.append("match_feedback = ?")
            args.append(patch.match_feedback)
            sets.append("match_feedback_at = ?")
            args.append(now)
        elif patch.cv_feedback is not None:
            if cur_status not in CV_FB_STAGES:
                raise HTTPException(
                    422, f"CV feedback only from cv_ready "
                    f"(current: {cur_status})")
            new_status = "cv_review"
            sets.append("cv_feedback = ?")
            args.append(patch.cv_feedback)
            sets.append("cv_feedback_at = ?")
            args.append(now)
        if new_status is not None and new_status != cur_status:
            if new_status not in VALID_STATUSES:
                raise HTTPException(400, f"unknown status: {new_status}")
            auto = ((patch.match_feedback is not None
                     and new_status == "score_review")
                    or (patch.cv_feedback is not None
                        and new_status == "cv_review"))
            if not auto and new_status not in MANUAL_OK.get(cur_status, set()):
                raise HTTPException(
                    422, f"transition {cur_status} -> {new_status} not allowed")
            # reopen restores the scored stage when a score exists
            if cur_status == "dismissed" and new_status == "scored" and not scored:
                new_status = "new"
            sets.append("status = ?")
            args.append(new_status)
            sets.append("status_updated_at = ?")
            args.append(now)
        # Post-application tracking: sub-stage of an applied job.
        # "" clears back to NULL (just applied, waiting to hear).
        effective_status = (new_status if new_status is not None
                            else cur_status)
        if patch.applied_stage is not None:
            if effective_status != "applied":
                raise HTTPException(
                    422, "applied_stage only on applied jobs "
                    f"(current: {cur_status})")
            if patch.applied_stage == "":
                sets.append("applied_stage = ?")
                args.append(None)
            else:
                if patch.applied_stage not in APPLIED_STAGES:
                    raise HTTPException(
                        400, f"unknown applied_stage: {patch.applied_stage}")
                sets.append("applied_stage = ?")
                args.append(patch.applied_stage)
        elif (new_status is not None and new_status != cur_status
                and new_status != "applied" and cur_status == "applied"):
            # Leaving applied (dismiss/reopen) drops the stale sub-stage.
            sets.append("applied_stage = ?")
            args.append(None)
        if patch.is_saved is not None:
            sets.append("is_saved = ?")
            args.append(1 if patch.is_saved else 0)
        if patch.notes is not None:
            sets.append("notes = ?")
            args.append(patch.notes)
        if sets:
            conn.execute(f"UPDATE job_match SET {', '.join(sets)} WHERE job_id = ?",
                         (*args, job_id))
            conn.commit()
        updated = conn.execute("SELECT * FROM job_match WHERE job_id = ?",
                               (job_id,)).fetchone()
        return {"match": _with_process_flag(dict(updated))}
    finally:
        conn.close()


@app.post("/api/jobs/manual", status_code=201)
def manual_job(body: ManualJob) -> dict[str, Any]:
    if not body.job_url.strip():
        raise HTTPException(400, "job_url is required (dedup key)")
    init_db()
    conn = get_conn()
    try:
        now = utcnow_iso()
        try:
            cur = conn.execute(
                "INSERT INTO fetched_jobs (job_url, site, title, company, location, "
                "description, search_term, search_terms, fetched_at, last_seen_at) "
                "VALUES (?, 'manual', ?, ?, ?, ?, 'manual', 'manual', ?, ?)",
                (body.job_url.strip(), body.title, body.company, body.location,
                 body.description, now, now))
        except Exception:
            raise HTTPException(409, "a job with this job_url already exists")
        job_id = cur.lastrowid
        conn.execute(
            "INSERT INTO job_match (job_id, status, status_updated_at) "
            "VALUES (?, 'new', ?)", (job_id, now))
        conn.commit()
        return {"id": job_id}
    finally:
        conn.close()


# ---------------------------------------------------------------- lessons

@app.get("/api/lessons")
def read_lessons(topic: Literal["scoring", "cv"],
                 job_url: str | None = None) -> dict[str, Any]:
    path = LESSON_FILES[topic]
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if job_url:
        text = "\n".join(l for l in text.splitlines() if job_url in l)
    return {"topic": topic, "text": text}


@app.post("/api/notes", status_code=201)
def append_note(note: NoteRequest) -> dict[str, str]:
    if not note.text.strip():
        raise HTTPException(400, "text is required")
    path = LESSON_FILES[note.topic]
    job_ref = ""
    if note.job_id is not None:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT id, job_url, title FROM fetched_jobs WHERE id = ?",
                (note.job_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HTTPException(404, "unknown job_id")
        job_ref = f" — job #{row['id']} ({row['title']}) {row['job_url']}"
    entry = f"\n{utcnow_iso()[:10]}{job_ref}: {note.text.strip()}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(entry)
    return {"topic": note.topic, "appended": entry.strip()}


# ---------------------------------------------------------------- CV files

@app.get("/files/{rel_path:path}")
def get_file(rel_path: str) -> FileResponse:
    target = (CV_DIR / rel_path).resolve()
    if CV_DIR.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(target)


# ---------------------------------------------------------------- UI (served from the same origin when built)
# ui/dist is created by `npm run build` locally or baked into the Docker image.
# `npm run dev` is optional for live UI updates on :5173, proxied to this API.
if UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
