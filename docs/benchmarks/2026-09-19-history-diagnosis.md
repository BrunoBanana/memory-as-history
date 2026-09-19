# History retrieval diagnosis — 2026-09-19

The unchanged hybrid trace has 405 multi-evidence questions: 370 lack at least
one required turn, and 326 need evidence across sessions. Of 1,372 missed gold
turns, 239 are within one position of a selected turn in the same session, 445
within three, and 20 have no lexical query-token overlap. All 69 regressions
against lexical recall remain in the diagnostic trace.

These are overlapping mechanical indicators, not verified causal classifications.
An adjacent gold turn is an opportunity, not a gain: adding it may displace
another relevant turn under the fixed five-turn budget. Cross-session misses
cannot be solved by adjacency alone. Token overlap does not establish meaning,
entity identity or adequate ranking. Pronoun/temporal/semantic root-cause labels
would require independent annotation, which this run does not manufacture.

Chosen experiment: explicit caller-asserted links plus optional one-turn session
neighbors, alongside structured event-time filtering. Unknown event times stay
unknown; upstream session dates are not event dates. External evaluation supplies
session/position only, never gold-derived links or time windows. The baseline
search remains available; an expansion that regresses is reported as such.

[Full ID-only diagnosis](results/2026-09-19-history-diagnosis.json.gz) includes every
eligible question and every regression. Reproduce with:

```sh
python scripts/diagnose_history_retrieval.py --data .benchmark-data/locomo10.json \
  --baseline docs/benchmarks/results/2026-09-18-semantic-locomo.json.gz \
  --output diagnosis.json
```

The frozen challenge has 48 cases: 24 dev and 24 evaluation, separated by topic,
with correlated English/Chinese translations and three authored families. Links
and time bounds are explicit fixture inputs. It is author-visible and does not
constitute a blind independent holdout or automatic relation/date extraction test.
