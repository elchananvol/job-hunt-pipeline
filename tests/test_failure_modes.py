"""Prove the mid-fetch data-loss bug in raw scrape_jobs vs the resilient wrapper.

Simulates: site A (indeed) succeeds, site B (linkedin) raises mid-fetch.
- raw scrape_jobs(all sites): expect EXCEPTION + zero rows (total loss).
- fetch_all_sites(): expect partial rows from site A preserved + error recorded for B.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import pandas as pd

from jobspy.model import JobPost, JobResponse, Location


def fake_ok_jobs(n=3, site="x"):
    jobs = []
    for i in range(n):
        jobs.append(
            JobPost(
                id=f"{site}-{i}",
                title=f"Eng {i}",
                company_name="Acme",
                job_url=f"https://example.com/{site}/{i}",
                location=Location(city="Tel Aviv", country="Israel"),
                description="desc",
            )
        )
    return JobResponse(jobs=jobs)


def run():
    from jobspy import __init__ as jobspy_pkg  # noqa
    import jobspy

    real_scrape_site_classes = {}

    # Patch at the scraper-class level: Indeed returns jobs, LinkedIn raises.
    from jobspy.indeed import Indeed
    from jobspy.linkedin import LinkedIn

    with patch.object(Indeed, "scrape", return_value=fake_ok_jobs(3, "indeed")), patch.object(
        LinkedIn, "scrape", side_effect=RuntimeError("simulated LinkedIn 429 mid-fetch")
    ):
        print("=== 1) RAW scrape_jobs(site=[indeed, linkedin]) ===")
        try:
            df = jobspy.scrape_jobs(
                site_name=["indeed", "linkedin"],
                search_term="software engineer",
                location="israel",
                results_wanted=10,
                hours_old=72,
                country_indeed="israel",
                verbose=0,
            )
            print(f"raw returned {len(df)} rows (unexpected - no exception)")
        except Exception as exc:
            print(f"raw RAISED {type(exc).__name__}: {exc}")
            print("-> raw result: 0 rows usable by caller (total loss of indeed's 3 jobs)")

        print()
        print("=== 2) RESILIENT fetch_all_sites([indeed, linkedin]) ===")
        import tempfile, os
        from server.fetch_jobs import fetch_all_sites

        tmp = os.path.join(tempfile.gettempdir(), "opencode_failtest.db")
        try:
            os.remove(tmp)
        except OSError:
            pass
        summary = fetch_all_sites(
            "software engineer",
            sites=["indeed", "linkedin"],
            db_path=tmp,
            results_wanted=10,
            hours_old=72,
            country_indeed="israel",
        )
        print(f"per_site indeed: rows={summary['per_site']['indeed']['rows']} err={summary['per_site']['indeed']['error']}")
        print(f"per_site linkedin: rows={summary['per_site']['linkedin']['rows']} err={summary['per_site']['linkedin']['error']}")
        print(f"total_rows={summary['total_rows']} inserted={summary['total_inserted']}")
        from server.jobs_db import count_jobs

        print(f"rows in sqlite after partial failure: {count_jobs(tmp)}")
        assert summary["total_rows"] == 3, "wrapper must preserve indeed's 3 jobs"
        assert count_jobs(tmp) == 3
        print("PASS: wrapper preserves fetched data despite mid-fetch failure")


if __name__ == "__main__":
    run()
