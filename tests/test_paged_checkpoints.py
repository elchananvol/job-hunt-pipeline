"""Prove per-page checkpoints + skip-and-continue: a dead page N is skipped,
later pages are still collected, and everything lands in df + SQLite.

Covers both paging strategies with mocked fetch_one_site (no network):
- offset site (indeed): pages return disjoint URL ranges; page 3 fails once.
- expanding site (bayt): each call returns the full prefix window;
  call 3 fails once.
Asserts: all 30 jobs collected despite the failed page + error recorded.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import server.fetch_jobs as fetch_jobs
from server.fetch_jobs import fetch_site_paged
from server.jobs_db import count_jobs


def make_df(urls):
    return pd.DataFrame(
        [
            {
                "job_url": u,
                "site": "test",
                "title": f"job {u}",
                "company": "Acme",
                "description": "d",
            }
            for u in urls
        ]
    )


def run_offset_case(tmp):
    calls = {"n": 0}

    def fake_fetch(site, search_term, **kw):
        calls["n"] += 1
        page = kw.get("offset", 0) // kw.get("results_wanted", 10)
        if calls["n"] == 3:
            return pd.DataFrame(), "RuntimeError: simulated 429 on page 3"
        start = page * 10
        return make_df([f"https://x/{i}" for i in range(start, start + 10)]), None

    orig = fetch_jobs.fetch_one_site
    fetch_jobs.fetch_one_site = fake_fetch
    try:
        df, err, meta = fetch_site_paged(
            "indeed", "software engineer", page_size=10, page_delay=0,
            db_path=tmp, results_wanted=30, hours_old=72,
        )
    finally:
        fetch_jobs.fetch_one_site = orig
    print(f"[offset] rows={len(df)} err={err}")
    print(f"[offset] meta={meta}")
    assert len(df) == 30, f"expected 30 jobs despite skipped page, got {len(df)}"
    assert err and "page 3 skipped" in err
    assert meta["strategy"] == "offset" and meta["pages"] == 4
    assert meta["failed_pages"] == 1
    assert meta["checkpoints"] == [10, 10, 10]
    assert count_jobs(tmp) == 30
    print("[offset] PASS: dead page 3 skipped, pages 1-2+4-5 in df + sqlite")


def run_expanding_case(tmp):
    calls = {"n": 0}

    def fake_fetch(site, search_term, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            return pd.DataFrame(), "TimeoutError: simulated mid-site crash"
        window = kw.get("results_wanted", 10)
        return make_df([f"https://y/{i}" for i in range(window)]), None

    orig = fetch_jobs.fetch_one_site
    fetch_jobs.fetch_one_site = fake_fetch
    try:
        df, err, meta = fetch_site_paged(
            "bayt", "software engineer", page_size=10, page_delay=0,
            db_path=tmp, results_wanted=30,
        )
    finally:
        fetch_jobs.fetch_one_site = orig
    print(f"[expanding] rows={len(df)} err={err}")
    print(f"[expanding] meta={meta}")
    assert len(df) == 30, f"expected 30 jobs despite skipped page, got {len(df)}"
    assert err and "page 3 skipped" in err
    assert meta["strategy"] == "expanding-window"
    assert meta["failed_pages"] == 1
    # window1=10 new, window2=20 -> 10 new, window3 fails, window4=30 -> 10 new
    assert meta["checkpoints"] == [10, 10, 10], meta["checkpoints"]
    assert count_jobs(tmp) == 30
    print("[expanding] PASS: dead page 3 skipped, later windows in df + sqlite")


def main():
    tmp = os.path.join(tempfile.gettempdir(), "opencode_paged_test.db")
    try:
        os.remove(tmp)
    except OSError:
        pass
    run_offset_case(tmp)
    try:
        os.remove(tmp)
    except OSError:
        pass
    run_expanding_case(tmp)
    print("ALL PAGED TESTS PASS")


if __name__ == "__main__":
    main()
