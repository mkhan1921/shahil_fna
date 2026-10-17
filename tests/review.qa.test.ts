/**
 * Edge cases of the review overlay found in QA (spec §11): a slot the reviewer rejected and then
 * states again, two live statements about one field, what a mark-unknown keeps for the label of
 * §9.3, an empty package, refusals from the API, sessions saved by an earlier build, package
 * coherence, the API client's retries and the queue's D and A keys.
 */
import { describe, expect, it, vi } from 'vitest';
import { normaliseDocument } from '@/lib/fna/defaults';
import { createReviewApi, type ReviewApiSettings } from '@/lib/review/api';
import { coherence } from '@/lib/review/exchange';
import { describeErrors, validate } from '@/lib/review/jsonSchema';
import { getPath } from '@/lib/review/paths';
import { buildQueue, flowStep, progress, queueKey, queueOrder } from '@/lib/review/queue';
import { applyAction, canSubmit, createSession, markSent, normaliseSession, refusedFacts, ReviewError, submit, toResult, unsentOps, type ReviewAction, type ReviewSession } from '@/lib/review/session';
import type { ReviewBundle, ReviewPackage, StateOp } from '@/lib/review/types';
import { F, loadPackage, readJson, schema, STAMP } from './reviewFixture';

const bundle = loadPackage().bundle;
const KAGISO = 'b4db1d21-aadf-4118-8b47-437035cf7952';
const NEO = 'a82e57cb-1f9a-4f5c-a128-fec7003ceaae';
const fresh = () => createSession(bundle, { docId: bundle.median_document.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
const act = (s: ReviewSession, ...actions: ReviewAction[]) => actions.reduce((acc, a) => applyAction(acc, a, STAMP), s);
const payloads = schema('op_payloads');
const PAYLOAD_DEF: Record<StateOp['op'], string> = { ADD: 'AddPayload', UPDATE: 'UpdatePayload', RETRACT: 'RetractPayload', CONFIRM: 'ConfirmPayload', MARK_UNKNOWN: 'MarkUnknownPayload' };
const expectValidOps = (ops: StateOp[]) => {
  for (const op of ops) expect(describeErrors(validate(payloads, op.payload, `#/$defs/${PAYLOAD_DEF[op.op]}`)), `${op.op} ${op.fact_id}`).toBe('');
};

describe('re-attribution onto a slot that already has a statement', () => {
  it('refuses while that statement is live, so two values are never merged into one nobody said', () => {
    // Kagiso's tertiary cost came from the call; Neo's did too.
    expect(() => act(fresh(), { type: 'reattribute', factId: F.neoTertiaryCost, to: { kind: 'instance', instance: 'person.kagiso_mokoena_2' } })).toThrow(ReviewError);
    expect(() => act(fresh(), { type: 'reattribute', factId: F.neoTertiaryCost, to: { kind: 'instance', instance: 'person.kagiso_mokoena_2' } })).toThrow(/already has a value/);
  });

  it('replaces a rejected statement there, keeping its prior, as the pipeline’s reducer does', () => {
    const s = act(
      fresh(),
      { type: 'reject', factId: F.tertiaryCost, reason: 'wrong_person' },
      { type: 'reattribute', factId: F.neoTertiaryCost, to: { kind: 'instance', instance: 'person.kagiso_mokoena_2' } },
    );
    expectValidOps(s.ops);
    const moved = s.facts[F.tertiaryCost];
    expect(moved).toMatchObject({ origin: 'reviewer', review: { status: 'pending' }, moved: false, priorText: 'R150,000 a year' });
    expect(moved.hypotheses).toEqual(s.facts[F.neoTertiaryCost].hypotheses);
    expect(s.facts[F.neoTertiaryCost].moved).toBe(true);
    expect(getPath(s.working, `dependants[${KAGISO}].tertiaryCostAnnual`)).toBe(getPath(fresh().working, `dependants[${NEO}].tertiaryCostAnnual`));
  });

  it('moving a fact away and back again revives it as one pending statement', () => {
    const away = act(fresh(), { type: 'reattribute', factId: F.schoolFees, to: { kind: 'instance', instance: 'person.kagiso_mokoena_2' } });
    const back = act(away, { type: 'reattribute', factId: 'dependants[].schoolFeesAnnual#person.kagiso_mokoena_2', to: { kind: 'instance', instance: 'person.neo_mokoena_1' } });
    expectValidOps(back.ops);
    expect(back.ops.map((o) => o.op)).toEqual(['RETRACT', 'ADD', 'RETRACT', 'ADD']);
    const neo = back.facts[F.schoolFees];
    expect(neo).toMatchObject({ moved: false, review: { status: 'pending' }, priorText: bundle.sidecar.fields[`dependants[${NEO}].schoolFeesAnnual`].prior_text });
    expect(neo.hypotheses).toHaveLength(1);
    // Neo shows the call's figure again; Kagiso falls back to the file's.
    expect(getPath(back.working, `dependants[${NEO}].schoolFeesAnnual`)).toBe(105_000);
    expect(getPath(away.working, `dependants[${NEO}].schoolFeesAnnual`)).toBe(98_000);
    expect(getPath(back.working, `dependants[${KAGISO}].schoolFeesAnnual`)).toBe(34_000);
    expect(buildQueue(back).needs.concat(buildQueue(back).quick).map((f) => f.factId)).toContain(F.schoolFees);
  });
});

describe('marking a fact unknown keeps what was there', () => {
  it('records the original value and status for the label of §9.3', () => {
    const s = act(fresh(), { type: 'setStatus', factId: F.bond, status: 'explicitly_unknown', note: 'statement to follow' });
    const f = s.facts[F.bond];
    expect(f.status).toBe('explicitly_unknown');
    expect(f.review).toMatchObject({ status: 'edited', original_status: 'stated', note: 'statement to follow' });
    expect(f.review.original_value).toEqual(fresh().facts[F.bond].quantity);
    expectValidOps(s.ops);
    // A later decision on the same fact keeps the originals on record.
    const t = act(s, { type: 'setStatus', factId: F.bond, status: 'stated', value: { mode: 'point', value: 1_850_000 } });
    expect(t.facts[F.bond].review).toMatchObject({ status: 'edited', original_status: 'stated' });
    expect(t.facts[F.bond].review.original_value).toEqual(fresh().facts[F.bond].quantity);
    expect(act(s, { type: 'reject', factId: F.bond, reason: 'misheard' }).facts[F.bond].review).toMatchObject({ status: 'rejected', original_status: 'stated' });
  });
});

describe('an empty review package', () => {
  const empty: ReviewBundle = {
    ...bundle,
    sidecar: { ...bundle.sidecar, fields: {}, blocking_flags: [], followups: [] },
    blocking_flags: [],
    followups: [],
    transcript: { ...bundle.transcript, segments: [], numeric_spans: [], quality: null },
    preview_forecast: null,
    quality: null,
  };

  it('opens, has nothing to review, and can be submitted as it stands', () => {
    const s = createSession(empty, { docId: empty.median_document.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
    const q = buildQueue(s);
    expect([q.flags, q.needs, q.quick, q.collapsed].every((g) => g.length === 0)).toBe(true);
    expect(progress(s, q)).toEqual({ total: 0, decided: 0, needs: 0, needsDecided: 0 });
    expect(queueOrder(q, true)).toEqual([]);
    expect(flowStep(s, [], null, 'accept')).toBeNull();
    expect(canSubmit(s)).toBe(true);
    const done = submit(s, STAMP.at);
    const result = toResult(done, { submitted: true, at: STAMP.at });
    expect(describeErrors(validate(schema('review_result'), result))).toBe('');
    expect(result.ops).toEqual([]);
    expect(coherence(empty)).toEqual([]);
  });
});

describe('ops the API refused', () => {
  it('are recorded once, by the app’s seq, and nothing is sent twice', () => {
    const s = act(fresh(), { type: 'accept', factId: F.income }, { type: 'accept', factId: F.smoker }, { type: 'reject', factId: F.bond, reason: 'misheard' });
    const [a, b, c] = s.ops.map((o) => o.seq);
    const sent = markSent(s, c, [{ seq: b, reason: 'CONFIRM names unknown fact' }], STAMP.at);
    expect(unsentOps(sent)).toEqual([]);
    expect(sent.refused).toEqual([{ seq: b, reason: 'CONFIRM names unknown fact', at: STAMP.at }]);
    const again = markSent(sent, c, [{ seq: b, reason: 'CONFIRM names unknown fact' }], '2026-10-16T10:00:00Z');
    expect(again.refused).toHaveLength(1);
    expect(refusedFacts(again).map((r) => [r.op.seq, r.fact?.factId, r.reason])).toEqual([[b, F.smoker, 'CONFIRM names unknown fact']]);
    expect(a).toBeLessThan(b);
    // The refusal is a matter of record, not a block: the review still submits by file or API.
    expect(canSubmit(act(again, { type: 'clearFlag', flagId: 'flag-bond-scale', resolution: 'dismissed', note: 'statement' }))).toBe(false); // other flags remain
  });

  it('a session saved before refusals were recorded loads with the new fields', () => {
    const old = { ...fresh() } as Partial<ReviewSession>;
    delete old.refused;
    delete old.conflicts;
    const s = normaliseSession(old as ReviewSession);
    expect(s.refused).toEqual([]);
    expect(s.conflicts).toEqual([]);
    expect(refusedFacts(s)).toEqual([]);
  });
});

describe('package coherence', () => {
  it('catches a sidecar of another profile version, a fact at two paths, and a row the client file lacks', () => {
    const b = structuredClone(bundle);
    b.sidecar.profile_version = b.profile_version + 1;
    const [path, field] = Object.entries(b.sidecar.fields).find(([p]) => p.startsWith(`dependants[${KAGISO}].`))!;
    b.sidecar.fields[`dependants[00000000-0000-4000-8000-000000000000].${path.split('.').pop()}`] = structuredClone(field);
    const problems = coherence(b);
    expect(problems).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/profile version/),
        expect.stringMatching(new RegExp(`fact ${field.fact_id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} stands at two paths`)),
        expect.stringMatching(/dependants\[00000000-0000-4000-8000-000000000000\].* is a row the client file does not have/),
      ]),
    );
    expect(coherence(bundle)).toEqual([]);
    expect(coherence(readJson<ReviewPackage>('tests', 'fixtures', 'pipeline_review_package.json').bundle)).toEqual([]);
    const other = structuredClone(bundle);
    other.prior_document = { ...normaliseDocument(structuredClone(other.prior_document!)), id: 'someone-else' };
    expect(coherence(other)).toEqual([expect.stringMatching(/different clients/)]);
  });
});

describe('the API client', () => {
  const ORIGINS = ['https://intel.practice.co.za'];
  const ON: ReviewApiSettings = { enabled: true, baseUrl: 'https://intel.practice.co.za/api', token: 'tok', reviewerId: 'adviser-sipho' };
  const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  vi.stubEnv('NEXT_PUBLIC_ADVISOR_INTEL_ORIGINS', ORIGINS.join(','));

  it('names the reviewer on every op batch and in a header, so a resend is applied once', async () => {
    const fetchImpl = vi.fn(async () => reply(200, { applied: [12], rejected: [] }));
    const ops = act(fresh(), { type: 'accept', factId: F.income }).ops;
    await createReviewApi(ON, fetchImpl).postOps('c1', ops);
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ reviewer_id: 'adviser-sipho', ops });
    expect(init.headers).toMatchObject({ 'X-Reviewer-Id': 'adviser-sipho' });
  });

  it('tries once more when the API cannot be reached or is briefly unavailable, and not otherwise', async () => {
    let n = 0;
    const flaky = vi.fn(async () => (++n === 1 ? reply(503, { detail: 'restarting' }) : reply(200, { ok: true })));
    const wait = vi.fn(async () => {});
    expect(await createReviewApi(ON, flaky, wait).getReview('c1')).toEqual({ ok: true });
    expect(flaky).toHaveBeenCalledTimes(2);
    expect(wait).toHaveBeenCalledTimes(1);
    let m = 0;
    const down = vi.fn(async () => {
      if (++m === 1) throw new TypeError('Failed to fetch');
      return reply(200, { ok: 1 });
    });
    expect(await createReviewApi(ON, down, wait).getReview('c1')).toEqual({ ok: 1 });
    const refused = vi.fn(async () => reply(409, { detail: { message: 'blocking flags must be cleared', open_flags: [] } }));
    await expect(createReviewApi(ON, refused, wait).submit('c1', toResult(fresh(), { submitted: true, at: STAMP.at }))).rejects.toThrow(/refused the request \(409\): blocking flags must be cleared/);
    expect(refused).toHaveBeenCalledTimes(1);
    const unauthorised = vi.fn(async () => reply(401, { detail: 'a valid bearer token is required' }));
    await expect(createReviewApi(ON, unauthorised, wait).getReview('c1')).rejects.toThrow(/did not accept the practice’s token \(401\)/);
    const missing = vi.fn(async () => reply(404, { detail: 'no call c1' }));
    await expect(createReviewApi(ON, missing, wait).getReview('c1')).rejects.toThrow(/does not know this call \(404\)/);
  });

  it('reads the meeting note and records who answered a follow-up', async () => {
    const note = { call_id: 'c1', run_id: 'r1', profile_version: 3, sentences: [{ section: 'concerns', text: 'Thabo is worried about fees.', fact_ids: [F.tertiaryCost] }], text: 'Client concerns\nThabo is worried about fees.', narrative_fields: { 'engagement.clientConcerns': 'Thabo is worried about fees.' } };
    const fetchImpl = vi.fn(async () => reply(200, note));
    const api = createReviewApi(ON, fetchImpl);
    expect((await api.getNote('c1')).narrative_fields['engagement.clientConcerns']).toBe('Thabo is worried about fees.');
    await api.answerFollowup('c1', 'fu-1', { text: 'About 62', source: 'followup' });
    const calls = fetchImpl.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toBe('https://intel.practice.co.za/api/calls/c1/note');
    expect(JSON.parse(String(calls[1][1].body))).toEqual({ text: 'About 62', source: 'followup', recorded_by: 'adviser-sipho' });
  });
});

describe('the queue’s D and A keys', () => {
  it('D opens the dismissal of the flag in view; A accepts every undecided “Check quickly” fact', () => {
    const s = fresh();
    const q = buildQueue(s);
    const order = queueOrder(q, false);
    expect(queueKey({ key: 'd' })).toBe('dismiss');
    expect(queueKey({ key: 'A' })).toBe('acceptQuick');
    expect(flowStep(s, order, 'flag:flag-bond-scale', 'dismiss')).toEqual({ do: 'dismissFlag', flagId: 'flag-bond-scale' });
    expect(flowStep(s, order, F.target, 'dismiss')).toBeNull();
    const step = flowStep(s, order, null, 'acceptQuick');
    expect(step).toMatchObject({ do: 'acceptGroup', group: 'quick' });
    if (step?.do !== 'acceptGroup') throw new Error('expected acceptGroup');
    expect(step.factIds).toEqual(q.quick.map((f) => f.factId));
    const done = act(s, { type: 'acceptMany', factIds: step.factIds });
    expect(flowStep(done, order, null, 'acceptQuick')).toBeNull();
    expect(flowStep(submit(act(done, { type: 'clearFlag', flagId: 'flag-bond-scale', resolution: 'dismissed', note: 'x' }, ...openFlagAccepts(done)), STAMP.at), order, 'flag:flag-bond-scale', 'dismiss')).toBeNull();
  });
});

/** Accept every remaining flagged fact, so the review can be submitted in a test. */
const openFlagAccepts = (s: ReviewSession): ReviewAction[] =>
  s.flags.filter((f) => !f.cleared && f.flag_id !== 'flag-bond-scale').map((f) => (f.kind === 'tier_a_missing' || f.kind === 'tier_a_contradicted' ? { type: 'clearFlag', flagId: f.flag_id, resolution: 'marked_unknown' } : { type: 'clearFlag', flagId: f.flag_id, resolution: 'accepted' }));
