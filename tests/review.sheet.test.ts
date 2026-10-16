/** The overlay on the adviser's own sheet: every fact-backed field is badged (spec §11.1). */
import { createElement, type ComponentType } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { OverlayProvider, type OverlayApi } from '@/components/review/overlay';
import { BudgetSection, EngagementSection, IncomeSection, PeopleSection } from '@/components/sheet/sections/Discovery';
import { EstateSection, GoalsSection, RiskSection } from '@/components/sheet/sections/Planning';
import { AssetsSection, CoverSection, LiabilitiesSection } from '@/components/sheet/sections/Wealth';
import type { SectionProps } from '@/components/sheet/types';
import { analyse } from '@/lib/fna/analysis';
import { defaultPractice } from '@/lib/fna/defaults';
import { slotAtPath } from '@/lib/review/catalog';
import { getPath, isDocumentPath } from '@/lib/review/paths';
import { badgeAt } from '@/lib/review/queue';
import { applyAction, createSession, entityOfRow, isReviewItem, type ReviewSession } from '@/lib/review/session';
import { ANCHOR, F, loadPackage, STAMP } from './reviewFixture';

const bundle = loadPackage().bundle;
const SECTIONS: ComponentType<SectionProps>[] = [EngagementSection, PeopleSection, IncomeSection, BudgetSection, AssetsSection, LiabilitiesSection, CoverSection, GoalsSection, EstateSection, RiskSection];

/** Render the data-entry sections inside a review overlay; record every path a cell asked about. */
const renderSheet = (s: ReviewSession) => {
  const asked = new Set<string>();
  const overlay: OverlayApi = {
    mode: 'review',
    markAt: (path) => {
      asked.add(path);
      return badgeAt(s, path);
    },
    currentPath: 'income.client.grossMonthly',
    openAt: () => undefined,
  };
  const props: SectionProps = { doc: s.working, update: () => undefined, analysis: analyse(s.working, ANCHOR), practice: defaultPractice() };
  const html = renderToStaticMarkup(createElement(OverlayProvider, { value: overlay }, ...SECTIONS.map((S, i) => createElement(S, { ...props, key: i }))));
  return { html, asked };
};

describe('the sheet in a review', () => {
  const s = createSession(bundle, { docId: bundle.median_document.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
  const { html, asked } = renderSheet(s);

  it('asks about extracted fields only, each at a path the catalog knows', () => {
    expect(asked.size).toBeGreaterThan(120);
    for (const path of asked) expect(slotAtPath(path, (c, k) => entityOfRow(s, c, k)), path).not.toBeNull();
  });

  it('has a cell for every fact the call touched that the file can hold', () => {
    const missing = Object.values(s.facts)
      .filter((f) => isReviewItem(f) && f.exported && isDocumentPath(f.path) && getPath(s.working, f.path) !== undefined)
      .map((f) => f.path)
      .filter((p) => !asked.has(p));
    expect(missing).toEqual([]);
  });

  it('draws the badges: changed with the prior struck through, new, confirmed, assumed', () => {
    expect(html).toContain('data-path="income.client.grossMonthly"');
    expect(html).toMatch(/Changed in the call: R100,000 → R118,000 a month/);
    expect(html).toMatch(/<s[^>]*title="Before the call: R100,000"[^>]*>R100,000<\/s>/);
    for (const label of ['New', 'Changed', 'Confirmed', 'Assumed', 'Unknown']) expect(html, label).toContain(`>${label}<`);
    expect(html).toContain('shadow-[inset_0_0_0_2px_var(--color-brand-2)]'); // the field in the popover is highlighted
  });

  it('shows decisions on the badges as the review goes', () => {
    const after = applyAction(s, { type: 'reject', factId: F.groceries, reason: 'misheard' }, STAMP);
    const { html: next } = renderSheet(after);
    const label = (h: string) => /<label data-path="expenses\.items\[groceries\]"[\s\S]*?aria-label="([^"]*)"/.exec(h)?.[1] ?? '';
    expect(label(html)).toMatch(/^Changed in the call: R9,000 → .* confidence\. Open the review popover\.$/);
    expect(label(next)).toMatch(/^Changed in the call: R9,000 → .*, rejected\. Open the review popover\.$/);
  });

  it('renders exactly as before outside a review', () => {
    const props: SectionProps = { doc: s.working, update: () => undefined, analysis: analyse(s.working, ANCHOR), practice: defaultPractice() };
    const plain = renderToStaticMarkup(createElement(IncomeSection, props));
    expect(plain).not.toContain('Changed in the call');
    expect(plain).toContain('data-path="income.client.grossMonthly"');
  });
});
