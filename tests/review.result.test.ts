/**
 * A whole review of the demo call, every kind of decision, submitted. The review result and the
 * reviewed client file are kept as fixtures: the pipeline's contract test replays the result through
 * its reducer onto the same profile and checks it materialises to the same file
 * (pipeline/tests/test_review_contracts.py).
 */
import { produce } from 'immer';
import { describe, expect, it } from 'vitest';
import { describeErrors, validate } from '@/lib/review/jsonSchema';
import { buildQueue } from '@/lib/review/queue';
import { applyAction, canSubmit, commitPending, createSession, editDocument, isDecided, openFlags, submit, toResult, type ReviewAction } from '@/lib/review/session';
import { evidenceFromSpan } from '@/lib/review/transcript';
import { F, loadPackage, schema, STAMP } from './reviewFixture';

const bundle = loadPackage().bundle;
const at = (minute: number) => ({ reviewerId: STAMP.reviewerId, at: `2026-10-16T09:${String(30 + minute).padStart(2, '0')}:00Z` });

const review = () => {
  let s = createSession(bundle, { docId: bundle.prior_document!.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
  let minute = 0;
  const act = (...actions: ReviewAction[]) => {
    for (const a of actions) s = applyAction(s, a, at(minute++));
  };
  const seg = bundle.transcript.segments.find((x) => x.segment_id === 's0031')!;
  act(
    // The flags first.
    { type: 'accept', factId: F.income },
    { type: 'edit', factId: F.retirementAge, value: { mode: 'range', low: 60, likely: 63, high: 65 }, note: 'Thabo leans to 63' },
    { type: 'clearFlag', flagId: 'flag-bond-scale', resolution: 'edited', value: { mode: 'point', value: 1_850_000 }, note: 'R1.85m, as on the statement' },
    { type: 'clearFlag', flagId: 'flag-disability-cover', resolution: 'dismissed', note: 'Thabo will send the benefit schedule' },
    { type: 'confirmTerm', factId: F.krugerType, canonicalName: 'Krugerrand gold bullion coins', productType: 'other', provider: '', confirmed: true },
    { type: 'edit', factId: F.ra, value: { mode: 'point', value: 280_000 }, note: 'Lerato read it from the statement' },
    // Then the queue.
    { type: 'reject', factId: F.krugerValue, reason: 'misheard', note: 'He said sixty thousand rand of coins, but meant the insured value' },
    { type: 'reattribute', factId: F.schoolFees, to: { kind: 'instance', instance: 'person.kagiso_mokoena_2' } },
    { type: 'reattributeSpeaker', factId: F.target, attribution: 'advisor_confirmed_by_client' },
    { type: 'setStatus', factId: F.schoolType, status: 'implied', value: { mode: 'options', options: [{ value: 'private', weight: 70 }, { value: 'public', weight: 30 }] } },
    { type: 'removeRow', factId: 'liabilities[].type#liability.credit_card_3', note: 'Paid off in August' },
    {
      type: 'addFact',
      slotId: 'expenses.items[]',
      to: { kind: 'instance', instance: 'transport' },
      value: { mode: 'point', value: 4_200 },
      evidence: [evidenceFromSpan(bundle.transcript, 's0031', 0, seg.text.indexOf(','))],
    },
    { type: 'addFact', slotId: 'goals[].name', to: { kind: 'new-row' }, value: { mode: 'point', value: 'Kitchen renovation' } },
    { type: 'followup', change: { action: 'reword', followupId: 'fu-ra-statement', question: 'Please send Thabo’s latest retirement annuity statement.' } },
    { type: 'followup', change: { action: 'drop', followupId: 'fu-retirement-income' } },
    { type: 'followup', change: { action: 'add', question: 'Has the new will been signed by both witnesses?', factIds: [F.spouseWill] } },
  );
  const goal = s.reviewerEntities[0];
  act({ type: 'addFact', slotId: 'goals[].cost', to: { kind: 'instance', instance: goal.entityId }, value: { mode: 'range', low: 150_000, likely: 180_000, high: 220_000 } });
  // A figure typed straight into the sheet: an edit of the fact there once the field loses focus.
  s = commitPending(
    editDocument(
      s,
      produce(s.working, (d) => {
        d.expenses.items.groceries = 11_500;
      }),
      at(minute++),
    ),
    at(minute++),
  );
  const q = buildQueue(s);
  act({ type: 'acceptMany', factIds: q.quick.filter((f) => !isDecided(f)).map((f) => f.factId) });
  return s;
};

describe('a whole review, submitted', () => {
  const s = review();

  it('clears every flag and can be submitted', () => {
    expect(openFlags(s)).toEqual([]);
    expect(canSubmit(s)).toBe(true);
    expect(s.flagClearances.map((c) => [c.flag_id, c.resolution]).sort()).toEqual(
      [
        ['flag-bond-scale', 'edited'],
        ['flag-disability-cover', 'dismissed'],
        ['flag-income-flip', 'accepted'],
        ['flag-krugerrands', 'accepted'],
        ['flag-ra-contradicted', 'edited'],
        ['flag-retirement-age', 'edited'],
      ].sort(),
    );
  });

  it('numbers its ops after the profile’s log, one after another', () => {
    expect(s.ops[0].seq).toBe(bundle.sidecar.last_seq + 1);
    s.ops.forEach((o, i) => expect(o.seq).toBe(bundle.sidecar.last_seq + 1 + i));
    expect(new Set(s.ops.map((o) => o.op))).toEqual(new Set(['ADD', 'UPDATE', 'RETRACT', 'CONFIRM']));
  });

  it('writes a review result the pipeline accepts, and the reviewed file it should rebuild', async () => {
    const done = submit(s, '2026-10-16T10:05:00Z');
    const result = toResult({ ...done, activeSeconds: 1260 }, { submitted: true, at: done.submittedAt! });
    expect(describeErrors(validate(schema('review_result'), result))).toBe('');
    expect(result.submitted_at).toBe('2026-10-16T10:05:00Z');
    expect(result.review_seconds).toBe(1260);
    await expect(`${JSON.stringify(result, null, 1)}\n`).toMatchFileSnapshot('fixtures/review_result.json');
    await expect(`${JSON.stringify({ ...done.working, id: done.docId }, null, 1)}\n`).toMatchFileSnapshot('fixtures/reviewed_document.json');
  });
});
