'use client';

import { RotateCcw } from 'lucide-react';
import { DEFAULT_ASSUMPTIONS, newGoal } from '@/lib/fna/defaults';
import { realRate } from '@/lib/fna/finance';
import { ageOn } from '@/lib/fna/idNumber';
import { RISK_CATEGORIES, RISK_QUESTIONS } from '@/lib/fna/riskProfile';
import { TAX_YEARS, getTaxTable } from '@/lib/fna/tax';
import type { PersonKey } from '@/lib/fna/types';
import { money, pct } from '@/lib/format';
import { firstNameOf, ownerOptions, people } from '../shared';
import { StatusPill, cx } from '../../ui';
import { AddRow, AreaC, Cells, DateC, Item, MoneyC, NumC, Pair, PairHead, PctC, ReadC, Section, SelectC, Sub, TextC, YesNoC } from '../cells';
import { focusRow } from '../focus';
import type { SectionProps } from '../types';

/* ================================================================== */
/* Goals: retirement, other goals, emergency                           */
/* ================================================================== */

export function GoalsSection({ doc, update, analysis }: SectionProps) {
  const lives = people(doc);
  return (
    <Section id="goals" n={8} title="Goals" right={<span>Targets in today’s money — the analysis inflates them</span>}>
      <Sub>Retirement</Sub>
      <Pair>
        {lives.map((k: PersonKey) => {
          const g = doc.retirement[k];
          const r = analysis.persons[k]!.retirement;
          const set = (fn: (x: typeof g) => void) => update((d) => fn(d.retirement[k]));
          return (
            <div key={k}>
              <PairHead right={<StatusPill status={r.status} />}>{firstNameOf(doc, k)}</PairHead>
              <Cells>
                <NumC label="Retire at" value={g.retirementAge} onChange={(v) => set((x) => void (x.retirementAge = v))} min={40} max={80} span={2} />
                <NumC label="Plan to age" value={g.planningAge} onChange={(v) => set((x) => void (x.planningAge = v))} min={70} max={110} span={2} hint="90 M / 95 F" />
                <SelectC label="Target as" value={g.targetMode} onChange={(v) => set((x) => void (x.targetMode = v))} options={{ percent: '% of income', amount: 'Rand amount' }} span={3} />
                {g.targetMode === 'percent' ? (
                  <PctC label="Replacement ratio" value={g.targetPercent} onChange={(v) => set((x) => void (x.targetPercent = v))} decimals={0} span={2} />
                ) : (
                  <MoneyC label="Income p.m. (today)" value={g.targetMonthly} onChange={(v) => set((x) => void (x.targetMonthly = v))} span={2} />
                )}
                <MoneyC label="Other income p.m." value={g.otherIncomeMonthly} onChange={(v) => set((x) => void (x.otherIncomeMonthly = v))} span={3} hint="rental, DB pension" />
                <ReadC label="Target p.m." value={money(r.targetMonthlyToday)} span={3} />
                <ReadC label="Projected capital (today)" value={money(r.projectedCapitalReal)} span={3} />
                <ReadC label="Required (today)" value={money(r.requiredCapitalReal)} span={3} />
                <ReadC label="Replacement" value={`${pct(r.replacementRatio)} of ${pct(r.targetReplacementRatio)}`} tone={r.status === 'covered' ? 'good' : 'bad'} span={3} />
                <ReadC label="Extra saving p.m." value={r.additionalMonthly > 0 ? money(r.additionalMonthly) : '—'} tone={r.additionalMonthly > 0 ? 'bad' : undefined} span={4} />
                <ReadC label="Capital lasts to" value={r.depletionAge ? `age ${Math.floor(r.depletionAge)}` : `${r.planningAge}+`} span={4} />
                <ReadC label="Initial drawdown" value={pct(r.initialDrawdown, 1)} tone={r.initialDrawdown > 0.05 ? 'bad' : undefined} span={4} />
              </Cells>
            </div>
          );
        })}
      </Pair>

      <Sub right={<span>Education plans are captured on each child under Client & family</span>}>Education</Sub>
      {analysis.education.length === 0 ? (
        <div className="border-b border-line bg-white px-3 py-1.5 text-[11.5px] text-faint">No children recorded.</div>
      ) : (
        <Cells>
          {analysis.education.map((e) => (
            <ReadC
              key={e.dependantId}
              span={Math.max(3, Math.floor(12 / analysis.education.length))}
              label={`${e.name} · from ${new Date().getFullYear() + Math.round(e.yearsToTertiary)}`}
              value={
                e.capitalAtStart <= 0
                  ? 'No tertiary plan'
                  : e.lumpSumRequired > 0
                    ? `${money(e.lumpSumRequired)} needed now`
                    : e.monthlyRequired > 0
                      ? `${money(e.capitalAtStart)} · save ${money(e.monthlyRequired)} p.m.`
                      : `${money(e.capitalAtStart)} · funded`
              }
              tone={e.status === 'covered' || e.status === 'na' ? undefined : 'bad'}
            />
          ))}
        </Cells>
      )}

      <Sub>Other goals</Sub>
      {doc.goals.map((g, i) => {
        const r = analysis.goals.find((x) => x.id === g.id);
        const set = (fn: (x: typeof g) => void) => update((d) => fn(d.goals[i]));
        return (
          <Item key={g.id} id={g.id} n={i + 1} onRemove={() => update((d) => void d.goals.splice(i, 1))}>
            <TextC label="Goal" value={g.name} onChange={(v) => set((x) => void (x.name = v))} span={3} />
            {ownerOptions(doc).length > 1 && <SelectC label="For" value={g.owner} onChange={(v) => set((x) => void (x.owner = v))} options={ownerOptions(doc)} span={1} />}
            <MoneyC label="Cost (today)" value={g.cost} onChange={(v) => set((x) => void (x.cost = v))} span={2} />
            <NumC label="Target year" value={g.targetYear} onChange={(v) => set((x) => void (x.targetYear = Math.round(v)))} min={2000} max={2100} span={1} plain />
            <SelectC label="Priority" value={g.priority} onChange={(v) => set((x) => void (x.priority = v))} options={{ high: 'High', medium: 'Medium', low: 'Low' }} span={1} />
            <MoneyC label="Saved" value={g.existingSavings} onChange={(v) => set((x) => void (x.existingSavings = v))} span={1} />
            <MoneyC label="Saving p.m." value={g.monthlyContribution} onChange={(v) => set((x) => void (x.monthlyContribution = v))} span={1} />
            {r && <ReadC label="Future cost · extra p.m." value={`${money(r.futureCost)} · ${r.monthlyRequired > 0 ? money(r.monthlyRequired) : 'on track'}`} tone={r.monthlyRequired > 0 ? 'bad' : 'good'} span={2} />}
          </Item>
        );
      })}
      <AddRow
        label="Add goal"
        empty={doc.goals.length ? undefined : 'Car replacement, home deposit, travel, wedding…'}
        onAdd={() => {
          const g = newGoal();
          update((d) => void d.goals.push(g));
          focusRow(g.id);
        }}
      />
    </Section>
  );
}

/* ================================================================== */
/* Estate                                                              */
/* ================================================================== */

export function EstateSection({ doc, update, analysis }: SectionProps) {
  const lives = people(doc);
  const minors = doc.dependants.some((d) => d.relationship === 'child' && (ageOn(d.dateOfBirth) ?? 0) < 18);
  const accrual = doc.household.maritalStatus === 'married' && doc.household.maritalRegime === 'anc-accrual';
  const widowed = doc.household.maritalStatus === 'widowed';
  return (
    <Section id="estate" n={9} title="Estate planning" right={<span>Estate duty, executor’s fees and CGT on death are calculated from Assets</span>}>
      <Pair>
        {lives.map((k) => {
          const e = doc.estate[k];
          const r = analysis.persons[k]!.estate;
          const set = (fn: (x: typeof e) => void) => update((d) => fn(d.estate[k]));
          return (
            <div key={k}>
              <PairHead right={r.liquidityShortfall > 0 ? <span className="font-semibold text-critical-ink">Liquidity short {money(r.liquidityShortfall)}</span> : 'Liquid'}>
                {firstNameOf(doc, k)}
              </PairHead>
              <Cells>
                <SelectC label="Valid will" value={e.hasWill} onChange={(v) => set((x) => void (x.hasWill = v))} options={{ yes: 'Yes', no: 'No', unsure: 'Unsure' }} span={2} need={e.hasWill === 'unsure'} />
                {e.hasWill === 'yes' && (
                  <>
                    <DateC label="Will signed" value={e.willDate} onChange={(v) => set((x) => void (x.willDate = v))} span={3} />
                    <TextC label="Will kept at" value={e.willLocation} onChange={(v) => set((x) => void (x.willLocation = v))} span={3} />
                    <TextC label="Executor" value={e.executor} onChange={(v) => set((x) => void (x.executor = v))} span={4} />
                  </>
                )}
                {e.hasWill === 'no' && <ReadC label="Intestate" value="Estate devolves under the Intestate Succession Act" tone="bad" span={10} />}
                {minors && <SelectC label="Guardian nominated" value={e.guardianNominated} onChange={(v) => set((x) => void (x.guardianNominated = v))} options={{ yes: 'Yes', no: 'No', na: 'N/A' }} span={3} />}
                <YesNoC label="Trust" value={e.hasTrust} onChange={(v) => set((x) => void (x.hasTrust = v))} span={1} />
                {e.hasTrust && <TextC label="Trust details" value={e.trustNotes} onChange={(v) => set((x) => void (x.trustNotes = v))} span={4} />}
                <MoneyC label="Funeral cost" value={e.funeralCost} onChange={(v) => set((x) => void (x.funeralCost = v))} span={2} />
                <MoneyC label="Cash bequests" value={e.cashBequests} onChange={(v) => set((x) => void (x.cashBequests = v))} span={2} />
                {accrual && <MoneyC label="Accrual start value" value={e.accrualCommencementValue} onChange={(v) => set((x) => void (x.accrualCommencementValue = v))} span={3} hint="per ANC" />}
                {widowed && <MoneyC label="Ported abatement (s4A)" value={e.portedAbatement} onChange={(v) => set((x) => void (x.portedAbatement = v))} span={3} />}
                <MoneyC label="Extra need p.m. on death" value={e.additionalIncomeNeedMonthly} onChange={(v) => set((x) => void (x.additionalIncomeNeedMonthly = v))} span={3} hint="childcare" />
                <MoneyC label="Other capital on death" value={e.additionalCapitalNeed} onChange={(v) => set((x) => void (x.additionalCapitalNeed = v))} span={3} />
                <AreaC label="Estate notes" value={e.notes} onChange={(v) => set((x) => void (x.notes = v))} span={12} />
                <ReadC label="Gross estate" value={money(r.grossEstate)} span={3} />
                <ReadC label="Estate duty" value={money(r.estateDuty)} span={3} tone={r.estateDuty > 0 ? 'bad' : undefined} />
                <ReadC label="CGT on death" value={money(r.cgt)} span={3} />
                <ReadC label="Executor’s fees" value={money(r.executorFees)} span={3} />
                <ReadC label="Cash required" value={money(r.cashRequired)} span={4} />
                <ReadC label="Cash in estate" value={money(r.cashAvailable)} span={4} />
                <ReadC label="Liquidity shortfall" value={r.liquidityShortfall > 0 ? money(r.liquidityShortfall) : 'None'} tone={r.liquidityShortfall > 0 ? 'bad' : 'good'} span={4} />
              </Cells>
            </div>
          );
        })}
      </Pair>
    </Section>
  );
}

/* ================================================================== */
/* Risk profile                                                        */
/* ================================================================== */

export function RiskSection({ doc, update, analysis }: SectionProps) {
  const r = analysis.risk;
  return (
    <Section
      id="risk"
      n={10} title="Risk profile"
      right={
        r.profile ? (
          <span>
            Profile <strong className="text-ink">{r.profile.label}</strong> · tolerance {r.tolerance?.label.toLowerCase()} · capacity {r.capacity?.label.toLowerCase()}
          </span>
        ) : (
          <span>
            {r.answered}/{r.total} answered
          </span>
        )
      }
      note="Recommended profile is the lower of tolerance (willingness) and capacity (ability) — GCC s8(1)(a). Type the option number to answer."
    >
      <Cells>
        {RISK_QUESTIONS.map((q, qi) => (
          <SelectC
            key={q.id}
            span={6}
            label={`${qi + 1}. ${q.question}`}
            hint={q.dimension}
            value={doc.riskProfile.answers[q.id] !== undefined ? String(doc.riskProfile.answers[q.id]) : ''}
            onChange={(v) =>
              update((d) => {
                if (v === '') delete d.riskProfile.answers[q.id];
                else d.riskProfile.answers[q.id] = Number(v);
              })
            }
            placeholder="—"
            need={doc.riskProfile.answers[q.id] === undefined}
            options={q.options.map((o, i) => ({ value: String(i), label: `(${i + 1}) ${o.label}` }))}
          />
        ))}
        <AreaC label="Adviser notes: risk profile & product knowledge" value={doc.riskProfile.notes} onChange={(v) => update((d) => void (d.riskProfile.notes = v))} span={12} />
      </Cells>
      <div className="flex flex-wrap border-b border-line">
        {RISK_CATEGORIES.map((c) => (
          <div key={c.key} className={cx('flex-1 border-r border-line px-2 py-1 text-center text-[11px] last:border-r-0', r.profile?.key === c.key ? 'bg-brand font-semibold text-white' : 'bg-white text-muted')}>
            {c.label}
            <span className="block text-[10px] opacity-80">{c.equity.replace(' growth assets', ' growth')}</span>
          </div>
        ))}
      </div>
      {r.mismatch && <div className="border-b border-line bg-warning-soft px-3 py-1 text-[11px] text-ink-2">Tolerance and capacity differ by two or more levels — discuss with the client.</div>}
    </Section>
  );
}

/* ================================================================== */
/* Assumptions                                                         */
/* ================================================================== */

export function AssumptionsSection({ doc, update, practice }: SectionProps) {
  const a = doc.assumptions;
  const set = <K extends keyof typeof a>(k: K, v: (typeof a)[K]) => update((d) => void (d.assumptions[k] = v));
  const real = (n: number) => `real ${pct(realRate(n, a.cpi), 1)}`;
  const base = practice?.defaultAssumptions ?? DEFAULT_ASSUMPTIONS;
  return (
    <Section
      id="assumptions"
      n={11} title="Assumptions"
      right={
        <button type="button" tabIndex={-1} onClick={() => update((d) => void (d.assumptions = { ...base }))} className="inline-flex items-center gap-1 font-semibold text-brand-2 hover:underline">
          <RotateCcw size={12} /> Practice defaults
        </button>
      }
      note="Disclosed in the report (GCC s7A). Defaults: CPI 4.5% (SARB 3% ±1 target; CPI 4.4% Aug 2026), returns CPI+4 / CPI+3 / CPI+2.5."
    >
      <Cells>
        <SelectC label="Tax tables" value={a.taxYear} onChange={(v) => set('taxYear', v)} options={TAX_YEARS().map((y) => ({ value: y, label: `${y} (${getTaxTable(y).status})` }))} span={2} />
        <PctC label="CPI" value={a.cpi} onChange={(v) => set('cpi', v)} span={1} />
        <PctC label="Salary escalation" value={a.salaryEscalation} onChange={(v) => set('salaryEscalation', v)} span={2} hint={real(a.salaryEscalation)} />
        <PctC label="Return pre-retirement" value={a.preRetirementReturn} onChange={(v) => set('preRetirementReturn', v)} span={2} hint={real(a.preRetirementReturn)} />
        <PctC label="Return post-retirement" value={a.postRetirementReturn} onChange={(v) => set('postRetirementReturn', v)} span={2} hint={real(a.postRetirementReturn)} />
        <PctC label="Return on dependants’ capital" value={a.riskCapitalReturn} onChange={(v) => set('riskCapitalReturn', v)} span={2} hint={real(a.riskCapitalReturn)} />
        <PctC label="Education inflation" value={a.educationInflation} onChange={(v) => set('educationInflation', v)} span={1} />
        <PctC label="Family income on death" value={a.deathIncomeReplacement} onChange={(v) => set('deathIncomeReplacement', v)} decimals={0} span={2} hint="of after-tax" />
        <SelectC
          label="Death income runs until"
          value={a.deathIncomeTerm}
          onChange={(v) => set('deathIncomeTerm', v)}
          options={{ auto: 'Auto (children / dependent spouse)', 'youngest-independent': 'Youngest independent', 'spouse-retirement': 'Spouse retires', fixed: 'Fixed years' }}
          span={3}
        />
        {a.deathIncomeTerm === 'fixed' && <NumC label="Fixed years" value={a.deathIncomeFixedYears} onChange={(v) => set('deathIncomeFixedYears', v)} suffix="yrs" min={0} max={60} span={1} />}
        <PctC label="Income protection target" value={a.incomeProtectionTarget} onChange={(v) => set('incomeProtectionTarget', v)} decimals={0} span={2} hint="of after-tax" />
        <MoneyC label="Disability adaptations" value={a.disabilityAdjustments} onChange={(v) => set('disabilityAdjustments', v)} span={2} />
        <YesNoC label="Lost employer contrib." value={a.disabilityIncludeRetirement} onChange={(v) => set('disabilityIncludeRetirement', v)} span={2} />
        <NumC label="Severe illness" value={a.severeIllnessMonths} onChange={(v) => set('severeIllnessMonths', v)} suffix="months" min={0} max={60} span={2} />
        <NumC label="Emergency fund" value={a.emergencyMonths} onChange={(v) => set('emergencyMonths', v)} suffix="months" min={0} max={24} span={2} />
        <PctC label="Executor’s fee" value={a.executorFeeRate} onChange={(v) => set('executorFeeRate', v)} decimals={2} span={2} hint="max 3.5%" />
        <PctC label="VAT" value={a.vatRate} onChange={(v) => set('vatRate', v)} decimals={0} span={1} />
      </Cells>
    </Section>
  );
}
