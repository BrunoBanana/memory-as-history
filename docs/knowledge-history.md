# Claims, evidence and knowledge history (1.3 preview)

The unpublished `1.3.0a1` preview separates **stored material**, **a claim about
that material**, **a recorded decision to adopt it**, and **a narrative using it**.
SQLite stores these relationships; it does not judge truth, compose explanations,
authenticate sources or infer what an individual knew. The
[reading report](research/2026-09-19-history-memory-reading.md) motivates the design
and distinguishes original theory from our engineering choices.

## Choose the view for the question

| Question | Tool | Boundary |
| --- | --- | --- |
| What background should stay in context? | `recall()` / `search()` | Existing anchor/canon priority; material is not necessarily a currently adopted assertion |
| Which judgments has the system adopted? | `recall_claims()` | Usable adopted claims; add `valid_at` for declared applicability at a particular time |
| What had been recorded by a past point? | `recall_claims(as_of=...)` / `inspect_claim(id, as_of=...)` | Claim/evidence ledger only, with current access restrictions |
| Which stored materials bear on this question? | `search_archive()` | Independent strict budget, no reserved priority slots; present material view |
| How do perspectives differ? | `list_narratives()` / `current_narrative(scope=...)` | Independent narrative scopes; scope is not an authorization boundary |

The legacy `resolve_conflict()` and `link_memories(relation="updates")` retain
their previous behavior. They do not implicitly create, adopt or retire claims.
Ordinary `remember()` also remains a simple material capture operation. Extraction
and judgment require explicit calls when the task needs them.

## Minimal lifecycle

```python
from memory_as_history.storage import Store

store = Store(":memory:")
try:
    source = store.remember(
        "The release is planned for June.", source="project notice",
        material_type="document", origin_id="notice-1",
        capture_context="Published notices only; no meeting transcript",
    )
    original = store.create_claim(
        "The release is planned for June.", "plan", "Extracted from notice",
        scope="release", asserted_by="project team",
    )
    store.add_evidence(original["id"], source.id, "supports", "Notice wording",
                       quote="planned for June", locator="paragraph 1")
    adopted = store.adopt_claim(original["id"], "Use as the recorded plan")
    checkpoint = adopted["events"][-1]["recorded_at"]

    notice = store.remember("The release is now planned for July.",
                            material_type="document", origin_id="notice-2")
    replacement = store.create_claim(
        "The release is planned for July.", "plan", "Updated notice", scope="release")
    store.add_evidence(replacement["id"], notice.id, "supports", "Notice wording")
    store.revise_claim(original["id"], replacement["id"], "New notice changes plan")

    assert store.recall_claims(scope="release")["claims"][0]["id"] == replacement["id"]
    assert store.recall_claims(as_of=checkpoint)["claims"][0]["id"] == original["id"]
    assert store.inspect_claim(original["id"])["status"] == "superseded"
finally:
    store.close()
```

Run `python examples/historical_claims.py` for a disposable Chinese example with
parallel product/operations narratives, late capture and archive counterevidence.

## Material and claim fields

`remember()` adds optional, immutable capture metadata:

- `material_type`: `unspecified` (default), `document`, `utterance`,
  `observation`, or `summary`. It describes the material, not its reliability.
- `origin_id`: caller-declared common original, trimmed and case-sensitive.
  Use the same ID for an original and its reposts. Unknown origin stays null;
  different IDs do not prove independence. The old `source` label is separate.
- `capture_context`: free text stating known collection limits or circumstances.
  It is a declaration, not a verified corpus-completeness record.

`create_claim(content, kind, reason, ...)` records immutable claim content and a
`proposed` event. Kinds: `assertion`, `observation`, `plan`, `commitment`,
`interpretation`, `self_report`. Optional fields: `scope` (default `global`),
`asserted_by`, `statement_at`, `valid_from`, `valid_until`, `security_sensitive`.
Original speech and an interpretation of it should be separate materials/claims.
A self-report about feeling distrusted is not evidence that others distrusted
the speaker. A plan or commitment never becomes an observed outcome automatically.

All supplied times use full timezone-aware ISO timestamps, normalized to UTC.
Unknown times remain null. Validity is `[valid_from, valid_until)`, with exclusive
end. `valid_at` only filters known bounds; null bounds mean **unknown**, not proven
applicability. Without `valid_at`, adoption discovery does not filter validity.

## Evidence and adoption

`add_evidence(claim_id, memory_id, stance, reason, quote?, locator?)` links active
material to a particular assertion. Stances are `supports`, `challenges`, `context`.
Quotes must be exact nonempty substrings; the locator is caller text and is not
independently checked. Quoting a sentence does not prove it entails the claim.
An active `(claim, memory, stance)` association is idempotent: another call returns
the existing association unchanged. To correct its quote/locator/reason, retract
it and add a new association. Contradictory stances are retained for inspection.

`inspect_claim()` returns evidence and `support`: supporting/challenging record
counts, explicit common-origin groups, unknown-origin count and
`independence_verified: false`. Five reposts sharing one origin produce five
material records and one origin group. No independent-source score is invented.
Legacy `provenance()` keeps its source-label rule and old fields, adding the same
explicit `independence_verified: false` and a `verification_basis` label. Legacy
tiers and security gates do not silently switch to the new origin-group model.

`adopt_claim(id, reason)` requires active supporting evidence and no source issues.
It accepts a proposed claim or explicitly re-reviews an adopted one. Challenges
remain visible; the caller must explain its decision. Adoption is not a truth
certificate. Sensitive claims (explicit flag or recognized injection pattern)
require the existing corroboration gate for every active evidence source, as do
individually sensitive sources. Origin-group labels never bypass that gate.

New/retracted evidence and source changes invalidate the recorded review of an
adopted claim. Default discovery excludes it until explicit re-adoption. Ordinary
material interpretation-review rules remain in force. `kind="interpretation"`
alone does not schedule a claim review cadence; source changes do require review.

`revise_claim(old_id, replacement_id, reason)` requires an adopted old claim and a
supported proposed replacement in the same scope. Both state changes are atomic
and have one recording time. The old claim becomes `superseded`; its replacement
ID is retained. Concurrent alternatives cannot both replace that same old claim.
The operation does not change raw material, anchor or canon status.

`withdraw_claim(id, reason)` ends a proposed/adopted judgment. Withdrawal and
supersession are terminal for that claim; create another supported claim if a
later decision changes course. `retract_evidence(id, reason)` retires an association,
not the underlying material. Every actual transition is audited atomically.

## Recording-time history and access

`as_of` is an inclusive **system recording time** for new claims, evidence and
claim events. It does not filter by event/statement time. A notice dated April 10
but imported April 20 cannot appear in knowledge as of April 15. The system does
not infer that a person read the notice. `inspect_claim()` returns null before
the claim was recorded and a structured missing-ID error for an unknown claim.

Event sequence resolves tied timestamps. An `as_of` timestamp includes all events
at that instant; it cannot select an intermediate tied event. Recording times
are nondecreasing across the ledger even if the wall clock moves backward. They
are not external clock attestations. All claim reads use one database snapshot.

Current source availability and sensitivity checks overlay the historical view.
If any material ever cited by a claim is currently forgotten/missing, inspection
redacts its derived text and evidence; discovery omits it even with
`include_inactive=True`. This is deliberately conservative, including retired
citations. A new claim can use permitted material instead. Restoring a source
does not automatically re-adopt the affected judgment or approve its narrative.

This is **not** full event replay of the old memory, frame, anchor, canon or review
state. Historical source checks use current source eligibility. The old explicit
`get`, `list_forgotten`, `current_narrative`, `narrative_history` and audit inspection
APIs retain their documented text-retention behavior. Soft forgetting is not
physical erasure, an authorization system, or tamper-proof storage.

## Parallel narratives and relationships

`narrate(..., scope="release/product", perspective="Product: planned date",
coverage="Published notices only", claim_ids=[...], link_ids=[...])` creates a
version in that scope. It supersedes only that scope's current version. Different
scopes can evaluate the same event by different criteria without an automatically
declared conflict. The default `global` scope preserves old usage.

Dependencies may include memory IDs, usable adopted claim IDs and active memory
relationship IDs. The latter two also bring their material under source guards.
Claim changes, evidence changes, relationship retirement and unusable material
require review of the affected account. Unrelated accounts stay current. Retired
relationships or superseded claims require a replacement narrative with valid
dependencies; `review_narrative()` cannot override them.

`current_narrative(scope="...")` and `narrative_history(scope="...")` deliberately
inspect retained text, including stale versions with **current** dependency
checks. `narrative_history()` without scope lists all versions. `list_narratives()`
discovers current versions across scopes, withholding stale text and free-text
metadata; use deliberate inspection when justified. `recall()` continues to show
only the usable global narrative or its review notice. The database does not
check whether a fluent narrative adds unsupported motives, causality or consensus.

## Independent archive retrieval

`search_archive(query, limit=10, frame?, session_id?, since?, until?)` uses the
existing model-free BM25 scorer across active material, including both working
and consolidated records. Anchors/canon have no reserved slots or ranking boost.
Ties use recording time descending, then ID. As in ordinary lexical retrieval,
zero-overlap candidates can fill remaining slots: relevance orders rather than
strictly excludes. Inspect the score and source before drawing conclusions.

Filters apply to **every** candidate. Event-time bounds are inclusive and exclude
unknown event times. `limit` is a strict nonnegative record limit, including zero;
it is not a byte/token budget. Forgotten records cannot appear. Retrieval reports
the filters, candidate/returned counts, truncation, unknown event-time count
(within frame/session before time filtering), and unknown collection completeness.
Returned material includes its declared capture context. No missing result proves
absence, agreement or suppressed disagreement. This mode does not auto-generate
counterclaims or score which account is true.

## Migration and operational limits

Initialization adds nullable/defaulted material and narrative columns plus
`claims`, `claim_events`, `claim_evidence` and their indexes, in the existing
serialized migration transaction. Existing memories keep their content, tiers,
timestamps and audit history. Old narratives belong to `global`; their perspective,
coverage and new dependencies are unknown/empty. No old material is automatically
converted to a claim and no historical adoption events are fabricated.

Keep a database backup before upgrading, and upgrade all writers together. An old
server can neither maintain the new ledger nor invalidate new dependencies; mixed
version writers are unsupported. Writes retain the 30-second SQLite lock timeout.
The ledger is append-only through these APIs, not protected against direct SQL
tampering. Queries currently scan the relevant claims/material; large knowledge
histories and event/evidence payload growth need separate scale measurements.

## Verification boundary

The new tests exercise authored behavioral cases, migrations, audit failures,
concurrent revision, source withdrawal and real MCP process restarts. They show
that explicit calls follow the contract. They do not show autonomous extraction,
reliable source independence judgments, semantic entailment, historical reasoning
quality, improved retrieval metrics or superiority to another product. See
[evaluation requirements](evaluation.md) before making those claims.
