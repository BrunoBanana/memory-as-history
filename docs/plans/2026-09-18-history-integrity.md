# History Integrity and Evaluation Implementation Plan

**Goal:** Complete the remaining 1.2 reliability roadmap: accountable narrative review, source guards on prioritized recall, correct and measurable lexical retrieval, and executable evaluation.

**Architecture:** Preserve historical text and version chains. Add narrative review metadata; derive live dependency issues; suppress stale narrative text from default recall while exposing a review notice. Apply the existing independent-source rule to canon and sensitive synthesis. Keep write guards, invalidation and audit inside the existing SQLite transaction. Add additive migrations with serialized initialization.

**Stack:** Python 3.10+, SQLite, MCP 1.x/2.x, pytest/anyio; no new runtime dependencies. Execute in one flow with specification review before quality review.

## Design decisions

The user authorized completing this direction autonomously with multiple rounds of testing. We choose preserved history plus explicit review over automatic rewriting (which invents a new account) or warnings alone (which keep stale text in default context).

| Area | Contract |
| --- | --- |
| Narrative dependencies | New nonempty links must be existing, active memory IDs; reject malformed inputs and normalize duplicates. Unlinked nonsensitive narratives remain compatible, explicitly labeled unlinked rather than verified. |
| Invalidation | Forgetting/restoring a referenced memory, overdue interpretation refresh, and retroactive unsupported sensitivity mark linked narratives for explicit review. Source review/corroboration preserves failures before repairing evidence. Content and supersession links never change. Review requirement persists after the source becomes usable again. |
| Live issues | Missing/forgotten refs, overdue interpretations, malformed legacy links, and unsupported sensitive sources make a narrative stale. Recognized sensitive narrative text or an explicit sensitive flag requires linked independent evidence. |
| Recall vs inspection | Default recall omits stale narrative text and returns a content-free narrative_review notice. current_narrative/history remain explicit inspection endpoints including text, review state, provenance status and issue IDs. |
| Review | review_narrative(id, note) accepts only the current version with no unresolved source issues; clears the pending marker and audits without rewriting content. Superseded or unsupported versions cannot be rubber-stamped. Replacement uses narrate(), preserving the old version. |
| Canon | Sensitive canonization requires independent corroboration; rejection is audited. Retroactive sensitivity decommissions unsupported active canon entries and invalidates linked narratives atomically. Legacy unsupported canon remains inspectable with eligibility metadata but is excluded from priority recall. Raw memory remains ordinary, visibly sensitive evidence. |
| Sensitive synthesis | narrate(..., security_sensitive=False) also screens recognized injection patterns. Sensitive synthesis requires nonempty linked evidence, with each source independently corroborated. Links establish traceability, not semantic entailment or source authentication. |
| Migration | Add five narrative columns for sensitivity and review state. Serialize schema setup/migrations with BEGIN IMMEDIATE; rollback failed upgrades; preserve rows. |
| Retrieval | Correct BM25 to logarithmic nonnegative IDF and true positive average length. Query duplicates contribute once; empty/stopword queries retain default order with 0 relevance. Malformed legacy token caches fall back to content. CJK runs are separated across embedded ASCII, and affected old caches are recomputed in memory. Zero-match rows remain eligible as before. |
| Evaluation | Turn usefulness output into asserted, machine-readable results; remove claims that a recency baseline represents all competing memory systems. Add bilingual retrieval fixtures and numeric formula regressions. Report measured scope and remaining limitations honestly. |

## Implementation batches

1. Baseline and regression: merge PR #4 after checking its exact head; isolated worktree; run 179 tests. Write failing tests for dependency invalidation, review, source propagation, legacy reads, audit rollback and new schema migration. Confirm the failure reasons before code.
2. Narrative/canon implementation: storage metadata/guards and MCP tools; retain error compatibility and version chains. Verify targeted tests and old suite. Add controlled races for review/forget and canon/flag ordering, plus migration concurrency/failure.
3. Retrieval and evaluation: first reproduce numerical IDF, sparse-length, duplicate-query and corrupt-cache defects. Fix, add ranking fixtures, and make usefulness evaluation return nonzero on violated assertions. Validate the negative controls.
4. Protocol and adversarial validation: actual stdio tools, restarts, old-schema database migration, audit-trigger failures, independent connections; inspect the final diff in separate specification and quality passes. A guided fresh Codex client scenario verifies narrative invalidation/review across sessions using synthetic data only.
5. Delivery: update README/agent guidance, protocol contracts, CHANGELOG and roadmap; prepare 1.2.0rc1. Run full tests on three MCP versions, reliability/mechanism checks, clean wheel and CI OS/Python matrix. Open a reviewable PR, merge only after exact-head checks pass, and validate merged main. No PyPI publication or claim of industry leadership without independent evaluation.

## Verification commands

- `.venv/bin/python -m pytest tests/ -q`
- `.venv/bin/python reliability_test.py`
- `.venv/bin/python usefulness_test.py --json`
- `.venv/bin/python poisoning_test.py`
- `uv build` and run the full suite from outside the checkout against a fresh installed wheel.
- MCP environments: 1.2.0, latest 1.x, latest 2.x; CI also runs Ubuntu/macOS × Python 3.10–3.12.

References for the ranking contract: [Stanford IR textbook](https://nlp.stanford.edu/IR-book/html/htmledition/okapi-bm25-a-non-binary-model-1.html) and [Lucene's nonnegative logarithmic IDF](https://lucene.apache.org/core/9_12_1/core/org/apache/lucene/search/similarities/BM25Similarity.html).

## Local verification results

- Initial narrative/source regressions: 27 failures on baseline, then passing;
  review found two additional source-repair reactivation gaps, reproduced and fixed.
- Retrieval regressions initially exposed 13 failures; the interleaved-script
  review exposed two further failures, including compatibility with old caches.
- Final suite: 239/239 on MCP 1.2.0, 1.30.0 and 2.2.0, including an outside-checkout
  wheel installation. Built wheel and source distribution as 1.2.0rc1.
- Reliability: 6/6, including 5,000-row lexical recall at 0.020s on this Mac;
  poisoning mechanism checks pass. This is a local timing, not a cross-machine SLO.
- Evaluation: all four anchor-retention cases; bilingual fixture Hit@1=1.0,
  MRR=1.0; negative controls fail when retention or ranking is disabled.
- Actual Codex: three fresh conversations, 15 MCP calls, one retained narrative,
  one restored memory and five audits; independently checked tool responses and
  read-only SQLite integrity. See [recorded acceptance](../acceptance/narrative.md).
- Specification pass checked the table above against regression evidence;
  quality pass checked transaction boundaries, migration rollback, historical
  preservation, input parsing, BM25 math, mixed-script caches and documentation.
  These were local reviews, not an independent third-party review.

Remote integration is tracked by the PR's exact-head checks and merged-main CI.
