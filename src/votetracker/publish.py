"""Publishes public JSON files, uploading only files whose content changed.

A manifest (``state/manifest.json``) remembers the SHA-256 of every
published file. Unchanged files are skipped, which keeps each hourly run to a
handful of uploads and well inside free storage-operation limits.
"""

from __future__ import annotations

import hashlib
import logging
import re

from votetracker.store import Store, dumps

log = logging.getLogger(__name__)

MANIFEST_KEY = "state/manifest.json"

# (pattern, Cache-Control). First match wins. Short caches for files that
# change often; longer for individual votes, which rarely change.
CACHE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^v1/meta\.json$"), "public, max-age=60"),
    (re.compile(r"^v1/votes/latest\.json$"), "public, max-age=120"),
    (re.compile(r"^v1/votes/(house|senate)/\d+-\d+/\d+\.json$"), "public, max-age=3600"),
    (re.compile(r"^v1/bills/"), "public, max-age=3600"),
    (re.compile(r"^v1/"), "public, max-age=300"),
]


def cache_control_for(key: str) -> str:
    for pattern, value in CACHE_RULES:
        if pattern.search(key):
            return value
    return "public, max-age=300"


class Publisher:
    def __init__(self, store: Store, dry_run: bool = False):
        self.store = store
        self.dry_run = dry_run
        self.manifest: dict[str, str] = store.get_json(MANIFEST_KEY, {}) or {}
        self.seen: set[str] = set()
        self.uploaded = 0
        self.skipped = 0

    def publish_json(self, key: str, obj) -> bool:
        """Publish one public JSON file. Returns True if it was uploaded."""
        if not key.startswith("v1/"):
            raise ValueError("public files must live under v1/")
        data = dumps(obj)
        digest = hashlib.sha256(data).hexdigest()
        self.seen.add(key)
        if self.manifest.get(key) == digest:
            self.skipped += 1
            return False
        if not self.dry_run:
            self.store.put(key, data, "application/json; charset=utf-8", cache_control_for(key))
        self.manifest[key] = digest
        self.uploaded += 1
        return True

    def save_manifest(self) -> None:
        if not self.dry_run:
            self.store.put_json(MANIFEST_KEY, self.manifest, "no-store")
        log.info("published: %d uploaded, %d unchanged", self.uploaded, self.skipped)
