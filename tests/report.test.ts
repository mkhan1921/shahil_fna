import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import Report from '@/components/report/Report';
import { analyse } from '@/lib/fna/analysis';
import { defaultPractice, newDocument } from '@/lib/fna/defaults';
import { draftRecommendations } from '@/lib/fna/recommend';
import { sampleDocument } from '@/lib/fna/sample';
import type { FnaDocument } from '@/lib/fna/types';

const render = (doc: FnaDocument) => renderToStaticMarkup(createElement(Report, { doc, analysis: analyse(doc), practice: defaultPractice() }));
const figureTitles = (html: string) => [...html.matchAll(/Figure (\d+)<\/span><span[^>]*>([^<]+)</g)].map((m) => [Number(m[1]), m[2]] as const);

describe('client report visuals', () => {
  const doc = sampleDocument();
  doc.advice.recommendations = draftRecommendations(doc, analyse(doc)).map((r) => (r.premiumMonthly > 0 ? r : { ...r, premiumMonthly: 500, premiumEscalation: 0.05 }));
  const html = render(doc);

  it('numbers every figure in order', () => {
    const figs = figureTitles(html);
    expect(figs.length).toBeGreaterThanOrEqual(12);
    expect(figs.map(([n]) => n)).toEqual(figs.map((_, i) => i + 1));
  });

  it('draws the charts a client needs to read the analysis', () => {
    const titles = figureTitles(html).map(([, t]) => t);
    for (const t of [
      'Where each rand of gross income goes',
      'What you own and what you owe',
      'Financial health against common guidelines',
      'How much of each protection need is already covered',
      'Cash your executor would need, against cash available in the estate',
      'Projected retirement savings compared with the capital needed',
      'Total monthly cost of the recommendations, year by year',
    ]) {
      expect(titles, t).toContain(t);
    }
  });

  it('gives every chart a text alternative and keeps the projection disclaimer beside projections', () => {
    const charts = html.match(/role="img"/g) ?? [];
    const labelled = html.match(/role="img"[^>]*aria-label="[^"]+"|aria-label="[^"]+"[^>]*role="img"/g) ?? [];
    expect(charts.length).toBeGreaterThan(10);
    expect(labelled.length).toBe(charts.length);
    expect(html).toContain('Projections are illustrations, not guarantees.');
  });

  it('marks shortfalls with a label, not colour alone', () => {
    expect(html).toContain('Shortfall');
    expect(html).toMatch(/aria-label="[^"]*shortfall R/);
  });

  it('renders an empty file without charts that have nothing to show', () => {
    const empty = render(newDocument());
    expect(empty).not.toContain('Total monthly cost of the recommendations');
    expect(empty).toContain('How much of each protection need is already covered');
  });
});
