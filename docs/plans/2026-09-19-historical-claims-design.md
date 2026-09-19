# Historical claims: next-version design

Approved direction: the user accepted the reading report's recommendation to
separate claims from evidence, record changes in knowledge, and support parallel
narratives and archive investigation. This document makes that scope concrete.

Target: **1.3.0a1**, an unpublished preview. SQLite and existing tools remain.
The 1.2 RC remains documented; this is not a stable-release or quality-leadership
claim. Research notes are preserved in `docs/research/`.

## Chosen approach and alternatives

Add immutable claims and evidence associations beside existing memories, with an
append-only claim event ledger. This preserves source material and old APIs while
making adoption and revision explicit. Extending mutable memory rows alone would
still conflate material with judgment. Rebuilding every old operation as event
sourcing would imply historical precision the old database cannot provide.

## Contract

- `remember()` stays simple. Optional material type, common origin identifier and
  capture context describe material, not truth. Legacy rows retain unknown type
  and origin; no invented evidence or migrated adoption events.
- Claims record content, kind, attribution, scope, statement time and optional
  validity interval. System recording time cannot be supplied by the caller.
  Plans, commitments, observations and interpretations never change type merely
  because a date passes. Claims initially remain proposed.
- Evidence links a particular claim to an existing active memory, with stance,
  reason, optional verbatim quote and locator. Quotes must occur in source text;
  this checks location, not entailment. Common explicit origin IDs collapse
  reposts for support reporting. Unknown origins do not imply independence.
- Adoption requires usable supporting material and records human/agent judgment,
  not verified truth. Changes to evidence or source eligibility require explicit
  re-adoption. Sensitive adoption retains the existing corroboration gate.
- Revision atomically adopts a proposed replacement in the same scope and
  supersedes the old adopted claim. Withdrawal is explicit. The original text,
  reasons, evidence and state transitions remain inspectable.
- `recall_claims()` defaults to usable adopted judgments. `inspect_claim()` and
  optional `as_of` inspect the claim ledger at a system recording time. A separate
  `valid_at` filters caller-declared applicability. Late evidence and later
  judgments cannot leak into past knowledge. Equal timestamps use ledger order.
  This is not replay of legacy memory/anchor/frame state or a person's knowledge.
- Current forgetting overrides historical claim access: derived text and evidence
  are redacted when their material is forgotten; default claim recall omits them.
  Restore alone does not approve affected judgments. Raw explicit legacy
  inspection APIs retain their documented behavior.
- Narratives have independent version chains by `scope` (default `global`), plus
  optional perspective and coverage description. Dependencies may include claims
  and memory relationship IDs. Their source memories are retained as dependencies;
  revision, evidence changes or relationship retirement invalidate only affected
  narratives. Existing default recall continues to show the global narrative;
  explicit scoped inspection/discovery exposes parallel accounts.
- `search_archive()` ranks all active material with a strict independent limit,
  applying frame/session/event filters to every candidate. Anchors and canon
  receive no reserved slots. Output states searched scope, candidate count,
  returned count, truncation and unknown event-time coverage. This measures only
  stored material; it cannot assert complete collection or consensus.

## Architecture and validation

Keep reusable transaction decorators in a small module and the claim ledger in a
separate mixin/module. Public writes retain `BEGIN IMMEDIATE`, atomic auditing and
rollback. Schema upgrades remain additive and atomic; no legacy reclassification.
Expose all new operations over MCP with existing literal-string handling and
structured errors. Add meaningful behavioral tests before implementation, real
stdio restart scenarios, migration/fault/concurrency tests, and a runnable example.

Verify late notices, forecasts, common-origin reposts, independent narrative
scopes, archive counterevidence, relationship withdrawal, changed self-description,
limited source coverage and source-versus-interpretation labeling. Storage tests
do not establish model reasoning quality; independently authored blind evaluation
and unguided client trials remain separate future evidence, not promised results.
