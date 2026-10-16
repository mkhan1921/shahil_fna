/** The review queue, the sheet's badges and the keyboard flow (spec §10.3, §11.1, §11.3). */
import { describe, expect, it } from 'vitest';
import {
  badgeAt,
  badgeOf,
  buildQueue,
  COLLAPSE_PRIORITY,
  entryFact,
  entryOf,
  flowStep,
  groupOf,
  nextEntry,
  nextUndecided,
  pBand,
  progress,
  queueKey,
  queueOrder,
} from '@/lib/review/queue';
import { applyAction, canSubmit, createSession, isDecided, isReviewItem, openFlags, type ReviewAction, type ReviewSession } from '@/lib/review/session';
import { F, loadPackage, STAMP } from './reviewFixture';

const bundle = loadPackage().bundle;
const fresh = (spotCheck = false) => createSession({ ...bundle, spot_check: spotCheck }, { docId: bundle.median_document.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
const act = (s: ReviewSession, ...actions: ReviewAction[]) => actions.reduce((acc, a) => applyAction(acc, a, STAMP), s);

describe('grouping and order', () => {
  const s = fresh();
  const q = buildQueue(s);

  it('puts every open blocking flag first, and keeps flagged facts out of the groups', () => {
    expect(q.flags.map((f) => f.flag.flag_id)).toEqual(bundle.blocking_flags.map((f) => f.flag_id));
    const flagged = new Set(bundle.blocking_flags.map((f) => f.fact_id));
    for (const f of [...q.needs, ...q.quick, ...q.collapsed]) expect(flagged.has(f.factId), f.factId).toBe(false);
    const order = queueOrder(q, false);
    expect(order.slice(0, q.flags.length).every((e) => e.startsWith('flag:'))).toBe(true);
  });

  it('queues every fact the call touched, once, and none it did not', () => {
    const queued = [...q.flags.map((f) => f.fact!.factId), ...q.needs, ...q.quick, ...q.collapsed].map((x) => (typeof x === 'string' ? x : x.factId));
    expect(new Set(queued).size).toBe(queued.length);
    expect(new Set(queued)).toEqual(new Set(Object.values(s.facts).filter(isReviewItem).map((f) => f.factId)));
    expect(s.facts['{p}.dateOfBirth#client'] && isReviewItem(s.facts['{p}.dateOfBirth#client'])).toBe(false);
  });

  it('sorts each group by review priority, highest first', () => {
    for (const g of [q.needs, q.quick, q.collapsed]) for (let i = 1; i < g.length; i++) expect(g[i - 1].priority).toBeGreaterThanOrEqual(g[i].priority);
  });

  it('sends low confidence, contradictions, tier A suggestions and big movers to "Needs you"', () => {
    expect(groupOf(s.facts[F.target], false)).toBe('needs'); // adviser-suggested tier A, low confidence
    expect(groupOf(s.facts[F.tertiaryCost], false)).toBe('needs'); // moves the odds by ~12 points
    expect(groupOf(s.facts[F.studyOccurs], false)).toBe('needs');
    expect(groupOf(s.facts[F.groceries], false)).toBe('quick'); // high confidence, small
    expect(pBand(0.85)).toBe('high');
    expect(pBand(0.6)).toBe('medium');
    expect(pBand(0.59)).toBe('low');
  });

  it('collapses auto-accepted facts below half a point of success odds; a spot check shows them all under "Needs you"', () => {
    expect(q.collapsed.length).toBeGreaterThan(0);
    for (const f of q.collapsed) {
      expect(f.autoAccepted).toBe(true);
      expect(f.priority).toBeLessThan(COLLAPSE_PRIORITY);
    }
    expect(queueOrder(q, false)).not.toContain(q.collapsed[0].factId);
    expect(queueOrder(q, true)).toContain(q.collapsed[0].factId);
    const spot = buildQueue(fresh(true));
    expect(spot.collapsed).toEqual([]);
    const auto = Object.values(s.facts).filter((f) => isReviewItem(f) && f.autoAccepted && !openFlags(s).some((x) => x.fact_id === f.factId));
    for (const f of auto) expect(spot.needs.map((x) => x.factId)).toContain(f.factId);
  });

  it('counts progress over flags, "Needs you" and "Check quickly"', () => {
    const p = progress(s, q);
    expect(p.total).toBe(q.flags.length + q.needs.length + q.quick.length);
    expect(p.decided).toBe(0);
    const after = act(s, { type: 'accept', factId: F.groceries }, { type: 'accept', factId: F.target });
    const p2 = progress(after);
    expect(p2.decided).toBe(2);
    expect(p2.needsDecided).toBe(1);
  });
});

describe('badges on the sheet', () => {
  const s = fresh();

  it('marks new, changed (with the prior to strike through), confirmed and assumed fields', () => {
    expect(badgeAt(s, 'income.client.grossMonthly')).toMatchObject({ kind: 'changed', label: 'Changed', prior: 'R100,000', flagged: true, decision: null });
    expect(badgeOf(s, s.facts[F.disabilityType])).toMatchObject({ kind: 'new', label: 'New', prior: null });
    expect(badgeOf(s, s.facts[F.smoker])).toMatchObject({ kind: 'confirmed', label: 'Confirmed', decision: 'auto' });
    expect(badgeOf(s, s.facts[F.disabilityDefinition])).toMatchObject({ kind: 'assumed', label: 'Assumed' });
    expect(badgeOf(s, s.facts[F.disabilityCover])).toMatchObject({ kind: 'unknown', label: 'Unknown' });
    expect(badgeOf(s, s.facts[F.ra])).toMatchObject({ kind: 'unknown', label: 'Contradicted', flagged: true });
    expect(badgeAt(s, 'client.dateOfBirth')).toBeNull(); // untouched by the call
  });

  it('says what a derivation did and how sure the system is', () => {
    const b = badgeOf(s, s.facts[F.spouseIncome])!;
    expect(b.kind).toBe('confirmed');
    expect(b.title).toContain('grossed up from take-home pay');
    expect(b.title).toMatch(/(high|medium|low) confidence$/);
  });

  it('follows the reviewer: accepted, edited, rejected; flags clear; a fact the reviewer adds is "Added"', () => {
    const after = act(
      s,
      { type: 'accept', factId: F.income },
      { type: 'edit', factId: F.groceries, value: { mode: 'point', value: 10500 } },
      { type: 'reject', factId: F.krugerValue, reason: 'misheard' },
      { type: 'addFact', slotId: 'expenses.items[]', to: { kind: 'instance', instance: 'transport' }, value: { mode: 'point', value: 4200 } },
    );
    expect(badgeAt(after, 'income.client.grossMonthly')).toMatchObject({ decision: 'accepted', flagged: false });
    expect(badgeOf(after, after.facts[F.groceries])?.decision).toBe('edited');
    expect(badgeOf(after, after.facts[F.krugerValue])?.decision).toBe('rejected');
    expect(badgeAt(after, 'expenses.items[transport]')).toMatchObject({ kind: 'new', label: 'Added', decision: 'accepted' });
  });
});

describe('the keyboard flow', () => {
  it('maps the keys: Enter accepts, Tab moves on, Shift goes back, E R U P, Escape', () => {
    expect(queueKey({ key: 'Enter' })).toBe('accept');
    expect(queueKey({ key: 'Enter', shiftKey: true })).toBe('prev');
    expect(queueKey({ key: 'Tab' })).toBe('next');
    expect(queueKey({ key: 'Tab', shiftKey: true })).toBe('prev');
    expect(queueKey({ key: 'ArrowDown' })).toBe('next');
    expect(queueKey({ key: 'k' })).toBe('prev');
    expect(queueKey({ key: 'e' })).toBe('edit');
    expect(queueKey({ key: 'R' })).toBe('reject');
    expect(queueKey({ key: 'u' })).toBe('unknown');
    expect(queueKey({ key: 'p' })).toBe('play');
    expect(queueKey({ key: 'Escape' })).toBe('close');
    expect(queueKey({ key: 'Enter', ctrlKey: true })).toBeNull();
    expect(queueKey({ key: 'x' })).toBeNull();
  });

  it('walks the queue: Enter starts at the first flag, Tab skips, Enter accepts and moves to the next undecided', () => {
    let s = fresh();
    const order = queueOrder(buildQueue(s), false);
    let current: string | null = null;
    const start = flowStep(s, order, current, 'accept');
    expect(start).toEqual({ do: 'select', entry: order[0] });
    current = order[0];
    expect(flowStep(s, order, current, 'next')).toEqual({ do: 'select', entry: order[1] });
    expect(flowStep(s, order, current, 'prev')).toBeNull(); // Shift+Tab leaves the queue at the top
    // The RA's contradiction: Enter accepts it and moves on to the next undecided entry.
    const step = flowStep(s, order, current, 'accept');
    expect(step).toEqual({ do: 'accept', factId: F.ra, next: order[1] });
    s = act(s, { type: 'accept', factId: F.ra });
    expect(openFlags(s).some((f) => f.fact_id === F.ra)).toBe(false);
    // Tab past a decided entry still stops on it; Enter's "next undecided" skips it.
    const decided = order.indexOf(F.groceries);
    s = act(s, { type: 'accept', factId: F.groceries });
    expect(nextEntry(s, order, order[decided - 1])).toBe(F.groceries);
    expect(nextEntry(s, order, order[decided - 1], { undecided: true })).not.toBe(F.groceries);
  });

  it('opens the term confirmation for an unknown term instead of accepting it, and wraps round to what is left', () => {
    const s = fresh();
    const order = queueOrder(buildQueue(s), false);
    const kruger = 'flag:flag-krugerrands';
    expect(entryOf(s, order, F.krugerType)).toBe(kruger);
    expect(flowStep(s, order, kruger, 'accept')).toEqual({ do: 'confirmTerm', factId: F.krugerType });
    const last = order[order.length - 1];
    const next = nextUndecided(s, order, last, entryFact(s, last)?.factId ?? null);
    expect(next).toBe(order[0]);
    expect(flowStep(s, order, F.target, 'edit')).toEqual({ do: 'panel', panel: 'edit', factId: F.target });
    expect(flowStep(s, order, F.target, 'unknown')).toMatchObject({ do: 'unknown', factId: F.target });
    expect(flowStep(s, order, F.target, 'play')).toEqual({ do: 'play', factId: F.target });
    expect(flowStep(s, order, F.target, 'close')).toBeNull();
    expect(flowStep(s, order, F.target, 'close', true)).toEqual({ do: 'close' });
  });

  it('reaches a review that can be submitted once every flag is cleared', () => {
    let s = fresh();
    expect(canSubmit(s)).toBe(false);
    const order = queueOrder(buildQueue(s), false);
    for (const entry of order.filter((e) => e.startsWith('flag:'))) {
      const step = flowStep(s, queueOrder(buildQueue(s), false), entry, 'accept');
      if (step?.do === 'accept') s = act(s, { type: 'accept', factId: step.factId });
      else if (step?.do === 'confirmTerm') s = act(s, { type: 'confirmTerm', factId: step.factId, canonicalName: 'Krugerrand gold bullion coins', productType: 'other', provider: '', confirmed: true });
    }
    expect(openFlags(s)).toEqual([]);
    expect(canSubmit(s)).toBe(true);
    expect(Object.values(s.facts).filter((f) => bundle.blocking_flags.some((b) => b.fact_id === f.factId)).every(isDecided)).toBe(true);
  });
});
