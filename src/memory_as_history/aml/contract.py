"""AML Add/Search contract mirror.

Single source of truth for the request/response shapes fixed by the Agent
Memory Leaderboard API Guide (Cycle 2). Both the HTTP layer and the memory
service go through this module, so validation and response assembly cannot
drift between the two.
"""

from __future__ import annotations

from typing import Any


class ContractError(Exception):
    """Structured contract violation, rendered as ``{"detail": {"reason": ...}}``.

    ``status`` follows the official error table: 422 for format/validation
    failures, 400 for unparseable payloads, 500 for internal errors.
    """

    def __init__(self, reason: str, status: int = 422):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def error_detail(reason: str, status: int = 422) -> tuple[dict, int]:
    """Official business-error envelope: ``{"detail": {"reason": ...}}``."""
    return {"detail": {"reason": reason}}, status


def _require_object(payload: Any, what: str = "request body") -> dict:
    if not isinstance(payload, dict):
        raise ContractError(f"{what} must be a JSON object", 422)
    return payload


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            f"{field} is required and must be a non-empty string", 422
        )
    return value


def parse_add(payload: Any) -> dict:
    """Validate and normalize an Add request body."""
    body = _require_object(payload)
    request_id = _require_text(body.get("request_id"), "request_id")
    user_id = _require_text(body.get("user_id"), "user_id")
    session_id = _require_text(body.get("session_id"), "session_id")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ContractError(
            "messages is required and must be a non-empty array", 422
        )
    parsed_messages = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ContractError(f"messages[{i}] must be an object", 422)
        role = _require_text(msg.get("role"), f"messages[{i}].role")
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ContractError(
                f"messages[{i}].content is required and must be a non-empty string",
                422,
            )
        timestamp = msg.get("timestamp")
        if timestamp is not None and (
            not isinstance(timestamp, int) or isinstance(timestamp, bool)
            or timestamp < 0
        ):
            raise ContractError(
                f"messages[{i}].timestamp must be a non-negative integer "
                "(Unix milliseconds)",
                422,
            )
        parsed_messages.append(
            {"role": role, "content": content, "timestamp": timestamp}
        )
    return {
        "request_id": request_id,
        "user_id": user_id,
        "session_id": session_id,
        "messages": parsed_messages,
    }


def parse_search(payload: Any) -> dict:
    """Validate and normalize a Search request body."""
    body = _require_object(payload)
    query = _require_text(body.get("query"), "query")
    user_id = _require_text(body.get("user_id"), "user_id")
    top_k = body.get("top_k")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        raise ContractError(
            "top_k is required and must be a positive integer", 422
        )
    options = body.get("options")
    if options is not None:
        if not isinstance(options, list) or not all(
            isinstance(o, str) for o in options
        ):
            raise ContractError(
                "options must be an array of strings when present", 422
            )
    return {
        "query": query,
        "user_id": user_id,
        "top_k": top_k,
        "options": options,
    }


def add_ok(request_id: str, user_id: str, session_id: str) -> dict:
    """Success response for Add: ``success: true`` plus the three IDs echoed
    verbatim from the request."""
    return {
        "success": True,
        "request_id": request_id,
        "user_id": user_id,
        "session_id": session_id,
    }
