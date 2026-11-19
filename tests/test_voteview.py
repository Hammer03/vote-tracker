import json
from datetime import date

from conftest import read, routes

from votetracker.http import FakeHttpClient
from votetracker.pipeline import Pipeline
from votetracker.sources import senate_votes, voteview
from votetracker.store import LocalStore

TODAY = date(2026, 11, 18)


def vv_routes(block_senate=True):
    r = routes()
    for kind in ("rollcalls", "votes", "members"):
        r[voteview.url(kind, 119)] = (200, read(f"voteview/S119_{kind}.csv"))
    if block_senate:
        for s in (1, 2):
            r[senate_votes.menu_url(119, s)] = (403, b"Access Denied")
    return r


def test_voteview_parse():
    http = FakeHttpClient(vv_routes())
    votes = {v["id"]: v for v in voteview.fetch_senate(http, 119)}
    assert set(votes) == {"senate-119-1-1", "senate-119-1-2", "senate-119-2-3"}

    v1 = votes["senate-119-1-1"]
    assert v1["bill"]["id"] == "119-s-5"
    assert v1["result"] == "Bill Failed"
    assert v1["totals"]["yea"] == 2 and v1["totals"]["nay"] == 2
    assert v1["source"]["url"].endswith("vote_119_1_00001.htm")  # official Senate page
    assert v1["source"]["via"] == "voteview"

    v2 = votes["senate-119-1-2"]
    assert v2["bill"] is None
    assert v2["nomination"]["id"] == "PN12"

    v3 = votes["senate-119-2-3"]
    moreno = next(p for p in v3["positions"] if p["bioguide"] == "M001242")
    assert moreno["vote"] == "not_voting"  # announced Yea is not a recorded vote
    assert moreno["raw"] == "Not Voting (announced Yea)"
    sanders = next(p for p in v3["positions"] if p["bioguide"] == "S000033")
    assert sanders["party"] == "I"
    assert len(v3["positions"]) == 4  # cast_code 0 (not a member) skipped


def test_pipeline_falls_back_when_senate_blocks(tmp_path, cfg):
    store = LocalStore(tmp_path)
    Pipeline(cfg, store, FakeHttpClient(vv_routes()), today=TODAY).run(
        states=False, money=False, bills=False, send_alerts=False)
    v = json.loads((tmp_path / "v1/votes/senate/119-2/3.json").read_text())
    assert v["source"]["name"].startswith("Voteview")
    meta = json.loads((tmp_path / "v1/meta.json").read_text())
    assert meta["counts"]["senate_votes"] == 3
    sanders = json.loads((tmp_path / "v1/members/S000033.json").read_text())
    assert sanders["stats"]["roll_calls"] == 3


def test_senate_gov_record_replaces_voteview(tmp_path, cfg):
    store = LocalStore(tmp_path)
    Pipeline(cfg, store, FakeHttpClient(vv_routes()), today=TODAY).run(
        states=False, money=False, bills=False, send_alerts=False)
    # Later, senate.gov answers again: its own records take over.
    Pipeline(cfg, store, FakeHttpClient(vv_routes(block_senate=False)), today=TODAY).run(
        states=False, money=False, bills=False, send_alerts=False)
    v1 = json.loads((tmp_path / "v1/votes/senate/119-1/1.json").read_text())
    assert v1["source"]["name"] == senate_votes.SOURCE_NAME
    assert v1["datetime"] == "2025-01-15T14:30:00-05:00"
    # A vote senate.gov doesn't list (in these fixtures) stays from Voteview.
    v3 = json.loads((tmp_path / "v1/votes/senate/119-2/3.json").read_text())
    assert v3["source"]["via"] == "voteview"
