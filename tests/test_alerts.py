from datetime import date

from conftest import routes

from votetracker import notify
from votetracker.http import FakeHttpClient
from votetracker.pipeline import Pipeline
from votetracker.sources import house_votes
from votetracker.store import LocalStore


def test_is_major():
    assert notify.is_major({"question": "On Passage of the Bill"})
    assert notify.is_major({"question": "On Motion to Suspend the Rules and Pass"})
    assert notify.is_major({"question": "On the Nomination"})
    assert notify.is_major({"question": "On Motion to Concur in the Senate Amendment"})
    assert notify.is_major({"motion_types": ["veto-override"], "question": "Override"})
    assert not notify.is_major({"question": "On Agreeing to the Amendment"})
    assert not notify.is_major({"question": "On Motion to Recommit"})
    assert not notify.is_major({"question": "On Agreeing to the Resolution",
                                "description": "Providing for consideration of the bill (H.R. 1)"})


def test_compose_wording():
    v = {"id": "x", "question": "On Passage", "result": "Passed", "bill": {"label": "H.R. 1"}}
    assert notify.compose("Rep. A", [(v, {"vote": "nay"})]) == ("Rep. A voted No", "H.R. 1: On Passage — Passed")
    assert notify.compose("Rep. A", [(v, {"vote": "not_voting"})])[0] == "Rep. A did not vote"
    title, body = notify.compose("Rep. A", [(v, {"vote": "yea"}), (v, {"vote": "nay"})])
    assert title == "Rep. A: 2 new votes"
    assert body.startswith("Latest: voted Yes on H.R. 1")


def test_old_votes_never_alert():
    v = {"id": "house-119-1-1", "date": "2025-01-01", "question": "On Passage", "positions":
         [{"bioguide": "A000001", "vote": "yea"}]}
    msgs = notify.plan([v], "federal", "bioguide", lambda _: "Rep. A", lambda b: f"members/{b}.json", date(2026, 11, 18))
    assert msgs == []


def test_federal_alerts_from_pipeline(tmp_path, cfg, monkeypatch):
    real = house_votes.fetch_new
    monkeypatch.setattr(house_votes, "fetch_new", lambda http, year, start, max_new=None:
                        real(http, year, 100 if (year == 2025 and start == 1) else start, max_new))
    store = LocalStore(tmp_path)
    today = date(2025, 4, 11)  # House rolls 100-101 were Apr 9-10, 2025
    Pipeline(cfg, store, FakeHttpClient(routes()), today=today).run(states=False)
    alerts = store.get_json("state/alerts/last_run.json")
    by_topic = {m["topic"]: m for m in alerts["sample"]}

    jordan = by_topic["m-J000289"]
    assert jordan["notification"]["title"] == "Rep. Jim Jordan: 2 new votes"
    assert jordan["data"]["path"] == "members/J000289.json"
    assert jordan["data"]["votes"] == "house-119-1-101,house-119-1-100"

    major = by_topic["m-J000289-major"]  # only the passage vote, not the amendment
    assert major["notification"]["title"] == "Rep. Jim Jordan voted Yes"
    assert major["data"]["votes"] == "house-119-1-100"

    # Senate votes in the fixtures are from January: too old to alert.
    assert not any(t.startswith("m-M001242") for t in by_topic)

    # A second run finds nothing new, so nothing is sent again.
    Pipeline(cfg, store, FakeHttpClient(routes()), today=today).run(states=False)
    assert store.get_json("state/alerts/last_run.json")["planned"] == 0
