'use client';

import { X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { closeEveryGap, COVER, discretionarySpending, emptyPlan, personPlan, planFromAdvice, withoutChange, type CoverKey, type PlanSummary } from '@/lib/fna/plan';
import type { PersonKey, PersonPlan } from '@/lib/fna/types';
import { money, moneyCompact, pct } from '@/lib/format';
import { cx } from '../ui';
import { inWords, Manicule, Ornament, ordinal, roman, Rule3 } from '../report/vintage/ink';
import { verdict } from '../report/vintage/narrative';
import { agendaText, answer, firstName, isPerPerson, measures, peopleFor, QUESTIONS, questionOfChange, questionText, TOPIC, verdicts, type Answer, type QuestionKey } from './answers';
import { accountFor, addressFor, basisFor, bridgeFor, fundedRows, studyCosts, type Account, type AccountRow, type AddressGroup, type AddressItem, type PlanCtx } from './brief';
import { BridgeChart, bridgeKey, CostColumns, costKey, Mark, NeedStrips, Plate, ProjectionChart, projectionKey, PROVISION_KEY, ProvisionStrips, StatusMark, STRIP_KEY } from './charts';
import { CheckRow, LeverCard, RandInput, Slider, Tabs, YearStepper } from './levers';

export interface SectionCtx extends PlanCtx {
  setPlan: (update: (p: PlanCtx['plan']) => PlanCtx['plan']) => void;
  summary: PlanSummary;
  /** Whether the levers are showing (the presenter can hide them). */
  levers: boolean;
  go: (id: string) => void;
}

/** Short names for the bar along the top. */
export const NAV_LABEL: Record<QuestionKey, string> = {
  life: 'Death',
  income: 'Income',
  disability: 'Disability',
  illness: 'Illness',
  estate: 'Estate',
  retirement: 'Retirement',
  education: 'Studies',
  goals: 'Goals',
  emergency: 'Emergencies',
  afford: 'Budget',
};

/** The italic line under each question, as under the report's chapter heads. */
const SUB: Record<QuestionKey, string> = {
  life: 'should death come early',
  income: 'should illness or injury stop the pay',
  disability: 'should a disability be lasting',
  illness: 'should a serious illness strike',
  estate: 'when the estate is wound up',
  retirement: 'the years after work',
  education: 'the cost of university',
  goals: 'what you are saving towards',
  emergency: 'when the unexpected happens',
  afford: 'whether the plan fits the budget',
};

export const sectionId = (q: QuestionKey) => `q-${q}`;

/** How many plates a question's leaf carries, so plates are numbered right through the session. */
export const plateCount = (q: QuestionKey, c: PlanCtx) => (q === 'retirement' || (q === 'education' && studyCosts(c).length > 0) ? 2 : 1);

const kicker = (n: number) => (n <= 10 ? `Question the ${ordinal(n)}` : `Question ${roman(n)}`);
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const roundUp = (n: number, step: number) => (n > 0 ? Math.ceil(n / step) * step : 0);
const niceTop = (n: number, step: number) => Math.max(step * 10, roundUp(n, step * 10));
const updatePerson = (c: SectionCtx, k: PersonKey, patch: Partial<PersonPlan>) => c.setPlan((p) => ({ ...p, people: { ...p.people, [k]: { ...personPlan(p, k), ...patch } } }));
const settling = (groups: AddressGroup[]) => groups.filter((g) => g.heading === 'Also to settle').flatMap((g) => g.items);
const itemised = (groups: AddressGroup[]) => groups.filter((g) => g.heading !== 'Also to settle');

/* ------------------------------------------------------------------ */
/* The pieces of a leaf                                                */
/* ------------------------------------------------------------------ */

function Leaf({ id, folio, labelledBy, className, children }: { id: string; folio: number; labelledBy: string; className?: string; children: ReactNode }) {
  return (
    <section id={id} className={cx('pv-leaf', className)} data-folio={folio} aria-labelledby={labelledBy}>
      {children}
    </section>
  );
}

/** “Question the Third” — the question — an italic line — the printer's rule. */
function ChapterHead({ id, kicker, title, sub }: { id: string; kicker: string; title: ReactNode; sub?: string }) {
  return (
    <header className="pv-chapter">
      <div className="pv-chapter-no">{kicker}</div>
      <h2 id={id}>{title}</h2>
      {sub && <p className="pv-chapter-sub">{sub}</p>}
      <Rule3 />
    </header>
  );
}

/** “The short of it”: the answer, ruled off above and below. */
function Short({ a }: { a: Answer }) {
  return (
    <p className="pv-abstract" aria-live="polite">
      <span className="pv-abstract-label">The short of it.</span> {a.status !== 'na' && <Mark status={a.status} />}
      {a.lead && <span className="pv-lead">{a.lead}</span>} {a.parts.map((p, i) => (typeof p === 'string' ? <span key={i}>{p}</span> : <b key={i}>{p.b}</b>))}
    </p>
  );
}

interface Cell {
  label: string;
  value: string;
  sub?: ReactNode;
  tone?: 'bad' | 'plan';
}

function Register({ cells }: { cells: Cell[] }) {
  return (
    <div className="pv-register">
      {cells.map((c) => (
        <div key={c.label} className={cx(c.tone && `is-${c.tone}`)}>
          <div className="pv-register-label">{c.label}</div>
          <div className="pv-register-value">{c.value}</div>
          {c.sub && <div className="pv-register-sub">{c.sub}</div>}
        </div>
      ))}
    </div>
  );
}

function Heading({ children, gloss }: { children: ReactNode; gloss?: ReactNode }) {
  return (
    <div className="pv-heading">
      <h3>{children}</h3>
      {gloss && <p>{gloss}</p>}
    </div>
  );
}

interface LeaderRow {
  key: string;
  label: ReactNode;
  value?: ReactNode;
  gloss?: ReactNode;
  tone?: AccountRow['tone'];
}

/** Entries joined to their figures by dot leaders, each with its explanation beneath. */
function Leaders({ rows }: { rows: LeaderRow[] }) {
  return (
    <dl className="pv-leaders">
      {rows.map((r) => (
        <div key={r.key} className={cx('pv-leader', r.tone && `is-${r.tone}`)}>
          <div className="pv-leader-line">
            <dt>{r.label}</dt>
            {r.value !== undefined && r.value !== '' && <span className="pv-dots" aria-hidden />}
            <dd>{r.value}</dd>
          </div>
          {r.gloss && <p className="pv-gloss">{r.gloss}</p>}
        </div>
      ))}
    </dl>
  );
}

const accountTitle = (q: QuestionKey, c: SectionCtx, k: PersonKey): ReactNode => {
  const n = firstName(c.today, k);
  switch (q) {
    case 'life':
      return (
        <>
          The family’s account <i>— should {n} die</i>
        </>
      );
    case 'income':
      return (
        <>
          The monthly account <i>— should {n} be unable to work</i>
        </>
      );
    case 'disability':
      return (
        <>
          The account <i>— should {n} be disabled for good</i>
        </>
      );
    case 'illness':
      return (
        <>
          The account <i>— should {n} fall seriously ill</i>
        </>
      );
    case 'estate':
      return (
        <>
          The executor’s account <i>— on the death of {n}</i>
        </>
      );
    default:
      return (
        <>
          The emergency account <i>— {inWords(c.doc.assumptions.emergencyMonths)} months of essentials</i>
        </>
      );
  }
};

/** A need set out as a balanced ledger: what would be needed, against what would meet it. */
function AccountView({ title, a }: { title: ReactNode; a: Account }) {
  const row = (r: AccountRow): LeaderRow => ({ key: r.key, label: r.label, value: Math.abs(r.amount) > 0.5 ? money(r.amount) : '—', gloss: r.gloss, tone: r.tone });
  const side = (head: string, rows: AccountRow[]) => (
    <div>
      <div className="pv-account-side">
        {head}
        {a.monthly ? ', a month' : ''}
      </div>
      <Leaders rows={rows.map(row)} />
      <div className="pv-account-total">
        <span>Total</span>
        <span>{money(a.total)}</span>
      </div>
    </div>
  );
  return (
    <div className="pv-account">
      <div className="pv-account-title">{title}</div>
      <div className="pv-account-sides">
        {side(a.needHeading, a.need)}
        {side('What would meet it', a.meet)}
      </div>
    </div>
  );
}

/** What a question is made of, where its parts are not a balanced account. */
function Schedule({ groups }: { groups: AddressGroup[] }) {
  if (!groups.length) return null;
  return (
    <div className={cx('pv-schedule', groups.length === 1 && 'is-one')}>
      {groups.map((g) => (
        <div key={g.heading}>
          <div className="pv-subhead">{g.heading}</div>
          <Leaders rows={g.items.map((it) => ({ key: it.title, label: it.title, value: it.figure, gloss: it.text, tone: /\bplan\b/i.test(it.title) ? 'plan' : undefined }))} />
        </div>
      ))}
    </div>
  );
}

function Matters({ items }: { items: AddressItem[] }) {
  if (!items.length) return null;
  return (
    <>
      <Heading gloss="what the figures alone cannot settle">Matters also to settle</Heading>
      <ul className="pv-matters">
        {items.map((it) => (
          <li key={it.title}>
            <Manicule />
            <div>
              <span className="pv-runin">{it.title}.</span> {it.figure && <b>{it.figure}. </b>}
              {it.text}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function Basis({ lines }: { lines: string[] }) {
  if (!lines.length) return null;
  return (
    <p className="pv-basis">
      <span className="pv-runin">The basis.</span> {lines.join(' ')}
    </p>
  );
}

function Levers({ children, cols = 1 }: { children: ReactNode; cols?: 1 | 2 | 3 }) {
  return (
    <div className="pv-levers">
      <Heading gloss="move one, and every figure on this leaf follows">What would change the answer</Heading>
      <div className={cx('pv-lever-grid', cols > 1 && `is-${cols}`)}>{children}</div>
    </div>
  );
}

function QuestionHead({ q, n, c, k, setK }: { q: QuestionKey; n: number; c: SectionCtx; k: PersonKey; setK: (k: PersonKey) => void }) {
  const ks = peopleFor(q, c);
  return (
    <>
      <ChapterHead id={`${sectionId(q)}-h`} kicker={kicker(n)} title={questionText(q, c, k)} sub={SUB[q]} />
      {ks.length > 1 && (
        <div className="pv-person">
          <Tabs label="Whose question" value={k} onChange={setK} options={ks.map((x) => ({ value: x, label: firstName(c.today, x) }))} />
        </div>
      )}
      <Short a={answer(q, c, k, c.summary.changes.length)} />
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Levers                                                              */
/* ------------------------------------------------------------------ */

const NEED_OF: Record<Exclude<CoverKey, 'estateCover'>, 'death' | 'incomeProtection' | 'disability' | 'severeIllness'> = {
  lifeCover: 'death',
  incomeProtection: 'incomeProtection',
  disability: 'disability',
  severeIllness: 'severeIllness',
};

/** The cover that would close this gap, given everything else in the plan. */
const coverTarget = (c: SectionCtx, k: PersonKey, cover: CoverKey, amount: number, step: number) => {
  const p = c.planned.persons[k]!;
  if (cover === 'estateCover') {
    // Cover paid to the estate attracts executor's fees, so it is grossed up.
    const fee = c.doc.assumptions.executorFeeRate * (1 + c.doc.assumptions.vatRate);
    return roundUp(amount + (p.estate.cashRequired - p.estate.cashAvailable) / (1 - fee), step);
  }
  const n = p[NEED_OF[cover]];
  return roundUp(amount + n.need - n.provision, step);
};

function CoverLever({ c, k, cover, label }: { c: SectionCtx; k: PersonKey; cover: CoverKey; label: string }) {
  const lv = personPlan(c.plan, k)[cover];
  const monthly = COVER[cover].monthly;
  const step = monthly ? 500 : cover === 'estateCover' ? 25_000 : 50_000;
  const target = coverTarget(c, k, cover, lv.amount, step);
  const max = monthly ? niceTop(Math.max(target * 1.5, 10_000), 500) : niceTop(Math.max(target * 1.5, cover === 'estateCover' ? 500_000 : 1_000_000), 50_000);
  const fmt = (v: number) => (v ? `+${monthly ? `${money(v)} a month` : moneyCompact(v)}` : 'none');
  const set = (patch: Partial<typeof lv>) => updatePerson(c, k, { [cover]: { ...lv, ...patch } });
  return (
    <LeverCard>
      <Slider
        label={label}
        value={lv.amount}
        min={0}
        max={max}
        step={step}
        format={fmt}
        marker={target > 0 ? { value: target, label: 'closes the gap' } : undefined}
        onChange={(v) => set({ amount: v })}
      >
        {lv.amount > 0 && <RandInput label="Premium quoted:" value={lv.premium} onChange={(v) => set({ premium: v })} />}
      </Slider>
    </LeverCard>
  );
}

function BonusLine({ c }: { c: SectionCtx }) {
  const { bonusUsed: used, bonusAvailable: bonus } = c.summary;
  if (used <= 0.5) return null;
  return used > bonus + 0.5 ? (
    <p className="pv-hint">
      <StatusMark status="shortfall" text={`${money(used)} planned from the bonus — ${money(used - bonus)} more than the ${money(bonus)} available.`} />
    </p>
  ) : (
    <p className="pv-hint">
      {money(used)} planned from the {money(bonus)} bonus.
    </p>
  );
}

const COVER_OF: Partial<Record<QuestionKey, { cover: CoverKey; label: string }>> = {
  life: { cover: 'lifeCover', label: 'Add life cover' },
  income: { cover: 'incomeProtection', label: 'Add income protection' },
  disability: { cover: 'disability', label: 'Add disability cover' },
  illness: { cover: 'severeIllness', label: 'Add severe illness cover' },
  estate: { cover: 'estateCover', label: 'Add life cover paid to the estate' },
};

function RetirementLevers({ c, k }: { c: SectionCtx; k: PersonKey }) {
  const t = c.today.persons[k]!.retirement;
  const p = c.planned.persons[k]!.retirement;
  const lv = personPlan(c.plan, k);
  const fileAge = c.doc.retirement[k].retirementAge;
  const target = roundUp(lv.extraRetirement + p.additionalMonthly, 100);
  return (
    <Levers cols={3}>
      {!t.retired && (
        <LeverCard>
          <Slider
            label="Retire at"
            value={lv.retirementAge ?? fileAge}
            min={Math.max(50, Math.ceil(t.currentAge) + 1)}
            max={75}
            step={1}
            format={(v) => `age ${v}`}
            marker={{ value: fileAge, label: 'in the file' }}
            onChange={(v) => updatePerson(c, k, { retirementAge: v === fileAge ? null : v })}
          />
        </LeverCard>
      )}
      <LeverCard>
        <Slider
          label="Income wanted"
          value={lv.incomeWanted ?? Math.round(t.targetMonthlyToday)}
          min={roundUp(t.targetMonthlyToday * 0.4, 1_000)}
          max={roundUp(Math.max(t.targetMonthlyToday * 1.5, 20_000), 1_000)}
          step={500}
          format={(v) => money(v)}
          marker={{ value: Math.round(t.targetMonthlyToday), label: 'in the file' }}
          hint="A month in retirement, in today’s money."
          onChange={(v) => updatePerson(c, k, { incomeWanted: Math.abs(v - t.targetMonthlyToday) < 1 ? null : v })}
        />
      </LeverCard>
      {!t.retired && (
        <LeverCard>
          <Slider
            label="Save more each month"
            value={lv.extraRetirement}
            min={0}
            max={niceTop(Math.max(target * 1.5, 5_000), 500)}
            step={100}
            format={(v) => (v ? `+${money(v)}` : 'none')}
            marker={target > 0 ? { value: target, label: 'closes the gap' } : undefined}
            hint={
              c.summary.taxRelief > 0.5
                ? `Into a retirement annuity. New saving earns about ${money(c.summary.taxRelief)} a year back in tax.`
                : 'Into a retirement annuity, rising with salary each year.'
            }
            onChange={(v) => updatePerson(c, k, { extraRetirement: v })}
          />
        </LeverCard>
      )}
      <div className="pv-stress is-wide">
        <span>And if investments grew</span>
        <Tabs
          size="sm"
          label="Investment returns"
          value={c.plan.returnShift}
          onChange={(v) => c.setPlan((x) => ({ ...x, returnShift: v }))}
          options={[
            { value: -0.02, label: '2% less' },
            { value: -0.01, label: '1% less' },
            { value: 0, label: 'as assumed' },
            { value: 0.01, label: '1% more' },
          ]}
        />
        <span>a year?</span>
      </div>
    </Levers>
  );
}

function EducationLevers({ c }: { c: SectionCtx }) {
  const rows = measures('education', c);
  const kids = c.doc.dependants.filter((d) => rows.some((r) => r.key === d.id));
  return (
    <Levers cols={kids.length > 1 ? 2 : 1}>
      {kids.map((d) => {
        const v = c.plan.education[d.id] ?? 0;
        const e = c.planned.education.find((x) => x.dependantId === d.id);
        const target = roundUp(v + (e?.monthlyRequired ?? 0), 100);
        return (
          <LeverCard key={d.id}>
            <Slider
              label={`Save for ${d.name.split(' ')[0] || 'this child'}`}
              value={v}
              min={0}
              max={niceTop(Math.max(target * 1.5, 2_000), 500)}
              step={100}
              format={(x) => (x ? `+${money(x)}` : 'none')}
              marker={target > 0 ? { value: target, label: 'closes the gap' } : undefined}
              hint="A month, from now until studies start."
              onChange={(x) => c.setPlan((p) => ({ ...p, education: { ...p.education, [d.id]: x } }))}
            />
          </LeverCard>
        );
      })}
    </Levers>
  );
}

function GoalLevers({ c }: { c: SectionCtx }) {
  const rows = measures('goals', c);
  const year = new Date().getFullYear();
  const goals = c.doc.goals.filter((g) => rows.some((r) => r.key === g.id));
  return (
    <Levers cols={goals.length > 1 ? 2 : 1}>
      {goals.map((g) => {
        const v = c.plan.goalSaving[g.id] ?? 0;
        const delay = Math.round(c.plan.goalDelay[g.id] ?? 0);
        const r = c.planned.goals.find((x) => x.id === g.id);
        const target = roundUp(v + (r?.monthlyRequired ?? 0), 100);
        return (
          <LeverCard key={g.id}>
            <div className="space-y-5">
              <Slider
                label={`Save for ${g.name || 'this goal'}`}
                value={v}
                min={0}
                max={niceTop(Math.max(target * 1.5, 2_000), 500)}
                step={100}
                format={(x) => (x ? `+${money(x)}` : 'none')}
                marker={target > 0 ? { value: target, label: 'closes the gap' } : undefined}
                hint="A month, until it is due."
                onChange={(x) => c.setPlan((p) => ({ ...p, goalSaving: { ...p.goalSaving, [g.id]: x } }))}
              />
              <YearStepper
                label="When"
                note={delay ? `${Math.abs(delay)} year${Math.abs(delay) === 1 ? '' : 's'} ${delay > 0 ? 'later' : 'sooner'} than planned` : 'as planned'}
                value={g.targetYear + delay}
                min={Math.max(year, g.targetYear - 10)}
                max={g.targetYear + 20}
                onChange={(y) => c.setPlan((p) => ({ ...p, goalDelay: { ...p.goalDelay, [g.id]: y - g.targetYear } }))}
              />
            </div>
          </LeverCard>
        );
      })}
    </Levers>
  );
}

function EmergencyLevers({ c }: { c: SectionCtx }) {
  const bonus = c.summary.bonusAvailable;
  const v = c.plan.emergencyTopUp;
  const target = roundUp(v + Math.max(0, c.planned.emergency.need - c.planned.emergency.provision), 1_000);
  return (
    <Levers>
      <LeverCard>
        <Slider
          label="Put aside from the bonus"
          value={v}
          min={0}
          max={niceTop(Math.max(target * 1.5, bonus, 20_000), 1_000)}
          step={1_000}
          format={(x) => (x ? money(x) : 'none')}
          marker={target > 0 ? { value: target, label: 'closes the gap' } : undefined}
          hint={bonus > 0.5 ? `Once-off, into a money-market account. The annual bonus is about ${money(bonus)} after tax.` : 'Once-off, into a money-market account.'}
          onChange={(x) => c.setPlan((p) => ({ ...p, emergencyTopUp: x }))}
        />
        <BonusLine c={c} />
      </LeverCard>
    </Levers>
  );
}

function AffordLevers({ c }: { c: SectionCtx }) {
  const cap = discretionarySpending(c.today);
  const bonus = c.today.cashflow.bonusNet;
  const unsecured = ['credit-card', 'store-card', 'personal-loan', 'overdraft', 'other'];
  // A bonus can reasonably settle unsecured or small debts; the home and car loans are left out.
  const debts = c.doc.liabilities.filter((l) => l.balance > 0 && (unsecured.includes(l.type) || l.balance <= Math.max(bonus, 1))).sort((a, b) => b.interestRate - a.interestRate);
  return (
    <Levers cols={debts.length ? 2 : 1}>
      <LeverCard>
        <Slider
          label="Spend less each month"
          value={Math.min(c.plan.spendingCut, cap)}
          min={0}
          max={Math.max(100, roundUp(cap, 100))}
          step={100}
          format={(v) => (v ? `−${money(v)}` : 'no change')}
          hint={`Of the ${money(cap)} a month that is not essential.`}
          onChange={(v) => c.setPlan((p) => ({ ...p, spendingCut: Math.min(v, cap) }))}
        />
      </LeverCard>
      {debts.length > 0 && (
        <LeverCard>
          <div className="pv-lever-label mb-3">Settle debt with the bonus</div>
          {debts.map((l) => (
            <CheckRow
              key={l.id}
              checked={c.plan.settleDebts.includes(l.id)}
              onChange={(on) => c.setPlan((p) => ({ ...p, settleDebts: on ? [...p.settleDebts, l.id] : p.settleDebts.filter((x) => x !== l.id) }))}
              label={l.description || l.type}
              detail={`${money(l.balance)} at ${pct(l.interestRate, 1)}; frees ${money(l.monthlyRepayment)} a month`}
            />
          ))}
          <BonusLine c={c} />
        </LeverCard>
      )}
    </Levers>
  );
}

/* ------------------------------------------------------------------ */
/* The question leaves                                                 */
/* ------------------------------------------------------------------ */

interface LeafNo {
  n: number;
  c: SectionCtx;
  /** The number of this leaf's first plate. */
  plate: number;
  folio: number;
}

const lump = (n: number, monthly?: boolean) => (monthly ? money(n) : moneyCompact(n));

function needCells(c: SectionCtx, q: QuestionKey, k: PersonKey): Cell[] {
  const [m] = measures(q, c, k);
  const unit = m.monthly ? 'a month' : undefined;
  const added = m.plan - m.today;
  const short = m.need - m.plan;
  return [
    { label: 'Needed', value: lump(m.need, m.monthly), sub: unit },
    { label: 'In place today', value: lump(m.today, m.monthly), sub: unit },
    {
      label: 'The plan adds',
      value: Math.abs(added) > 0.5 ? `${added > 0 ? '+' : '−'}${lump(Math.abs(added), m.monthly)}` : '—',
      sub: Math.abs(added) > 0.5 ? unit : 'nothing yet',
      tone: added > 0.5 ? 'plan' : undefined,
    },
    short > 0.5
      ? { label: 'Still short', value: lump(short, m.monthly), sub: unit, tone: 'bad' }
      : { label: short < -0.5 ? 'More than needed' : 'Still short', value: short < -0.5 ? lump(-short, m.monthly) : 'nothing', sub: short < -0.5 ? unit : 'fully provided for' },
  ];
}

const NEED_WORDS = { add: 'a part of the need', total: 'the whole need', less: 'already in place', plan: 'added by the plan', good: 'to spare', bad: 'still short' };
const NEED_READ =
  'Read from the top. Each hatched line adds one part of the need, building to the cross-hatched whole. The stippled lines take away what is already in place, and the blue line what the plan adds. Whatever is left, hatched in red, is still short.';
const ESTATE_READ =
  'Each hatched line is a cost the executor must pay, building to the cash the estate needs. The stippled line takes away the cash already in the estate, and the blue line what the plan adds. Anything left, hatched in red, would have to be found by selling assets.';

const plateTitle = (q: QuestionKey, n: string, months: number) => {
  switch (q) {
    case 'life':
      return `What the family would need should ${n} die, and what would meet it`;
    case 'income':
      return `The income that would stop should ${n} be unable to work, and what would replace it`;
    case 'disability':
      return `The costs of a lasting disability for ${n}, and what would meet them`;
    case 'illness':
      return `The cost of ${n} recovering from a serious illness, and what would meet it`;
    case 'estate':
      return `What ${n}’s executor would have to pay, and the cash to pay it`;
    default:
      return `${cap(inWords(months))} months of essential spending, and the savings set aside for it`;
  }
};

function NeedSection({ q, n, c, plate, folio }: LeafNo & { q: QuestionKey }) {
  const ks = peopleFor(q, c);
  const [k, setK] = useState<PersonKey>(ks[0] ?? 'client');
  const who = ks.includes(k) ? k : (ks[0] ?? 'client');
  const bridge = bridgeFor(q, c, who)!;
  const acc = accountFor(q, c, who);
  const lever = COVER_OF[q];
  return (
    <Leaf id={sectionId(q)} folio={folio} labelledBy={`${sectionId(q)}-h`}>
      <QuestionHead q={q} n={n} c={c} k={who} setK={setK} />
      <Register cells={needCells(c, q, who)} />
      {c.levers && lever && (
        <Levers>
          <CoverLever c={c} k={who} cover={lever.cover} label={lever.label} />
        </Levers>
      )}
      {c.levers && q === 'emergency' && <EmergencyLevers c={c} />}
      <Plate n={plate} title={plateTitle(q, firstName(c.today, who), c.doc.assumptions.emergencyMonths)} keyItems={bridgeKey(bridge, NEED_WORDS)} read={q === 'estate' ? ESTATE_READ : NEED_READ}>
        <BridgeChart bridge={bridge} />
      </Plate>
      {acc && <AccountView title={accountTitle(q, c, who)} a={acc} />}
      <Matters items={acc ? acc.notes : settling(addressFor(q, c, who))} />
      <Basis lines={basisFor(q, c, who)} />
    </Leaf>
  );
}

const RET_WORDS = { add: 'savings and contributions', plan: 'extra saving in the plan', total: 'expected at retirement', bad: 'short of what is needed', good: 'more than is needed' };

function RetirementSection({ n, c, plate, folio }: LeafNo) {
  const ks = peopleFor('retirement', c);
  const [k, setK] = useState<PersonKey>(ks[0] ?? 'client');
  const who = ks.includes(k) ? k : (ks[0] ?? 'client');
  const t = c.today.persons[who]!.retirement;
  const p = c.planned.persons[who]!.retirement;
  const name = firstName(c.today, who);
  const lasts = (r: typeof p) => (r.depletionAge !== null && r.depletionAge < r.planningAge ? `age ${Math.floor(r.depletionAge)}` : `beyond ${r.planningAge}`);
  const runsShort = p.depletionAge !== null && p.depletionAge < p.planningAge;
  const changed = Math.abs(p.projectedCapitalReal - t.projectedCapitalReal) > 1 || p.retirementAge !== t.retirementAge;
  const bridge = bridgeFor('retirement', c, who)!;
  const groups = addressFor('retirement', c, who);
  return (
    <Leaf id={sectionId('retirement')} folio={folio} labelledBy={`${sectionId('retirement')}-h`}>
      <QuestionHead q="retirement" n={n} c={c} k={who} setK={setK} />
      <Register
        cells={[
          { label: 'Income wanted', value: money(p.targetMonthlyToday), sub: 'a month, in today’s money' },
          {
            label: 'It could pay',
            value: money(p.sustainableMonthlyToday),
            sub: changed ? `today ${money(t.sustainableMonthlyToday)}` : 'a month, for life',
            tone: p.sustainableMonthlyToday < p.targetMonthlyToday - 0.5 ? 'bad' : undefined,
          },
          { label: 'The money lasts to', value: lasts(p), sub: changed ? `today ${lasts(t)}` : `planning to ${p.planningAge}`, tone: runsShort ? 'bad' : undefined },
          { label: `Saved by ${p.retirementAge}`, value: moneyCompact(p.projectedCapitalReal), sub: `of ${moneyCompact(p.requiredCapitalReal)} needed` },
        ]}
      />
      {c.levers && <RetirementLevers c={c} k={who} />}
      <Plate
        n={plate}
        title={`${name}’s savings by age${changed ? ', today and with the plan' : ''}`}
        keyItems={projectionKey(t, p)}
        read="The line climbs while saving and falls once the income is drawn; a circle marks every fifth year. The dashed line is the capital needed at retirement, and a red cross marks the age at which the money would run out. Touch the plate for the figure at any age."
      >
        <ProjectionChart today={t} plan={p} />
      </Plate>
      <Plate
        n={plate + 1}
        title={`Where ${name}’s savings at ${p.retirementAge} would come from`}
        keyItems={bridgeKey(bridge, RET_WORDS)}
        read="Savings already made, grown to retirement, and the contributions still to come add up to what is expected at retirement, in today’s money. The dashed upright marks what is needed."
      >
        <BridgeChart bridge={bridge} />
      </Plate>
      <Schedule groups={itemised(groups)} />
      <Matters items={settling(groups)} />
      <Basis lines={basisFor('retirement', c, who)} />
    </Leaf>
  );
}

function SavingSection({ q, n, c, plate, folio }: LeafNo & { q: 'education' | 'goals' }) {
  const rows = measures(q, c);
  const need = rows.reduce((s, r) => s + r.need, 0);
  const today = rows.reduce((s, r) => s + Math.min(r.today, r.need), 0);
  const plan = rows.reduce((s, r) => s + Math.min(r.plan, r.need), 0);
  const short = need - plan;
  const series = q === 'education' ? studyCosts(c) : [];
  const years = series.flatMap((x) => x.points.map((p) => p.year));
  const both = q === 'education' && years.length > 0;
  const groups = addressFor(q, c);
  return (
    <Leaf id={sectionId(q)} folio={folio} labelledBy={`${sectionId(q)}-h`}>
      <QuestionHead q={q} n={n} c={c} k="client" setK={() => {}} />
      <Register
        cells={[
          { label: q === 'education' ? 'Needed as studies start' : 'Cost when due', value: moneyCompact(need) },
          { label: 'Expected to be saved', value: moneyCompact(today) },
          {
            label: 'The plan adds',
            value: plan - today > 0.5 ? `+${moneyCompact(plan - today)}` : '—',
            sub: plan - today > 0.5 ? undefined : 'nothing yet',
            tone: plan - today > 0.5 ? 'plan' : undefined,
          },
          short > 0.5 ? { label: 'Still short', value: moneyCompact(short), tone: 'bad' } : { label: 'Still short', value: 'nothing', sub: 'fully provided for' },
        ]}
      />
      {c.levers && (q === 'education' ? <EducationLevers c={c} /> : <GoalLevers c={c} />)}
      {both && (
        <Plate
          n={plate}
          title={`University costs year by year, ${Math.min(...years)} to ${Math.max(...years)}`}
          keyItems={costKey(series)}
          read="Each column is one year’s fees and costs, in the money of the year they are paid, so they rise each year with education inflation."
        >
          <CostColumns series={series} />
        </Plate>
      )}
      <Plate
        n={plate + (both ? 1 : 0)}
        title={q === 'education' ? 'Each child’s studies, against what is being saved' : 'Each goal, against what is being saved'}
        keyItems={STRIP_KEY}
        read={`Each box is what ${q === 'education' ? 'a child’s studies will need when they start' : 'a goal will cost when it is due'}. The hatching is what is expected to be saved by then, blue what the plan adds, and red what is still short.`}
      >
        <NeedStrips rows={rows} met={q === 'education' ? 'paid for' : 'on track'} />
      </Plate>
      <Schedule groups={itemised(groups)} />
      <Matters items={settling(groups)} />
      <Basis lines={basisFor(q, c)} />
    </Leaf>
  );
}

const BUDGET_WORDS = { total: 'take-home pay', less: 'spent each month', plan: 'the plan’s changes', good: 'left over', bad: 'overspent' };

function AffordSection({ n, c, plate, folio }: LeafNo) {
  const t = c.today.cashflow;
  const p = c.planned.cashflow;
  const cost = p.totalOutgoings - t.totalOutgoings;
  const bridge = bridgeFor('afford', c)!;
  const groups = addressFor('afford', c);
  return (
    <Leaf id={sectionId('afford')} folio={folio} labelledBy={`${sectionId('afford')}-h`}>
      <QuestionHead q="afford" n={n} c={c} k="client" setK={() => {}} />
      <Register
        cells={[
          { label: 'Take-home pay', value: money(p.takeHome), sub: 'a month' },
          { label: 'Spoken for today', value: money(t.totalOutgoings), sub: 'a month' },
          { label: 'The plan', value: Math.abs(cost) > 0.5 ? money(cost, { sign: true }) : '—', sub: Math.abs(cost) > 0.5 ? 'a month' : 'nothing yet', tone: cost > 0.5 ? 'plan' : undefined },
          { label: 'Left over', value: money(p.surplus), sub: Math.abs(p.surplus - t.surplus) > 0.5 ? `today ${money(t.surplus)}` : 'a month', tone: p.surplus < -0.5 ? 'bad' : undefined },
        ]}
      />
      {c.levers && <AffordLevers c={c} />}
      <Plate
        n={plate}
        title="Where each month’s pay goes"
        keyItems={bridgeKey(bridge, BUDGET_WORDS)}
        read="Read from the top: take-home pay, less each kind of spending, leaves what is free each month. The blue lines are the plan’s changes, and the last line is what would be free once they are made."
      >
        <BridgeChart bridge={bridge} />
      </Plate>
      {c.summary.unquoted > 0 && (
        <p className="pv-note">
          {cap(inWords(c.summary.unquoted))} new cover{c.summary.unquoted === 1 ? ' has' : 's have'} no premium yet, so the plan will cost more once {c.summary.unquoted === 1 ? 'it is' : 'they are'}{' '}
          quoted.
        </p>
      )}
      <Schedule groups={itemised(groups)} />
      <Matters items={settling(groups)} />
      <Basis lines={basisFor('afford', c)} />
    </Leaf>
  );
}

export function QuestionSection({ q, ...rest }: LeafNo & { q: QuestionKey }) {
  if (q === 'retirement') return <RetirementSection {...rest} />;
  if (q === 'education' || q === 'goals') return <SavingSection q={q} {...rest} />;
  if (q === 'afford') return <AffordSection {...rest} />;
  return <NeedSection q={q} {...rest} />;
}

/* ------------------------------------------------------------------ */
/* The title leaf, with the contents                                   */
/* ------------------------------------------------------------------ */

export function StartingPoints({ c }: { c: SectionCtx }) {
  const b = (label: string, title: string, onClick: () => void) => (
    <button type="button" onClick={onClick} title={title} className="pv-button-2">
      {label}
    </button>
  );
  return (
    <div className="pv-begin">
      <span>Begin from</span>
      {b('Every gap closed', 'Cover, saving and education amounts that meet every need — then dial back to what fits the budget', () => c.setPlan(() => closeEveryGap(c.doc, c.today)))}
      {c.doc.advice.recommendations.length > 0 && b('The recorded advice', 'The recommendations already in the record of advice', () => c.setPlan(() => planFromAdvice(c.doc, c.today)))}
      {c.summary.changes.length > 0 && b('No changes', 'Clear every lever', () => c.setPlan(() => emptyPlan()))}
    </div>
  );
}

const lowerFirst = (s: string) => (/^[A-Z][a-z]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s);

export function ContentsSection({ c, list, title, prepared, firm }: { c: SectionCtx; list: QuestionKey[]; title: string; prepared: string; firm: string }) {
  const statuses = list.flatMap((q) => (isPerPerson(q) ? peopleFor(q, c).map((k) => verdicts(q, c, k).plan.status) : q === 'afford' ? [] : [verdicts(q, c).plan.status]));
  return (
    <Leaf id="contents" folio={1} labelledBy="contents-h" className="pv-title-leaf">
      <div className="pv-title-top">
        {firm && <span>{firm}</span>}
        <span>Private &amp; Confidential</span>
      </div>
      <div className="pv-title-kicker">The Planning Session</div>
      <div className="pv-title-of">for</div>
      <h1 id="contents-h" className="pv-title-main">
        {title}
      </h1>
      <Ornament width={210} />
      <p className="pv-title-sub">being a working session on the questions you asked: where you stand today, what would answer each one, and what it would cost.</p>
      <p className="pv-imprint">{prepared}</p>
      <p className="pv-verdict">{verdict(statuses, true).replace(/\.$/, c.summary.changes.length ? ', with the plan.' : '.')}</p>
      <nav className="pv-contents" aria-label="The questions">
        <div className="pv-contents-head">Contents</div>
        <ol>
          {list.map((q, i) => {
            const ks: (PersonKey | null)[] = isPerPerson(q) ? peopleFor(q, c) : [null];
            return (
              <li key={q}>
                <button type="button" onClick={() => c.go(sectionId(q))}>
                  <span className="pv-q">
                    <span className="pv-q-no">{roman(i + 1)}.</span>
                    <span className="pv-q-text">{agendaText(q, c)}</span>
                  </span>
                  <span className="pv-q-status">
                    {ks.map((k) => {
                      const v = verdicts(q, c, k ?? 'client');
                      return (
                        <StatusMark
                          key={k ?? 'h'}
                          status={v.plan.status}
                          text={
                            <>
                              {k && ks.length > 1 && <i>{firstName(c.today, k)}: </i>}
                              {v.plan.text}
                              {v.today.text !== v.plan.text && <span className="pv-was"> (today {lowerFirst(v.today.text)})</span>}
                            </>
                          }
                        />
                      );
                    })}
                  </span>
                </button>
              </li>
            );
          })}
          <li>
            <button type="button" onClick={() => c.go('plan')}>
              <span className="pv-q">
                <span className="pv-q-no">{roman(list.length + 1)}.</span>
                <span className="pv-q-text">What have we agreed?</span>
              </span>
            </button>
          </li>
        </ol>
      </nav>
      <Plate
        n={1}
        title="How much of each need is provided for, today and with the plan"
        keyItems={PROVISION_KEY}
        read="Each bar is one need, drawn to its own full measure: the hatching is what is provided for today, blue what the plan adds, and red what remains. Choose one to turn to its question."
      >
        <ProvisionStrips rows={fundedRows(c)} onSelect={(q) => c.go(sectionId(q))} />
      </Plate>
      <StartingPoints c={c} />
    </Leaf>
  );
}

/* ------------------------------------------------------------------ */
/* What have we agreed?                                                */
/* ------------------------------------------------------------------ */

export function PlanSection({ c, folio, action }: { c: SectionCtx; folio: number; action: ReactNode }) {
  const s = c.summary;
  const cost = c.planned.cashflow.totalOutgoings - c.today.cashflow.totalOutgoings;
  const groups = QUESTIONS.map((q) => ({ q, changes: s.changes.filter((ch) => questionOfChange(ch.key) === q) })).filter((g) => g.changes.length);
  const a: Answer =
    s.changes.length === 0
      ? { status: 'na', lead: 'Nothing yet.', parts: ['Move a lever under any question, or begin from one of the starting points below.'] }
      : s.surplusPlan >= -0.5
        ? {
            status: 'covered',
            lead: 'It fits.',
            parts:
              cost > 0.5
                ? ['The changes cost ', { b: `${money(cost)} a month` }, ', leaving ', { b: `${money(s.surplusPlan)} a month` }, ' free.']
                : ['After the changes, ', { b: `${money(s.surplusPlan)} a month` }, ' is free.'],
          }
        : {
            status: 'shortfall',
            lead: 'It does not fit yet.',
            parts:
              cost > 0.5
                ? ['The changes cost ', { b: `${money(cost)} a month` }, ', which is ', { b: money(-s.surplusPlan) }, ' more than is free.']
                : ['Spending would still be ', { b: `${money(-s.surplusPlan)} a month` }, ' more than comes in.'],
          };
  let no = 0;
  return (
    <Leaf id="plan" folio={folio} labelledBy="plan-h">
      <ChapterHead id="plan-h" kicker="In Conclusion" title="What have we agreed?" sub="the changes made in this session, and what they would cost" />
      <Short a={a} />
      {s.changes.length > 0 && (
        <Register
          cells={[
            { label: 'The plan costs', value: money(cost, { sign: true }), sub: 'a month', tone: cost > 0.5 ? 'plan' : undefined },
            { label: 'Free today', value: money(s.surplusToday), sub: 'a month' },
            { label: 'Free with the plan', value: money(s.surplusPlan), sub: 'a month', tone: s.surplusPlan < -0.5 ? 'bad' : undefined },
            {
              label: 'From the bonus',
              value: s.bonusUsed > 0.5 ? money(s.bonusUsed) : '—',
              sub: `of ${money(s.bonusAvailable)}, once-off`,
              tone: s.bonusUsed > s.bonusAvailable + 0.5 ? 'bad' : undefined,
            },
          ]}
        />
      )}
      {s.unquoted > 0 && (
        <p className="pv-note">
          {cap(inWords(s.unquoted))} new cover{s.unquoted === 1 ? ' has' : 's have'} no premium yet, so the cost will rise once {s.unquoted === 1 ? 'it is' : 'they are'} quoted.
        </p>
      )}
      {groups.length > 0 && (
        <div className="pv-articles">
          {groups.map(({ q, changes }) => (
            <div key={q}>
              <div className="pv-subhead">
                <button type="button" onClick={() => c.go(sectionId(q))} className="hover:underline">
                  {TOPIC[q]}
                </button>
              </div>
              <ol>
                {changes.map((ch) => (
                  <li key={ch.key} className="pv-article">
                    <span className="pv-article-no">{++no}.</span>
                    <Leaders
                      rows={[
                        {
                          key: ch.key,
                          label: ch.label,
                          value: Math.abs(ch.monthly) > 0.5 ? `${money(ch.monthly, { sign: true })} a month` : (ch.detail ?? ''),
                          gloss: Math.abs(ch.monthly) > 0.5 ? ch.detail : undefined,
                        },
                      ]}
                    />
                    <button type="button" onClick={() => c.setPlan((p) => withoutChange(p, ch.key))} className="pv-strike" aria-label={`Strike out: ${ch.label}`} title="Strike it out of the plan">
                      <X size={18} />
                    </button>
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </div>
      )}
      <div className="pv-plan-actions">
        {action}
        <StartingPoints c={c} />
      </div>
    </Leaf>
  );
}
