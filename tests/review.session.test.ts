import { produce } from 'immer';
import { describe, expect, it } from 'vitest';
import { analyse } from '@/lib/fna/analysis';
import { normaliseDocument } from '@/lib/fna/defaults';
import type { FnaDocument } from '@/lib/fna/types';
import { describeErrors, validate } from '@/lib/review/jsonSchema';
import { getPath } from '@/lib/review/paths';
import {
  applyAction,
  canSubmit,
  commitPending,
  createSession,
  editDocument,
  entityOfRow,
  factAt,
  openFlags,
  ReviewError,
  submit,
  toResult,
  type ReviewAction,
  type ReviewSession,
} from '@/lib/review/session';
import type { StateOp } from '@/lib/review/types';
import { rowIdForNewEntity } from '@/lib/review/uuid';
import { F, loadPackage, schema, STAMP } from './reviewFixture';

const pkg = loadPackage();
const bundle = pkg.bundle;
const KAGISO = 'b4db1d21-aadf-4118-8b47-437035cf7952';
const NEO = 'a82e57cb-1f9a-4f5c-a128-fec7003ceaae';
const CARD = '266b6809-9095-4f85-8b8b-73e33312c1dd';
const RA_ROW = '320970a9-fb2b-4abc-abf9-fccc58beab15';

const fresh = () => createSession(bundle, { docId: bundle.median_document.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
const act = (s: ReviewSession, ...actions: ReviewAction[]) => actions.reduce((acc, a) => applyAction(acc, a, STAMP), s);
const payloads = schema('op_payloads');
const PAYLOAD_DEF: Record<StateOp['op'], string> = { ADD: 'AddPayload', UPDATE: 'UpdatePayload', RETRACT: 'RetractPayload', CONFIRM: 'ConfirmPayload', MARK_UNKNOWN: 'MarkUnknownPayload' };
const expectValidOps = (ops: StateOp[]) => {
  for (const op of ops) expect(describeErrors(validate(payloads, op.payload, `#/$defs/${PAYLOAD_DEF[op.op]}`)), `${op.op} ${op.fact_id}`).toBe('');
};

describe('starting a review', () => {
  const s = fresh();

  it('shows the pipeline’s median document and indexes every fact by its path', () => {
    expect(s.working).toEqual(normaliseDocument(structuredClone(bundle.median_document)));
    expect(factAt(s, 'income.client.grossMonthly')?.factId).toBe(F.income);
    expect(factAt(s, `dependants[${KAGISO}].tertiaryCostAnnual`)?.factId).toBe(F.tertiaryCost);
    expect(openFlags(s)).toHaveLength(bundle.blocking_flags.length);
    expect(canSubmit(s)).toBe(false);
  });

  it('knows the rows the call created by the ids the pipeline gave them', () => {
    const policyRow = rowIdForNewEntity(bundle.call_id, 'policy.group_disability_new');
    expect(getPath(s.working, `policies[${policyRow}].type`)).toBe('disability-lump');
    expect(entityOfRow(s, 'policies', policyRow)).toBe('policy.group_disability_new');
    expect(entityOfRow(s, 'dependants', KAGISO)).toBe('person.kagiso_mokoena_2');
  });
});

describe('each reviewer action is a stamped, numbered op, and the sheet follows it', () => {
  it('accept: CONFIRM after the profile’s last op, and the flag on the fact clears', () => {
    const s = act(fresh(), { type: 'accept', factId: F.income });
    expect(s.ops).toHaveLength(1);
    const [op] = s.ops;
    expect(op).toMatchObject({ op: 'CONFIRM', fact_id: F.income, actor: 'reviewer', seq: bundle.sidecar.last_seq + 1, segment_id: null });
    expect(op.payload).toMatchObject({ review: { status: 'accepted', reviewer_id: STAMP.reviewerId, reviewed_at: STAMP.at } });
    expectValidOps(s.ops);
    expect(s.facts[F.income].review.status).toBe('accepted');
    expect(s.flags.find((f) => f.flag_id === 'flag-income-flip')).toMatchObject({ cleared: true, resolution: 'accepted', cleared_by: STAMP.reviewerId });
    expect(s.flagClearances).toEqual([{ flag_id: 'flag-income-flip', resolution: 'accepted', reviewer_id: STAMP.reviewerId, at: STAMP.at, note: '' }]);
    expect(s.working.income.client.grossMonthly).toBe(118_000);
  });

  it('edit with a range: UPDATE with a triangular, the original kept, the median in the sheet', () => {
    const s = act(fresh(), { type: 'edit', factId: F.retirementAge, value: { mode: 'range', low: 60, likely: 63, high: 65 } });
    const [op] = s.ops;
    expect(op.op).toBe('UPDATE');
    expect(op.payload).toMatchObject({ replace_hypotheses: [{ value: { dist: { kind: 'triangular', low: 60, mode: 63, high: 65 }, unit: 'age' }, weight: 1 }], review: { status: 'edited' } });
    expect(op.payload).not.toHaveProperty('status');
    expectValidOps(s.ops);
    const f = s.facts[F.retirementAge];
    expect(f.review.original_value?.dist).toEqual({ kind: 'triangular', low: 60, mode: 62, high: 65 });
    expect(f.valueText).toBe('60–65, most likely 63');
    expect(s.working.retirement.client.retirementAge).toBe(63);
    expect(s.flags.find((x) => x.flag_id === 'flag-retirement-age')?.resolution).toBe('edited');
  });

  it('edit with options: numbers become a discrete distribution, labels a categorical one', () => {
    const s = act(
      fresh(),
      { type: 'edit', factId: F.target, value: { mode: 'options', options: [{ value: 60, weight: 1 }, { value: 70, weight: 3 }] } },
      { type: 'edit', factId: F.schoolType, value: { mode: 'options', options: [{ value: 'private', weight: 30 }, { value: 'public', weight: 70 }] } },
    );
    expectValidOps(s.ops);
    // Target percent is a rate: its slot allows a mixture of points, not a discrete distribution.
    expect(s.facts[F.target].quantity?.dist).toEqual({ kind: 'mixture', components: [[0.25, { kind: 'point', value: 0.6 }], [0.75, { kind: 'point', value: 0.7 }]] });
    expect(s.working.retirement.client.targetPercent).toBe(0.7);
    expect(s.facts[F.schoolType].quantity?.dist).toEqual({ kind: 'categorical', probs: { private: 0.3, public: 0.7 } });
    expect(s.working.dependants.find((d) => d.id === KAGISO)?.schoolType).toBe('public');
    expect(s.facts[F.schoolType].change).toBe('confirmed');
  });

  it('reject: RETRACT with the reason, and the field falls back to the prior FNA', () => {
    const s = act(fresh(), { type: 'reject', factId: F.tertiaryCost, reason: 'misheard', note: 'She said one fifty, not four fifty' });
    expect(s.ops[0]).toMatchObject({ op: 'RETRACT', payload: { reason: 'misheard', note: 'She said one fifty, not four fifty', review: { status: 'rejected' } } });
    expectValidOps(s.ops);
    expect(s.working.dependants.find((d) => d.id === KAGISO)?.tertiaryCostAnnual).toBe(150_000);
    expect(s.facts[F.tertiaryCost].review).toMatchObject({ status: 'rejected', reason: 'misheard' });
  });

  it('change status: unknown is MARK_UNKNOWN; stated with a value is an UPDATE', () => {
    const s = act(
      fresh(),
      { type: 'setStatus', factId: F.disabilityCover, status: 'explicitly_unknown', note: 'HR to confirm' },
      { type: 'setStatus', factId: F.bonus, status: 'not_discussed' },
    );
    expect(s.ops.map((o) => o.op)).toEqual(['MARK_UNKNOWN', 'UPDATE']);
    expectValidOps(s.ops);
    expect(s.flags.find((f) => f.flag_id === 'flag-disability-cover')?.resolution).toBe('marked_unknown');
    expect(s.facts[F.bonus]).toMatchObject({ status: 'not_discussed', quantity: null });
    expect(s.working.income.client.annualBonus).toBe(100_000);
    const t = act(fresh(), { type: 'setStatus', factId: F.disabilityCover, status: 'stated', value: { mode: 'point', value: 1_500_000 } });
    expect(t.ops[0]).toMatchObject({ op: 'UPDATE', payload: { status: 'stated', replace_hypotheses: [{ value: { dist: { kind: 'point', value: 1_500_000 } } }] } });
    const row = rowIdForNewEntity(bundle.call_id, 'policy.group_disability_new');
    expect(getPath(t.working, `policies[${row}].cover`)).toBe(1_500_000);
  });

  it('re-attribute: RETRACT wrong_entity, then ADD the same hypotheses and evidence under the right child', () => {
    const s = act(fresh(), { type: 'reattribute', factId: F.schoolFees, to: { kind: 'instance', instance: 'person.kagiso_mokoena_2' } });
    expect(s.ops.map((o) => [o.op, o.fact_id])).toEqual([
      ['RETRACT', F.schoolFees],
      ['ADD', 'dependants[].schoolFeesAnnual#person.kagiso_mokoena_2'],
    ]);
    expectValidOps(s.ops);
    const add = s.ops[1];
    if (add.op !== 'ADD') throw new Error('expected an ADD');
    expect(add.payload).toMatchObject({ source: 'reviewer', instance_key: 'person.kagiso_mokoena_2', fna_path: `dependants[${KAGISO}].schoolFeesAnnual`, entity_refs: ['person.kagiso_mokoena_2'] });
    expect(add.payload.hypotheses).toEqual(bundle.sidecar.fields[`dependants[${NEO}].schoolFeesAnnual`].hypotheses.map((h) => ({ value: h.quantity, weight: h.weight, rationale: h.rationale, revises: h.revises })));
    expect(add.payload.evidence).toEqual(bundle.sidecar.fields[`dependants[${NEO}].schoolFeesAnnual`].evidence);
    expect(s.working.dependants.find((d) => d.id === NEO)?.schoolFeesAnnual).toBe(98_000);
    expect(s.working.dependants.find((d) => d.id === KAGISO)?.schoolFeesAnnual).toBe(105_000);
    expect(s.facts['dependants[].schoolFeesAnnual#person.kagiso_mokoena_2']).toMatchObject({ change: 'changed', priorText: 'R34,000 a year' });
  });

  it('re-attribute the speaker: an UPDATE of who said it', () => {
    const s = act(fresh(), { type: 'reattributeSpeaker', factId: F.target, attribution: 'advisor_confirmed_by_client' });
    expect(s.ops[0]).toMatchObject({ op: 'UPDATE', payload: { speaker_attribution: 'advisor_confirmed_by_client', review: { status: 'edited', reason: 'wrong_speaker' } } });
    expectValidOps(s.ops);
  });

  it('add a missed fact with a transcript span: ADD from the reviewer, then CONFIRM, on a row with the pipeline’s id', () => {
    const seg = bundle.transcript.segments.find((x) => x.segment_id === 's0023')!;
    const start = seg.text.indexOf('through work');
    const s = act(fresh(), {
      type: 'addFact',
      slotId: 'policies[].premiumMonthly',
      to: { kind: 'new-row' },
      value: { mode: 'point', value: 420 },
      evidence: [{ segment_id: 's0023', char_start: start, char_end: start + 12, quote: 'through work', speaker_role: 'client_primary', t_start: seg.t_start, t_end: seg.t_end, asr_mean_conf: 0.8, role: 'supports', aligner_score: null }],
    });
    expect(s.ops.map((o) => o.op)).toEqual(['ADD', 'CONFIRM']);
    expectValidOps(s.ops);
    expect(s.ops[0].segment_id).toBe('s0023');
    const entity = s.reviewerEntities[0];
    expect(entity.rowId).toBe(rowIdForNewEntity(bundle.call_id, entity.entityId));
    expect(getPath(s.working, `policies[${entity.rowId}].premiumMonthly`)).toBe(420);
    expect(s.facts[`policies[].premiumMonthly#${entity.entityId}`]).toMatchObject({ origin: 'reviewer', review: { status: 'accepted' } });
  });

  it('remove a prior row: RETRACT removed on its identifying field, and the row leaves the sheet', () => {
    const s = act(fresh(), { type: 'removeRow', factId: 'liabilities[].type#liability.credit_card_3', note: 'Paid off in August' });
    expect(s.ops[0]).toMatchObject({ op: 'RETRACT', payload: { reason: 'removed' } });
    expect(s.working.liabilities.some((l) => l.id === CARD)).toBe(false);
    expect(() => act(fresh(), { type: 'removeRow', factId: 'liabilities[].balance#liability.credit_card_3' })).toThrow(ReviewError);
  });

  it('accept all in a group: one CONFIRM each, skipping facts already decided', () => {
    const s = act(fresh(), { type: 'accept', factId: F.smoker }, { type: 'acceptMany', factIds: [F.smoker, F.groceries, F.spouseIncome] });
    expect(s.ops.map((o) => [o.op, o.fact_id])).toEqual([
      ['CONFIRM', F.smoker],
      ['CONFIRM', F.groceries],
      ['CONFIRM', F.spouseIncome],
    ]);
    expect(s.ops.map((o) => o.seq)).toEqual([1, 2, 3].map((n) => bundle.sidecar.last_seq + n));
  });

  it('confirm a term: a glossary record, the type corrected when the adviser says so, and its flag cleared', () => {
    const s = act(fresh(), { type: 'confirmTerm', factId: F.krugerType, canonicalName: 'Krugerrand gold bullion coins', productType: 'other', provider: '', confirmed: true });
    expect(s.termConfirmations).toEqual([{ term: 'Krugerrands', canonical_name: 'Krugerrand gold bullion coins', product_type: 'other', provider: '', confirmed: true, reviewer_id: STAMP.reviewerId, at: STAMP.at }]);
    expect(s.ops.map((o) => o.op)).toEqual(['CONFIRM']);
    expect(s.flags.find((f) => f.flag_id === 'flag-krugerrands')?.resolution).toBe('accepted');
    const t = act(fresh(), { type: 'confirmTerm', factId: F.krugerType, canonicalName: 'Krugerrand exchange traded fund', productType: 'shares', provider: 'Satrix', confirmed: false });
    expect(t.ops[0]).toMatchObject({ op: 'UPDATE', payload: { replace_hypotheses: [{ value: { dist: { kind: 'point', value: 'shares' } } }] } });
    const kruger = rowIdForNewEntity(bundle.call_id, 'property.krugerrands_new');
    expect(getPath(t.working, `assets[${kruger}].type`)).toBe('shares');
    expect(t.flags.find((f) => f.flag_id === 'flag-krugerrands')?.resolution).toBe('edited');
  });

  it('follow-ups: reword, drop and add are recorded for prompt tuning', () => {
    const s = act(
      fresh(),
      { type: 'followup', change: { action: 'reword', followupId: 'fu-ra-statement', question: 'Please send Thabo’s latest retirement annuity statement.' } },
      { type: 'followup', change: { action: 'drop', followupId: 'fu-retirement-income' } },
      { type: 'followup', change: { action: 'add', question: 'Has the new will been signed by both witnesses?', factIds: [F.spouseWill] } },
    );
    expect(s.followupEdits.map((e) => [e.followup_id, e.action])).toEqual([
      ['fu-ra-statement', 'reword'],
      ['fu-retirement-income', 'drop'],
      [null, 'add'],
    ]);
    expect(s.followups.find((q) => q.followup_id === 'fu-retirement-income')?.status).toBe('dropped');
    expect(s.followups.at(-1)).toMatchObject({ followup_id: 'added-1', status: 'open', fact_ids: [F.spouseWill] });
    expect(s.ops).toEqual([]);
  });
});

describe('blocking flags hold back submission', () => {
  it('until each is cleared by accepting, editing, marking unknown or dismissing with a reason', () => {
    let s = fresh();
    expect(() => submit(s, STAMP.at)).toThrow(/blocking flag/);
    expect(() => act(s, { type: 'clearFlag', flagId: 'flag-bond-scale', resolution: 'dismissed' })).toThrow(/why/);
    s = act(
      s,
      { type: 'clearFlag', flagId: 'flag-ra-contradicted', resolution: 'edited', value: { mode: 'point', value: 280_000 }, note: 'From the statement' },
      { type: 'clearFlag', flagId: 'flag-bond-scale', resolution: 'dismissed', note: 'The bond statement says R1.85m' },
      { type: 'clearFlag', flagId: 'flag-disability-cover', resolution: 'marked_unknown' },
      { type: 'clearFlag', flagId: 'flag-krugerrands', resolution: 'accepted' },
      { type: 'accept', factId: F.retirementAge },
      { type: 'accept', factId: F.income },
    );
    expect(openFlags(s)).toEqual([]);
    expect(s.flagClearances.map((c) => [c.flag_id, c.resolution])).toEqual([
      ['flag-ra-contradicted', 'edited'],
      ['flag-bond-scale', 'dismissed'],
      ['flag-disability-cover', 'marked_unknown'],
      ['flag-krugerrands', 'accepted'],
      ['flag-retirement-age', 'accepted'],
      ['flag-income-flip', 'accepted'],
    ]);
    expect(s.facts[F.ra]).toMatchObject({ status: 'stated', review: { status: 'edited', original_status: 'contradicted' } });
    expect(s.working.retirementFunds.find((f) => f.id === RA_ROW)?.value).toBe(280_000);
    expect(canSubmit(s)).toBe(true);
    const done = submit(s, '2026-10-16T09:41:00Z');
    expect(() => applyAction(done, { type: 'accept', factId: F.bonus }, STAMP)).toThrow(/submitted/);
    const result = toResult(done, { submitted: true, at: done.submittedAt! });
    expect(describeErrors(validate(schema('review_result'), result))).toBe('');
    expect(result).toMatchObject({ call_id: bundle.call_id, run_id: bundle.run_id, base_profile_version: bundle.profile_version, submitted: true });
  });
});

describe('editing the sheet directly', () => {
  const sheet = (s: ReviewSession, recipe: (d: FnaDocument) => void) => editDocument(s, produce(s.working, recipe), STAMP);

  it('waits for the field to lose focus, then edits the fact there', () => {
    let s = sheet(fresh(), (d) => void (d.expenses.items.groceries = 12));
    s = sheet(s, (d) => void (d.expenses.items.groceries = 12500));
    expect(s.ops).toEqual([]);
    expect(s.pending).toEqual(['expenses.items[groceries]']);
    expect(canSubmit(s)).toBe(false);
    s = commitPending(s, STAMP);
    expect(s.ops).toHaveLength(1);
    expect(s.ops[0]).toMatchObject({ op: 'UPDATE', fact_id: F.groceries, payload: { replace_hypotheses: [{ value: { dist: { kind: 'point', value: 12_500 }, unit: 'ZAR', period: 'monthly' } }] } });
    expect(s.working.expenses.items.groceries).toBe(12_500);
  });

  it('adds a fact where the pipeline had none: a recall miss', () => {
    const s = commitPending(sheet(fresh(), (d) => void (d.expenses.items.rent = 9_000)), STAMP);
    expect(s.ops.map((o) => [o.op, o.fact_id])).toEqual([
      ['ADD', 'expenses.items[]#rent'],
      ['CONFIRM', 'expenses.items[]#rent'],
    ]);
    expectValidOps(s.ops);
  });

  it('typing a value back to what the review holds makes no op; plain fields never do', () => {
    let s = sheet(fresh(), (d) => {
      d.income.client.grossMonthly = 120_000;
      d.client.email = 'thabo.m@example.co.za';
    });
    s = sheet(s, (d) => void (d.income.client.grossMonthly = 118_000));
    s = commitPending(s, STAMP);
    expect(s.ops).toEqual([]);
    expect(s.working.client.email).toBe('thabo.m@example.co.za');
  });

  it('gives a row added in the sheet the id the pipeline will give it, and anchors it with its identifying field', () => {
    let s = sheet(fresh(), (d) => void d.dependants.push({ ...d.dependants[0], id: 'local-tmp', name: 'Lindiwe', relationship: 'parent' }));
    const entity = s.reviewerEntities[0];
    expect(entity.rowId).toBe(rowIdForNewEntity(bundle.call_id, entity.entityId));
    expect(s.working.dependants.at(-1)?.id).toBe(entity.rowId);
    s = commitPending(s, STAMP);
    expect(s.ops.map((o) => [o.op, o.fact_id])).toContainEqual(['ADD', `dependants[].relationship#${entity.entityId}`]);
  });

  it('removing a prior row in the sheet retracts its identifying field', () => {
    const s = sheet(fresh(), (d) => void (d.liabilities = d.liabilities.filter((l) => l.id !== CARD)));
    expect(s.ops[0]).toMatchObject({ op: 'RETRACT', fact_id: 'liabilities[].type#liability.credit_card_3', payload: { reason: 'removed' } });
    expect(s.working.liabilities.some((l) => l.id === CARD)).toBe(false);
  });
});

describe('a client file changed on this device after it was exported for the call', () => {
  it('keeps the device’s changes the call did not touch and lists the ones it did', () => {
    const local = produce(normaliseDocument(structuredClone(bundle.prior_document!)), (d) => {
      d.client.mobile = '082 000 0000';
      d.income.client.grossMonthly = 110_000;
      d.expenses.items.rent = 2_000;
      d.engagement.clientConcerns = 'Worried about Kagiso’s fees.';
    });
    const s = createSession(bundle, { docId: local.id, reviewerId: STAMP.reviewerId, now: STAMP.at, local });
    expect(s.working.client.mobile).toBe('082 000 0000');
    expect(s.working.engagement.clientConcerns).toBe('Worried about Kagiso’s fees.');
    expect(s.working.expenses.items.rent).toBe(2_000);
    expect(s.ops.map((o) => o.fact_id)).toContain('expenses.items[]#rent');
    expect(s.working.income.client.grossMonthly).toBe(118_000);
    expect(s.conflicts).toEqual([{ path: 'income.client.grossMonthly', local: 110_000, call: 118_000 }]);
  });

  it('starting over on a device that holds an earlier review of the same call keeps one copy of each row the call made', () => {
    const earlier = act(fresh(), { type: 'reject', factId: F.krugerValue, reason: 'misheard' });
    const kruger = rowIdForNewEntity(bundle.call_id, 'property.krugerrands_new');
    expect(getPath(earlier.working, `assets[${kruger}].value`)).toBe(0);
    const again = createSession(bundle, { docId: bundle.prior_document!.id, reviewerId: STAMP.reviewerId, now: STAMP.at, local: earlier.working });
    expect(again.working.assets.filter((a) => a.id === kruger)).toHaveLength(1);
    expect(getPath(again.working, `assets[${kruger}].value`)).toBe(60_000);
  });
});

describe('the needs analysis follows the review', () => {
  it('runs on the working document, so a decision moves the needs at once', () => {
    const before = analyse(fresh().working, new Date('2026-10-15T12:00:00'));
    const after = analyse(act(fresh(), { type: 'reject', factId: F.income, reason: 'misheard' }).working, new Date('2026-10-15T12:00:00'));
    expect(before.persons.client!.death.status).toBe('shortfall');
    expect(after.persons.client!.death.status).toBe('partial');
  });
});
