from conftest import read

from votetracker.sources import house_votes, senate_votes


def test_house_vote_parse():
    v = house_votes.parse(read("house/2025/roll100.xml"), 2025)
    assert v["id"] == "house-119-1-100"
    assert (v["congress"], v["session"], v["number"]) == (119, 1, 100)
    assert v["date"] == "2025-04-09"
    assert v["datetime"] == "2025-04-09T17:14:00-04:00"  # Eastern daylight time
    assert v["required"] == "2/3"
    assert v["result"] == "Passed"
    assert v["bill"]["id"] == "119-hr-1228"
    assert v["totals"] == {"yea": 2, "nay": 2, "present": 0, "not_voting": 1, "excused": 0, "other": 0}
    assert v["official_party_totals"]["D"]["not_voting"] == 1
    jordan = next(p for p in v["positions"] if p["bioguide"] == "J000289")
    assert jordan == {"bioguide": "J000289", "name": "Jordan", "party": "R", "state": "OH", "vote": "yea", "raw": "Yea"}
    assert v["source"]["url"] == "https://clerk.house.gov/Votes/2025100"


def test_house_amendment_and_aye_no():
    v = house_votes.parse(read("house/2025/roll101.xml"), 2025)
    assert v["amendment"] == {"number": "3", "author": "Kaptur of Ohio Amendment No. 3"}
    assert v["totals"]["yea"] == 4 and v["totals"]["nay"] == 1
    assert v["required"] == "1/2"


def test_house_quorum_is_not_a_bill():
    v = house_votes.parse(read("house/2026/roll001.xml"), 2026)
    assert v["bill"] is None
    assert v["required"] is None
    assert v["session"] == 2
    assert v["totals"]["present"] == 4


def test_house_fetch_new_stops_at_end(http):
    found = house_votes.fetch_new(http, 2025, 100)
    assert [v["number"] for v in found] == [100, 101]
    # 100, 101 found; 102 and 103 missing -> stop.
    assert http.calls[-1].endswith("roll103.xml")


def test_senate_menu():
    assert senate_votes.parse_menu(read("senate/vote_menu_119_1.xml")) == [1, 2]


def test_senate_vote_parse_bill():
    lis = {"S438": "H001104", "S434": "M001242", "S313": "S000033", "S422": "W000800"}
    v = senate_votes.parse(read("senate/vote_119_1_00001.xml"), lis)
    assert v["id"] == "senate-119-1-1"
    assert v["datetime"] == "2025-01-15T14:30:00-05:00"  # Eastern standard time
    assert v["bill"]["id"] == "119-s-5"
    assert v["nomination"] is None
    assert v["result"] == "Bill Failed"
    assert v["required"] == "1/2"
    former = next(p for p in v["positions"] if p["lis"] == "S999")
    assert former["bioguide"] is None  # mapped later from historical data
    assert v["source"]["url"].endswith("vote_119_1_00001.htm")


def test_senate_vote_parse_nomination():
    v = senate_votes.parse(read("senate/vote_119_1_00002.xml"), {})
    assert v["bill"] is None
    assert v["nomination"]["id"] == "PN12"
    assert v["nomination"]["url"] == "https://www.congress.gov/nomination/119th-congress/12"
    assert v["totals"]["not_voting"] == 1
