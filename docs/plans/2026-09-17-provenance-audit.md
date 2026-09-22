# Provenance and Unpin Audit Implementation Plan

**Goal:** Require independent corroboration for new testimony classifications and make every successful explicit unpin request accountable while retaining the legacy call form.

**Architecture:** Reuse `independent_corroboration_count()` inside the existing write transaction for both testimony upgrade and sensitive pinning. Record corroboration attempts and unpin outcomes in the existing audit table. Do not migrate or reinterpret stored historical tiers silently.

**Stack:** Existing Python/sqlite3/MCP implementation; no new dependency or database column. Follow executing-plans, test-driven-development, specification review, and quality review.

## Decisions

| Topic | Contract |
| --- | --- |
| Known origin | At least one nonblank corroborating source different from the normalized original source |
| Unknown origin (`None`, empty, whitespace) | At least two distinct, nonblank corroborating sources |
| Duplicate / same source | Record the observation, but it contributes no new independent source and cannot alone upgrade archive |
| Source normalization | Trim surrounding whitespace; preserve case and source identity rather than guessing aliases |
| New testimony capture | Reject `remember(tier='testimony')` with a self-correcting error; capture archive, then corroborate with evidence |
| Interpretation | Never auto-upgrade it through corroboration |
| Corroboration audit | Log every successful corroboration record; log `corroborate_upgrade` exactly when archive becomes testimony |
| Explicit unpin | Add optional `reason`; reject explicitly empty/whitespace reasons; log actual removal as `unpin` |
| Legacy unpin | Omitted/None reason still works and uses an honest fixed audit note identifying the omitted reason |
| No-op unpin | Preserve idempotence, including unknown IDs; log `unpin_noop`, so the history distinguishes a removal from an already-unpinned state |
| Return compatibility | Storage `unpin` still returns None. MCP retains `memory_id` / `unpinned: true`, meaning the requested unpinned state, and adds structured errors for invalid reasons |

Alternatives considered: making unpin's reason immediately mandatory would break existing clients; manufacturing an inferred reason would misrepresent the user. Keeping optional reasons with an explicit legacy marker is the compatible choice. Automatically downgrading historical testimony would rewrite earlier judgments without evidence; preserve it and document the boundary instead.

## Historical data boundary

Databases open without tier rewrites or invented audit entries. Existing testimony can have insufficient evidence under the new rule (older releases upgraded on the first corroboration, and allowed callers to declare testimony directly). For those rows, tier is a historical classification, not a new verification guarantee. `provenance(memory_id)` is a new read-only Store/MCP query for source labels, independent count, and `corroboration_satisfied`; it warns on unsupported historical testimony without rewriting it. The internal `independent_corroboration_count(id)` delegates to this shared calculation; sensitive pinning continues to use that count rather than trusting the tier. Additional genuine corroboration can establish support; bulk reclassification/repair is a separate, explicit future operation. Earlier unpins without audit cannot be reconstructed.

Source labels are caller-supplied identifiers, not authenticated attestations. Repeated messages from one speaker or copies of one document are not independent sources; callers must not invent different labels to pass the gate.

## Task 1 — Regression tests before code

- Add `tests/test_provenance_audit.py`: same-source and duplicate evidence, unknown origins, normalization, interpretation preservation, direct testimony rejection, legacy-row preservation, complete corroboration audit, unpin reasons/no-ops, and failed-audit rollback.
- Add real stdio cases in `tests/test_server.py` for evidence-gated upgrade, self-correcting testimony errors, optional unpin reason schema, successful/no-op/legacy unpin responses, optional reason schema and invalid reason rollback; verify persisted outcome/reason audit at the storage layer.
- Update the old independent-report fixture to specify its original source, so it actually represents two distinct sources.
- Run the new tests against the current implementation and confirm the intended failures.

## Task 2 — Storage and MCP implementation

- In `storage.py`, reject direct testimony capture; record each corroboration and only upgrade when the shared independent-source predicate succeeds.
- Extend `unpin` with an optional reason, validate before deletion, and audit the actual outcome in the same transaction. Preserve the existing None return and unknown-ID idempotence.
- Add `provenance(memory_id)` using one joined read snapshot for the original source, stored tier, and corroborations; expose it over MCP with structured missing-ID errors.
- In `server.py`, pass the optional reason through, return structured validation errors, and correct tool descriptions/hints to describe the new evidence requirements.
- Run targeted regressions, then the full suite and fault-injection tests; review specification compliance before code quality.

## Task 3 — Compatibility, docs, and delivery

- Update README in both languages, AGENT_GUIDE, CHANGELOG, and roadmap/tracker. Clearly separate independent sources from repeated turns by the same speaker.
- Keep this stage in the 1.2 development version. Verify MCP 1.2.0/current 1.x/2.x, the reliability battery, mechanism scripts, and a clean installed wheel.
- Open a PR against main and confirm current-head CI. The previous PRs are already merged; do not merge the new behavior change or publish a release in this batch.

## Acceptance

- New testimony classifications always have the same independent-evidence threshold as sensitive pinning.
- Every corroboration write and explicit unpin outcome is auditable; audit failure rolls back related mutations.
- Existing one-argument unpin calls and their return shapes remain compatible.
- Old databases keep their stored tiers and audit history unchanged on reopen.


## Implementation and review evidence (2026-09-18)

Baseline: main `bc5ae62`, identical to the merged transaction stage. The
existing suite had 156 cases under `tests/` (162 if pytest also collects the
six top-level reliability tests). All 21 new regression cases failed against
the old implementation; after the change, `tests/` passes 177/177.

Specification review: archive upgrade, sensitive pinning, and retroactive
sensitivity share the same evidence count. Known and unknown origins,
whitespace/duplicate sources, interpretation preservation, direct testimony
rejection, and legacy testimony are covered. `provenance()` uses one joined
read snapshot and makes no writes. Unpin accepts old calls and preserves both
Python and MCP return formats; audit actions distinguish removal and no-op,
including unknown IDs. Explicit reasons are retained verbatim, consistent
with existing reason validation; only source comparisons strip whitespace.

Quality review: all new writes remain within the existing outer transaction.
An audit failure during corroboration rolls back evidence; the existing
late-upgrade failure test also rolls back the new earlier corroboration log.
An unpin audit failure restores the anchor even across a later commit and
reconnect. Concurrent unpins record exactly one removal and one no-op.
No schema change or runtime dependency was introduced. Source identity is
caller-asserted and is not authentication; broader canon/narrative policy
remains a separate roadmap item.

Local verification: MCP 1.2.0, 1.30.0, and 2.2.0 each pass 177/177 on
macOS / Python 3.12.13. Reliability battery: 6/6. Mechanism checks retain
anchors at all four noise sizes and reject uncorroborated poisoning attempts.
Wheel and sdist build successfully. A fresh environment installed the wheel
and passed 177/177 from outside the checkout; imports resolved to site-packages.
Review delivery: PR #3 (historical; PR refs were removed in the 2026-09-22 repository recreation, see CHANGELOG),
based on main. The PR current-head checks are the authoritative remote CI
record (six OS/Python jobs and three MCP compatibility jobs). All nine passed
at `a3a6d40`; on the next authorized batch PR #3 was merged as `8aa14eb`.
No package release has been published.
