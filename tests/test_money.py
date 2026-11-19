import io
import json
import zipfile
from datetime import date

from conftest import routes

from votetracker.http import FakeHttpClient
from votetracker.money import cycle_before, current_cycle
from votetracker.pipeline import Pipeline
from votetracker.sources import fec_bulk
from votetracker.store import LocalStore

TODAY = date(2026, 11, 18)
JORDAN, KAPTUR, WELCH = "H6OH04082", "H2OH09031", "S2VT00235"


def make_zip(kind: str, cycle: int, rows: list[dict]) -> bytes:
    cols = fec_bulk.COLUMNS[kind]
    lines = ["|".join(str(r.get(c, "")) for c in cols) for r in rows]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{kind}{str(cycle)[-2:]}.txt", "\n".join(lines) + "\n")
    return buf.getvalue()


def weball(cand, receipts, disb, bop, cop, end, indiv=0, pacs=0, party=0, cand_contrib=0):
    return {"CAND_ID": cand, "CAND_ICI": "I", "TTL_RECEIPTS": receipts, "TTL_DISB": disb, "COH_BOP": bop,
            "COH_COP": cop, "TTL_INDIV_CONTRIB": indiv, "OTHER_POL_CMTE_CONTRIB": pacs, "POL_PTY_CONTRIB": party,
            "CAND_CONTRIB": cand_contrib, "CVG_END_DT": end}


def pas2(giver, cand, tx, amount, memo=""):
    return {"CMTE_ID": giver, "CAND_ID": cand, "TRANSACTION_TP": tx, "TRANSACTION_AMT": amount, "MEMO_CD": memo}


def fec_routes():
    r = routes()
    r[fec_bulk.url("weball", 2024)] = (200, make_zip("weball", 2024, [
        weball(JORDAN, 1000000, 900000, 150000, 250000, "12/31/2024"),
        weball(KAPTUR, 800000, 700000, 50000, 150000, "12/31/2024"),
    ]))
    r[fec_bulk.url("weball", 2026)] = (200, make_zip("weball", 2026, [
        weball(JORDAN, 600000, 500000, 250000, 350000, "09/30/2026", indiv=400000, pacs=150000, party=5000),
        weball(KAPTUR, 300000, 350000, 150000, 100000, "09/30/2026", indiv=200000, pacs=90000),
        weball("H9XX99999", 1, 1, 1, 1, "09/30/2026"),  # not a sitting member: ignored
    ]))
    cm = [
        {"CMTE_ID": "C00000001", "CMTE_NM": "EXAMPLE ENGINEERS PAC", "CMTE_TP": "Q", "ORG_TP": "C",
         "CONNECTED_ORG_NM": "EXAMPLE ENGINEERING INC"},
        {"CMTE_ID": "C00000002", "CMTE_NM": "EXAMPLE SUPER PAC", "CMTE_TP": "O"},
        {"CMTE_ID": "C00000003", "CMTE_NM": "EXAMPLE WORKERS UNION PAC", "CMTE_TP": "Q", "ORG_TP": "L"},
    ]
    r[fec_bulk.url("cm", 2026)] = (200, make_zip("cm", 2026, cm))
    r[fec_bulk.url("pas2", 2026)] = (200, make_zip("pas2", 2026, [
        pas2("C00000001", JORDAN, "24K", 5000),
        pas2("C00000001", JORDAN, "24K", 2500),
        pas2("C00000001", JORDAN, "24K", 9999, memo="X"),  # memo entry: not counted
        pas2("C00000003", KAPTUR, "24K", 5000),
        pas2("C00000002", JORDAN, "24E", 100000),
        pas2("C00000002", KAPTUR, "24A", 40000),
        pas2("C00000001", "H9XX99999", "24K", 1000),  # not a sitting member
    ]))
    return r


def load(root, key):
    return json.loads((root / key).read_text())


def test_cycles():
    assert current_cycle(date(2025, 3, 1)) == 2026
    assert current_cycle(date(2026, 3, 1)) == 2026
    assert cycle_before("2025-01-03") == 2024
    assert cycle_before("2023-01-03") == 2022
    assert cycle_before("2026-05-01") == 2026  # special election in an even year


def test_fec_date():
    assert fec_bulk.fec_date("09/30/2026") == "2026-09-30"
    assert fec_bulk.fec_date("09302026") == "2026-09-30"
    assert fec_bulk.fec_date("") is None


def test_money_end_to_end(tmp_path, cfg):
    store = LocalStore(tmp_path)
    http = FakeHttpClient(fec_routes())
    Pipeline(cfg, store, http, today=TODAY).run(states=False, bills=False, send_alerts=False)

    doc = load(tmp_path, "v1/money/J000289.json")
    assert doc["fec_candidate_id"] == JORDAN
    assert doc["cash"]["start_of_term"] == {"amount": 250000.0, "as_of": "2024-12-31"}
    assert doc["cash"]["latest"] == {"amount": 350000.0, "as_of": "2026-09-30"}
    assert doc["cash"]["change"] == 100000.0
    assert doc["this_term"]["receipts"] == 600000.0
    assert doc["this_term"]["from_pacs_and_other_committees"] == 150000.0

    top = doc["committee_contributions"]["this_term"][0]
    assert top["committee_id"] == "C00000001"
    assert top["direct"] == 7500.0  # memo entry excluded
    assert top["kind"] == "PAC"
    assert top["connected_org"] == "EXAMPLE ENGINEERING INC"
    assert top["org_type"] == "Corporation"

    outside = doc["outside_spending"]["this_term"][0]
    assert outside["name"] == "EXAMPLE SUPER PAC"
    assert outside["kind"] == "Super PAC"
    assert outside["support"] == 100000.0 and outside["oppose"] == 0.0
    assert any("individual" in n.lower() for n in doc["notes"])

    kaptur = load(tmp_path, "v1/money/K000009.json")
    assert kaptur["cash"]["change"] == -50000.0
    assert kaptur["outside_spending"]["this_term"][0]["oppose"] == 40000.0

    # One committee's giving across all members, for donor search
    sp = load(tmp_path, "v1/money/committees/C00000002.json")
    assert sp["totals_to_current_members"] == {"direct": 0.0, "support": 100000.0, "oppose": 40000.0,
                                               "party_coordinated": 0.0}
    assert {m["bioguide"] for m in sp["members"]} == {"J000289", "K000009"}
    index = load(tmp_path, "v1/money/committees/index.json")["committees"]
    assert index[0]["committee_id"] == "C00000002"

    # Member profile links to the money file
    prof = load(tmp_path, "v1/members/J000289.json")
    assert prof["money"]["path"] == "money/J000289.json"
    assert prof["money"]["raised_this_term"] == 600000.0

    # Second run the same day: no FEC downloads
    http2 = FakeHttpClient(fec_routes())
    Pipeline(cfg, store, http2, today=TODAY).run(states=False, bills=False, send_alerts=False)
    assert not any("fec.gov" in c for c in http2.calls)
    assert load(tmp_path, "v1/members/J000289.json")["money"]["path"] == "money/J000289.json"


def test_unchanged_bulk_file_is_skipped():
    class Http:
        def get(self, url, params=None, headers=None):
            from votetracker.http import Response
            assert headers == {"If-None-Match": '"abc"'}
            return Response(url=url, status=304, content=b"", headers={})
    body, etag = fec_bulk.download(Http(), "pas2", 2026, '"abc"')
    assert body is None and etag == '"abc"'
