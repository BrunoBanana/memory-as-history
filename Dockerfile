# memory-as-history — AML Add/Search service (text track)
#
# Build:  docker build -t memory-as-history-aml .
# Run:    docker run -d --name aml \
#           -p 8000:8000 \
#           -v aml-data:/data \
#           -e AML_API_KEY=<your-memory-system-key> \
#           memory-as-history-aml
#
# TLS: put this behind a reverse proxy (see docs/aml/ for a Caddy example).
# The service itself speaks plain HTTP on 8000.

FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md README.zh-CN.md LICENSE ./
COPY src ./src

# Default install is model-free (BM25). To enable hybrid search (local E5),
# uncomment the semantic extra; the pinned model downloads on first use and
# needs roughly 2-4 GB of RAM.
RUN pip install --no-cache-dir .

ENV AML_DATA_DIR=/data \
    AML_API_KEY="" \
    AML_SEARCH_MODE=lexical \
    PYTHONUNBUFFERED=1

VOLUME /data
EXPOSE 8000

CMD ["memory-as-history-aml", "--host", "0.0.0.0", "--port", "8000"]
