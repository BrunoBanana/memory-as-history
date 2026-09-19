# Knowledge-history preview validation

Local validation on 2026-09-20 (Asia/Shanghai), Python 3.12.13, macOS arm64,
SQLite 3.50.4. Runtime changes are in commit `9a239e0` and its predecessors;
subsequent release documentation changes do not change the tested modules.

| Check | Result |
| --- | --- |
| Full suite, MCP 2.2.0 | 419 passed |
| Full suite, MCP 1.2.0 (minimum) | 419 passed |
| Full suite, MCP 1.30.0 | 419 passed |
| Wheel installed into a fresh environment outside the checkout, MCP 2.2.0 | 419 passed |
| Existing reliability battery | 6/6 passed, including concurrent writers and 5,000 material records |
| Existing authored protocol benchmark | 120 episodes, 990/990 assertions; also passed from the installed wheel |
| Existing usefulness and poisoning checks | Passed; authored controls, not a security certification |
| Lexical development/history benchmarks | Completed without harness errors; no new retrieval improvement claimed |
| Existing two-process history MCP acceptance | 6/6 checks passed |
| New MCP tests | 9 passed, including a three-process revision/history/withdrawal lifecycle |
| Disposable example | Passed from checkout and installed wheel |
| Wheel/sdist contents, compilation, local Markdown links and whitespace | Passed |

The 70 new cases are 34 claim/evidence cases, 27 historical-view cases and 9 MCP
cases. They include parameterizations, so they are not 70 independent real-world
histories. The original 349 tests remain; one exact expected provenance dictionary
was extended to include the two deliberately additive explanation fields.

Review followed specification checks then transaction/API/compatibility checks.
Additional regressions caught and corrected two behavioral gaps during review:
historical queries could observe the gap between separately timestamped revision
events, and scoped discovery initially exposed stale narrative text. Evidence
updates now share their recording time with their review-invalidating event too.
Legacy source counts explicitly disclaim verified independence.

The first external wheel test run passed 416 tests and failed three evaluation
tests because the copied test harness omitted `evaluations/retrieval.json`.
Copying the unchanged original `evaluations/` directory fixed that test setup;
the complete rerun passed 419. No package code was changed to hide those failures.
The verified imported storage module was under the fresh environment's
`site-packages`, not a source checkout. The sdist includes the required fixtures.

## Reproduce

```bash
python -m pip install -e '.[test]'
python -m pytest tests/ -q
python reliability_test.py
python usefulness_test.py --json
python poisoning_test.py
python -m memory_as_history.benchmarks protocol --output protocol.json
python -m memory_as_history.benchmarks development --output development.json
python -m memory_as_history.benchmarks history --output history.json
python scripts/history_acceptance.py --output history-mcp.json
python examples/historical_claims.py
```

Repeat the suite in separate environments with `mcp==1.2.0`, `mcp==1.30.0` and
`mcp==2.2.0`. For wheel validation, install the built wheel with its `[test]` extra
into a fresh environment outside the checkout; copy `tests/`, `scripts/`,
`examples/`, `evaluations/` and root test scripts, but no `src/` or editable install.

Tested module SHA-256:

| Module | SHA-256 |
| --- | --- |
| `storage.py` | `37633e8a14e5fbdb9f1dea2f261b27cb27218f70249b02fd042ef57adf12c5ee` |
| `knowledge.py` | `69f3f62b6f9c833b61bcfc67b9938d212ab6adfdc539b7f1a7a13f29ad1afe0a` |
| `transactions.py` | `613f8c4e37b6989403f4d01adc8475cafc4293df19f93ab8c21627a1d8c54199` |

The locally checked wheel was `memory_as_history-1.3.0a1-py3-none-any.whl`, SHA-256
`d748687c82af06b2436f77dcf2634b9d2a1d68ed19de46d8c78bfe8475dad9eb`.
It was not uploaded to PyPI. Documentation-only rebuilds can change package hashes;
the source-module checks above identify the tested runtime.

These are local conformance results and self-review, not independent replication,
blind model evaluation or repeated unguided-client tests. The existing 5,000-row
check measures material storage, not claim/evidence history scaling. See the
[evaluation boundary](../evaluation.md) and [API limitations](../knowledge-history.md).
Remote cross-platform and Python 3.10–3.12 results are recorded in the PR/main CI.
