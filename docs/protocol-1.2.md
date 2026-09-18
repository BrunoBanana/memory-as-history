# 1.2 protocol contracts

The storage API and MCP tools use the same rules. MCP guard failures return
`{error, message, hint}`; a transport success alone does not mean the operation
succeeded. Historical inspection endpoints deliberately expose retained text.

## Optional semantic search

`search(query, limit=10, frame=None, mode="hybrid")` adds local semantic ranking
without changing recall's history rules or database schema. Encoding runs outside
the writer transaction; a fresh recall revalidates eligibility before output.
Scores cannot promote or corroborate sources. Changed/new candidates that were
not encoded appear after surviving ranked candidates in fresh lexical order,
with their count exposed in retrieval metadata. Missing models or invalid scores
are explicit failures. See [setup, modes and concurrency details](semantic-search.md).

## Narrative dependencies and review

`narrate(content, reason, memory_ids=None, security_sensitive=False)` creates a
new current version and supersedes the previous one in one audited transaction.
Nonempty links must be existing, active IDs, with no overdue interpretation.
Malformed lists are rejected, duplicate IDs normalized. An ordinary unlinked
account remains accepted for compatibility, with `provenance_status="unlinked"`
and a warning. Linking evidence proves traceability, not truth or entailment.

Narratives add these persistent fields:

| Field | Meaning |
| --- | --- |
| `security_sensitive` | Explicit sensitivity or a recognized injection pattern at creation |
| `review_required_at` | First invalidation since the last successful narrative review |
| `review_reason` | Server-generated explanation of that invalidation |
| `last_reviewed_at` | Last successful explicit narrative review |
| `review_note` | Caller reasoning from that review |

Returned metadata also includes `review_status` (`current` or `stale`),
`provenance_status` (`linked` or `unlinked`) and `source_issues`. Each issue has
`memory_id` (nullable for malformed/missing evidence) and an `issue` code:
`missing`, `forgotten`, `stale_interpretation`, `insufficient_corroboration`,
`missing_evidence`, or `invalid_memory_ids`. The `current` review status does not
mean verified truth, and historical supersession remains a separate property.

Forgetting/restoring a dependency or flagging it sensitive without sufficient
support records `narrative_invalidated`, preserving text and version links.
Refreshing overdue interpretations also invalidates dependent narratives.
Source review/corroboration checks dependency failures before repairing the
source, so repairing evidence cannot silently reactivate an old synthesis.
Existing review markers remain until explicit narrative review, even when live
source issues have cleared. Historical versions may also carry invalidation
metadata; their text and supersession history never change.

`recall()` includes usable `narrative`, or null plus a `narrative_review` notice
containing only ID, review status, issue IDs/codes and invalidation metadata.
The stale narrative text is absent. `current_narrative()` and
`narrative_history()` expose the text for deliberate inspection, even when stale.
This is accountable forgetting, not hard erasure or access control.

`review_narrative(narrative_id, note)` requires a nonblank note, the current
version, and no live source issues. It clears the invalidation, timestamps the
review, and logs `review_narrative` without rewriting text. The caller must
actually compare the account with the evidence. For a changed account, call
`narrate()` with valid dependencies; the old version remains inspectable.

## Source criticism across priority routes

Sensitive `pin()` and `canonize()` require independent corroboration. A narrative
cannot cite an unsupported sensitive memory. A narrative flagged sensitive
explicitly or by a recognized content pattern requires at least one link and
independent corroboration for **every** linked memory, even if those memories
are not individually flagged. Denials log `pin_denied`, `canonize_denied` or
`narrate_denied`; no business mutation precedes those logs. Failed denial audits
roll back instead of taking the intentional-denial commit path.

Known origins need one different source; unknown/blank origins need two distinct
corroborating labels. Labels are trimmed, case-sensitive and caller-supplied.
They cannot authenticate source independence; clients must not fabricate them.
Pattern detection only covers known shapes and is not a general injection filter.
Raw sensitive memories remain recallable as evidence, never as instructions.

Retroactive `flag_sensitive()` removes unsupported anchors, decommissions every
active canon scope (`decanonize_by_sensitivity` per scope), and invalidates
linked narratives in the same transaction. Sufficiently supported entries stay
active. Reestablishing evidence does not automatically re-pin or re-canonize.
`list_canon()` exposes legacy unsupported entries with
`eligible_for_recall=false` and a warning. They do not take priority slots;
their ordinary memory remains eligible, visibly marked sensitive.

## Retrieval

For ordinary eligible memories, the BM25 score sums each distinct query token
once, with `k1=1.5`, `b=.75` and
`idf=log(1 + (N-df+.5)/(df+.5))`. Average length includes empty documents without
clamping positive values below one. Document term frequency is preserved.
Formula reference: [Lucene BM25Similarity](https://lucene.apache.org/core/9_12_1/core/org/apache/lucene/search/similarities/BM25Similarity.html).

ASCII words are lowercased, single-character ASCII words and the declared
English stopwords omitted. CJK runs use overlapping bigrams; an isolated CJK
character remains a token. The declared Chinese stopword entries do not filter
CJK bigrams. Mixed scripts are separate runs. This is lexical retrieval, not
semantic matching or a full language segmenter.

Malformed/missing legacy caches and caches affected by the pre-1.2 mixed-script
bigram bug are tokenized from content in memory; no cache
repair is silently persisted. A provided empty/stopword query returns default
ordering with `relevance=0`; no query omits relevance. Zero-overlap rows remain
eligible. Ties preserve consolidated-first/newest-first order. Scores are rounded
to four decimals for output, after ranking. Anchors and eligible canon retain
priority; frame filters apply only to ordinary memories.

## Migration and compatibility

Opening a database adds the five narrative columns inside a serialized
`BEGIN IMMEDIATE` schema transaction. Concurrent startups cannot race column
creation; failed upgrades roll back schema and data. Existing text, IDs, source
labels, and audit rows are preserved. Old malformed links are labeled stale
instead of crashing recall. Opening a database does not invent audit history.

New response fields are additive. New narratives with nonexistent/forgotten links
are now rejected; clients must use current evidence or explicitly unlinked
nonsensitive accounts. Clients must honor `narrative_review`, examine structured
errors, and tolerate additional response fields. Older servers do not enforce
these rules even if they can open the upgraded schema; do not treat downgrade
as behaviorally equivalent. Formal release packaging is separate from this RC.
