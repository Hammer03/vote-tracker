from votetracker.util import (
    congress_for_year,
    html_to_text,
    ordinal,
    parse_bill_ref,
    party_code,
    session_for_year,
    years_for_congress,
)


def test_congress_numbers():
    assert congress_for_year(2025) == 119
    assert congress_for_year(2026) == 119
    assert congress_for_year(2027) == 120
    assert years_for_congress(119) == (2025, 2026)
    assert session_for_year(119, 2025) == 1
    assert session_for_year(119, 2026) == 2


def test_ordinal():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 119, 120, 121, 122, 123)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd",
        "119th", "120th", "121st", "122nd", "123rd",
    ]


def test_parse_bill_ref_house_and_senate_styles():
    assert parse_bill_ref("H R 1228", 119)["id"] == "119-hr-1228"
    assert parse_bill_ref("H RES 888", 119)["label"] == "H.Res. 888"
    assert parse_bill_ref("H J RES 131", 119)["type"] == "hjres"
    assert parse_bill_ref("S J RES 80", 119)["type"] == "sjres"
    assert parse_bill_ref("H CON RES 58", 119)["type"] == "hconres"
    assert parse_bill_ref("S.Con.Res. 33", 119)["type"] == "sconres"
    ref = parse_bill_ref("S 2403", 119)
    assert ref["url"] == "https://www.congress.gov/bill/119th-congress/senate-bill/2403"


def test_parse_bill_ref_non_bills():
    for raw in ("QUORUM", "MOTION", "ADJOURN", "", None, "PN 12"):
        assert parse_bill_ref(raw, 119) is None


def test_party_code():
    assert party_code("Democrat") == "D"
    assert party_code("Republican") == "R"
    assert party_code("Independent") == "I"
    assert party_code("D") == "D"
    assert party_code(None) is None


def test_html_to_text():
    out = html_to_text("<p><strong>Act</strong></p><p>Does &amp; things.</p><ul><li>One</li><li>Two</li></ul>")
    assert out == "Act\nDoes & things.\n- One\n- Two"
