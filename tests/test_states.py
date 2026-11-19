import json
from datetime import date, datetime, timezone

from conftest import read, routes

from votetracker.http import FakeHttpClient
from votetracker.pipeline import Pipeline
from votetracker.sources import openstates as os_src
from votetracker.store import LocalStore

NOW = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
CAROL = "11111111-aaaa-4bbb-8ccc-000000000003"
DAN = "11111111-aaaa-4bbb-8ccc-000000000004"
ERIN = "11111111-aaaa-4bbb-8ccc-000000000005"


def load(root, key):
    return json.loads((root / key).read_text())


def run_states(tmp_path, cfg, http=None, **kw):
    store = LocalStore(tmp_path)
    http = http or FakeHttpClient(routes())
    report = Pipeline(cfg, store, http, today=NOW.date()).run(
        only_states={"OH"}, now=NOW, bills=False, **kw)
    return store, http, report


def test_person_csv_normalizing():
    import csv, io
    rows = list(csv.DictReader(io.StringIO(read("openstates/oh.csv").decode())))
    dan = os_src.normalize_person(rows[3], "OH")
    assert dan["id"] == DAN
    assert dan["party_code"] == "D"
    assert dan["title"] == "State Representative"
    assert [o["type"] for o in dan["offices"]] == ["capitol", "district"]
    assert dan["offices"][1]["phone"] == "937-555-0100"
    alice = os_src.normalize_person(rows[0], "OH")
    assert alice["title"] == "State Senator"
    assert alice["social"] == {"twitter": "AliceExample"}


def test_titles_by_state():
    assert os_src.title_for("CA", "lower") == "Assembly Member"
    assert os_src.title_for("VA", "lower") == "Delegate"
    assert os_src.title_for("NE", "legislature") == "State Senator"
    assert os_src.title_for("OH", "upper") == "State Senator"


def test_active_sessions():
    j = json.loads(read("openstates/jurisdictions.json"))["results"][0]
    sessions = os_src.active_sessions(j, date(2026, 11, 18))
    assert [s["identifier"] for s in sessions] == ["136"]
    # Between sessions: fall back to the latest one that started
    sessions = os_src.active_sessions(j, date(2027, 6, 1))
    assert [s["identifier"] for s in sessions] == ["136"]


def test_vote_normalizing_named_and_counts_only():
    page = json.loads(read("openstates/bills_oh_136_p1.json"))
    hb = page["results"][0]
    v = os_src.normalize_vote(hb, hb["votes"][0], "OH", {})
    assert v["id"].startswith("oh-")
    assert v["result"] == "Passed"
    assert v["chamber"] == "lower"
    assert v["totals"]["yea"] == 1 and v["totals"]["nay"] == 1 and v["totals"]["excused"] == 1
    assert v["source"]["url"] == "https://www.legislature.ohio.gov/legislation/136/hb101/votes"
    sb = page["results"][1]
    v2 = os_src.normalize_vote(sb, sb["votes"][0], "OH", {})
    assert v2["named_votes"] is False
    assert v2["totals"]["yea"] == 12 and v2["totals"]["nay"] == 20 and v2["totals"]["not_voting"] == 1


def test_budget_shares_the_day(tmp_path):
    store = LocalStore(tmp_path)
    b = os_src.Budget(store, 240, datetime(2026, 11, 18, 0, 5, tzinfo=timezone.utc))
    assert b.run_limit == 10  # 240 left / 24 runs
    late = os_src.Budget(store, 240, datetime(2026, 11, 18, 23, 5, tzinfo=timezone.utc))
    assert late.run_limit == 240  # last run of the day may use what's left
    for _ in range(10):
        b.take()
    try:
        b.take()
    except os_src.BudgetExhausted:
        pass
    else:
        raise AssertionError("expected BudgetExhausted")
    b.save()
    again = os_src.Budget(store, 240, datetime(2026, 11, 18, 1, 5, tzinfo=timezone.utc))
    assert again.used == 10


def test_states_without_key_publish_legislators_only(tmp_path, cfg):
    store, http, report = run_states(tmp_path, cfg)
    assert not any("v3.openstates.org" in c for c in http.calls)
    leg = load(tmp_path, "v1/states/OH/legislature.json")
    assert leg["chambers"]["lower"]["members"] == 3
    assert leg["coverage"]["roll_calls"] == 0
    dist9 = next(d for d in leg["chambers"]["lower"]["districts"] if d["district"] == "9")
    assert len(dist9["members"]) == 2  # multi-member district kept together


def test_states_with_key_end_to_end(tmp_path, cfg):
    cfg.openstates_api_key = "OS_TEST"
    store, http, report = run_states(tmp_path, cfg)
    assert report["states"]["state_votes_added"] == 2
    assert report["states"]["openstates_requests_today"] == 2  # jurisdictions + one bills page

    leg = load(tmp_path, "v1/states/OH/legislature.json")
    assert leg["sessions"][0]["identifier"] == "136"
    assert leg["chambers"]["upper"]["seats"] == 2
    assert leg["coverage"]["roll_calls"] == 2
    assert leg["coverage"]["with_names"] == 1

    latest = load(tmp_path, "v1/state-votes/OH/latest.json")["votes"]
    assert [v["bill"]["label"] for v in latest] == ["HB 101", "SB 7"]

    erin = load(tmp_path, f"v1/legislators/{ERIN}.json")
    assert erin["stats"]["excused"] == 1
    assert erin["stats"]["missed"] == 0  # excused is not counted as missed
    assert erin["stats"]["covers_from"] == "2026-11-17"
    carol_votes = load(tmp_path, f"v1/legislators/{CAROL}/votes.json")["votes"]
    assert carol_votes[0]["position"] == "yea"

    bill = load(tmp_path, "v1/state-bills/OH/136/22222222-bbbb-4ccc-8ddd-000000000001.json")
    assert bill["full_text_url"].endswith("01_PH/pdf/")
    assert bill["abstracts"][0]["text"].startswith("To name")
    assert len(bill["roll_call_votes"]) == 1

    cursors = store.get_json("state/openstates/cursors.json")
    assert cursors["OH"]["136"]["since"] == "2026-11-17T19:30:00+00:00"

    # Alerts: state votes are recent, so followers of each legislator get one.
    alerts = store.get_json("state/alerts/last_run.json")
    topics = {m["topic"] for m in alerts["sample"]}
    assert f"s-{CAROL}" in topics and f"s-{CAROL}-major" in topics  # third consideration = passage
    assert f"s-{DAN}" in topics
    carol = next(m for m in alerts["sample"] if m["topic"] == f"s-{CAROL}")
    assert carol["notification"]["title"] == "Carol Test voted Yes"
    assert carol["notification"]["body"] == "HB 101: Third Consideration — Passed"
    assert alerts["firebase"] is False

    # Second run the same day: cached jurisdictions/people, cursor moves on, nothing new.
    store2, http2, report2 = run_states(tmp_path, cfg)
    assert report2["states"]["state_votes_added"] == 0
    assert not any(c.endswith("/jurisdictions") for c in http2.calls)
    assert not any("data.openstates.org" in c for c in http2.calls)
    assert report2["alerts"]["planned"] == 0
