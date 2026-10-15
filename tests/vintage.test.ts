import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { verdict } from '@/components/report/vintage/narrative';
import { hundredths, spread, wrap } from '@/components/report/vintage/plates';
import VintageReport from '@/components/report/vintage/VintageReport';
import { analyse } from '@/lib/fna/analysis';
import { defaultPractice, newDocument } from '@/lib/fna/defaults';
import { draftRecommendations } from '@/lib/fna/recommend';
import { sampleDocument } from '@/lib/fna/sample';
import type { FnaDocument } from '@/lib/fna/types';

const render = (doc: FnaDocument, appendix = false) => renderToStaticMarkup(createElement(VintageReport, { doc, analysis: analyse(doc), practice: defaultPractice(), appendix }));
const ROMAN: Record<string, number> = { I: 1, V: 5, X: 10, L: 50 };
const fromRoman = (s: string) => [...s].reduce((t, c, i, a) => (ROMAN[c] < (ROMAN[a[i + 1]] ?? 0) ? t - ROMAN[c] : t + ROMAN[c]), 0);
const plates = (html: string) => [...html.matchAll(/class="vr-plate-no">Plate ([IVXL]+)<\/span><span class="vr-plate-title">([^<]+)</g)].map((m) => [fromRoman(m[1]), m[2]] as const);

describe('vintage report', () => {
  const doc = sampleDocument();
  doc.advice.recommendations = draftRecommendations(doc, analyse(doc));
  const html = render(doc);

  it('numbers its plates in order and draws the ones a reader needs', () => {
    const p = plates(html);
    expect(p.length).toBeGreaterThanOrEqual(14);
    expect(p.map(([n]) => n)).toEqual(p.map((_, i) => i + 1));
    const titles = p.map(([, t]) => t);
    for (const t of [
      'The state of provision: how full is each vessel?',
      'The household in the balance',
      'Of every R100 that comes home',
      'What the family would need, beside what is in place',
      'What falls due, and when',
    ])
      expect(titles, t).toContain(t);
  });

  it('opens with the verdict in words and the most serious matters first', () => {
    const text = html.replace(/<[^>]+>/g, '');
    expect(text).toContain('Of the nineteen needs and goals we measured, one is fully provided for');
    const first = text.indexOf('Lerato has no will');
    const later = text.indexOf('A severe illness, such as cancer');
    expect(first).toBeGreaterThan(0);
    expect(later).toBeGreaterThan(first);
  });

  it('gives every plate a text alternative with its figures', () => {
    const imgs = html.match(/role="img"/g) ?? [];
    const labelled = html.match(/role="img" aria-label="[^"]+"/g) ?? [];
    expect(imgs.length).toBeGreaterThan(10);
    expect(labelled.length).toBe(imgs.length);
    expect(html).toMatch(/aria-label="[^"]*shortfall R/);
  });

  it('balances the executor’s account', () => {
    const totals = [...html.matchAll(/vr-account-total"><span>Total<\/span><span>([^<]+)</g)].map((m) => m[1]);
    expect(totals.length).toBe(4);
    expect(totals[0]).toBe(totals[1]);
    expect(totals[2]).toBe(totals[3]);
  });

  it('numbers the articles of advice in the order they are printed', () => {
    const nos = [...html.matchAll(/class="vr-article-no">([IVXL]+)\./g)].map((m) => fromRoman(m[1]));
    expect(nos).toEqual(nos.map((_, i) => i + 1));
  });

  it('renders an empty file, a single client and the appendix without broken figures', () => {
    const empty = render(newDocument());
    const single = sampleDocument();
    single.household.includeSpouse = false;
    single.household.maritalStatus = 'single';
    const one = render(single, true);
    for (const out of [empty, one]) {
      expect(out).not.toMatch(/NaN|Infinity|undefined/);
      expect(plates(out).map(([n]) => n)).toEqual(plates(out).map((_, i) => i + 1));
    }
    expect(one).not.toContain('should Lerato die');
    expect(one).not.toContain('Lerato’s retirement savings');
    expect(one).toContain('The Workings');
  });
});

describe('vintage helpers', () => {
  it('shares a hundred coins exactly', () => {
    expect(hundredths([53_379, 30_450, 2_045, 7_500, 1_883]).reduce((t, n) => t + n, 0)).toBe(100);
    expect(hundredths([1, 1, 1])).toEqual([34, 33, 33]);
    expect(hundredths([0, 0])).toEqual([0, 0]);
  });

  it('wraps labels at word boundaries and spreads labels apart', () => {
    expect(wrap('Support for parents & other dependants', 21)).toEqual(['Support for parents &', 'other dependants']);
    const ys = spread(
      [
        { y: 50, h: 20 },
        { y: 52, h: 20 },
        { y: 54, h: 20 },
      ],
      0,
      200,
    );
    expect(ys[1] - ys[0]).toBeGreaterThanOrEqual(20);
    expect(ys[2] - ys[1]).toBeGreaterThanOrEqual(20);
  });

  it('states the verdict in words', () => {
    expect(verdict(['covered', 'partial', 'shortfall', 'shortfall', 'na'])).toBe('Of the four needs we measured, one is fully provided for, one is partly provided for and two fall well short.');
    expect(verdict(['shortfall'])).toBe('Of the one need we measured, none is yet fully provided for and one falls well short.');
  });
});
