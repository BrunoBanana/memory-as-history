"""AML (Agent Memory Leaderboard) adapter for memory-as-history.

Exposes the project's memory storage and BM25 retrieval through the
competition's fixed Add/Search HTTP contract. Text-track only; see
docs/plans/2026-09-30-aml-adapter.md for the design and boundaries.
"""

from .contract import ContractError, add_ok, error_detail, parse_add, parse_search
from .memory_service import DEFAULT_DATA_DIR, MemoryService
from .server import AMLHandler, build_server, make_handler, serve

__all__ = [
    "AMLHandler",
    "ContractError",
    "DEFAULT_DATA_DIR",
    "MemoryService",
    "add_ok",
    "build_server",
    "error_detail",
    "make_handler",
    "parse_add",
    "parse_search",
    "serve",
]
