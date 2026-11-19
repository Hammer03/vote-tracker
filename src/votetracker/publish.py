"""Publishes public JSON files, uploading only files whose content changed.

A manifest (``state/manifest.json``) remembers the SHA-256 of every
published file. Unchanged files are skipped, which keeps each hourly run to a
handful of uploads and well inside free storage-operation limits.

Uploads run in parallel. A file that still fails after retries is left out
of the manifest, so the next run uploads it again; one bad upload never
stops the whole run.
"""

from __future__ import annotations

import hashlib
import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor

from votetracker.store import Store, dumps

log = logging.getLogger(__name__)

MANIFEST_KEY = "state/manifest.json"
UPLOAD_WORKERS = 8

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
    def __init__(self, store: Store, dry_run: bool = False, workers: int = UPLOAD_WORKERS):
        self.store = store
        self.dry_run = dry_run
        self.manifest: dict[str, str] = store.get_json(MANIFEST_KEY, {}) or {}
        self.seen: set[str] = set()
        self.uploaded = 0
        self.skipped = 0
        self.failed: list[str] = []
        self._pending: dict[str, tuple[str, Future]] = {}
        self._pool = ThreadPoolExecutor(max_workers=workers) if not dry_run else None

    def publish_json(self, key: str, obj) -> bool:
        """Queue one public JSON file. Returns True if it needs uploading."""
        if not key.startswith("v1/"):
            raise ValueError("public files must live under v1/")
        data = dumps(obj)
        digest = hashlib.sha256(data).hexdigest()
        self.seen.add(key)
        if self.manifest.get(key) == digest:
            self.skipped += 1
            return False
        if self.dry_run:
            self.manifest[key] = digest
            self.uploaded += 1
            return True
        previous = self._pending.get(key)
        if previous:
            previous[1].result()  # same file published twice in one run: keep order
        fut = self._pool.submit(self.store.put, key, data, "application/json; charset=utf-8", cache_control_for(key))
        self._pending[key] = (digest, fut)
        if len(self._pending) >= 2000:
            self._collect()
        return True

    def _collect(self) -> None:
        for key, (digest, fut) in list(self._pending.items()):
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 - any storage error
                log.warning("upload failed, will retry next run: %s (%s)", key, exc)
                self.failed.append(key)
                self.manifest.pop(key, None)
                continue
            self.manifest[key] = digest
            self.uploaded += 1
        self._pending.clear()

    def save_manifest(self) -> None:
        self._collect()
        if self._pool:
            self._pool.shutdown(wait=True)
            self._pool = None
        if not self.dry_run:
            self.store.put_json(MANIFEST_KEY, self.manifest, "no-store")
        log.info("published: %d uploaded, %d unchanged, %d failed", self.uploaded, self.skipped, len(self.failed))
