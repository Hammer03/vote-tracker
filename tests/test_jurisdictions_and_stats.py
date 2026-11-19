import copy

from conftest import read

from votetracker import jurisdictions
from votetracker.builders import stats
from votetracker.sources import senate_votes


def test_base_jurisdictions():
    base = jurisdictions.load_base()
    states = [j for j in base if j["kind"] == "state"]
    assert len(base) == 56
    assert len(states) == 50
    assert sum(j["house_seats"] for j in states) == 435


def test_dc_becomes_a_state_automatically(legs):
    resolved = {j["code"]: j for j in jurisdictions.resolve(legs.current)}
    assert resolved["DC"]["kind"] == "district"
    assert resolved["DC"]["voting_representation"] is False

    # Simulate the official data adding two senators for DC.
    members = copy.deepcopy(legs.current)
    for i, bid in enumerate(("Z000001", "Z000002")):
        members.append({"id": {"bioguide": bid}, "name": {"first": "New", "last": f"Senator{i}"},
                        "terms": [{"type": "sen", "state": "DC", "start": "2029-01-03", "end": "2035-01-03",
                                   "party": "Democrat"}]})
    resolved = {j["code"]: j for j in jurisdictions.resolve(members)}
    assert resolved["DC"]["kind"] == "state"
    assert resolved["DC"]["voting_representation"] is True
    assert resolved["DC"]["senators"] == ["Z000001", "Z000002"]


def test_new_unknown_jurisdiction_is_kept(legs):
    members = copy.deepcopy(legs.current)
    members.append({"id": {"bioguide": "Z000009"}, "name": {"first": "A", "last": "B"},
                    "terms": [{"type": "rep", "state": "ZZ", "district": 0, "start": "2029-01-03", "end": "2031-01-03",
                               "party": "Independent"}]})
    codes = {j["code"] for j in jurisdictions.resolve(members)}
    assert "ZZ" in codes


def test_stats_missed_and_with_party(legs):
    lis = dict(legs.lis_to_bioguide)
    v1 = senate_votes.parse(read("senate/vote_119_1_00001.xml"), lis)
    v2 = senate_votes.parse(read("senate/vote_119_1_00002.xml"), lis)
    s = stats.compute([v1, v2], legs.caucus_code)

    husted = s["H001104"]["senate"]
    assert husted["roll_calls"] == 2
    assert husted["missed"] == 1
    assert husted["missed_pct"] == 50.0

    # Sanders is an Independent who caucuses with Democrats (per the data).
    sanders = s["S000033"]["senate"]
    # Vote 1: Democrats (incl. Sanders + the unmatched former senator, party D) voted Nay; Sanders Nay -> same.
    # Vote 2: Welch Yea vs Sanders Nay -> Democrats split evenly -> not counted.
    assert sanders["with_party_eligible"] == 1
    assert sanders["with_party"] == 1
    assert sanders["with_party_pct"] == 100.0

    moreno = s["M001242"]["senate"]
    assert moreno["with_party_eligible"] == 2  # vote 2: only Moreno voted among R -> majority yea
    assert moreno["with_party"] == 2
