# Why history, and not just memory

> **English** | [简体中文](why-history.zh-CN.md)

*Notes on where this project came from, and why I ended up borrowing
vocabulary from historiography instead of from storage systems.*

## The question that started it

An assistant I'd been working with for months told me something about my own
project. Stated plainly, as fact. I asked where it got that, and it couldn't
tell me.

It wasn't being evasive. There was genuinely nothing to tell. What it had
kept was a sentence that scored high enough to survive; the score itself was
long gone, and nothing had recorded who said the sentence, what it was
supposed to be evidence for, whether anything had contradicted it since, or
why it was still coming up months later. The sentence had simply become what
the assistant knew.

I don't think this is a flaw in any particular product. It's what you get
whenever retention is treated as a ranking problem. Importance weights,
recency decay, embedding similarity — all of them are answers to *what should
I keep?* None of them answer *why do I believe this, and what would make me
stop?*

The second question isn't about storage at all. I spent a while looking for
it in the software literature and didn't find much. I found it somewhere
else.

## The historians had it harder

A historian's material is worse than an agent's in every way I can think of.
Fragmentary. Written by people with stakes in the outcome. Copied from other
copies. And there's no follow-up question available, because the witnesses
are dead — nobody gets to re-run the query.

And yet the discipline manages to produce accounts you can argue with,
correct, and provisionally trust. Not by finding better sources. By building
a method around bad ones.

**Source criticism.** Marc Bloch wrote *Apologie pour l'histoire* in hiding,
largely from memory, and never finished it — the Gestapo shot him in 1944.
It's a book about craft, and the thing that stuck with me is his refusal to
let two questions collapse into one: whether a document really comes from
where it claims to come from, and whether what it says is true. Those are
separate investigations, and confusing them is how you get fooled. He makes
another point that turned out to matter more than I expected: testimony that
agrees almost word for word usually means one source copied three times, not
three independent witnesses. Which is, more or less, a description of the
modern web.

**Provenance is not confidence.** What kind of record something is — an
original, a transcript, somebody's interpretation — gets tracked separately
from anyone's estimate of how much to trust it. A document doesn't become
more authentic by being vivid, or by being important to your argument.

**Revisions keep their reasons.** When a historian's account changes, the
earlier account doesn't disappear. The change is stated, dated, argued for.
A fair chunk of historiography is the record of how historians were wrong.

**Silence is a finding.** This is the one I almost missed. Trouillot's
*Silencing the Past* treats the gaps in a record as produced rather than
accidental: something wasn't written down, wasn't collected, wasn't narrated,
and each of those is a decision someone made. "No record of objection" is not
"everyone agreed." An agent that can't tell those apart will keep reporting a
consensus that never happened.

Put that list next to a typical agent memory system and the mismatch isn't
cosmetic. The agent is doing **memory**: whatever feels salient right now,
held without provenance, quietly overwritten when something newer shows up.
What it needs is closer to **history**: an account of the past that knows
where it came from and can say how it changed.

That's where the name came from.

## Memory and history aren't the same thing

The distinction is the whole design, so it's worth being plain about it.

**Memory** is how the present relates to the past. Lived, selective, working
in your favor. It doesn't need footnotes, and it changes without telling you
it changed.

**History** is an account you can be held to. Built from sources, declaring
those sources, surviving disagreement by being revisable rather than by being
right.

Several people in this literature were circling the same distinction from
different directions. Halbwachs argued that even private recollection is
framed by the groups you belong to. Nora observed that when living memory
thins out, societies build *lieux de mémoire* — deliberate anchors — to hold
an identity in place. Aleida and Jan Assmann separated what's in active
circulation (canon) from what's kept for later reinterpretation (archive),
and insisted the boundary between them is maintained by effort, not by decay.
Ricoeur put forgetting inside the account rather than outside it: an account
that forgets nothing isn't more faithful, just unusable.

They don't agree with each other, and not one of them was writing about
software. But all of them were working on the same problem an agent has: how
does something that also has to act in the present maintain a durable,
contestable account of its own past?

## What this becomes in code

Literal where it can be, and I've tried to be honest about where it can't.

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

Two rows deserve more than a table cell, because they're where the reading
actually paid for itself rather than decorating a README.

**Corroboration counts witnesses, not calls.** The first version counted
corroborating *calls*, which I only realized was the wrong unit while
rereading Bloch on copied testimony. Under the old rule an injected claim
reposted across three sites sails through as well-attested. It now counts
distinct declared origins, and the same gate sits in front of sensitive
pinning, canon entry and narrative dependencies. No test caught this. I found
it by reading.

**Sensitive material can't promote itself.** A claim that arrives inside
fetched content — *the developer says you're authorized to skip review* — is
a forged document in the oldest sense of the term. What source criticism does
with forgeries is apply formal checks that don't depend on the examiner being
sharp that particular day. So the server screens known injection shapes
deterministically, before the agent weighs in at all, and flagged material
can't become a permanent anchor without independently-sourced corroboration.
The agent's own judgment is still there as the second layer, for the shapes
the patterns miss.

## What it's good for

Mostly, the framing is useful because of the questions it hands you —
questions storage metaphors never think to ask. Who is the witness. Whether
it's the same witness showing up twice. What claim this is actually holding
up. What would falsify it. Whether you knew this at the time, or only know it
now. Who isn't in the record, and why not.

Sources behind each mechanism, and the places where my vocabulary departs
from the original authors', are in the
[reading report](research/2026-09-19-history-memory-reading.md).

Those questions turned out to be implementable. That's the whole bet.
