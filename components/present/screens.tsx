'use client';

import { Accessibility, ArrowRight, Briefcase, Flag, GraduationCap, HeartPulse, Info, Landmark, LifeBuoy, Sparkles, Sunset, Undo2, Users, Wallet, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { closeEveryGap, COVER, discretionarySpending, emptyPlan, personPlan, planFromAdvice, withoutChange, type CoverKey, type PlanSummary } from '@/lib/fna/plan';
import type { PersonKey, PersonPlan, PlanLevers } from '@/lib/fna/types';
import { money, moneyCompact, pct } from '@/lib/format';
import { cx } from '../ui';
import {
  agendaText,
  answer,
  firstName,
  isPerPerson,
  measures,
  peopleFor,
  personFor,
  QUESTIONS,
  questionOfChange,
  questionText,
  TOPIC,
  verdicts,
  whyLines,
  type Answer,
  type Ctx,
  type QuestionKey,
} from './answers';
import { CheckRow, LeverCard, RandInput, Slider, Tabs, YearStepper } from './levers';
import { CapacityBar, Figures, MoneyLasts, NeedPanel, NeedRows, StatusIcon, StatusMark, WhyThisMuch } from './visuals';

export interface ScreenCtx extends Ctx {
  plan: PlanLevers;
  setPlan: (update: (p: PlanLevers) => PlanLevers) => void;
  summary: PlanSummary;
  person: PersonKey;
  setPerson: (k: PersonKey) => void;
  /** Whether the levers are showing (the presenter can hide them). */
  levers: boolean;
  go: (screen: string) => void;
}

export const ICON: Record<QuestionKey, typeof Users> = {
  life: Users,
  income: Briefcase,
  disability: Accessibility,
  illness: HeartPulse,
  estate: Landmark,
  retirement: Sunset,
  education: GraduationCap,
  goals: Flag,
  emergency: LifeBuoy,
  afford: Wallet,
};

const roundUp = (n: number, step: number) => (n > 0 ? Math.ceil(n / step) * step : 0);
const niceTop = (n: number, step: number) => Math.max(step * 10, roundUp(n, step * 10));
const updatePerson = (c: ScreenCtx, k: PersonKey, patch: Partial<PersonPlan>) => c.setPlan((p) => ({ ...p, people: { ...p.people, [k]: { ...personPlan(p, k), ...patch } } }));

/* ------------------------------------------------------------------ */
/* Pieces every screen shares                                          */
/* ------------------------------------------------------------------ */

function Eyebrow({ icon: Icon, children }: { icon?: typeof Users; children: ReactNode }) {
  return (
    <div className="inline-flex items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.12em] text-[var(--pv-ink-2)]">
      {Icon && <Icon size={16} aria-hidden />} {children}
    </div>
  );
}

function Title({ children }: { children: ReactNode }) {
  return <h1 className="mt-3 max-w-5xl text-balance text-[clamp(28px,min(3.4vw,6.2vh),52px)] font-semibold leading-[1.08] tracking-[-0.02em]">{children}</h1>;
}

export function AnswerLine({ a }: { a: Answer }) {
  return (
    <p className="mt-4 flex max-w-4xl items-start gap-3 text-[clamp(18px,min(1.9vw,3.5vh),27px)] leading-snug text-[var(--pv-ink-2)]" aria-live="polite">
      {a.status !== 'na' && <StatusIcon status={a.status} size={26} className="mt-[0.18em]" />}
      <span className="text-balance">
        {a.lead && <span className="font-semibold text-[var(--pv-ink)]">{a.lead} </span>}
        {a.parts.map((p, i) =>
          typeof p === 'string' ? (
            <span key={i}>{p}</span>
          ) : (
            <b key={i} className="whitespace-nowrap font-semibold text-[var(--pv-ink)]">
              {p.b}
            </b>
          ),
        )}
      </span>
    </p>
  );
}

function Levers({ children, cols = 1 }: { children: ReactNode; cols?: 1 | 2 | 3 }) {
  return (
    <div>
      <div className="mb-3 text-[12.5px] font-semibold uppercase tracking-[0.12em] text-[var(--pv-ink-2)]">What would change the answer</div>
      <div className={cx('grid items-start gap-4', cols === 1 ? 'max-w-xl' : cols === 2 ? 'md:grid-cols-2' : 'md:grid-cols-2 xl:grid-cols-3')}>{children}</div>
    </div>
  );
}

function Note({ children }: { children: ReactNode }) {
  return (
    <p className="mt-4 flex items-start gap-2 text-[14.5px] text-[var(--pv-ink-2)]">
      <Info size={16} className="mt-[2px] shrink-0" aria-hidden /> <span>{children}</span>
    </p>
  );
}

/** A question on its own screen: the question, the answer in a sentence, one visual, and the levers that change it. */
function Frame({ q, c, k, children, levers }: { q: QuestionKey; c: ScreenCtx; k: PersonKey; children: ReactNode; levers?: ReactNode }) {
  const ks = peopleFor(q, c);
  return (
    <section key={`${q}-${k}`} className="pv-in">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Eyebrow icon={ICON[q]}>{TOPIC[q]}</Eyebrow>
        {ks.length > 1 && <Tabs label="Whose question" value={k} onChange={c.setPerson} options={ks.map((x) => ({ value: x, label: firstName(c.today, x) }))} />}
      </div>
      <Title>{questionText(q, c, k)}</Title>
      <AnswerLine a={answer(q, c, k, c.summary.changes.length)} />
      <div className="mt-[clamp(20px,4vh,40px)]">{children}</div>
      {c.levers && levers && <div className="mt-[clamp(24px,4.5vh,44px)]">{levers}</div>}
    </section>
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
const coverTarget = (c: ScreenCtx, k: PersonKey, cover: CoverKey, amount: number, step: number) => {
  const p = c.planned.persons[k]!;
  if (cover === 'estateCover') {
    // Cover paid to the estate attracts executor's fees, so it is grossed up.
    const fee = c.doc.assumptions.executorFeeRate * (1 + c.doc.assumptions.vatRate);
    return roundUp(amount + (p.estate.cashRequired - p.estate.cashAvailable) / (1 - fee), step);
  }
  const n = p[NEED_OF[cover]];
  return roundUp(amount + n.need - n.provision, step);
};

function CoverLever({ c, k, cover, label }: { c: ScreenCtx; k: PersonKey; cover: CoverKey; label: string }) {
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

const bonusLine = (c: ScreenCtx) => {
  const { bonusUsed: used, bonusAvailable: bonus } = c.summary;
  if (used <= 0.5) return null;
  return used > bonus + 0.5 ? (
    <p className="mt-3 text-[13.5px]">
      <StatusMark status="shortfall" text={`${money(used)} planned from the bonus — ${money(used - bonus)} more than the ${money(bonus)} available.`} />
    </p>
  ) : (
    <p className="mt-3 text-[13.5px] text-[var(--pv-ink-2)]">
      {money(used)} planned from the {money(bonus)} bonus.
    </p>
  );
};

/* ------------------------------------------------------------------ */
/* The question screens                                                */
/* ------------------------------------------------------------------ */

const COVER_OF: Partial<Record<QuestionKey, { cover: CoverKey; label: string }>> = {
  life: { cover: 'lifeCover', label: 'Add life cover' },
  income: { cover: 'incomeProtection', label: 'Add income protection' },
  disability: { cover: 'disability', label: 'Add disability cover' },
  illness: { cover: 'severeIllness', label: 'Add severe illness cover' },
  estate: { cover: 'estateCover', label: 'Add life cover paid to the estate' },
};

function NeedScreen({ q, c, k }: { q: QuestionKey; c: ScreenCtx; k: PersonKey }) {
  const m = measures(q, c, k)[0];
  const why = whyLines(q, c, k);
  const lever = COVER_OF[q];
  const fmt = (x: number) => (m.monthly ? `${money(x)} a month` : moneyCompact(x));
  // Other parts of the plan can add to this need's provision too: cover paid to the estate, new savings.
  const own = lever ? personPlan(c.plan, k)[lever.cover].amount : 0;
  const other = Math.max(0, m.plan - m.today) - own;
  return (
    <Frame
      q={q}
      c={c}
      k={k}
      levers={
        lever && (
          <Levers>
            <CoverLever c={c} k={k} cover={lever.cover} label={lever.label} />
          </Levers>
        )
      }
    >
      <NeedPanel m={m} />
      {other > Math.max(0.5, m.need * 0.005) && <Note>What the plan adds includes {fmt(other)} from its other changes, such as cover paid to the estate or new savings.</Note>}
      {why && <WhyThisMuch title={`What makes up the ${fmt(m.need)}?`} lines={why} />}
    </Frame>
  );
}

function RetirementScreen({ c, k }: { c: ScreenCtx; k: PersonKey }) {
  const t = c.today.persons[k]!.retirement;
  const p = c.planned.persons[k]!.retirement;
  const lv = personPlan(c.plan, k);
  const fileAge = c.doc.retirement[k].retirementAge;
  const n = firstName(c.today, k);
  const changed = Math.abs(p.projectedCapitalReal - t.projectedCapitalReal) > 1 || p.retirementAge !== t.retirementAge;
  const target = roundUp(lv.extraRetirement + p.additionalMonthly, 100);
  return (
    <Frame
      q="retirement"
      c={c}
      k={k}
      levers={
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
                    : 'Into a retirement annuity, rising with salaries each year.'
                }
                onChange={(v) => updatePerson(c, k, { extraRetirement: v })}
              />
            </LeverCard>
          )}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[14px] text-[var(--pv-ink-2)] md:col-span-2 xl:col-span-3">
            <span>Stress test: what if investments grew</span>
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
            <span>a year? This changes every investment in the plan, not only {n}’s.</span>
          </div>
        </Levers>
      }
    >
      <MoneyLasts today={t} plan={p} />
      <div className="mt-3 flex flex-wrap items-center gap-x-8 gap-y-2 text-[15px] text-[var(--pv-ink-2)]">
        <span>
          Saved by {p.retirementAge}: <b className="font-semibold text-[var(--pv-ink)]">{moneyCompact(p.projectedCapitalReal)}</b>
          {changed && <> (today {moneyCompact(t.projectedCapitalReal)})</>}
        </span>
        <span>
          It could pay <b className="font-semibold text-[var(--pv-ink)]">{money(p.sustainableMonthlyToday)} a month</b> for life{changed && <> (today {money(t.sustainableMonthlyToday)})</>}
        </span>
        <span className="text-[13.5px]">In today’s money.</span>
      </div>
    </Frame>
  );
}

function EducationScreen({ c }: { c: ScreenCtx }) {
  const rows = measures('education', c);
  const kids = c.doc.dependants.filter((d) => rows.some((r) => r.key === d.id));
  return (
    <Frame
      q="education"
      c={c}
      k="client"
      levers={
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
      }
    >
      <NeedRows rows={rows} met="Paid for" />
      <Note>Amounts are what the studies will cost when they start, with fees rising each year.</Note>
    </Frame>
  );
}

function GoalsScreen({ c }: { c: ScreenCtx }) {
  const rows = measures('goals', c);
  const year = new Date().getFullYear();
  const goals = c.doc.goals.filter((g) => rows.some((r) => r.key === g.id));
  return (
    <Frame
      q="goals"
      c={c}
      k="client"
      levers={
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
      }
    >
      <NeedRows rows={rows} met="On track" />
      <Note>Amounts are what each goal will cost by then, with prices rising each year.</Note>
    </Frame>
  );
}

function EmergencyScreen({ c }: { c: ScreenCtx }) {
  const m = measures('emergency', c)[0];
  const why = whyLines('emergency', c);
  const bonus = c.summary.bonusAvailable;
  const v = c.plan.emergencyTopUp;
  const target = roundUp(v + Math.max(0, c.planned.emergency.need - c.planned.emergency.provision), 1_000);
  return (
    <Frame
      q="emergency"
      c={c}
      k="client"
      levers={
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
            {bonusLine(c)}
          </LeverCard>
        </Levers>
      }
    >
      <NeedPanel m={m} />
      {why && <WhyThisMuch title="What counts as essential?" lines={why} />}
    </Frame>
  );
}

function AffordScreen({ c }: { c: ScreenCtx }) {
  const cap = discretionarySpending(c.today);
  const bonus = c.today.cashflow.bonusNet;
  const unsecured = ['credit-card', 'store-card', 'personal-loan', 'overdraft', 'other'];
  // A bonus can reasonably settle unsecured or small debts; the home and car loans are left out.
  const debts = c.doc.liabilities.filter((l) => l.balance > 0 && (unsecured.includes(l.type) || l.balance <= Math.max(bonus, 1))).sort((a, b) => b.interestRate - a.interestRate);
  const s = c.summary;
  return (
    <Frame
      q="afford"
      c={c}
      k="client"
      levers={
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
              <div className="mb-3 text-[15px] font-semibold">Settle debt with the bonus</div>
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
              {bonusLine(c)}
            </LeverCard>
          )}
        </Levers>
      }
    >
      <CapacityBar takeHome={c.planned.cashflow.takeHome} today={c.today.cashflow.totalOutgoings} planned={c.planned.cashflow.totalOutgoings} />
      {s.unquoted > 0 && (
        <Note>
          {s.unquoted} new cover{s.unquoted === 1 ? ' has' : 's have'} no premium yet, so the plan will cost more once {s.unquoted === 1 ? 'it is' : 'they are'} quoted.
        </Note>
      )}
    </Frame>
  );
}

export function QuestionScreen({ q, c }: { q: QuestionKey; c: ScreenCtx }) {
  const k = personFor(q, c, c.person);
  switch (q) {
    case 'retirement':
      return <RetirementScreen c={c} k={k} />;
    case 'education':
      return <EducationScreen c={c} />;
    case 'goals':
      return <GoalsScreen c={c} />;
    case 'emergency':
      return <EmergencyScreen c={c} />;
    case 'afford':
      return <AffordScreen c={c} />;
    default:
      return <NeedScreen q={q} c={c} k={k} />;
  }
}

/* ------------------------------------------------------------------ */
/* Starting points                                                     */
/* ------------------------------------------------------------------ */

function StartingPoints({ c }: { c: ScreenCtx }) {
  const b = (label: string, title: string, onClick: () => void, icon: ReactNode) => (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--pv-border)] px-3 py-1.5 text-[14px] font-medium text-[var(--pv-ink-2)] transition-colors hover:border-[var(--pv-plan)] hover:text-[var(--pv-ink)]"
    >
      {icon} {label}
    </button>
  );
  return (
    <div className="flex flex-wrap items-center gap-2 text-[14px] text-[var(--pv-ink-2)]">
      <span className="mr-1">Start from:</span>
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

/* ------------------------------------------------------------------ */
/* The agenda                                                          */
/* ------------------------------------------------------------------ */

function AgendaCard({ q, c }: { q: QuestionKey; c: ScreenCtx }) {
  const Icon = ICON[q];
  const ks: (PersonKey | null)[] = isPerPerson(q) ? peopleFor(q, c) : [null];
  return (
    <button
      type="button"
      onClick={() => c.go(q)}
      className="pv-card group flex items-start gap-4 p-4 text-left transition-colors hover:border-[var(--pv-plan)] focus-visible:border-[var(--pv-plan)] focus-visible:outline-none sm:p-5"
    >
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--pv-plan-wash)] text-[var(--pv-plan)]">
        <Icon size={19} aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-balance text-[clamp(16.5px,1.3vw,19px)] font-semibold leading-snug">{agendaText(q, c)}</span>
        <span className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-[14.5px]">
          {ks.map((k) => {
            const v = verdicts(q, c, k ?? 'client');
            return (
              <span key={k ?? 'household'} className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5">
                {k && ks.length > 1 && <span className="text-[var(--pv-ink-2)]">{firstName(c.today, k)}</span>}
                <StatusMark status={v.plan.status} text={v.plan.text} />
                {v.today.text !== v.plan.text && (
                  <span className="text-[13px] text-[var(--pv-muted)]">today {/^[A-Z][a-z]/.test(v.today.text) ? v.today.text.charAt(0).toLowerCase() + v.today.text.slice(1) : v.today.text}</span>
                )}
              </span>
            );
          })}
        </span>
      </span>
      <ArrowRight size={18} className="mt-1 shrink-0 text-[var(--pv-plan)] opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
    </button>
  );
}

export function AgendaScreen({ c, list }: { c: ScreenCtx; list: QuestionKey[] }) {
  return (
    <section className="pv-in">
      <Title>What would you like to answer today?</Title>
      <p className="mt-3 max-w-3xl text-balance text-[clamp(17px,1.5vw,21px)] leading-snug text-[var(--pv-ink-2)]">
        Pick a question. Each one has its own screen, with the levers that change the answer.
      </p>
      <div className="mt-[clamp(20px,3.5vh,36px)] grid gap-3 lg:grid-cols-2">
        {list.map((q) => (
          <AgendaCard key={q} q={q} c={c} />
        ))}
      </div>
      <div className="mt-8">
        <StartingPoints c={c} />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* What have we agreed?                                                */
/* ------------------------------------------------------------------ */

export function PlanScreen({ c, action }: { c: ScreenCtx; action: ReactNode }) {
  const s = c.summary;
  const cost = c.planned.cashflow.totalOutgoings - c.today.cashflow.totalOutgoings;
  const groups = QUESTIONS.map((q) => ({ q, changes: s.changes.filter((ch) => questionOfChange(ch.key) === q) })).filter((g) => g.changes.length);
  const a: Answer =
    s.changes.length === 0
      ? { status: 'na', lead: 'Nothing yet.', parts: ['Move a lever on any question, or start from one of the starting points below.'] }
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
            lead: 'It doesn’t fit yet.',
            parts:
              cost > 0.5
                ? ['The changes cost ', { b: `${money(cost)} a month` }, ' — ', { b: money(-s.surplusPlan) }, ' more than is free.']
                : ['Spending would still be ', { b: `${money(-s.surplusPlan)} a month` }, ' more than comes in.'],
          };
  return (
    <section className="pv-in">
      <Eyebrow icon={Sparkles}>The plan</Eyebrow>
      <Title>What have we agreed?</Title>
      <AnswerLine a={a} />
      {s.unquoted > 0 && (
        <Note>
          {s.unquoted} new cover{s.unquoted === 1 ? ' has' : 's have'} no premium yet — the cost will rise once {s.unquoted === 1 ? 'it is' : 'they are'} quoted.
        </Note>
      )}
      {s.changes.length > 0 && (
        <div className="mt-[clamp(20px,4vh,36px)]">
          <Figures
            items={[
              { key: 'cost', label: 'The plan costs', value: money(cost, { sign: true }), unit: 'a month' },
              { key: 'today', label: 'Free today', value: money(s.surplusToday), unit: 'a month' },
              { key: 'plan', label: 'Free with the plan', value: money(s.surplusPlan), unit: 'a month', status: s.surplusPlan >= -0.5 ? 'covered' : 'shortfall' },
              ...(s.bonusUsed > 0.5
                ? [
                    {
                      key: 'bonus',
                      label: `Once-off, of the ${moneyCompact(s.bonusAvailable)} bonus`,
                      value: money(s.bonusUsed),
                      status: s.bonusUsed > s.bonusAvailable + 0.5 ? ('shortfall' as const) : ('covered' as const),
                    },
                  ]
                : []),
            ]}
          />
        </div>
      )}
      <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-4">
        {action}
        <StartingPoints c={c} />
      </div>
      {groups.length > 0 && (
        <div className="mt-[clamp(24px,4vh,40px)] divide-y divide-[var(--pv-grid)] border-y border-[var(--pv-grid)]">
          {groups.map(({ q, changes }) => {
            const Icon = ICON[q];
            return (
              <div key={q} className="grid gap-x-8 gap-y-2 py-3.5 md:grid-cols-[200px_minmax(0,1fr)]">
                <button
                  type="button"
                  onClick={() => c.go(q)}
                  title="Open this question"
                  className="inline-flex items-center gap-2 self-start text-left text-[15px] font-semibold text-[var(--pv-ink-2)] hover:text-[var(--pv-plan)]"
                >
                  <Icon size={17} aria-hidden /> {TOPIC[q]}
                </button>
                <ul className="space-y-1.5">
                  {changes.map((ch) => (
                    <li key={ch.key} className="flex items-baseline gap-4">
                      <span className="min-w-0 flex-1 text-[16.5px] leading-snug">
                        <span className="font-medium">{ch.label}</span>
                        {ch.detail && <span className="text-[14px] text-[var(--pv-ink-2)]"> · {ch.detail}</span>}
                      </span>
                      {Math.abs(ch.monthly) > 0.5 && <span className="whitespace-nowrap text-[15.5px] font-semibold pv-tabular">{money(ch.monthly, { sign: true })} pm</span>}
                      <button
                        type="button"
                        onClick={() => c.setPlan((p) => withoutChange(p, ch.key))}
                        className="self-center rounded p-1 text-[var(--pv-muted)] hover:bg-[var(--pv-plan-wash)] hover:text-[var(--pv-ink)]"
                        aria-label={`Remove: ${ch.label}`}
                        title="Remove from the plan"
                      >
                        <X size={16} />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
