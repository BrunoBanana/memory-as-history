"""HTTP-layer contract tests for the AML Add/Search service.

Boots the real stdlib ThreadingHTTPServer and checks routing, authentication,
health checks, and the exact wire shapes the official smoke validates.
"""

from __future__ import annotations

import json
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from memory_as_history.aml import build_server

PORT = 8765


@pytest.fixture()
def http_server():
    data_dir = Path(tempfile.mkdtemp(prefix="aml-http-"))
    httpd = build_server(host="127.0.0.1", port=PORT, data_dir=data_dir)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{PORT}"
    httpd.shutdown()
    httpd.server_close()


def _post(url: str, body: dict, token: str | None = None):
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_health_returns_2xx(http_server):
    with urllib.request.urlopen(f"{http_server}/health", timeout=10) as resp:
        assert resp.status == 200
        assert json.loads(resp.read()) == {"status": "ok"}


def test_add_then_search_over_http(http_server):
    status, body = _post(
        f"{http_server}/add",
        {
            "request_id": "run:conv:chunk0",
            "user_id": "u1",
            "session_id": "s1",
            "messages": [{"role": "user", "content": "北京是中国的首都"}],
        },
    )
    assert status == 200
    assert body == {
        "success": True,
        "request_id": "run:conv:chunk0",
        "user_id": "u1",
        "session_id": "s1",
    }
    status, body = _post(
        f"{http_server}/search",
        {"query": "中国的首都", "user_id": "u1", "top_k": 5},
    )
    assert status == 200
    assert isinstance(body, dict) and isinstance(body["data"], list)
    assert body["data"][0]["content"] == "北京是中国的首都"


def test_unknown_route_is_404(http_server):
    status, body = _post(f"{http_server}/nope", {"a": 1})
    assert status == 404
    assert "detail" in body


def test_malformed_json_is_400(http_server):
    req = urllib.request.Request(
        f"{http_server}/add",
        data=b"not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        body = json.loads(exc.read().decode("utf-8"))
        assert "detail" in body


def test_validation_error_is_422(http_server):
    status, body = _post(f"{http_server}/search", {"query": "q", "user_id": "u1"})
    assert status == 422
    assert body["detail"]["reason"]


def test_api_key_required_when_set(tmp_path):
    httpd = build_server(
        host="127.0.0.1", port=PORT + 1, data_dir=tmp_path, api_key="secret"
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{PORT + 1}"
    try:
        status, _ = _post(
            f"{base}/add",
            {
                "request_id": "r",
                "user_id": "u1",
                "session_id": "s1",
                "messages": [{"role": "user", "content": "x"}],
            },
        )
        assert status == 401
        status, _ = _post(
            f"{base}/add",
            {
                "request_id": "r",
                "user_id": "u1",
                "session_id": "s1",
                "messages": [{"role": "user", "content": "x"}],
            },
            token="secret",
        )
        assert status == 200
        # health stays unauthenticated
        with urllib.request.urlopen(f"{base}/health", timeout=10) as resp:
            assert resp.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
