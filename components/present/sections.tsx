'use client';

import { Sparkles, Undo2, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { closeEveryGap, COVER, discretionarySpending, emptyPlan, personPlan, planFromAdvice, withoutChange, type CoverKey, type PlanSummary } from '@/lib/fna/plan';
import type { PersonKey, PersonPlan } from '@/lib/fna/types';
import { money, moneyCompact, pct } from '@/lib/format';
import { cx } from '../ui';
import { agendaText, answer, firstName, isPerPerson, measures, peopleFor, QUESTIONS, questionOfChange, questionText, verdicts, type Answer, type QuestionKey } from './answers';
import { addressFor, basisFor, bridgeFor, fundedRows, retirementSources, studyCosts, type AddressGroup, type PlanCtx } from './brief';
import { BridgeChart, bridgeLegend, BulletChart, CostColumns, DumbbellChart, Plate, ProjectionChart, StatusIcon, StatusMark } from './charts';
import { roman } from '../report/vintage/ink';
import { verdict } from '../report/vintage/narrative';
import { CheckRow, LeverCard, RandInput, Slider, Tabs, YearStepper } from './levers';

export interface SectionCtx extends PlanCtx {
  setPlan: (update: (p: PlanCtx['plan']) => PlanCtx['plan']) => void;
  summary: PlanSummary;
  /** Whether the levers are showing (the presenter can hide them). */
  levers: boolean;
  go: (id: string) => void;
}

/** Short names for the contents bar. */
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

export const KICKER: Record<QuestionKey, string> = {
  life: 'If someone dies',
  income: 'If someone cannot work',
  disability: 'Permanent disability',
  illness: 'Serious illness',
  estate: 'The estate',
  retirement: 'Retirement',
  education: 'The children’s studies',
  goals: 'Goals',
  emergency: 'Emergencies',
  afford: 'The budget',
};

export const sectionId = (q: QuestionKey) => `q-${q}`;

const roundUp = (n: number, step: number) => (n > 0 ? Math.ceil(n / step) * step : 0);
const niceTop = (n: number, step: number) => Math.max(step * 10, roundUp(n, step * 10));
const updatePerson = (c: SectionCtx, k: PersonKey, patch: Partial<PersonPlan>) => c.setPlan((p) => ({ ...p, people: { ...p.people, [k]: { ...personPlan(p, k), ...patch } } }));

/* ------------------------------------------------------------------ */
/* Typographic pieces                                                  */
/* ------------------------------------------------------------------ */

const Rule3 = () => (
  <div className="pv-rule3" aria-hidden>
    <span />
    <span />
    <span />
  </div>
);

export function AnswerLine({ a }: { a: Answer }) {
  return (
    <p className="pv-answer" aria-live="polite">
      {a.status !== 'na' && <StatusIcon status={a.status} size={22} />}
      {a.lead && <span className="pv-lead">{a.lead} </span>}
      {a.parts.map((p, i) => (typeof p === 'string' ? <span key={i}>{p}</span> : <b key={i}>{p.b}</b>))}
    </p>
  );
}

interface Cell {
  label: string;
  value: string;
  sub?: ReactNode;
  tone?: 'bad' | 'good' | 'plan';
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

function Address({ groups, columns = false }: { groups: AddressGroup[]; columns?: boolean }) {
  let n = 0;
  return (
    <div className={cx('pv-address min-w-0', columns && 'gap-x-10 md:columns-2')}>
      {groups.map((g) => (
        <div key={g.heading} className="break-inside-avoid">
          <h3>{g.heading}</h3>
          <ol>
            {g.items.map((it) => (
              <li key={it.title}>
                <span className="pv-no">{roman(++n)}.</span>
                <div>
                  <div className="pv-leader">
                    <span>{it.title}</span>
                    {it.figure && (
                      <>
                        <span className="pv-dots" aria-hidden />
                        <span>{it.figure}</span>
                      </>
                    )}
                  </div>
                  <p>{it.text}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  );
}

function Basis({ lines }: { lines: string[] }) {
  if (!lines.length) return null;
  return (
    <div className="pv-basis">
      <span aria-hidden className="not-italic">
        ☞
      </span>
      <div>
        <span className="pv-sc not-italic">The basis. </span>
        {lines.map((l) => (
          <p key={l} className="inline">
            {l}{' '}
          </p>
        ))}
      </div>
    </div>
  );
}

function Levers({ children, cols = 1 }: { children: ReactNode; cols?: 1 | 2 | 3 }) {
  return (
    <div>
      <div className="pv-levers-head">
        <span className="pv-kicker">What would change the answer</span>
      </div>
      <div className={cx('mx-auto grid items-start gap-4', cols === 1 ? 'max-w-xl' : cols === 2 ? 'md:grid-cols-2' : 'md:grid-cols-2 xl:grid-cols-3')}>{children}</div>
    </div>
  );
}

function Head({ q, n, c, k, setK }: { q: QuestionKey; n: number; c: SectionCtx; k: PersonKey; setK: (k: PersonKey) => void }) {
  const ks = peopleFor(q, c);
  return (
    <header>
      <span className="pv-kicker">
        {roman(n)} · {KICKER[q]}
      </span>
      <Rule3 />
      <h2 id={`${sectionId(q)}-h`} className="pv-title">
        {questionText(q, c, k)}
      </h2>
      {ks.length > 1 && (
        <div className="pv-person">
          <Tabs label="Whose question" value={k} onChange={setK} options={ks.map((x) => ({ value: x, label: firstName(c.today, x) }))} />
        </div>
      )}
      <AnswerLine a={answer(q, c, k, c.summary.changes.length)} />
    </header>
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
  const fmt = (v: number) => (v ? `+${monthly ? `${money(v)} pm` : moneyCompact(v)}` : 'none');
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
        {lv.amount > 0 && <RandInput label="Premium quoted" value={lv.premium} onChange={(v) => set({ premium: v })} />}
      </Slider>
    </LeverCard>
  );
}

function BonusLine({ c }: { c: SectionCtx }) {
  const { bonusUsed: used, bonusAvailable: bonus } = c.summary;
  if (used <= 0.5) return null;
  return used > bonus + 0.5 ? (
    <p className="mt-3 text-[15px]">
      <StatusMark status="shortfall" text={`${money(used)} planned from the bonus — ${money(used - bonus)} more than the ${money(bonus)} available.`} />
    </p>
  ) : (
    <p className="mt-3 text-[15px]" style={{ color: 'var(--pv-ink-2)' }}>
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
          format={(v) => `${money(v)} pm`}
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
            format={(v) => (v ? `+${money(v)} pm` : 'none')}
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
      <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-2 text-[15.5px] md:col-span-2 xl:col-span-3" style={{ color: 'var(--pv-ink-2)' }}>
        <span className="italic">Stress test: what if investments grew</span>
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
        <span className="italic">a year?</span>
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
              format={(x) => (x ? `+${money(x)} pm` : 'none')}
              marker={target > 0 ? { value: target, label: 'closes the gap' } : undefined}
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
                format={(x) => (x ? `+${money(x)} pm` : 'none')}
                marker={target > 0 ? { value: target, label: 'closes the gap' } : undefined}
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
          format={(v) => (v ? `−${money(v)} pm` : 'no change')}
          hint={`Of the ${money(cap)} a month that is not essential.`}
          onChange={(v) => c.setPlan((p) => ({ ...p, spendingCut: Math.min(v, cap) }))}
        />
      </LeverCard>
      {debts.length > 0 && (
        <LeverCard>
          <div className="mb-3 text-[16px] font-semibold">Settle debt with the bonus</div>
          <div className="space-y-2">
            {debts.map((l) => (
              <CheckRow
                key={l.id}
                checked={c.plan.settleDebts.includes(l.id)}
                onChange={(on) => c.setPlan((p) => ({ ...p, settleDebts: on ? [...p.settleDebts, l.id] : p.settleDebts.filter((x) => x !== l.id) }))}
                label={l.description || l.type}
                detail={`${money(l.balance)} at ${pct(l.interestRate, 1)} · frees ${money(l.monthlyRepayment)} a month`}
              />
            ))}
          </div>
          <BonusLine c={c} />
        </LeverCard>
      )}
    </Levers>
  );
}

/* ------------------------------------------------------------------ */
/* The question sections                                               */
/* ------------------------------------------------------------------ */

const lump = (n: number, monthly?: boolean) => (monthly ? `${money(n)}` : moneyCompact(n));

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
      sub: Math.abs(added) > 0.5 ? unit : 'no change yet',
      tone: added > 0.5 ? 'plan' : undefined,
    },
    short > 0.5
      ? { label: 'Still short', value: lump(short, m.monthly), sub: unit, tone: 'bad' }
      : { label: short < -0.5 ? 'More than needed' : 'Still short', value: short < -0.5 ? lump(-short, m.monthly) : 'Nothing', tone: 'good' },
  ];
}

function NeedSection({ q, n, c }: { q: QuestionKey; n: number; c: SectionCtx }) {
  const ks = peopleFor(q, c);
  const [k, setK] = useState<PersonKey>(ks[0] ?? 'client');
  const who = ks.includes(k) ? k : (ks[0] ?? 'client');
  const bridge = bridgeFor(q, c, who)!;
  const [m] = q === 'emergency' ? measures(q, c) : measures(q, c, who);
  const lever = COVER_OF[q];
  const fmt = (v: number) => (m.monthly ? `${money(v)} a month` : moneyCompact(v));
  return (
    <>
      <Head q={q} n={n} c={c} k={who} setK={setK} />
      <Register cells={needCells(c, q, who)} />
      <div className="mt-8 grid items-start gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Plate
          title={
            <>
              <b>{fmt(m.need)} needed</b>, {fmt(m.today)} in place
              {Math.abs(m.plan - m.today) > 0.5 ? `, ${fmt(Math.abs(m.plan - m.today))} ${m.plan > m.today ? 'added' : 'taken away'} by the plan` : ''}.
            </>
          }
          sub={`What the need is made of, what already pays towards it, and what is left. ${m.monthly ? 'Rand a month.' : 'Rand, in today’s money.'}`}
          legend={bridgeLegend(bridge)}
        >
          <BridgeChart bridge={bridge} />
        </Plate>
        <Address groups={addressFor(q, c, who)} />
      </div>
      <div className="mt-6">
        <Basis lines={basisFor(q, c, who)} />
      </div>
      {c.levers && lever && (
        <Levers>
          <CoverLever c={c} k={who} cover={lever.cover} label={lever.label} />
        </Levers>
      )}
      {c.levers && q === 'emergency' && <EmergencyLevers c={c} />}
    </>
  );
}

function RetirementSection({ n, c }: { n: number; c: SectionCtx }) {
  const ks = peopleFor('retirement', c);
  const [k, setK] = useState<PersonKey>(ks[0] ?? 'client');
  const who = ks.includes(k) ? k : (ks[0] ?? 'client');
  const t = c.today.persons[who]!.retirement;
  const p = c.planned.persons[who]!.retirement;
  const s = retirementSources(c, who);
  const lasts = (r: typeof p) => (r.depletionAge !== null && r.depletionAge < r.planningAge ? `age ${Math.floor(r.depletionAge)}` : `${r.planningAge}+`);
  const runsShort = p.depletionAge !== null && p.depletionAge < p.planningAge;
  const changed = Math.abs(p.projectedCapitalReal - t.projectedCapitalReal) > 1 || p.retirementAge !== t.retirementAge;
  const bridge = bridgeFor('retirement', c, who)!;
  return (
    <>
      <Head q="retirement" n={n} c={c} k={who} setK={setK} />
      <Register
        cells={[
          { label: 'Income wanted', value: money(p.targetMonthlyToday), sub: 'a month, today’s money' },
          {
            label: 'It could pay',
            value: money(p.sustainableMonthlyToday),
            sub: changed ? `today ${money(t.sustainableMonthlyToday)}` : 'a month, for life',
            tone: p.sustainableMonthlyToday >= p.targetMonthlyToday - 0.5 ? 'good' : 'bad',
          },
          { label: 'The money lasts to', value: lasts(p), sub: changed ? `today ${lasts(t)}` : `planning to ${p.planningAge}`, tone: runsShort ? 'bad' : 'good' },
          { label: `Saved by ${p.retirementAge}`, value: moneyCompact(s.total), sub: `of ${moneyCompact(s.needed)} needed` },
        ]}
      />
      <div className="mt-8">
        <Plate
          title={
            <>
              <b>{runsShort ? `The money runs out at ${Math.floor(p.depletionAge!)}` : `The money lasts beyond ${p.planningAge}`}</b>
              {changed
                ? ` with the plan; on today’s path it ${t.depletionAge !== null && t.depletionAge < t.planningAge ? `runs out at ${Math.floor(t.depletionAge)}` : `lasts beyond ${t.planningAge}`}.`
                : ', drawing the income wanted.'}
            </>
          }
          sub="Retirement savings by age, while saving and then while drawing the income. Rand, in today’s money."
        >
          <ProjectionChart today={t} plan={p} />
        </Plate>
      </div>
      <div className="mt-8 grid items-start gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Plate
          title={
            <>
              <b>
                {moneyCompact(s.total)} expected at {p.retirementAge}
              </b>
              , against {moneyCompact(s.needed)} needed.
            </>
          }
          sub="Where the savings at retirement come from. Rand, in today’s money."
          legend={bridgeLegend(bridge)}
        >
          <BridgeChart bridge={bridge} />
        </Plate>
        <Address groups={addressFor('retirement', c, who)} />
      </div>
      <div className="mt-6">
        <Basis lines={basisFor('retirement', c, who)} />
      </div>
      {c.levers && <RetirementLevers c={c} k={who} />}
    </>
  );
}

function SavingSection({ q, n, c }: { q: 'education' | 'goals'; n: number; c: SectionCtx }) {
  const rows = measures(q, c);
  const need = rows.reduce((s, r) => s + r.need, 0);
  const today = rows.reduce((s, r) => s + Math.min(r.today, r.need), 0);
  const plan = rows.reduce((s, r) => s + Math.min(r.plan, r.need), 0);
  const short = need - plan;
  const series = q === 'education' ? studyCosts(c) : [];
  const years = series.flatMap((x) => x.points.map((p) => p.year));
  const total = series.reduce((s, x) => s + x.points.reduce((t, p) => t + p.cost, 0), 0);
  return (
    <>
      <Head q={q} n={n} c={c} k="client" setK={() => {}} />
      <Register
        cells={[
          { label: q === 'education' ? 'Needed when studies start' : 'Cost when due', value: moneyCompact(need) },
          { label: 'Expected to be saved', value: moneyCompact(today) },
          {
            label: 'The plan adds',
            value: plan - today > 0.5 ? `+${moneyCompact(plan - today)}` : '—',
            sub: plan - today > 0.5 ? undefined : 'no change yet',
            tone: plan - today > 0.5 ? 'plan' : undefined,
          },
          short > 0.5 ? { label: 'Still short', value: moneyCompact(short), tone: 'bad' } : { label: 'Still short', value: 'Nothing', tone: 'good' },
        ]}
      />
      <div className={cx('mt-8 grid items-start gap-8', q === 'education' && 'lg:grid-cols-2')}>
        {q === 'education' && years.length > 0 && (
          <Plate
            title={
              <>
                <b>Studies will cost {moneyCompact(total)}</b> between {Math.min(...years)} and {Math.max(...years)}.
              </>
            }
            sub="University costs each year, rising with education inflation. Rand, in the year they are paid."
          >
            <CostColumns series={series} />
          </Plate>
        )}
        <Plate
          title={
            short > 0.5 ? (
              <>
                <b>{moneyCompact(short)} short</b> across {rows.length === 1 ? rows[0].label : `${rows.length} ${q === 'education' ? 'children' : 'goals'}`}.
              </>
            ) : (
              <>
                <b>Fully provided for</b>, every {q === 'education' ? 'child' : 'goal'}.
              </>
            )
          }
          sub={
            q === 'education'
              ? 'What each child needs when studies start, against what is expected to be saved by then.'
              : 'What each goal will cost when it is due, against what is expected to be saved by then.'
          }
        >
          <BulletChart rows={rows} met={q === 'education' ? 'Paid for' : 'On track'} />
        </Plate>
      </div>
      <div className="mt-8">
        <Address groups={addressFor(q, c)} columns />
      </div>
      <Basis lines={basisFor(q, c)} />
      {c.levers && (q === 'education' ? <EducationLevers c={c} /> : <GoalLevers c={c} />)}
    </>
  );
}

function AffordSection({ n, c }: { n: number; c: SectionCtx }) {
  const t = c.today.cashflow;
  const p = c.planned.cashflow;
  const cost = p.totalOutgoings - t.totalOutgoings;
  const bridge = bridgeFor('afford', c)!;
  return (
    <>
      <Head q="afford" n={n} c={c} k="client" setK={() => {}} />
      <Register
        cells={[
          { label: 'Take-home pay', value: money(p.takeHome), sub: 'a month' },
          { label: 'Spoken for today', value: money(t.totalOutgoings), sub: 'a month' },
          { label: 'The plan', value: Math.abs(cost) > 0.5 ? money(cost, { sign: true }) : '—', sub: Math.abs(cost) > 0.5 ? 'a month' : 'no change yet', tone: cost > 0.5 ? 'plan' : undefined },
          { label: 'Left over', value: money(p.surplus), sub: Math.abs(p.surplus - t.surplus) > 0.5 ? `today ${money(t.surplus)}` : 'a month', tone: p.surplus >= -0.5 ? 'good' : 'bad' },
        ]}
      />
      <div className="mt-8 grid items-start gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Plate
          title={
            <>
              <b>{money(t.takeHome)} comes in</b> each month; {p.surplus >= -0.5 ? `${money(p.surplus)} is left over` : `the plan spends ${money(-p.surplus)} more than that`}
              {c.summary.changes.length ? ' with the plan' : ''}.
            </>
          }
          sub="Take-home pay, through each kind of spending, to what is left over, and what the plan changes. Rand a month."
          legend={bridgeLegend(bridge)}
        >
          <BridgeChart bridge={bridge} />
        </Plate>
        <Address groups={addressFor('afford', c)} />
      </div>
      {c.summary.unquoted > 0 && (
        <p className="mt-6 text-center italic" style={{ color: 'var(--pv-ink-2)' }}>
          {c.summary.unquoted} new cover{c.summary.unquoted === 1 ? ' has' : 's have'} no premium yet, so the plan will cost more once {c.summary.unquoted === 1 ? 'it is' : 'they are'} quoted.
        </p>
      )}
      <div className="mt-4">
        <Basis lines={basisFor('afford', c)} />
      </div>
      {c.levers && <AffordLevers c={c} />}
    </>
  );
}

export function QuestionSection({ q, n, c }: { q: QuestionKey; n: number; c: SectionCtx }) {
  return (
    <section id={sectionId(q)} className="pv-section" aria-labelledby={`${sectionId(q)}-h`}>
      {q === 'retirement' ? (
        <RetirementSection n={n} c={c} />
      ) : q === 'education' || q === 'goals' ? (
        <SavingSection q={q} n={n} c={c} />
      ) : q === 'afford' ? (
        <AffordSection n={n} c={c} />
      ) : (
        <NeedSection q={q} n={n} c={c} />
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Contents                                                            */
/* ------------------------------------------------------------------ */

export function StartingPoints({ c }: { c: SectionCtx }) {
  const b = (label: string, title: string, onClick: () => void, icon: ReactNode) => (
    <button type="button" onClick={onClick} title={title} className="pv-link-btn">
      {icon} {label}
    </button>
  );
  return (
    <div className="flex flex-wrap items-center justify-center gap-2 text-[15.5px]" style={{ color: 'var(--pv-ink-2)' }}>
      <span className="mr-1 italic">Start from:</span>
      {b(
        'Every gap closed',
        'Cover, saving and education amounts that meet every need — then dial back to what fits the budget',
        () => c.setPlan(() => closeEveryGap(c.doc, c.today)),
        <Sparkles size={15} />,
      )}
      {c.doc.advice.recommendations.length > 0 &&
        b('The recorded advice', 'The recommendations already in the record of advice', () => c.setPlan(() => planFromAdvice(c.doc, c.today)), <Sparkles size={15} />)}
      {c.summary.changes.length > 0 && b('Today, no changes', 'Clear every lever', () => c.setPlan(() => emptyPlan()), <Undo2 size={15} />)}
    </div>
  );
}

export function ContentsSection({ c, list, title, prepared }: { c: SectionCtx; list: QuestionKey[]; title: string; prepared: string }) {
  const statuses = list.flatMap((q) => (isPerPerson(q) ? peopleFor(q, c).map((k) => verdicts(q, c, k).plan.status) : q === 'afford' ? [] : [verdicts(q, c).plan.status]));
  return (
    <section id="contents" className="pv-section" aria-labelledby="contents-h">
      <span className="pv-kicker">The planning session</span>
      <Rule3 />
      <h1 id="contents-h" className="pv-title">
        {title}
      </h1>
      <p className="pv-answer">{prepared}</p>
      <p className="mx-auto mt-6 max-w-[38em] text-center text-[19px]">{verdict(statuses, true).replace(/\.$/, c.summary.changes.length ? ', with the plan.' : '.')}</p>
      <div className="mt-10 grid items-start gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <h2 className="pv-sc mb-2 border-b pb-1 text-[18px] font-normal" style={{ borderColor: 'var(--pv-ink)' }}>
            The questions
          </h2>
          <ol className="m-0 list-none p-0">
            {list.map((q, i) => {
              const ks: (PersonKey | null)[] = isPerPerson(q) ? peopleFor(q, c) : [null];
              return (
                <li key={q} className="border-b py-2.5" style={{ borderColor: 'var(--pv-faint)' }}>
                  <button type="button" onClick={() => c.go(sectionId(q))} className="grid w-full grid-cols-[2.4em_minmax(0,1fr)] text-left">
                    <span className="pv-sc" style={{ color: 'var(--pv-ink-2)' }}>
                      {roman(i + 1)}.
                    </span>
                    <span>
                      <span className="block text-[18px] leading-snug hover:underline">{agendaText(q, c)}</span>
                      <span className="mt-1 flex flex-wrap gap-x-5 gap-y-0.5 text-[15.5px]" style={{ color: 'var(--pv-ink-2)' }}>
                        {ks.map((k) => {
                          const v = verdicts(q, c, k ?? 'client');
                          return (
                            <span key={k ?? 'h'} className="inline-flex items-center gap-1.5">
                              {k && ks.length > 1 && <span className="italic">{firstName(c.today, k)}:</span>}
                              <StatusMark status={v.plan.status} text={v.plan.text} />
                              {v.today.text !== v.plan.text && (
                                <span className="text-[14px] italic">(today {/^[A-Z][a-z]/.test(v.today.text) ? v.today.text.charAt(0).toLowerCase() + v.today.text.slice(1) : v.today.text})</span>
                              )}
                            </span>
                          );
                        })}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
            <li className="py-2.5">
              <button type="button" onClick={() => c.go('plan')} className="grid w-full grid-cols-[2.4em_minmax(0,1fr)] text-left">
                <span className="pv-sc" style={{ color: 'var(--pv-ink-2)' }}>
                  {roman(list.length + 1)}.
                </span>
                <span className="text-[18px] italic hover:underline">What have we agreed?</span>
              </button>
            </li>
          </ol>
        </div>
        <Plate
          title={
            <>
              <b>How much of each need is provided for</b>, today and with the plan.
            </>
          }
          sub="Choose one to go to its question."
        >
          <DumbbellChart rows={fundedRows(c)} onSelect={(q) => c.go(sectionId(q))} />
        </Plate>
      </div>
      <div className="mt-10">
        <StartingPoints c={c} />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* What have we agreed?                                                */
/* ------------------------------------------------------------------ */

export function PlanSection({ c, n, action }: { c: SectionCtx; n: number; action: ReactNode }) {
  const s = c.summary;
  const cost = c.planned.cashflow.totalOutgoings - c.today.cashflow.totalOutgoings;
  const groups = QUESTIONS.map((q) => ({ q, changes: s.changes.filter((ch) => questionOfChange(ch.key) === q) })).filter((g) => g.changes.length);
  const a: Answer =
    s.changes.length === 0
      ? { status: 'na', lead: 'Nothing yet.', parts: ['Move a lever under any question, or start from one of the starting points.'] }
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
                ? ['The changes cost ', { b: `${money(cost)} a month` }, ' — ', { b: money(-s.surplusPlan) }, ' more than is free.']
                : ['Spending would still be ', { b: `${money(-s.surplusPlan)} a month` }, ' more than comes in.'],
          };
  return (
    <section id="plan" className="pv-section" aria-labelledby="plan-h">
      <span className="pv-kicker">{roman(n)} · The plan</span>
      <Rule3 />
      <h2 id="plan-h" className="pv-title">
        What have we agreed?
      </h2>
      <AnswerLine a={a} />
      {s.changes.length > 0 && (
        <Register
          cells={[
            { label: 'The plan costs', value: money(cost, { sign: true }), sub: 'a month', tone: cost > 0.5 ? 'plan' : undefined },
            { label: 'Free today', value: money(s.surplusToday), sub: 'a month' },
            { label: 'Free with the plan', value: money(s.surplusPlan), sub: 'a month', tone: s.surplusPlan >= -0.5 ? 'good' : 'bad' },
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
        <p className="mt-4 text-center italic" style={{ color: 'var(--pv-ink-2)' }}>
          {s.unquoted} new cover{s.unquoted === 1 ? ' has' : 's have'} no premium yet — the cost will rise once {s.unquoted === 1 ? 'it is' : 'they are'} quoted.
        </p>
      )}
      <div className="mt-6 flex flex-col items-center gap-4">
        {action}
        <StartingPoints c={c} />
      </div>
      {groups.length > 0 && (
        <div className="mx-auto mt-10 max-w-4xl">
          {groups.map(({ q, changes }) => (
            <div key={q} className="mb-6">
              <h3 className="pv-sc mb-2 flex items-baseline justify-between border-b pb-1 text-[17px] font-normal" style={{ borderColor: 'var(--pv-ink)' }}>
                <button type="button" onClick={() => c.go(sectionId(q))} className="hover:underline">
                  {KICKER[q]}
                </button>
              </h3>
              <ul className="m-0 list-none p-0">
                {changes.map((ch) => (
                  <li key={ch.key} className="flex items-start gap-3 py-1.5">
                    <div className="min-w-0 flex-1">
                      <div className="pv-leader">
                        <span className="!font-normal" style={{ fontVariantCaps: 'normal', letterSpacing: 0 }}>
                          {ch.label}
                        </span>
                        <span className="pv-dots" aria-hidden />
                        <span>{Math.abs(ch.monthly) > 0.5 ? `${money(ch.monthly, { sign: true })} pm` : (ch.detail ?? '')}</span>
                      </div>
                      {Math.abs(ch.monthly) > 0.5 && ch.detail && (
                        <div className="text-[15px] italic" style={{ color: 'var(--pv-ink-2)' }}>
                          {ch.detail}
                        </div>
                      )}
                    </div>
                    <button type="button" onClick={() => c.setPlan((p) => withoutChange(p, ch.key))} className="pv-icon-btn !h-8 !w-8" aria-label={`Remove: ${ch.label}`} title="Remove from the plan">
                      <X size={16} />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
