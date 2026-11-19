"""Push alerts for followed officials, through Firebase Cloud Messaging topics.

How following works, with no user accounts and no list of who follows whom:

* The app keeps the user's follows on the phone.
* For each followed official it subscribes the phone to an FCM topic:
    federal member:   m-{bioguide}          every new roll call
                      m-{bioguide}-major    final votes only (passage, confirmation, veto override, ...)
    state legislator: s-{openstates id}     every new roll call
                      s-{openstates id}-major
* After each update run, this module sends one message per official who
  has new votes. Firebase delivers it to every phone subscribed to that
  topic (iPhone through APNs, Android directly). FCM is free.

Wording is plain fact: who, how they voted, on what, and the result.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import date, timedelta

log = logging.getLogger(__name__)

FCM_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
MAX_MESSAGES_PER_RUN = 1500
RECENT_DAYS = 3

POSITION_WORDS = {
    "yea": "voted Yes",
    "nay": "voted No",
    "present": "voted Present",
    "not_voting": "did not vote",
    "excused": "was excused",
    "other": "voted",
}

_MAJOR_RE = re.compile(
    r"passage|\bpass\b|suspend the rules and (pass|agree)|concur|conference report|override|"
    r"nomination|confirmation|ratif|guilty|impeach|final reading|third reading",
    re.I,
)


def topic(level: str, member_id: str, major: bool = False) -> str:
    prefix = "m" if level == "federal" else "s"
    return f"{prefix}-{member_id}" + ("-major" if major else "")


def is_major(vote: dict) -> bool:
    """Votes that decide the fate of a bill or nomination."""
    types = set(vote.get("motion_types") or [])
    if types & {"passage", "veto-override"}:
        return True
    if "providing for consideration" in (vote.get("description") or "").lower():
        return False  # procedural rule for debate
    text = " ".join(x for x in (vote.get("question"), vote.get("question_full")) if x)
    return bool(_MAJOR_RE.search(text))


def _label(vote: dict) -> str:
    if vote.get("bill"):
        return vote["bill"]["label"]
    if vote.get("nomination"):
        return vote["nomination"]["id"]
    return vote.get("question") or "a roll call"


def compose(name: str, items: list[tuple[dict, dict]]) -> tuple[str, str]:
    """items: [(vote, position)], newest first."""
    vote, pos = items[0]
    words = POSITION_WORDS.get(pos["vote"], "voted")
    if pos["vote"] == "other" and pos.get("raw"):
        words = f"voted {pos['raw']}"
    question = vote.get("question") or ""
    result = vote.get("result") or ""
    if len(items) == 1:
        title = f"{name} {words}"
        body = f"{_label(vote)}: {question}" + (f" — {result}" if result else "")
    else:
        title = f"{name}: {len(items)} new votes"
        body = f"Latest: {words} on {_label(vote)} ({question})" + (f" — {result}" if result else "")
    return title[:120], body[:240]


def plan(new_votes: list[dict], level: str, id_key: str, name_of, profile_path, today: date) -> list[dict]:
    """Build FCM messages for votes that are new this run and recent."""
    cutoff = (today - timedelta(days=RECENT_DAYS)).isoformat()
    per_member: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for v in new_votes:
        if (v.get("date") or "") < cutoff:
            continue  # never alert on old votes found while catching up
        for p in v["positions"]:
            if p.get(id_key):
                per_member[p[id_key]].append((v, p))

    messages = []
    for member_id, items in per_member.items():
        items.sort(key=lambda vp: (vp[0].get("datetime") or vp[0].get("date") or "", vp[0]["id"]), reverse=True)
        name = name_of(member_id) or items[0][1].get("name") or "Your official"
        groups = [(False, items), (True, [vp for vp in items if is_major(vp[0])])]
        for major, group in groups:
            if not group:
                continue
            title, body = compose(name, group)
            messages.append(
                {
                    "topic": topic(level, member_id, major),
                    "notification": {"title": title, "body": body},
                    "data": {
                        "type": "votes",
                        "level": level,
                        "member": member_id,
                        "path": profile_path(member_id),
                        "votes": ",".join(vp[0]["id"] for vp in group[:10]),
                    },
                    "android": {"collapse_key": topic(level, member_id, major)},
                    "apns": {"headers": {"apns-collapse-id": topic(level, member_id, major)[:64]}},
                }
            )
    return messages


class FcmSender:
    def __init__(self, service_account_json: str):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        info = json.loads(service_account_json)
        self.project = info["project_id"]
        creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        self.session = AuthorizedSession(creds)

    def send(self, message: dict) -> bool:
        r = self.session.post(FCM_URL.format(project=self.project), json={"message": message}, timeout=30)
        if r.status_code != 200:
            log.warning("FCM %s: HTTP %s %s", message.get("topic"), r.status_code, r.text[:200])
        return r.status_code == 200


class RecordingSender:
    """Used when Firebase isn't set up yet, and in tests: records instead of sending."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, message: dict) -> bool:
        self.sent.append(message)
        return True


def deliver(messages: list[dict], sender) -> dict:
    sent = failed = 0
    for m in messages[:MAX_MESSAGES_PER_RUN]:
        if sender.send(m):
            sent += 1
        else:
            failed += 1
    skipped = max(0, len(messages) - MAX_MESSAGES_PER_RUN)
    return {"planned": len(messages), "sent": sent, "failed": failed, "skipped_over_cap": skipped}
