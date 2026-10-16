/** The impact strip: the app's own needs analysis before and after a decision, beside the forecast (§11.1, §12.7–12.8). */
import { describe, expect, it } from 'vitest';
import { analyse } from '@/lib/fna/analysis';
import { attributionSentence, bundleDate, callDate, compareNeeds, forecastDeterministic, movedNeeds, needBand, needLines, posText, retirementViews } from '@/lib/review/impact';
import { applyAction, createSession } from '@/lib/review/session';
import { ANCHOR, F, loadPackage, STAMP } from './reviewFixture';

const bundle = loadPackage().bundle;
const forecast = bundle.preview_forecast!;
const fresh = () => createSession(bundle, { docId: bundle.median_document.id, reviewerId: STAMP.reviewerId, now: STAMP.at });

describe('needs before and after a decision', () => {
  const s = fresh();
  const now = analyse(s.working, ANCHOR);

  it('lists every headline need per person and for the household, keyed as the forecast’s bands are', () => {
    const keys = needLines(now).map((n) => n.key);
    for (const k of ['life.client', 'disability.spouse', 'income-protection.client', 'severe-illness.spouse', 'funeral.client', 'estate.client', 'retirement.spouse', 'education.household', 'emergency.household']) expect(keys).toContain(k);
    for (const k of Object.keys(forecast.need_bands)) expect(keys, k).toContain(k);
  });

  it('shows which needs a fact moves: Thabo’s new salary flips his life cover and income protection', () => {
    const without = applyAction(s, { type: 'reject', factId: F.income, reason: 'misheard' }, STAMP);
    const moved = movedNeeds(compareNeeds(analyse(without.working, ANCHOR), now));
    const byKey = Object.fromEntries(moved.map((d) => [d.key, d]));
    for (const k of ['life.client', 'income-protection.client']) {
      expect(byKey[k]?.statusChanged, k).toBe(true);
      expect(byKey[k].before.status).toBe('partial');
      expect(byKey[k].after.status).toBe('shortfall');
      expect(byKey[k].shortfallChange).toBeGreaterThan(0);
    }
    expect(moved.every((d) => d.statusChanged || d.shortfallChange !== 0)).toBe(true);
  });

  it('ignores rand-level noise and reports no movement for a fact that moves nothing', () => {
    expect(movedNeeds(compareNeeds(now, now))).toEqual([]);
    const accepted = applyAction(s, { type: 'accept', factId: F.groceries }, STAMP);
    expect(movedNeeds(compareNeeds(now, analyse(accepted.working, ANCHOR)))).toEqual([]);
  });

  it('a range’s reading matters: retiring at 60–62 is a shortfall, at 63–65 partial (the flag’s status flip)', () => {
    const early = applyAction(s, { type: 'edit', factId: F.retirementAge, value: { mode: 'point', value: 61 } }, STAMP);
    const late = applyAction(s, { type: 'edit', factId: F.retirementAge, value: { mode: 'point', value: 64 } }, STAMP);
    const status = (x: typeof s) => retirementViews(analyse(x.working, ANCHOR)).find((r) => r.person === 'client')!.status;
    expect(status(early)).toBe('shortfall');
    expect(status(late)).toBe('partial');
  });
});

describe('the forecast beside the FNA', () => {
  it('states the success probability with its Monte Carlo interval', () => {
    expect(posText(forecast)).toBe('71% (70–72%)');
  });

  it('phrases the variance attribution as the pipeline does', () => {
    expect(attributionSentence(forecast, null)).toBe(bundle.attribution_text);
    expect(attributionSentence(forecast, 'Given.')).toBe('Given.');
    expect(attributionSentence(null, null)).toBeNull();
  });

  it('reads the deterministic figures and need bands the forecast run recorded', () => {
    expect(forecastDeterministic(forecast, 'client', 'projected_capital')).toBe(31_012_251);
    expect(forecastDeterministic(forecast, 'spouse', 'nothing')).toBeNull();
    expect(needBand(forecast, 'life.client')).toEqual(forecast.need_bands['life.client']);
    expect(needBand(forecast, 'life.nobody')).toBeNull();
    expect(needBand(null, 'life.client')).toBeNull();
  });

  it('analyses as at the call: noon on the call’s date', () => {
    expect(callDate('2026-10-15').getTime()).toBe(ANCHOR.getTime());
    expect(bundleDate(bundle).getTime()).toBe(ANCHOR.getTime());
    expect(Number.isNaN(callDate('not a date').getTime())).toBe(false);
  });
});
