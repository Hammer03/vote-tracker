from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from votetracker.config import Config
from votetracker.http import FakeHttpClient
from votetracker.sources import house_votes, legislators, senate_votes
from votetracker.sources.congress_gov import API
from votetracker.sources.openstates import API as OS_API
from votetracker.sources.openstates import PEOPLE_CSV

FIX = Path(__file__).parent / "fixtures"


def read(rel: str) -> bytes:
    return (FIX / rel).read_bytes()


def routes() -> dict:
    r = {}
    for key, url in legislators.FILES.items():
        name = url.rsplit("/", 1)[1]
        r[url] = (200, read(f"legislators/{name}"))
    r[house_votes.xml_url(2025, 100)] = (200, read("house/2025/roll100.xml"))
    r[house_votes.xml_url(2025, 101)] = (200, read("house/2025/roll101.xml"))
    r[house_votes.xml_url(2026, 1)] = (200, read("house/2026/roll001.xml"))
    r[senate_votes.menu_url(119, 1)] = (200, read("senate/vote_menu_119_1.xml"))
    r[senate_votes.xml_url(119, 1, 1)] = (200, read("senate/vote_119_1_00001.xml"))
    r[senate_votes.xml_url(119, 1, 2)] = (200, read("senate/vote_119_1_00002.xml"))
    r[f"{API}/bill/119/hr/1228"] = (200, read("congress_gov/bill_119_hr_1228.json"))
    r[f"{API}/bill/119/hr/1228/summaries"] = (200, read("congress_gov/bill_119_hr_1228_summaries.json"))
    r[f"{API}/bill/119/hr/1228/text"] = (200, read("congress_gov/bill_119_hr_1228_text.json"))
    r[f"{API}/bill/119/s/5"] = (200, read("congress_gov/bill_119_s_5.json"))

    # Open States
    r[f"{OS_API}/jurisdictions"] = (200, read("openstates/jurisdictions.json"))
    r[PEOPLE_CSV.format(abbr="oh")] = (200, read("openstates/oh.csv"))

    def bills(params):
        p = dict(params or [])
        if p.get("jurisdiction") == "oh" and p.get("session") == "136" and p.get("page") == "1":
            if p.get("updated_since", "") < "2026-11-17T19:30:00+00:00":
                return 200, read("openstates/bills_oh_136_p1.json")
            return 200, b'{"results": [], "pagination": {"per_page": 20, "page": 1, "max_page": 1, "total_items": 0}}'
        return 404, b"{}"

    r[f"{OS_API}/bills"] = bills
    return r


@pytest.fixture
def http():
    client = FakeHttpClient(routes())
    # House votes 1..99 of 2025 don't exist in the fixtures; start at 100.
    return client


@pytest.fixture
def cfg():
    c = Config()
    c.congress_gov_api_key = "TEST_KEY"
    c.r2_account_id = c.r2_access_key_id = c.r2_secret_access_key = c.r2_bucket = None
    c.openstates_api_key = None
    c.firebase_service_account = None
    c.openstates_daily_budget = 240
    c.openstates_backfill_days = 14
    return c


@pytest.fixture
def today():
    return date(2026, 11, 18)


@pytest.fixture
def legs(http):
    return legislators.fetch(http)
