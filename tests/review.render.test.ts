/** The overlay's pieces render for every fact of both review packages (spec §11.1–11.4). */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { AddFactDialog, FollowupDialog, NoteDialog, SubmitDialog, TermDialog, TranscriptDialog } from '@/components/review/dialogs';
import FieldPopover, { type Panel } from '@/components/review/FieldPopover';
import { ImpactStrip, ReviewQueue } from '@/components/review/ReviewRail';
import type { CallAudio } from '@/components/review/useCallAudio';
import ValueEditor from '@/components/review/ValueEditor';
import { analyse } from '@/lib/fna/analysis';
import type { MeetingNote } from '@/lib/review/api';
import { slotById } from '@/lib/review/catalog';
import { compareNeeds, movedNeeds } from '@/lib/review/impact';
import { buildQueue } from '@/lib/review/queue';
import { applyAction, createSession, isReviewItem, type ReviewSession } from '@/lib/review/session';
import type { ReviewPackage } from '@/lib/review/types';
import { toEditor } from '@/lib/review/values';
import { ANCHOR, F, loadPackage, readJson, STAMP } from './reviewFixture';

const audio: CallAudio = { available: true, local: false, play: () => undefined, playing: null, openFile: () => undefined, error: null };
const noop = () => undefined;
const start = (pkg: ReviewPackage) => createSession(pkg.bundle, { docId: pkg.bundle.median_document.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
const demo = start(loadPackage());
const scripted = start(readJson<ReviewPackage>('tests', 'fixtures', 'pipeline_review_package.json'));

const popover = (s: ReviewSession, factId: string, panel: Panel = 'view') =>
  renderToStaticMarkup(
    createElement(FieldPopover, { session: s, fact: s.facts[factId], asAt: ANCHOR, audio, panel, setPanel: noop, dispatch: () => true, onClose: noop, onDecided: noop, onConfirmTerm: noop }),
  );

describe('the field popover', () => {
  // Every popover runs the needs analysis twice (with and without its fact): slow on a loaded machine, so given room.
  it('renders for every fact of both packages', { timeout: 60_000 }, () => {
    for (const s of [demo, scripted]) for (const f of Object.values(s.facts).filter(isReviewItem)) expect(() => popover(s, f.factId), f.factId).not.toThrow();
  });

  it('keeps what was said apart from what the system takes it to mean', () => {
    const html = popover(demo, F.retirementAge);
    expect(html).toContain('60–65, most likely 62');
    expect(html.indexOf('What was said')).toBeLessThan(html.indexOf('What the system takes it to mean'));
    expect(html).toContain('somewhere between sixty and sixty-five. Probably sixty-two, if the numbers allow it.');
    expect(html).toContain('Play from');
    expect(html).toContain('Blocks submission');
    expect(html).toMatch(/Moves the success odds by about 9 points/);
  });

  it('names the confidence band, the speaker, a derivation, a term to confirm and the readings of an unclear amount', () => {
    expect(popover(demo, F.target)).toMatch(/low<\/span> confidence/);
    expect(popover(demo, F.target)).toContain('The adviser’s suggestion');
    expect(popover(demo, F.spouseIncome)).toContain('Grossed up from take-home pay');
    expect(popover(scripted, 'income.{p}.grossMonthly#client')).toContain('Grossed up from take-home pay');
    expect(popover(demo, F.krugerType)).toContain('Confirm or correct the term');
    expect(popover(demo, F.bond)).toMatch(/could be R1,850,000 \(86%\) or R185,000 \(14%\)/);
  });

  it('shows what the fact does to the needs', () => {
    expect(popover(demo, F.income)).toContain('What it does to the needs');
    expect(popover(demo, F.studyOccurs)).toContain('Forecast only');
  });

  it('opens each action’s panel', () => {
    expect(popover(demo, F.retirementAge, 'edit')).toContain('Save edit');
    expect(popover(demo, F.retirementAge, 'status')).toContain('Save as stated');
    expect(popover(demo, F.retirementAge, 'reject')).toContain('Wrong person');
    expect(popover(demo, F.schoolFees, 'reattribute')).toContain('It is about someone or something else');
    expect(popover(demo, F.target, 'speaker')).toContain('Who said it?');
  });
});

describe('the rail', () => {
  it('lists the queue with flags first, and the impact of the last decision', () => {
    const s = applyAction(demo, { type: 'accept', factId: F.income }, STAMP);
    const before = analyse(demo.working, ANCHOR);
    const after = analyse(s.working, ANCHOR);
    const queue = renderToStaticMarkup(
      createElement(ReviewQueue, { session: s, queue: buildQueue(s), current: F.target, showCollapsed: false, setShowCollapsed: noop, onSelect: noop, onKeyDown: noop, onAcceptGroup: noop, onClearFlag: () => true, dismissing: 'flag-bond-scale', setDismissing: noop }),
    );
    expect(queue.indexOf('Blocking — clear before submitting')).toBeLessThan(queue.indexOf('Needs you'));
    expect(queue).toContain('aria-activedescendant="queue-retirement.{p}.targetPercent#client"');
    expect(queue).toContain('Accept all');
    expect(queue).toContain('Why the flag does not apply'); // the D key opens the dismissal of the flag in view
    expect(queue).toContain('dismiss flag');
    const impact = renderToStaticMarkup(createElement(ImpactStrip, { session: s, analysis: after, last: { label: 'Accepted Thabo · Gross monthly', deltas: movedNeeds(compareNeeds(before, after)) }, names: { client: 'Thabo', spouse: 'Lerato' }, onOpenFact: noop }));
    expect(impact).toContain('71%');
    expect(impact).toContain('70–72%');
    expect(impact).toContain('Of the uncertainty in this plan');
    expect(impact).toContain('FNA projection');
    expect(impact).toContain('Accepted Thabo · Gross monthly');
  });
});

describe('the dialogs', () => {
  it('render the follow-ups, a term, adding a fact, the transcript and submitting', () => {
    const f = renderToStaticMarkup(createElement(FollowupDialog, { session: demo, open: true, onClose: noop, dispatch: () => true }));
    expect(f).toContain('Thabo, would you say 60, 62 or 65');
    const t = renderToStaticMarkup(createElement(TermDialog, { session: demo, fact: demo.facts[F.krugerType], onClose: noop, dispatch: () => true }));
    expect(t).toContain('Krugerrand gold bullion coins');
    const a = renderToStaticMarkup(createElement(AddFactDialog, { session: demo, open: true, onClose: noop, dispatch: () => true, audio, error: null }));
    expect(a).toContain('Which field?');
    const tr = renderToStaticMarkup(createElement(TranscriptDialog, { session: demo, open: true, onClose: noop, audio, fact: demo.facts[F.bond] }));
    expect(tr.replace(/<[^>]+>/g, '')).toContain('We still owe about eighteen fifty on the bond.');
    expect(tr).toContain('bg-warning/35'); // the quote behind the fact in view is highlighted
    const sub = renderToStaticMarkup(
      createElement(SubmitDialog, { session: demo, open: true, onClose: noop, onSubmit: noop, apiAvailable: false, busy: false, error: null, household: 'Thabo & Lerato Mokoena', localChanged: true, notice: null, onDownloadResult: noop, onDownloadFile: noop }),
    );
    expect(sub).toContain('Submit review');
    expect(sub).toContain('The client file changed while you reviewed');
    const refusedSub = renderToStaticMarkup(
      createElement(SubmitDialog, { session: demo, open: true, onClose: noop, onSubmit: noop, apiAvailable: true, busy: false, error: null, household: 'Thabo & Lerato Mokoena', localChanged: false, refused: 2, notice: null, onDownloadResult: noop, onDownloadFile: noop }),
    );
    expect(refusedSub).toContain('The pipeline refused 2 of your changes');
    const note = renderToStaticMarkup(createElement(NoteDialog, { session: demo, open: true, onClose: noop, load: () => new Promise<MeetingNote>(() => {}), onApply: async () => {} }));
    expect(note).toContain('Meeting note');
    expect(note).toContain('never from the transcript');
  });

  it('the value editor offers a point, a range and options in the field’s own units', () => {
    const slot = slotById('retirement.{p}.retirementAge')!;
    const html = renderToStaticMarkup(createElement(ValueEditor, { slot, initial: toEditor(slot, demo.facts[F.retirementAge].quantity), onSave: noop, onCancel: noop }));
    for (const s of ['One value', 'Range', 'Options', 'Lowest', 'Most likely', 'Highest']) expect(html).toContain(s);
    const pct = slotById('retirement.{p}.targetPercent')!;
    expect(renderToStaticMarkup(createElement(ValueEditor, { slot: pct, initial: toEditor(pct, demo.facts[F.target].quantity), onSave: noop, onCancel: noop }))).toContain('value="70"');
  });
});
