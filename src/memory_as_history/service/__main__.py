"""CLI entry point: ``python -m memory_as_history.service``.

Runs the standardized memory Add/Search HTTP service. Configuration
mirrors a generic deployment contract:

- ``--data-dir`` / ``MEMORY_DATA_DIR``: storage root (per-user SQLite
  files). The legacy ``AML_DATA_DIR`` name is also accepted.
- ``--api-key`` / ``MEMORY_API_KEY``: Memory System Key; empty = no auth
  (allowed for public smoke only). Legacy ``AML_API_KEY`` is accepted.
- ``--search-mode`` / ``MEMORY_SEARCH_MODE``: lexical (default, BM25) or
  hybrid (local E5; downloads the pinned model on first use).
"""

from __future__ import annotations

import argparse
import os


def _env(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v is not None:
            return v
    return default


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="memory-as-history-server",
        description="memory-as-history Add/Search HTTP service "
        "(standardized memory write/read protocol).",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=_env("MEMORY_DATA_DIR", "AML_DATA_DIR"))
    parser.add_argument("--api-key", default=_env("MEMORY_API_KEY", "AML_API_KEY"))
    parser.add_argument(
        "--search-mode",
        default=_env("MEMORY_SEARCH_MODE", "AML_SEARCH_MODE", default="lexical"),
        choices=("lexical", "hybrid", "semantic"),
    )
    parser.add_argument(
        "--semantic-min",
        type=float,
        default=float(_env("MEMORY_SEMANTIC_MIN", "AML_SEMANTIC_MIN", default="0.85")),
        help="minimum cosine similarity for a semantic-only evidence hit",
    )
    parser.add_argument(
        "--use-temporal",
        type=lambda v: v.lower() in ("1", "true", "yes", "on"),
        default=_env("MEMORY_USE_TEMPORAL", "AML_USE_TEMPORAL", default="true")
        .lower()
        in ("1", "true", "yes", "on"),
        help="rerank evidence by explicit temporal clues (memory-as-history)",
    )
    args = parser.parse_args()

    if args.search_mode in ("hybrid", "semantic"):
        # Pre-warm the shared E5 model inside THIS process so the first
        # /search never pays the ~13s cold-start cost. The class-level
        # singleton is process-scoped, so a separate warm-up process would be
        # useless. Warm-up failure must never prevent serving.
        try:
            from memory_as_history.semantic import LocalE5

            LocalE5(allow_download=False).similarities(
                "query: warm", ["passage: warm"]
            )
        except Exception:
            pass

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
