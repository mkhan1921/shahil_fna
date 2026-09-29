'use client';

import { ASSET_BEHAVIOUR, ASSET_TYPE, BENEFICIARY, DISABILITY_DEFINITION, LIABILITY_TYPE, POLICY_TYPE, PRE_RETIREMENT_FUNDS, RETIREMENT_FUND_TYPE, UNSECURED_DEBT } from '@/lib/fna/catalog';
import { newAsset, newLiability, newPolicy, newRetirementFund } from '@/lib/fna/defaults';
import { monthsToRepay, remainingInterest } from '@/lib/fna/finance';
import { getTaxTable } from '@/lib/fna/tax';
import type { Earmark, PersonKey } from '@/lib/fna/types';
import { money, moneyCompact, pct } from '@/lib/format';
import { firstNameOf, hasSpouse, ownerOptions, people, personOptions } from '../shared';
import { AddRow, DateC, Item, MoneyC, NumC, PctC, ReadC, Section, SelectC, Sub, TextC, YesNoC } from '../cells';
import { focusRow } from '../focus';
import type { SectionProps } from '../types';

const EARMARK: Record<Earmark, string> = {
  general: 'General',
  emergency: 'Emergency fund',
  retirement: 'Retirement',
  education: 'Education',
  goal: 'Other goal',
};

const term = (months: number) => {
  if (!Number.isFinite(months)) return 'never';
  const y = Math.floor(months / 12);
  const m = months % 12;
  return [y ? `${y}y` : '', m ? `${m}m` : ''].filter(Boolean).join(' ') || '—';
};

/* ================================================================== */
/* Assets & retirement funds                                           */
/* ================================================================== */

export function AssetsSection({ doc, update, analysis }: SectionProps) {
  const nw = analysis.netWorth;
  const spouse = hasSpouse(doc);
  const table = getTaxTable(doc.assumptions.taxYear);
  return (
    <Section
      id="assets"
      title="5 · Assets & retirement funds"
      right={
        <>
          <span>
            Assets <strong className="text-ink tabular">{moneyCompact(nw.assets + nw.retirement)}</strong>
          </span>
          <span>
            Liabilities <strong className="text-ink tabular">{moneyCompact(nw.liabilities)}</strong>
          </span>
          <span>
            Net worth <strong className="text-ink tabular">{moneyCompact(nw.netWorth)}</strong>
          </span>
        </>
      }
    >
      <Sub right={<span>Values include two-pot savings, retirement & vested components</span>}>Retirement funds</Sub>
      {doc.retirementFunds.map((f, i) => {
        const set = (fn: (x: typeof f) => void) => update((d) => fn(d.retirementFunds[i]));
        const pre = PRE_RETIREMENT_FUNDS.includes(f.type);
        const employer = f.type === 'pension' || f.type === 'provident';
        return (
          <Item key={f.id} id={f.id} n={i + 1} onRemove={() => update((d) => void d.retirementFunds.splice(i, 1))}>
            <SelectC label="Type" value={f.type} onChange={(v) => set((x) => void (x.type = v))} options={RETIREMENT_FUND_TYPE} span={1.75} />
            {spouse && <SelectC label="Member" value={f.owner} onChange={(v) => set((x) => void (x.owner = v))} options={personOptions(doc)} span={1} />}
            <TextC label="Provider" value={f.provider} onChange={(v) => set((x) => void (x.provider = v))} span={1.5} />
            <TextC label="Fund / product" value={f.name} onChange={(v) => set((x) => void (x.name = v))} span={1.75} />
            <MoneyC label="Value" value={f.value} onChange={(v) => set((x) => void (x.value = v))} span={1.5} />
            {pre && <MoneyC label="Savings pot" value={f.savingsPot} onChange={(v) => set((x) => void (x.savingsPot = v))} span={1} />}
            {pre && <MoneyC label={employer ? 'Member p.m.' : 'Contrib. p.m.'} value={f.employeeMonthly} onChange={(v) => set((x) => void (x.employeeMonthly = v))} span={1} />}
            {employer && <MoneyC label="Employer p.m." value={f.employerMonthly} onChange={(v) => set((x) => void (x.employerMonthly = v))} span={1} />}
            {pre && <YesNoC label="Payroll" value={f.viaPayroll} onChange={(v) => set((x) => void (x.viaPayroll = v))} span={0.75} />}
            {f.type === 'living-annuity' && (
              <PctC label="Drawdown" value={f.drawdownRate} onChange={(v) => set((x) => void (x.drawdownRate = v))} span={1} hint={money((f.value * f.drawdownRate) / 12)} />
            )}
            <YesNoC label="Nominees" value={f.beneficiariesNominated} onChange={(v) => set((x) => void (x.beneficiariesNominated = v))} span={0.75} />
          </Item>
        );
      })}
      <AddRow
        label="Add retirement fund"
        onAdd={() => {
          const f = newRetirementFund();
          update((d) => void d.retirementFunds.push(f));
          focusRow(f.id);
        }}
      />

      <Sub right={<span>Base cost drives CGT on death · “To spouse” drives s4(q) and roll-over</span>}>Assets</Sub>
      {doc.assets.map((a, i) => {
        const set = (fn: (x: typeof a) => void) => update((d) => fn(d.assets[i]));
        const b = ASSET_BEHAVIOUR[a.type];
        const investment = b.group === 'investment' || b.group === 'liquid';
        return (
          <Item key={a.id} id={a.id} n={i + 1} onRemove={() => update((d) => void d.assets.splice(i, 1))}>
            <SelectC label="Type" value={a.type} onChange={(v) => set((x) => void (x.type = v))} options={ASSET_TYPE} span={2} />
            <TextC label="Description" value={a.description} onChange={(v) => set((x) => void (x.description = v))} span={2} />
            {spouse && <SelectC label="Owner" value={a.owner} onChange={(v) => set((x) => void (x.owner = v))} options={ownerOptions(doc)} span={1} />}
            <MoneyC label="Market value" value={a.value} onChange={(v) => set((x) => void (x.value = v))} span={1.5} />
            {!b.cgtExempt && <MoneyC label="Base cost (CGT)" value={a.baseCost} onChange={(v) => set((x) => void (x.baseCost = v))} span={1.5} />}
            {investment && <TextC label="Provider" value={a.provider} onChange={(v) => set((x) => void (x.provider = v))} span={1} />}
            {investment && <MoneyC label="Contrib. p.m." value={a.monthlyContribution} onChange={(v) => set((x) => void (x.monthlyContribution = v))} span={1} />}
            {investment && <SelectC label="Earmarked" value={a.earmark} onChange={(v) => set((x) => void (x.earmark = v))} options={EARMARK} span={1} />}
            {(b.group === 'property' || b.group === 'business') && (
              <MoneyC label="Income p.m." value={a.monthlyIncome} onChange={(v) => set((x) => void (x.monthlyIncome = v))} span={1} />
            )}
            {spouse && <YesNoC label="To spouse" value={a.bequeathToSpouse} onChange={(v) => set((x) => void (x.bequeathToSpouse = v))} span={1} />}
            {a.type === 'tfsa' && a.monthlyContribution * 12 > table.tfsa.annual && (
              <ReadC label="TFSA limit" value={`Over ${money(table.tfsa.annual)} p.a. — 40% penalty`} tone="bad" span={3} />
            )}
          </Item>
        );
      })}
      <AddRow
        label="Add asset"
        onAdd={() => {
          const a = newAsset();
          update((d) => void d.assets.push(a));
          focusRow(a.id);
        }}
      />
    </Section>
  );
}

/* ================================================================== */
/* Liabilities                                                         */
/* ================================================================== */

export function LiabilitiesSection({ doc, update, analysis }: SectionProps) {
  const spouse = hasSpouse(doc);
  const r = analysis.ratios;
  const order = [...doc.liabilities].filter((l) => l.balance > 0).sort((a, b) => b.interestRate - a.interestRate);
  return (
    <Section
      id="liabilities"
      title="6 · Liabilities"
      right={
        <>
          <span>
            Repayments <strong className="text-ink tabular">{money(analysis.cashflow.debtRepayments)}</strong>
          </span>
          <span>
            Debt / gross <strong className={r.debtToIncome > 0.36 ? 'text-critical-ink tabular' : 'text-ink tabular'}>{pct(r.debtToIncome)}</strong>
          </span>
          <span>
            Housing / gross <strong className={r.housingToIncome > 0.3 ? 'text-critical-ink tabular' : 'text-ink tabular'}>{pct(r.housingToIncome)}</strong>
          </span>
        </>
      }
    >
      {doc.liabilities.map((l, i) => {
        const set = (fn: (x: typeof l) => void) => update((d) => fn(d.liabilities[i]));
        const months = monthsToRepay(l.balance, l.interestRate, l.monthlyRepayment);
        const interest = remainingInterest(l.balance, l.interestRate, l.monthlyRepayment);
        const expensive = UNSECURED_DEBT.includes(l.type) && l.interestRate >= 0.18;
        return (
          <Item key={l.id} id={l.id} n={i + 1} onRemove={() => update((d) => void d.liabilities.splice(i, 1))}>
            <SelectC label="Type" value={l.type} onChange={(v) => set((x) => void (x.type = v))} options={LIABILITY_TYPE} span={1.45} />
            <TextC label="Description" value={l.description} onChange={(v) => set((x) => void (x.description = v))} span={1} />
            <TextC label="Lender" value={l.lender} onChange={(v) => set((x) => void (x.lender = v))} span={1} />
            {(l.type === 'home-loan' || l.type === 'vehicle-finance') && (
              <SelectC
                label="Secured against"
                value={l.linkedAssetId}
                onChange={(v) => set((x) => void (x.linkedAssetId = v))}
                options={doc.assets.map((a) => ({ value: a.id, label: a.description || ASSET_TYPE[a.type] }))}
                placeholder="—"
                span={1.1}
              />
            )}
            {spouse && <SelectC label="Debtor" value={l.owner} onChange={(v) => set((x) => void (x.owner = v))} options={ownerOptions(doc)} span={0.75} />}
            <MoneyC label="Balance" value={l.balance} onChange={(v) => set((x) => void (x.balance = v))} span={1.25} />
            <PctC label="Rate" value={l.interestRate} onChange={(v) => set((x) => void (x.interestRate = v))} decimals={2} span={0.75} hint={expensive ? 'high' : undefined} />
            <MoneyC label="Instalment" value={l.monthlyRepayment} onChange={(v) => set((x) => void (x.monthlyRepayment = v))} span={1} />
            <YesNoC label="Credit life" value={l.creditLifeCover} onChange={(v) => set((x) => void (x.creditLifeCover = v))} span={0.9} />
            <YesNoC label="Settle death" value={l.settleOnDeath} onChange={(v) => set((x) => void (x.settleOnDeath = v))} span={0.9} />
            <YesNoC label="Settle disab." value={l.settleOnDisability} onChange={(v) => set((x) => void (x.settleOnDisability = v))} span={0.9} />
            <ReadC
              label="Paid off in"
              value={l.balance > 0 && l.monthlyRepayment > 0 ? term(months) : '—'}
              sub={Number.isFinite(interest) && interest > 0 ? `+${moneyCompact(interest)} int.` : undefined}
              tone={!Number.isFinite(months) && l.balance > 0 && l.monthlyRepayment > 0 ? 'bad' : undefined}
              span={1}
            />
          </Item>
        );
      })}
      <AddRow
        label="Add liability"
        empty={doc.liabilities.length ? undefined : 'Home loan, vehicle finance, cards, personal loans…'}
        onAdd={() => {
          const l = newLiability();
          update((d) => void d.liabilities.push(l));
          focusRow(l.id);
        }}
      />
      {order.length > 1 && (
        <div className="border-b border-line bg-brand-soft/40 px-3 py-1 text-[11px] text-ink-2">
          Avalanche order (highest rate first):{' '}
          {order.map((l, i) => (
            <span key={l.id}>
              {i > 0 && ' → '}
              <strong>{l.description || LIABILITY_TYPE[l.type]}</strong> {pct(l.interestRate, 1)}
            </span>
          ))}
        </div>
      )}
    </Section>
  );
}

/* ================================================================== */
/* Existing cover                                                      */
/* ================================================================== */

export function CoverSection({ doc, update }: SectionProps) {
  const lives = people(doc);
  const total = (k: PersonKey, t: string, monthly = false) =>
    doc.policies.filter((p) => p.lifeAssured === k && p.type === t).reduce((s, p) => s + (monthly ? p.monthlyBenefit : p.cover), 0);
  return (
    <Section
      id="cover"
      title="7 · Existing cover"
      right={lives.map((k) => (
        <span key={k}>
          {firstNameOf(doc, k)}: life <strong className="text-ink tabular">{moneyCompact(total(k, 'life'))}</strong> · IP{' '}
          <strong className="text-ink tabular">{money(total(k, 'income-protection', true))}</strong> · SI <strong className="text-ink tabular">{moneyCompact(total(k, 'severe-illness'))}</strong>
        </span>
      ))}
      note="Include employer group benefits from the benefit statement (mark as group). Beneficiary determines whether proceeds pay the estate or go directly to dependants."
    >
      {doc.policies.map((p, i) => {
        const set = (fn: (x: typeof p) => void) => update((d) => fn(d.policies[i]));
        const monthly = p.type === 'income-protection' || p.type === 'family-income';
        return (
          <Item key={p.id} id={p.id} n={i + 1} onRemove={() => update((d) => void d.policies.splice(i, 1))}>
            <SelectC label="Benefit" value={p.type} onChange={(v) => set((x) => void (x.type = v))} options={POLICY_TYPE} span={2} />
            {lives.length > 1 && <SelectC label="Life assured" value={p.lifeAssured} onChange={(v) => set((x) => void (x.lifeAssured = v))} options={personOptions(doc)} span={1} />}
            <TextC label="Insurer / scheme" value={p.insurer} onChange={(v) => set((x) => void (x.insurer = v))} span={2} />
            <YesNoC label="Group" value={p.isGroup} onChange={(v) => set((x) => void (x.isGroup = v))} span={1} />
            {monthly ? (
              <MoneyC label="Benefit p.m." value={p.monthlyBenefit} onChange={(v) => set((x) => void (x.monthlyBenefit = v))} span={2} />
            ) : (
              <MoneyC label="Cover" value={p.cover} onChange={(v) => set((x) => void (x.cover = v))} span={2} />
            )}
            {p.type === 'life' && <SelectC label="Beneficiary" value={p.beneficiary} onChange={(v) => set((x) => void (x.beneficiary = v))} options={BENEFICIARY} span={2} />}
            {p.type === 'income-protection' && (
              <>
                <NumC label="Waiting" value={p.waitingPeriodMonths} onChange={(v) => set((x) => void (x.waitingPeriodMonths = v))} suffix="m" min={0} max={24} span={1} />
                <NumC label="To age" value={p.benefitToAge} onChange={(v) => set((x) => void (x.benefitToAge = v))} min={0} max={100} span={1} hint="0=temp" />
              </>
            )}
            {p.type === 'family-income' && <NumC label="Paid for" value={p.benefitTermYears} onChange={(v) => set((x) => void (x.benefitTermYears = v))} suffix="yrs" min={0} max={60} span={1} />}
            {(p.type === 'disability-lump' || p.type === 'income-protection') && (
              <SelectC label="Definition" value={p.definition} onChange={(v) => set((x) => void (x.definition = v))} options={DISABILITY_DEFINITION} span={2} />
            )}
            {(p.type === 'severe-illness' || p.type === 'disability-lump') && <YesNoC label="Accelerated" value={p.accelerated} onChange={(v) => set((x) => void (x.accelerated = v))} span={1} />}
            {!p.isGroup && <MoneyC label="Premium p.m." value={p.premiumMonthly} onChange={(v) => set((x) => void (x.premiumMonthly = v))} span={1} />}
            {!p.isGroup && <PctC label="Prem. esc." value={p.premiumEscalation} onChange={(v) => set((x) => void (x.premiumEscalation = v))} span={1} />}
            {!p.isGroup && <PctC label="Cover esc." value={p.coverEscalation} onChange={(v) => set((x) => void (x.coverEscalation = v))} span={1} />}
            <TextC label="Policy no." value={p.policyNumber} onChange={(v) => set((x) => void (x.policyNumber = v))} span={1} />
            {!p.isGroup && <DateC label="Inception" value={p.inceptionDate} onChange={(v) => set((x) => void (x.inceptionDate = v))} span={1} />}
            <TextC label="Notes" value={p.notes} onChange={(v) => set((x) => void (x.notes = v))} span={2} placeholder="3× salary, exclusions…" />
          </Item>
        );
      })}
      <AddRow
        label="Add policy / benefit"
        onAdd={() => {
          const p = newPolicy('client');
          update((d) => void d.policies.push(p));
          focusRow(p.id);
        }}
      />
    </Section>
  );
}
