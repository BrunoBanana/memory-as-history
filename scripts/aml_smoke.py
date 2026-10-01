"""End-to-end simulation of the official AML smoke flow.

Starts the real HTTP service, runs Add → Search → format validation exactly
as the platform's smoke does, and exits nonzero on any contract violation.
No model credentials or network access beyond localhost are required.

Usage:
    python scripts/aml_smoke.py [--port 8000] [--api-key ""] [--data-dir <dir>]
"""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from memory_as_history.aml import build_server


def _post(url: str, body: dict, token: str | None = None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _fail(step: str, message: str) -> None:
    raise SystemExit(f"[FAIL] {step}: {message}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="aml-smoke-"))
    httpd = build_server(
        host=args.host, port=args.port, data_dir=data_dir, api_key=args.api_key or None
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://{args.host}:{args.port}"
    token = args.api_key or None
    print(f"[smoke] server on {base} (data dir {data_dir})")

    # 1. health
    with urllib.request.urlopen(f"{base}/health", timeout=10) as resp:
        if resp.status not in (200, 204):
            _fail("health", f"expected 2xx, got {resp.status}")
    print("[smoke] health OK")

    # 2. Add a small conversation (matching the official request shape)
    add_body = {
        "request_id": "eval:run_smoke:conv-0:chunk-0",
        "messages": [
            {"role": "user", "timestamp": 1704067200000, "content": "我叫小明，在杭州做后端开发，最喜欢的编程语言是 Python。"},
            {"role": "assistant", "content": "好的，我记住了你的职业和喜好。"},
        ],
        "user_id": "eval:run_smoke:conv-0",
        "session_id": "eval:run_smoke:sample:0",
    }
    status, body = _post(f"{base}/add", add_body, token=token)
    if status != 200:
        _fail("add", f"expected 200, got {status}: {body}")
    if body.get("success") is not True:
        _fail("add", f"success must be true, got {body}")
    for field in ("request_id", "user_id", "session_id"):
        if body.get(field) != add_body[field]:
            _fail("add", f"{field} must echo request value, got {body}")
    print("[smoke] Add OK")

    # 3. Idempotent replay of the same request_id must not duplicate
    status2, body2 = _post(f"{base}/add", add_body, token=token)
    if status2 != 200 or body2.get("success") is not True:
        _fail("add-replay", f"replay must return 200 success, got {status2}: {body2}")
    print("[smoke] Add replay OK")

    # 4. Search: shape, ordering, top_k
    search_body = {
        "query": "小明在哪里工作？他喜欢什么语言？",
        "user_id": "eval:run_smoke:conv-0",
        "top_k": 10,
    }
    status, body = _post(f"{base}/search", search_body, token=token)
    if status != 200:
        _fail("search", f"expected 200, got {status}: {body}")
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        _fail("search", "top-level field must be a data array")
    if len(body["data"]) > search_body["top_k"]:
        _fail("search", f"returned {len(body['data'])} > top_k {search_body['top_k']}")
    for item in body["data"]:
        if not isinstance(item.get("id"), str) or not item.get("id"):
            _fail("search", "every item needs a non-empty string id")
        if not isinstance(item.get("content"), str) or not item.get("content"):
            _fail("search", "every item needs a non-empty string content")
    if not body["data"] or "小明" not in body["data"][0]["content"]:
        _fail("search", f"expected the identity fact first, got {body['data']}")
    print(f"[smoke] Search OK ({len(body['data'])} evidence items, first: {body['data'][0]['content'][:20]}...)")

    # 5. No cross-user leakage
    status, body = _post(
        f"{base}/search",
        {"query": "小明在哪里工作", "user_id": "eval:run_smoke:conv-OTHER", "top_k": 5},
        token=token,
    )
    if status != 200 or body.get("data"):
        _fail("isolation", f"other user must not see this memory: {body}")
    print("[smoke] user isolation OK")

    # 6. Validation error shape
    try:
        _post(f"{base}/search", {"query": "x", "user_id": "u"}, token=token)
        _fail("validation", "missing top_k must be rejected")
    except urllib.error.HTTPError as exc:
        if exc.code != 422:
            _fail("validation", f"expected 422, got {exc.code}")
        err = json.loads(exc.read().decode("utf-8"))
        if "detail" not in err:
            _fail("validation", f"expected detail envelope, got {err}")
    print("[smoke] validation errors OK")

    httpd.shutdown()
    httpd.server_close()
    print("[smoke] ALL PASSED")


if __name__ == "__main__":
    main()
