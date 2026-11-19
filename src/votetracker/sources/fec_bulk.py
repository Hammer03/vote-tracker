"""Campaign money from the Federal Election Commission's bulk data files.

Using bulk files (instead of thousands of API calls) keeps this free and
inside every rate limit. Files are pipe-delimited, one zip per two-year cycle:

* weball{YY}  Candidate summaries: money raised and spent, cash on hand at
              the start and end of the cycle, money from individuals, PACs,
              parties, and the candidate.
* pas2{YY}    Contributions from committees to candidates, and independent
              expenditures for or against them.
* cm{YY}      Committee master: committee names, types, connected organizations.

https://www.fec.gov/data/browse-data/?tab=bulk-data

FEC rules forbid using individual contributor information for commercial
purposes or to solicit donations. This module only reads committee-level
files; individual donors are never named.
"""

from __future__ import annotations

import io
import logging
import zipfile

log = logging.getLogger(__name__)

BULK = "https://www.fec.gov/files/bulk-downloads"
SOURCE = {"name": "Federal Election Commission (bulk data)", "url": "https://www.fec.gov/data/browse-data/?tab=bulk-data"}

# Column orders from the FEC data dictionaries (files have no header row).
COLUMNS = {
    "weball": [
        "CAND_ID", "CAND_NAME", "CAND_ICI", "PTY_CD", "CAND_PTY_AFFILIATION", "TTL_RECEIPTS",
        "TRANS_FROM_AUTH", "TTL_DISB", "TRANS_TO_AUTH", "COH_BOP", "COH_COP", "CAND_CONTRIB",
        "CAND_LOANS", "OTHER_LOANS", "CAND_LOAN_REPAY", "OTHER_LOAN_REPAY", "DEBTS_OWED_BY",
        "TTL_INDIV_CONTRIB", "CAND_OFFICE_ST", "CAND_OFFICE_DISTRICT", "SPEC_ELECTION",
        "PRIM_ELECTION", "RUN_ELECTION", "GEN_ELECTION", "GEN_ELECTION_PRECENT",
        "OTHER_POL_CMTE_CONTRIB", "POL_PTY_CONTRIB", "CVG_END_DT", "INDIV_REFUNDS", "CMTE_REFUNDS",
    ],
    "pas2": [
        "CMTE_ID", "AMNDT_IND", "RPT_TP", "TRANSACTION_PGI", "IMAGE_NUM", "TRANSACTION_TP",
        "ENTITY_TP", "NAME", "CITY", "STATE", "ZIP_CODE", "EMPLOYER", "OCCUPATION",
        "TRANSACTION_DT", "TRANSACTION_AMT", "OTHER_ID", "CAND_ID", "TRAN_ID", "FILE_NUM",
        "MEMO_CD", "MEMO_TEXT", "SUB_ID",
    ],
    "cm": [
        "CMTE_ID", "CMTE_NM", "TRES_NM", "CMTE_ST1", "CMTE_ST2", "CMTE_CITY", "CMTE_ST",
        "CMTE_ZIP", "CMTE_DSGN", "CMTE_TP", "CMTE_PTY_AFFILIATION", "CMTE_FILING_FREQ",
        "ORG_TP", "CONNECTED_ORG_NM", "CAND_ID",
    ],
}

# pas2 transaction types we use.
DIRECT = {"24K", "24Z"}          # contribution / in-kind contribution to the candidate's campaign
SUPPORT = {"24E", "24F"}         # independent expenditure / communication cost FOR the candidate
OPPOSE = {"24A", "24N"}          # independent expenditure / communication cost AGAINST the candidate
PARTY_COORDINATED = {"24C"}      # coordinated party expenditure

COMMITTEE_TYPES = {
    "O": "Super PAC",
    "V": "Hybrid PAC", "W": "Hybrid PAC",
    "N": "PAC", "Q": "PAC",
    "X": "Party committee", "Y": "Party committee", "Z": "Party committee",
    "H": "Candidate committee", "S": "Candidate committee", "P": "Candidate committee",
    "I": "Independent expenditure filer",
    "U": "Single-candidate independent expenditure committee",
    "C": "Communication cost filer",
    "E": "Electioneering communication filer",
    "D": "Delegate committee",
}
ORG_TYPES = {
    "C": "Corporation", "L": "Labor organization", "M": "Membership organization",
    "T": "Trade association", "V": "Cooperative", "W": "Corporation without capital stock",
}


def url(name: str, cycle: int) -> str:
    return f"{BULK}/{cycle}/{name}{str(cycle)[-2:]}.zip"


def money(value: str | None) -> float:
    try:
        return round(float(value), 2) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0


def fec_date(value: str | None) -> str | None:
    """'12/31/2024' or '12312024' -> '2024-12-31'."""
    v = (value or "").strip()
    if not v:
        return None
    if "/" in v:
        m, d, y = v.split("/")
        return f"{y}-{int(m):02d}-{int(d):02d}"
    if len(v) == 8 and v.isdigit():
        return f"{v[4:]}-{v[:2]}-{v[2:4]}"
    return None


def iter_rows(zip_bytes: bytes, name: str):
    cols = COLUMNS[name]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        member = next((n for n in zf.namelist() if n.lower().endswith(".txt")), zf.namelist()[0])
        with zf.open(member) as fh:
            for raw in io.TextIOWrapper(fh, encoding="latin-1", newline=""):
                parts = raw.rstrip("\r\n").split("|")
                if len(parts) < len(cols):
                    parts += [""] * (len(cols) - len(parts))
                yield dict(zip(cols, parts))


def download(http, name: str, cycle: int, etag: str | None):
    """Returns (bytes | None, new_etag). None means unchanged or unavailable."""
    headers = {"If-None-Match": etag} if etag else None
    resp = http.get(url(name, cycle), headers=headers)
    if resp.status == 304:
        return None, etag
    if not resp.ok:
        log.warning("FEC %s %s: HTTP %s", name, cycle, resp.status)
        return None, etag
    return resp.content, resp.headers.get("ETag") or resp.headers.get("etag")


# ------------------------------------------------------------------ parsers

def parse_weball(zip_bytes: bytes, wanted: set[str]) -> dict[str, dict]:
    out = {}
    for r in iter_rows(zip_bytes, "weball"):
        cid = r["CAND_ID"].strip()
        if cid not in wanted:
            continue
        out[cid] = {
            "receipts": money(r["TTL_RECEIPTS"]),
            "disbursements": money(r["TTL_DISB"]),
            "cash_start": money(r["COH_BOP"]),
            "cash_end": money(r["COH_COP"]),
            "from_individuals": money(r["TTL_INDIV_CONTRIB"]),
            "from_pacs_and_other_committees": money(r["OTHER_POL_CMTE_CONTRIB"]),
            "from_parties": money(r["POL_PTY_CONTRIB"]),
            "from_candidate": money(r["CAND_CONTRIB"]),
            "loans_from_candidate": money(r["CAND_LOANS"]),
            "other_loans": money(r["OTHER_LOANS"]),
            "debts": money(r["DEBTS_OWED_BY"]),
            "transfers_from_authorized": money(r["TRANS_FROM_AUTH"]),
            "coverage_end": fec_date(r["CVG_END_DT"]),
            "status": r["CAND_ICI"].strip() or None,  # I incumbent, C challenger, O open seat
        }
    return out


def parse_cm(zip_bytes: bytes) -> dict[str, dict]:
    out = {}
    for r in iter_rows(zip_bytes, "cm"):
        cid = r["CMTE_ID"].strip()
        if not cid:
            continue
        out[cid] = {
            "name": r["CMTE_NM"].strip(),
            "type": r["CMTE_TP"].strip() or None,
            "designation": r["CMTE_DSGN"].strip() or None,
            "party": r["CMTE_PTY_AFFILIATION"].strip() or None,
            "org_type": r["ORG_TP"].strip() or None,
            "connected_org": r["CONNECTED_ORG_NM"].strip() or None,
            "state": r["CMTE_ST"].strip() or None,
            "candidate": r["CAND_ID"].strip() or None,
        }
    return out


def parse_pas2(zip_bytes: bytes, wanted: set[str]) -> dict[str, dict[str, dict]]:
    """{candidate_id: {committee_id: {direct, support, oppose, party_coordinated, count}}}"""
    out: dict[str, dict[str, dict]] = {}
    for r in iter_rows(zip_bytes, "pas2"):
        cand = r["CAND_ID"].strip()
        if cand not in wanted or r["MEMO_CD"].strip().upper() == "X":
            continue
        tx = r["TRANSACTION_TP"].strip().upper()
        if tx in DIRECT:
            bucket = "direct"
        elif tx in SUPPORT:
            bucket = "support"
        elif tx in OPPOSE:
            bucket = "oppose"
        elif tx in PARTY_COORDINATED:
            bucket = "party_coordinated"
        else:
            continue
        giver = r["CMTE_ID"].strip()
        row = out.setdefault(cand, {}).setdefault(
            giver, {"direct": 0.0, "support": 0.0, "oppose": 0.0, "party_coordinated": 0.0, "count": 0})
        row[bucket] = round(row[bucket] + money(r["TRANSACTION_AMT"]), 2)
        row["count"] += 1
    return out


def committee_label(info: dict | None) -> dict:
    info = info or {}
    return {
        "name": info.get("name"),
        "kind": COMMITTEE_TYPES.get(info.get("type") or "", "Committee"),
        "connected_org": info.get("connected_org"),
        "org_type": ORG_TYPES.get(info.get("org_type") or ""),
        "party": info.get("party"),
    }
