"""Resilient JobSpy fetcher with per-site isolation + per-page SQLite checkpoints.

Why this wrapper exists (failure analysis of python-jobspy, GitHub main
@ fda080a — same flaw exists in PyPI release 1.1.82):
- jobspy.scrape_jobs() runs one ThreadPool over all sites and calls
  future.result() with NO try/except (__init__.py lines 119-126).
  => If ANY single site raises (timeout, 429, LinkedIn per-job
     LinkedInException, Indeed GraphQL error...), the exception
     propagates and the WHOLE call fails with NO DataFrame returned.
     Partial results from sites that already finished are DISCARDED.
- LinkedIn.scrape() itself returns partial job_list on page-level
  HTTP errors, but re-raises LinkedInException on per-job parse
  errors (linkedin/__init__.py:162-163) -> kills the whole multi-site run.
- Indeed._scrape_page returns [] on non-OK HTTP, but session.post()
  timeouts/connection errors propagate uncaught.

Two-level defense (both checkpoint to SQLite IMMEDIATELY):
1. Per-SITE isolation: each site scraped via its own scrape_jobs() call
   inside try/except. A later site failing cannot destroy earlier
   sites' data.
2. Per-PAGE checkpoints (fetch_site_paged): each site is fetched in
   small pages and every page is committed to SQLite before the next
   page starts. A crash on page N keeps pages 1..N-1.

Paging strategy per site (offset support verified against the installed
jobspy source):
- OFFSET paging (scraper honors ScraperInput.offset):
  linkedin, indeed, glassdoor, google.
  linkedin: start = offset//10*10; indeed/google: slice [offset:...];
  glassdoor: range_start = 1 + offset//jobs_per_page.
  NOTE indeed/google re-fetch from page 1 on every call (they fetch
  results_wanted+offset then slice), so paging costs extra requests.
- EXPANDING-WINDOW paging (scraper IGNORES offset, always restarts at
  page 1): zip_recruiter, bayt, bdjobs. We re-request a growing window
  (page, 2*page, ...) and keep only never-before-seen job_urls.
  Same refetch cost profile as indeed; terminates when a call adds
  zero new jobs.

Pages within a site may overlap (e.g. linkedin rounds offset down to
a multiple of 10), so accumulation is diffed by job_url; the DB's
PRIMARY KEY on job_url is the final dedup guarantee.

Defaults per mission spec:
- site_name: linkedin, indeed by default (the only sites yielding data for
  israel; all 7 available via sites=[...] / --sites)
- location="israel", distance=50, job_type="fulltime", is_remote=False,
  results_wanted=100, easy_apply=False, description_format="markdown",
  hours_old=72, verbose=0, linkedin_fetch_description=True,
  country_indeed="israel", proxies=None.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import time
from datetime import datetime, timezone

import pandas as pd

from server.jobs_db import (
    DB_PATH,
    create_fetch_run,
    finish_attempt,
    finish_fetch_run,
    init_db,
    save_jobs,
    start_attempt,
)

ALL_SITES = [
    "linkedin",
    "zip_recruiter",
    "indeed",
    "glassdoor",
    "google",
    "bayt",
    "bdjobs",
]

# Default fetch targets: only sites that return data for location="israel"
# from a plain connection (verified live 2026-09-16). The rest are either
# region-blocked (zip_recruiter 403, bayt 403, bdjobs 302), unsupported for
# Israel (glassdoor raises), or need special syntax with ~0 yield (google).
# Override per run with fetch_all_sites(sites=[...]) or --sites.
DEFAULT_SITES = ["linkedin", "indeed"]

DEFAULTS = dict(
    location="israel",
    distance=50,
    job_type="fulltime",
    is_remote=False,
    results_wanted=100,
    easy_apply=False,
    description_format="markdown",
    offset=0,
    hours_old=72,
    verbose=0,
    linkedin_fetch_description=True,
    linkedin_company_ids=None,
    country_indeed="israel",
    enforce_annual_salary=False,
    proxies=None,
)

# Paging strategy per site (see module docstring for source evidence).
OFFSET_PAGED_SITES = {"linkedin", "indeed", "glassdoor", "google"}
EXPANDING_SITES = {"zip_recruiter", "bayt", "bdjobs"}
MAX_PAGES = 40  # job-board APIs cap out ~1000 jobs per search anyway


def validate_params(site_names: list[str], job_type, hours_old, easy_apply, is_remote) -> list[str]:
    """Return human-readable warnings for Indeed/LinkedIn documented limitations."""
    warnings = []
    sites = {s.lower() for s in site_names}
    if "indeed" in sites:
        active = []
        if hours_old:
            active.append("hours_old")
        if job_type or is_remote:
            active.append("job_type/is_remote")
        if easy_apply:
            active.append("easy_apply")
        if len(active) > 1:
            warnings.append(
                f"[indeed] limitation: only ONE of {active} is applied by the API. "
                f"Lib precedence: hours_old > easy_apply > job_type/is_remote. "
                f"With defaults (hours_old=72 + job_type=fulltime) the job_type filter is SILENTLY IGNORED. "
                f"Tip: set hours_old=None to enforce job_type/is_remote."
            )
    if "linkedin" in sites:
        if hours_old and easy_apply:
            warnings.append(
                "[linkedin] limitation: only ONE of hours_old / easy_apply can be used. "
                "Lib sends both (f_TPR + f_AL) which LinkedIn may ignore/reject. "
                "Tip: keep easy_apply=False when hours_old is set."
            )
    return warnings


def fetch_one_site(site: str, search_term: str, **overrides) -> tuple[pd.DataFrame, str | None]:
    """Scrape a SINGLE site in ONE call. Never raises: returns (df, error_str)."""
    from jobspy import scrape_jobs

    params = dict(DEFAULTS)
    params.update(overrides or {})
    google_term = params.pop("google_search_term", None) or search_term
    params["search_term"] = search_term
    params["google_search_term"] = google_term

    try:
        df = scrape_jobs(site_name=[site], **params)
        if df is None:
            return pd.DataFrame(), None
        return df, None
    except Exception as exc:  # noqa: BLE001 - isolation boundary, must not propagate
        err = f"{type(exc).__name__}: {exc}"
        return pd.DataFrame(), err


def _page_new_urls(df: pd.DataFrame, seen: set[str]) -> pd.DataFrame:
    """Return only rows whose job_url was not seen before in this run."""
    if df is None or len(df) == 0 or "job_url" not in df.columns:
        return pd.DataFrame()
    mask = ~df["job_url"].astype(str).isin(seen)
    return df[mask]


def fetch_site_paged(
    site: str,
    search_term: str,
    page_size: int = 25,
    page_delay: float = 4.0,
    db_path=DB_PATH,
    save_incremental: bool = True,
    **overrides,
) -> tuple[pd.DataFrame, str | None, dict]:
    """Fetch ONE site page-by-page, checkpointing each page to SQLite immediately.

    Never raises: returns (combined_df, error_str_or_None, meta).
    Skip-and-continue: a failed page is recorded and SKIPPED — the next
    page is requested with the advanced offset/window, so one dead page
    (429, timeout, parse crash) cannot stop the run from reaching its
    target. A crash on page N keeps pages 1..N-1 (already committed in
    SQLite AND returned in combined_df) and still collects N+1...
    """
    target = int(overrides.get("results_wanted", DEFAULTS["results_wanted"]) or 0)
    base_offset = int(overrides.get("offset", DEFAULTS["offset"]) or 0)
    if target <= 0 or page_size <= 0:
        df, err = fetch_one_site(site, search_term, **overrides)
        saved = {"inserted": 0, "updated": 0, "skipped": 0}
        if save_incremental and len(df):
            saved = save_jobs(df, search_term=search_term, db_path=db_path)
        return df, err, {
            "strategy": "single-call",
            "pages": 1,
            "checkpoints": [len(df)],
            "saved": saved,
        }

    use_offset = site.lower() in OFFSET_PAGED_SITES
    strategy = "offset" if use_offset else "expanding-window"
    seen: set[str] = set()
    frames: list[pd.DataFrame] = []
    checkpoints: list[int] = []
    pages = 0
    window = 0
    errors: list[str] = []
    inserted = updated = skipped = 0

    while len(seen) < target and pages < MAX_PAGES:
        if use_offset:
            page_offset = base_offset + pages * page_size
            want = min(page_size, target - len(seen))
            call_kwargs = dict(overrides, results_wanted=want, offset=page_offset)
            page_desc = f"offset={page_offset} want={want}"
        else:
            window = min(window + page_size, target)
            call_kwargs = dict(overrides, results_wanted=window, offset=0)
            page_desc = f"window={window}"
        df, err = fetch_one_site(site, search_term, **call_kwargs)
        pages += 1
        if err:
            # Dead page: record it and continue with the next offset/window
            # (already advanced via `pages`/`window` above) toward the target.
            errors.append(f"page {pages} skipped [{strategy} {page_desc}]: {err}")
            continue
        new_df = _page_new_urls(df, seen)
        if len(df) == 0 or len(new_df) == 0:
            break  # exhausted, or offset-ignorant loop repeating itself
        for url in new_df["job_url"].astype(str):
            seen.add(url)
        frames.append(new_df)
        checkpoints.append(len(new_df))
        if save_incremental:
            s = save_jobs(new_df, search_term=search_term, db_path=db_path)
            inserted += s["inserted"]
            updated += s["updated"]
            skipped += s["skipped"]
        if len(seen) >= target:
            break
        if not use_offset and window >= target:
            break  # cannot expand the window any further
        if page_delay > 0:
            time.sleep(page_delay)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    error = " ; ".join(errors) if errors else None
    meta = {
        "strategy": strategy,
        "pages": pages,
        "failed_pages": len(errors),
        "checkpoints": checkpoints,
        "saved": {"inserted": inserted, "updated": updated, "skipped": skipped},
    }
    return combined, error, meta


def _fetch_all_sites_single(
    search_term: str,
    sites: list[str] | None = None,
    db_path=DB_PATH,
    save_incremental: bool = True,
    parallel: bool = False,
    page_size: int = 25,
    page_delay: float = 4.0,
    _run_id: int | None = None,
    _pass_no: int = 1,
    **overrides,
) -> dict:
    """One full pass over all sites (see fetch_all_sites for the loop).

    When _run_id is given, each site-pass is recorded as a fetch_attempts row.
    page_size <= 0 disables paging (legacy single call per site).
    Returns summary dict:
      {search_term, started_at, per_site: {site: {rows, error, saved,
        strategy, pages, checkpoints}}, total_rows, total_inserted,
       total_updated, warnings}
    """
    from server.jobs_db import utcnow_iso

    sites = list(sites or DEFAULT_SITES)
    warnings = validate_params(
        sites,
        overrides.get("job_type", DEFAULTS["job_type"]),
        overrides.get("hours_old", DEFAULTS["hours_old"]),
        overrides.get("easy_apply", DEFAULTS["easy_apply"]),
        overrides.get("is_remote", DEFAULTS["is_remote"]),
    )
    init_db(db_path)
    started_at = utcnow_iso()
    per_site: dict = {}
    total_rows = total_inserted = total_updated = 0

    def _run(site: str):
        attempt_id = (start_attempt(_run_id, _pass_no, site, db_path)
                      if _run_id is not None else None)
        if page_size > 0:
            df, err, meta = fetch_site_paged(
                site,
                search_term,
                page_size=page_size,
                page_delay=page_delay,
                db_path=db_path,
                save_incremental=save_incremental,
                **overrides,
            )
            saved, meta = meta["saved"], meta
        else:
            df, err = fetch_one_site(site, search_term, **overrides)
            saved = {"inserted": 0, "updated": 0, "skipped": 0}
            if save_incremental and len(df):
                # Incremental persistence: a later site crashing cannot wipe this.
                saved = save_jobs(df, search_term=search_term, db_path=db_path)
            meta = {
                "strategy": "single-call",
                "pages": 1,
                "failed_pages": 0,
                "checkpoints": [len(df)],
            }
        if attempt_id is not None:
            finish_attempt(attempt_id, meta.get("strategy"), meta.get("pages", 0),
                           meta.get("failed_pages", 0), len(df),
                           saved.get("inserted", 0), saved.get("updated", 0),
                           saved.get("skipped", 0), err, db_path)
        return site, df, err, saved, meta

    if parallel:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(sites), 7)) as ex:
            futs = {ex.submit(_run, s): s for s in sites}
            ordered = []
            for fut in concurrent.futures.as_completed(futs):
                try:
                    ordered.append(fut.result())
                except Exception as exc:  # should never happen (_run never raises)
                    ordered.append(
                        (futs[fut], pd.DataFrame(), f"wrapper: {exc}", {},
                         {"strategy": "?", "pages": 0, "checkpoints": []})
                    )
            # restore requested site order for stable reporting
            order = {s: i for i, s in enumerate(sites)}
            ordered.sort(key=lambda t: order.get(t[0], 99))
    else:
        ordered = [_run(s) for s in sites]

    frames = []
    for site, df, err, saved, meta in ordered:
        n = len(df)
        total_rows += n
        total_inserted += saved.get("inserted", 0)
        total_updated += saved.get("updated", 0)
        per_site[site] = {
            "rows": n,
            "error": err,
            "saved": saved,
            "strategy": meta.get("strategy"),
            "pages": meta.get("pages"),
            "checkpoints": meta.get("checkpoints"),
        }
        if n:
            frames.append(df)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return {
        "search_term": search_term,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sites": sites,
        "warnings": warnings,
        "per_site": per_site,
        "total_rows": total_rows,
        "total_inserted": total_inserted,
        "total_updated": total_updated,
        "combined": combined,
    }


def fetch_all_sites(
    search_term: str,
    sites: list[str] | None = None,
    db_path=DB_PATH,
    save_incremental: bool = True,
    parallel: bool = False,
    page_size: int = 25,
    page_delay: float = 4.0,
    target_new: int | None = None,
    max_passes: int = 6,
    **overrides,
) -> dict:
    """One user request = one fetch_runs row; each site-pass = attempt rows.

    target_new: keep running FULL passes until this many NEW jobs (inserted)
      accumulated across passes, or a pass adds nothing (source exhausted),
      or max_passes is hit. None = single pass (legacy behavior).
    Returns the single-pass summary shape plus run_id, a passes log, and
    CUMULATIVE totals across passes (per_site shows the last pass).
    """
    import json

    sites = list(sites or DEFAULT_SITES)
    params_json = json.dumps(
        {"results_wanted": overrides.get("results_wanted", DEFAULTS["results_wanted"]),
         "hours_old": overrides.get("hours_old", DEFAULTS["hours_old"]),
         "location": overrides.get("location", DEFAULTS["location"]),
         "job_type": overrides.get("job_type", DEFAULTS["job_type"]),
         "page_size": page_size, "max_passes": max_passes}, default=str)
    run_id = create_fetch_run(search_term, sites, target_new, params_json, db_path)
    passes_log: list[dict] = []
    frames = []
    cum_rows = cum_ins = cum_upd = 0
    last: dict = {}
    passes = 0
    try:
        while True:
            passes += 1
            s = _fetch_all_sites_single(
                search_term, sites=sites, db_path=db_path,
                save_incremental=save_incremental, parallel=parallel,
                page_size=page_size, page_delay=page_delay,
                _run_id=run_id, _pass_no=passes, **overrides)
            last = s
            passes_log.append({
                "pass_no": passes, "rows": s["total_rows"],
                "inserted": s["total_inserted"], "updated": s["total_updated"],
                "errors": {k: v["error"] for k, v in s["per_site"].items()
                           if v["error"]},
            })
            cum_rows += s["total_rows"]
            cum_ins += s["total_inserted"]
            cum_upd += s["total_updated"]
            if len(s["combined"]):
                frames.append(s["combined"])
            if target_new is None:
                break
            if cum_ins >= target_new:
                break
            if s["total_inserted"] == 0:
                break  # exhausted: a full pass added nothing new
            if passes >= max_passes:
                break
    except Exception as exc:
        finish_fetch_run(run_id, "error", passes, cum_rows, cum_ins, cum_upd,
                         str(exc), db_path)
        raise
    finish_fetch_run(run_id, "done", passes, cum_rows, cum_ins, cum_upd,
                     None, db_path)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    summary = dict(last)
    summary.update({
        "run_id": run_id, "passes": passes_log, "target_new": target_new,
        "total_rows": cum_rows, "total_inserted": cum_ins,
        "total_updated": cum_upd,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "combined": combined,
    })
    return summary


def print_summary(summary: dict) -> None:
    print(f"search_term: {summary['search_term']}  run_id={summary.get('run_id')} "
          f"passes={len(summary.get('passes', []))} target_new={summary.get('target_new')}")
    for w in summary.get("warnings", []):
        print(f"WARNING {w}")
    print(f"{'site':<15}{'rows':>7}  {'ins':>5} {'upd':>5}  {'pgs':>3}  strategy   error")
    for site in summary["sites"]:
        info = summary["per_site"][site]
        saved = info.get("saved", {})
        print(
            f"{site:<15}{info['rows']:>7}  {saved.get('inserted', 0):>5} "
            f"{saved.get('updated', 0):>5}  {info.get('pages', 0):>3}  "
            f"{str(info.get('strategy')):<10} {info['error'] or ''}"
        )
    print(
        f"TOTAL rows={summary['total_rows']} "
        f"inserted={summary['total_inserted']} updated={summary['total_updated']}"
    )
    empty = [s for s in summary["sites"]
             if summary["per_site"][s]["rows"] == 0
             and not summary["per_site"][s]["error"]]
    if empty:
        print(f"NOTE no jobs returned from: {', '.join(empty)} "
              f"(empty result or site-side block - see JobSpy logs above)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Resilient JobSpy fetch -> SQLite (fetched_jobs).")
    ap.add_argument("--search", required=True, help="search_term, e.g. 'software engineer'")
    ap.add_argument("--google-search", default=None)
    ap.add_argument("--sites", default=",".join(DEFAULT_SITES),
                    help=f"comma-separated sites (all: {','.join(ALL_SITES)})")
    ap.add_argument("--location", default=DEFAULTS["location"])
    ap.add_argument("--results", type=int, default=DEFAULTS["results_wanted"])
    ap.add_argument("--hours-old", type=int, default=DEFAULTS["hours_old"])
    ap.add_argument("--job-type", default=DEFAULTS["job_type"])
    ap.add_argument("--country-indeed", default=DEFAULTS["country_indeed"])
    ap.add_argument("--no-linkedin-desc", action="store_true")
    ap.add_argument("--parallel", action="store_true", help="fetch sites concurrently (still isolated)")
    ap.add_argument("--page-size", type=int, default=25,
                    help="jobs per checkpoint page; 0 = single call per site (no per-page checkpoints)")
    ap.add_argument("--page-delay", type=float, default=4.0,
                    help="seconds to wait between pages of the same site (politeness vs rate limits)")
    ap.add_argument("--target-new", type=int, default=None,
                    help="keep running full passes until this many NEW jobs "
                         "accumulated (default: single pass)")
    ap.add_argument("--max-passes", type=int, default=6,
                    help="pass cap for --target-new loops")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    summary = fetch_all_sites(
        args.search,
        sites=sites,
        db_path=args.db,
        save_incremental=not args.no_save,
        parallel=args.parallel,
        page_size=args.page_size,
        page_delay=args.page_delay,
        target_new=args.target_new,
        max_passes=args.max_passes,
        location=args.location,
        results_wanted=args.results,
        hours_old=args.hours_old,
        job_type=args.job_type,
        country_indeed=args.country_indeed,
        linkedin_fetch_description=not args.no_linkedin_desc,
        google_search_term=args.google_search or args.search,
    )
    print_summary(summary)
    # Non-zero exit if EVERY site failed (total loss scenario we guard against).
    if summary["total_rows"] == 0 and any(v["error"] for v in summary["per_site"].values()):
        print("All sites failed - check network/rate limits.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
