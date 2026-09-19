"""Claim-specific evidence and an append-only history of recorded judgments.

Material, caller adoption and independently verified truth are distinct. This
ledger starts with explicit new claims; it does not reconstruct legacy history.
"""
from datetime import datetime, timezone
import uuid

from .history import check_limit, event_time, time_range
from .transactions import _read_snapshot, _transactional


MATERIAL_TYPES = ('unspecified', 'document', 'utterance', 'observation', 'summary')
CLAIM_KINDS = ('assertion', 'observation', 'plan', 'commitment', 'interpretation', 'self_report')
STANCES = ('supports', 'challenges', 'context')
STATE_ACTIONS = ('proposed', 'adopted', 'withdrawn', 'superseded')

KNOWLEDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    asserted_by TEXT,
    statement_at TEXT,
    valid_from TEXT,
    valid_until TEXT,
    recorded_at TEXT NOT NULL,
    security_sensitive INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS claim_events (
    sequence INTEGER PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    replacement_id TEXT REFERENCES claims(id)
);
CREATE INDEX IF NOT EXISTS claim_event_history ON claim_events(claim_id, sequence);
CREATE TABLE IF NOT EXISTS claim_evidence (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    memory_id TEXT NOT NULL REFERENCES memories(id),
    stance TEXT NOT NULL,
    reason TEXT NOT NULL,
    quote TEXT,
    locator TEXT,
    origin_id TEXT,
    recorded_at TEXT NOT NULL,
    retracted_at TEXT,
    retraction_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS active_claim_evidence
    ON claim_evidence(claim_id, memory_id, stance) WHERE retracted_at IS NULL;
CREATE INDEX IF NOT EXISTS evidence_memory ON claim_evidence(memory_id);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} is required and cannot be empty')
    return value


def optional_text(value, field):
    return None if value is None else text(value, field)


def material_values(material_type, origin_id, capture_context):
    if material_type not in MATERIAL_TYPES:
        raise ValueError(f'material_type must be one of {MATERIAL_TYPES}')
    return {'material_type': material_type,
            'origin_id': optional_text(origin_id, 'origin_id').strip() if origin_id is not None else None,
            'capture_context': optional_text(capture_context, 'capture_context')}


class KnowledgeMixin:
    """Uses the Store's connection, lock, audit and source-eligibility helpers."""

    def _knowledge_time(self):
        # Keep ledger times nondecreasing even if the host wall clock moves back.
        latest = self._conn.execute('SELECT recorded_at FROM claim_events ORDER BY sequence DESC LIMIT 1').fetchone()
        now = _now()
        return max(now, latest['recorded_at']) if latest else now

    def _claim_row(self, claim_id):
        row = self._conn.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
        if row is None:
            raise KeyError(f'no such claim: {claim_id}')
        return dict(row)

    def _claim_event(self, claim_id, action, reason, replacement_id=None, *, recorded_at=None):
        self._conn.execute(
            'INSERT INTO claim_events(claim_id,action,reason,recorded_at,replacement_id) VALUES (?,?,?,?,?)',
            (claim_id, action, reason, recorded_at or self._knowledge_time(), replacement_id))

    @_transactional
    def create_claim(self, content: str, kind: str, reason: str, *, scope: str = 'global',
                     asserted_by: str | None = None, statement_at: str | None = None,
                     valid_from: str | None = None, valid_until: str | None = None,
                     security_sensitive: bool = False) -> dict:
        """Record a proposed judgment; no inference of truth, occurrence or adoption."""
        from .storage import _looks_injected
        content, reason, scope = text(content, 'content'), text(reason, 'reason'), text(scope, 'scope')
        if kind not in CLAIM_KINDS:
            raise ValueError(f'kind must be one of {CLAIM_KINDS}')
        asserted_by = optional_text(asserted_by, 'asserted_by')
        statement_at = event_time(statement_at, 'statement_at')
        valid_from, valid_until = time_range(valid_from, valid_until)
        cid = uuid.uuid4().hex[:12]
        self._conn.execute(
            'INSERT INTO claims(id,content,kind,scope,asserted_by,statement_at,valid_from,valid_until,'
            'recorded_at,security_sensitive) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (cid, content, kind, scope, asserted_by, statement_at, valid_from, valid_until,
             self._knowledge_time(), int(bool(security_sensitive or _looks_injected(content)))))
        self._claim_event(cid, 'proposed', reason)
        self._log(cid, 'create_claim', reason)
        return self._claim_view(cid)

    @_transactional
    def add_evidence(self, claim_id: str, memory_id: str, stance: str, reason: str, *,
                     quote: str | None = None, locator: str | None = None) -> dict:
        """Associate material with one claim. Origin grouping is caller-asserted."""
        text(reason, 'reason')
        optional_text(quote, 'quote')
        optional_text(locator, 'locator')
        if stance not in STANCES:
            raise ValueError(f'stance must be one of {STANCES}')
        claim = self._claim_view(claim_id)
        if claim['status'] in ('withdrawn', 'superseded'):
            raise ValueError('cannot add evidence to a withdrawn or superseded claim; create a new claim')
        memory = self.get(memory_id)
        if memory is None:
            raise KeyError(f'no such memory: {memory_id}')
        if memory.is_forgotten:
            raise ValueError('cannot use a forgotten memory as evidence')
        if quote is not None and quote not in memory.content:
            raise ValueError('quote must be a verbatim substring of the material; use reason for interpretation')
        existing = self._conn.execute(
            'SELECT * FROM claim_evidence WHERE claim_id=? AND memory_id=? AND stance=? AND retracted_at IS NULL',
            (claim_id, memory_id, stance)).fetchone()
        if existing:
            return dict(existing)
        eid = uuid.uuid4().hex[:12]
        self._conn.execute(
            'INSERT INTO claim_evidence(id,claim_id,memory_id,stance,reason,quote,locator,origin_id,recorded_at) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (eid, claim_id, memory_id, stance, reason, quote, locator, memory.origin_id, self._knowledge_time()))
        self._claim_event(claim_id, 'evidence_added', f'evidence={eid}: {reason}')
        self._log(claim_id, 'add_evidence', f'evidence={eid} memory={memory_id} stance={stance}: {reason}')
        return dict(self._conn.execute('SELECT * FROM claim_evidence WHERE id=?', (eid,)).fetchone())

    @_transactional
    def retract_evidence(self, evidence_id: str, reason: str) -> dict:
        text(reason, 'reason')
        row = self._conn.execute('SELECT * FROM claim_evidence WHERE id=?', (evidence_id,)).fetchone()
        if row is None:
            raise KeyError(f'no such evidence: {evidence_id}')
        if row['retracted_at'] is None:
            self._conn.execute('UPDATE claim_evidence SET retracted_at=?,retraction_reason=? WHERE id=?',
                               (self._knowledge_time(), reason, evidence_id))
            self._claim_event(row['claim_id'], 'evidence_retracted', f'evidence={evidence_id}: {reason}')
            self._log(row['claim_id'], 'retract_evidence', f'evidence={evidence_id}: {reason}')
        # Use the same access overlay as claim inspection: no forgotten quote leaks.
        viewed = self._claim_view(row['claim_id'])
        if viewed['redacted']:
            return {'id': evidence_id, 'claim_id': row['claim_id'], 'redacted': True}
        return dict(self._conn.execute('SELECT * FROM claim_evidence WHERE id=?', (evidence_id,)).fetchone())

    def _require_adoptable(self, claim):
        if claim['status'] not in ('proposed', 'adopted'):
            raise ValueError(f"cannot adopt a {claim['status']} claim; create a new claim")
        if claim['redacted']:
            raise ValueError('claim has forgotten source material')
        if any(issue['issue'] == 'insufficient_corroboration' for issue in claim['source_issues']):
            raise PermissionError('claim requires independent corroboration of sensitive supporting material')
        if claim['source_issues']:
            raise ValueError(f"claim has unresolved source issues: {claim['source_issues']}")
        if not claim['support']['supporting_records']:
            raise ValueError('adoption requires active supporting evidence for this claim')

    @_transactional
    def adopt_claim(self, claim_id: str, reason: str) -> dict:
        """Record adoption/review, not a certification that the assertion is true."""
        text(reason, 'reason')
        claim = self._claim_view(claim_id)
        self._require_adoptable(claim)
        self._claim_event(claim_id, 'adopted', reason)
        self._log(claim_id, 'adopt_claim', reason)
        return self._claim_view(claim_id)

    @_transactional
    def revise_claim(self, claim_id: str, replacement_id: str, reason: str) -> dict:
        """Atomically adopt a proposed replacement and retire the old judgment."""
        text(reason, 'reason')
        old, new = self._claim_view(claim_id), self._claim_view(replacement_id)
        if old['status'] != 'adopted':
            raise ValueError('only an adopted claim can be revised')
        if new['status'] != 'proposed':
            raise ValueError('replacement must be a proposed claim')
        if old['scope'] != new['scope']:
            raise ValueError('revision must stay within the same scope')
        self._require_adoptable(new)
        now = self._knowledge_time()
        self._claim_event(replacement_id, 'adopted', reason, recorded_at=now)
        self._claim_event(claim_id, 'superseded', reason, replacement_id, recorded_at=now)
        self._log(claim_id, 'revise_claim', f'replacement={replacement_id}: {reason}')
        return self._claim_view(replacement_id)

    @_transactional
    def withdraw_claim(self, claim_id: str, reason: str) -> dict:
        text(reason, 'reason')
        claim = self._claim_view(claim_id)
        if claim['status'] not in ('proposed', 'adopted'):
            raise ValueError(f"cannot withdraw a {claim['status']} claim")
        self._claim_event(claim_id, 'withdrawn', reason)
        self._log(claim_id, 'withdraw_claim', reason)
        return self._claim_view(claim_id)

    def _claim_view(self, claim_id, as_of=None):
        claim = self._claim_row(claim_id)
        if as_of is not None and claim['recorded_at'] > as_of:
            return None
        events = [dict(r) for r in self._conn.execute(
            'SELECT * FROM claim_events WHERE claim_id=? ORDER BY sequence', (claim_id,))
                  if as_of is None or r['recorded_at'] <= as_of]
        states = [e for e in events if e['action'] in STATE_ACTIONS]
        state = states[-1] if states else {'action': 'proposed', 'sequence': 0, 'replacement_id': None}
        claim.update(status=state['action'], replacement_id=state['replacement_id'],
                     as_of=as_of, time_basis='system_recorded_time', current_access_policy_applied=True)
        all_evidence = [dict(r) for r in self._conn.execute(
            'SELECT * FROM claim_evidence WHERE claim_id=? ORDER BY recorded_at,id', (claim_id,))]
        # A later withdrawal of material overrides historical discovery. Even a
        # retracted citation may have contributed to retained derived text.
        redacted = any(self.get(e['memory_id']) is None or self.get(e['memory_id']).is_forgotten
                       for e in all_evidence)
        if redacted:
            return {key: claim[key] for key in ('id', 'scope', 'kind', 'status', 'recorded_at', 'as_of', 'time_basis')} | {
                'content': None, 'redacted': True, 'eligible_for_recall': False,
                'review_required': True, 'current_access_policy_applied': True,
                'warning': 'Derived text withheld because source material is currently unavailable.'}
        evidence, issues = [], []
        for item in all_evidence:
            if as_of is not None and item['recorded_at'] > as_of:
                continue
            if as_of is not None and item['retracted_at'] is not None and item['retracted_at'] > as_of:
                item['retracted_at'] = item['retraction_reason'] = None
            item['active'] = item['retracted_at'] is None
            if item['active']:
                issues.extend(self._narrative_sources([item['memory_id']], bool(claim['security_sensitive'])))
            evidence.append(item)
        active = [e for e in evidence if e['active']]
        supporting = [e for e in active if e['stance'] == 'supports']
        claim['support'] = {
            'supporting_records': len(supporting),
            'challenging_records': sum(e['stance'] == 'challenges' for e in active),
            'origin_groups': sorted({e['origin_id'] for e in supporting if e['origin_id'] is not None}),
            'unknown_origin_records': sum(e['origin_id'] is None for e in supporting),
            'independence_verified': False,
            'basis': 'caller_asserted_associations_and_common_origins',
        }
        claim['review_required'] = state['action'] == 'adopted' and (
            any(e['sequence'] > state['sequence'] for e in events) or bool(issues) or not supporting)
        claim.update(events=events, evidence=evidence, source_issues=issues, redacted=False,
                     security_sensitive=bool(claim['security_sensitive']))
        claim['eligible_for_recall'] = claim['status'] == 'adopted' and not claim['review_required']
        return claim

    @_read_snapshot
    def inspect_claim(self, claim_id: str, as_of: str | None = None) -> dict | None:
        """Inspect claim/evidence history, with current source-access restrictions."""
        return self._claim_view(claim_id, event_time(as_of, 'as_of'))

    @_read_snapshot
    def recall_claims(self, query: str | None = None, limit: int = 10, scope: str | None = None,
                      as_of: str | None = None, valid_at: str | None = None,
                      include_inactive: bool = False) -> dict:
        """Discover recorded judgments; default is usable, adopted claims only."""
        check_limit(limit)
        optional_text(query, 'query')
        optional_text(scope, 'scope')
        if type(include_inactive) is not bool:
            raise ValueError('include_inactive must be a boolean')
        as_of, valid_at = event_time(as_of, 'as_of'), event_time(valid_at, 'valid_at')
        rows = self._conn.execute('SELECT id,scope FROM claims ORDER BY recorded_at DESC,id').fetchall()
        candidates = []
        for row in rows:
            if scope is not None and row['scope'] != scope:
                continue
            claim = self._claim_view(row['id'], as_of)
            if claim is None or claim['redacted'] or (not include_inactive and not claim['eligible_for_recall']):
                continue
            if valid_at is not None and (
                (claim['valid_from'] is not None and valid_at < claim['valid_from']) or
                (claim['valid_until'] is not None and valid_at >= claim['valid_until'])
            ):
                continue
            candidates.append(claim)
        if query is not None:
            candidates = self._rank_by_query(candidates, query)
        return {'claims': candidates[:limit], 'eligible_count': len(candidates),
                'truncated': len(candidates) > limit, 'scope': scope, 'as_of': as_of, 'valid_at': valid_at,
                'time_basis': 'system_recorded_time', 'current_access_policy_applied': True,
                'warning': 'Adoption records a judgment, not verified truth or what a person knew. '
                           'Legacy material history is not replayed; absent validity bounds are unknown.'}

    def _invalidate_claims_for_memory(self, memory_id, reason, only_if_source_unusable=False):
        ids = [r[0] for r in self._conn.execute(
            'SELECT DISTINCT claim_id FROM claim_evidence WHERE memory_id=? AND retracted_at IS NULL',
            (memory_id,))]
        for cid in ids:
            claim = self._claim_view(cid)
            if claim['status'] in ('withdrawn', 'superseded'):
                continue
            if only_if_source_unusable and not (claim['redacted'] or claim['source_issues']):
                continue
            self._claim_event(cid, 'source_changed', f'memory={memory_id}: {reason}')
            self._log(cid, 'claim_source_changed', f'memory={memory_id}: {reason}')
