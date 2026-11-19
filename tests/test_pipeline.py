import json

from votetracker.http import FakeHttpClient
from votetracker.pipeline import Pipeline
from votetracker.publish import cache_control_for
from votetracker.store import LocalStore

from conftest import routes


def load(root, key):
    return json.loads((root / key).read_text())


def run(tmp_path, cfg, today, http=None, **kw):
    store = LocalStore(tmp_path)
    http = http or FakeHttpClient(routes())
    report = Pipeline(cfg, store, http, today=today).run(**kw)
    return store, http, report


def test_end_to_end(tmp_path, cfg, today, monkeypatch):
    # Start House 2025 at roll 100 (the fixtures don't include 1-99).
    from votetracker.sources import house_votes

    real = house_votes.fetch_new
    monkeypatch.setattr(house_votes, "fetch_new", lambda http, year, start, max_new=None:
                        real(http, year, 100 if (year == 2025 and start == 1) else start, max_new))

    store, http, report = run(tmp_path, cfg, today)
    assert report["new_or_changed_votes"] == 5

    meta = load(tmp_path, "v1/meta.json")
    assert meta["counts"] == {"members": 10, "house_votes": 3, "senate_votes": 2, "bills": 2}
    assert "not affiliated" in meta["disclaimer"]

    # Vote detail with bill title from Congress.gov
    v = load(tmp_path, "v1/votes/house/119-1/100.json")
    assert v["bill"]["title"] == "Prioritizing Veterans' Survivors Act"
    assert v["source"]["url"].startswith("https://clerk.house.gov/")

    # Former senator matched through historical data
    s1 = load(tmp_path, "v1/votes/senate/119-1/1.json")
    assert any(p["bioguide"] == "X000999" for p in s1["positions"])

    # Bill file with CRS summary and text links
    bill = load(tmp_path, "v1/bills/119/hr1228.json")
    assert bill["summary"]["text"].startswith("This bill moves the office")
    assert bill["summary"]["author"] == "Congressional Research Service"
    assert bill["full_text_url"].endswith("eh.htm")
    assert [r["number"] for r in bill["roll_call_votes"]] == [100, 101]

    # Member profile
    jordan = load(tmp_path, "v1/members/J000289.json")
    assert jordan["title"] == "Representative"
    assert jordan["district"] == 4
    assert jordan["stats"]["roll_calls"] == 3
    assert jordan["stats"]["missed"] == 0
    assert jordan["contact"]["switchboard"] == "(202) 224-3121"
    assert len(jordan["recent_votes"]) == 3
    history = load(tmp_path, "v1/members/J000289/votes-119.json")
    assert history["votes"][0]["id"] == "house-119-2-1"  # newest first

    norton = load(tmp_path, "v1/members/N000147.json")
    assert norton["title"] == "Delegate"
    assert norton["votes_on_floor"] is False

    # State lookup
    ohio = load(tmp_path, "v1/states/OH.json")
    assert len(ohio["senators"]) == 2
    assert len(ohio["house"]) == 15
    d4 = next(h for h in ohio["house"] if h["district"] == 4)
    assert d4["member"]["bioguide"] == "J000289"
    vt = load(tmp_path, "v1/states/VT.json")
    assert [h["district"] for h in vt["house"]] == [0]

    latest = load(tmp_path, "v1/votes/latest.json")["votes"]
    assert latest[0]["id"] == "house-119-2-1"
    assert load(tmp_path, "v1/votes/house/119-1.json")["votes"][0]["number"] == 101


def test_second_run_only_republishes_meta(tmp_path, cfg, today, monkeypatch):
    from votetracker.sources import house_votes

    real = house_votes.fetch_new
    monkeypatch.setattr(house_votes, "fetch_new", lambda http, year, start, max_new=None:
                        real(http, year, 100 if (year == 2025 and start == 1) else start, max_new))
    run(tmp_path, cfg, today)
    _, http2, report = run(tmp_path, cfg, today)
    assert report["new_or_changed_votes"] == 0
    assert report["uploaded"] <= 1  # at most meta.json (its timestamp)
    # No bill lookups needed on the second run
    assert not any("api.congress.gov" in c for c in http2.calls)


def test_runs_without_api_key(tmp_path, cfg, today):
    cfg.congress_gov_api_key = None
    _, http, report = run(tmp_path, cfg, today)
    assert not any("api.congress.gov" in c for c in http.calls)
    bill = load(tmp_path, "v1/bills/119/s5.json")
    assert bill["title"] is None
    assert bill["label"] == "S. 5"


def test_cache_rules():
    assert cache_control_for("v1/meta.json") == "public, max-age=60"
    assert cache_control_for("v1/votes/house/119-1/100.json") == "public, max-age=3600"
    assert cache_control_for("v1/members/J000289.json") == "public, max-age=300"
