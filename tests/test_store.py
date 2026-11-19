import io

from botocore.response import StreamingBody
from botocore.stub import Stubber

from votetracker.publish import Publisher
from votetracker.store import LocalStore, R2Store


def test_r2_store_get_put_and_missing():
    store = R2Store("acct123", "AKID", "SECRET", "vote-tracker-data")
    assert store.client.meta.endpoint_url == "https://acct123.r2.cloudflarestorage.com"
    with Stubber(store.client) as stub:
        stub.add_response(
            "put_object",
            {},
            {"Bucket": "vote-tracker-data", "Key": "v1/meta.json", "Body": b"{}",
             "ContentType": "application/json; charset=utf-8", "CacheControl": "public, max-age=60"},
        )
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b'{"a":1}'), 7)},
            {"Bucket": "vote-tracker-data", "Key": "v1/meta.json"},
        )
        stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
        store.put("v1/meta.json", b"{}", "application/json; charset=utf-8", "public, max-age=60")
        assert store.get_json("v1/meta.json") == {"a": 1}
        assert store.get("v1/missing.json") is None


def test_publisher_skips_unchanged(tmp_path):
    store = LocalStore(tmp_path)
    pub = Publisher(store)
    assert pub.publish_json("v1/a.json", {"x": 1}) is True
    pub.save_manifest()
    pub2 = Publisher(store)
    assert pub2.publish_json("v1/a.json", {"x": 1}) is False
    assert pub2.publish_json("v1/a.json", {"x": 2}) is True


def test_gz_bundles_roundtrip(tmp_path):
    store = LocalStore(tmp_path)
    rows = [{"id": "a", "n": 1}, {"id": "b", "n": 2}]
    store.put_jsonl_gz("state/x.jsonl.gz", rows)
    assert store.get_jsonl_gz("state/x.jsonl.gz") == rows
    assert store.get_jsonl_gz("state/missing.jsonl.gz") == []


def test_local_store_refuses_escape(tmp_path):
    store = LocalStore(tmp_path / "root")
    try:
        store.put("../outside.json", b"x", "text/plain")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
