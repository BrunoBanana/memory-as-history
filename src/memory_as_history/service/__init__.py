"""Standardized memory Add/Search service for memory-as-history.

Exposes the project's memory storage and BM25 retrieval through a
fixed Add/Search HTTP contract (write transcripts, retrieve evidence),
the same shape used by memory evaluation harnesses. Design notes live
in ``docs/evaluation.md`` and the plan history.
"""

from .contract import ContractError, add_ok, error_detail, parse_add, parse_search
from .memory_service import DEFAULT_DATA_DIR, MemoryService
from .server import MemoryHandler, build_server, make_handler, serve

__all__ = [
    "MemoryHandler",
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
