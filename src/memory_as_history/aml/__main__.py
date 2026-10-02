"""CLI entry point: ``python -m memory_as_history.aml``.

Runs the AML Add/Search HTTP service. Configuration mirrors the official
deployment contract:

- ``--data-dir`` / ``AML_DATA_DIR``: storage root (per-user SQLite files).
- ``--api-key`` / ``AML_API_KEY``: Memory System Key; empty = no auth
  (allowed for public smoke only).
- ``--search-mode`` / ``AML_SEARCH_MODE``: lexical (default, BM25) or
  hybrid (local E5; downloads the pinned model on first use).
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="memory-as-history-aml",
        description="Agent Memory Leaderboard Add/Search service "
        "(text track, open-source division).",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=os.environ.get("AML_DATA_DIR"))
    parser.add_argument("--api-key", default=os.environ.get("AML_API_KEY"))
    parser.add_argument(
        "--search-mode",
        default=os.environ.get("AML_SEARCH_MODE", "lexical"),
        choices=("lexical", "hybrid", "semantic"),
    )
    parser.add_argument(
        "--semantic-min",
        type=float,
        default=float(os.environ.get("AML_SEMANTIC_MIN", "0.85")),
        help="minimum cosine similarity for a semantic-only evidence hit",
    )
    parser.add_argument(
        "--use-temporal",
        type=lambda v: v.lower() in ("1", "true", "yes", "on"),
        default=os.environ.get("AML_USE_TEMPORAL", "true").lower()
        in ("1", "true", "yes", "on"),
        help="rerank evidence by explicit temporal clues (memory-as-history)",
    )
    args = parser.parse_args()

    from .server import serve

    serve(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        api_key=args.api_key,
        search_mode=args.search_mode,
        semantic_min=args.semantic_min,
        use_temporal=args.use_temporal,
    )


if __name__ == "__main__":
    main()
