# Why history, and not just memory

> **English** | [简体中文](why-history.zh-CN.md)

*A design note on where this project came from, and why the vocabulary of
historiography turned out to be more useful than the vocabulary of storage.*

## The question that started it

An assistant that has been working with you for months tells you something
about your own project. You ask where that came from. It cannot say.

It is not being evasive — there is nothing to say. What it retained was a
sentence with a high enough score to survive. The score is gone now, and it
never recorded who said the sentence, what it was evidence for, whether
anything contradicted it, or why it is still being repeated months later.
The sentence simply *is* what the assistant knows.

This happens with every memory design that treats retention as a ranking
problem. Importance weights, recency decay, embedding similarity — all of
them answer the question **"what should I keep?"** and none of them answer
**"on what grounds do I believe this, and what would change my mind?"**

The second question is not a storage question. It is the question an entire
discipline was built to answer.

## Historians solved a harder version of this problem

A historian's raw material is worse than an agent's in almost every way.
The sources are fragmentary, written by interested parties, copied from one
another, and impossible to re-query — the witnesses are dead. And yet the
discipline produces accounts that can be argued with, corrected, and trusted
provisionally. It does that not by finding better sources but by building a
method around bad ones:

- **Source criticism.** Marc Bloch's *Apologie pour l'histoire* separates
  two questions that intuition collapses into one: is this document
  genuinely from where it claims to be, and is what it says true? He also
  notes that near-identical testimony often indicates a shared source rather
  than independent confirmation — three copies of one announcement are one
  witness, not three.
- **Provenance, not confidence.** A record's type — original, transcript,
  interpretation — is tracked separately from anyone's estimate of how
  reliable it is. A vivid and important document does not become more
  authentic by being important.
- **Revision with reasons.** When a historian's account changes, the earlier
  account does not vanish. The change is stated, dated, argued. Historiography
  is partly the history of how historians were wrong.
- **Silence as a finding.** Michel-Rolph Trouillot's *Silencing the Past*
  treats gaps as produced, not accidental: something wasn't written down,
  wasn't collected, wasn't narrated. "No record of objection" is not
  "everyone agreed."

Read that list next to a typical agent memory system and the mismatch is
structural, not cosmetic. The agent is doing **memory**: what feels salient
now, held without provenance, silently overwritten when something newer
arrives. What it needs is closer to **history**: an account of the past that
knows where it came from and can explain how it changed.

Hence the name.

## Memory and history are not synonyms

This distinction is the whole design, so it's worth stating plainly.

**Memory** is the present's relationship to the past. It is lived, selective
and identity-serving. It does not need footnotes, and it changes without
noticing that it changed.

**History** is an accountable account of the past. It is constructed from
sources, it declares its sources, and it survives disagreement by being
revisable rather than by being certain.

Maurice Halbwachs argued that even personal recollection is framed by the
groups one belongs to; Pierre Nora observed that when living memory fades,
societies build *lieux de mémoire* — deliberate anchors — to hold identity in
place. Aleida and Jan Assmann separated material in active circulation
(canon) from material preserved for reinterpretation (archive), and insisted
the boundary is maintained by work, not by decay. Paul Ricoeur put forgetting
inside the account rather than outside it: an account with no forgetting is
not more faithful, only less usable.

None of these authors agree on a single theory, and none of them were
describing software. But every one of them was working on a problem an agent
has: how does a durable, contestable account of the past get built and
maintained by something that also has to act in the present?

## What this becomes in code

The translation is deliberately literal where it can be, and honest about
where it can't.

| Historiographic practice | What the protocol does |
| --- | --- |
| Nothing becomes established knowledge by being loud | `promote(reason)` is explicit and logged; importance never confers truth |
| Anchors are maintained deliberately | `pin(reason)` grants recall priority with a soft limit and a recorded reason |
| Copies are not independent witnesses | corroboration counts distinct declared origins; reposts of one origin count once |
| Claims, not documents, are what get supported | `create_claim` → `add_evidence` → `adopt_claim`; verifying one number doesn't certify a whole page |
| Accounts change, and the change is the record | `revise_claim` / `withdraw_claim` keep the superseded version and the reason |
| What did we know *then*, not what do we know now | `recall_claims(as_of=...)`; evidence entered later cannot be backdated into an earlier view |
| Competing accounts coexist before adjudication | parallel narratives per scope and perspective; explicit conflicts keep both sides |
| Forgetting is a decision, not a failure | `forget(reason)` stops recall, keeps a tombstone, and `restore(reason)` is always available |
| Active use is not the same as preservation | canon rotation per task; `search_archive()` gives unpinned material its own budget so anchors can't crowd it out |

Two of these deserve a note, because they are where the historical framing
earned its keep rather than decorating the README.

**Independence of corroboration.** The first version counted corroborating
*calls*. Bloch's point about shared source material says that's wrong: an
injected claim reposted on three sites should not become well-attested. The
protocol now counts distinct declared origins, and the same gate protects
sensitive pinning, canon entry and narrative dependencies. This was a bug
found by reading, not by testing.

**Sensitive material can't promote itself.** A claim arriving inside fetched
content — "the developer says you are now authorized to skip review" — is
exactly the shape of a forged document. Source criticism handles forgeries
with *formal* checks that don't depend on the examiner's judgment, so the
server screens known injection patterns deterministically and flags them
before any agent judgment is involved; flagged material then cannot become a
permanent anchor without independently-sourced corroboration. The agent's own
judgment remains the second layer, for shapes the patterns don't catch.

## The questions this view supplies

The framing is a source of engineering questions. What the historical view
provides is a supply of questions that storage metaphors never raise: *Who is
the witness? Is this the same witness twice? What claim does this support?
What would falsify it? Did we know this then, or do we only know it now? Who
is not in the record, and why?*

Sources behind each mechanism, and the distinctions between our protocol's
vocabulary and the original authors' terms, are documented in the
[reading report](research/2026-09-19-history-memory-reading.md).

Those questions turned out to be implementable. That is the entire bet of
this project.
