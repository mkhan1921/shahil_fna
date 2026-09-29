'use client';

import { CreditCard, Plus } from 'lucide-react';
import { LIABILITY_TYPE, UNSECURED_DEBT } from '@/lib/fna/catalog';
import { newLiability } from '@/lib/fna/defaults';
import { monthsToRepay, remainingInterest } from '@/lib/fna/finance';
import { money, pct } from '@/lib/format';
import {
  Badge,
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Grid,
  ItemCard,
  MoneyField,
  PercentField,
  SelectField,
  Stat,
  StepHeader,
  TextField,
  Toggle,
} from '../ui';
import type { StepProps } from '../workspace/types';
import { hasSpouse, ownerOptions } from './shared';

const term = (months: number) => {
  if (!Number.isFinite(months)) return 'Never — instalment below interest';
  const y = Math.floor(months / 12);
  const m = months % 12;
  return [y ? `${y} yr${y === 1 ? '' : 's'}` : '', m ? `${m} mo` : ''].filter(Boolean).join(' ') || 'Paid';
};

export default function Liabilities({ doc, update, analysis }: StepProps) {
  const r = analysis.ratios;
  const spouse = hasSpouse(doc);
  const avalanche = [...doc.liabilities].filter((l) => l.balance > 0).sort((a, b) => b.interestRate - a.interestRate);

  return (
    <>
      <StepHeader
        eyebrow="Step 6"
        title="Liabilities"
        description="Home loans, vehicle finance and other debt. Balances determine the capital needed to settle debt on death or disability."
        actions={
          <Button icon={<Plus size={15} />} onClick={() => update((d) => void d.liabilities.push(newLiability()))}>
            Add liability
          </Button>
        }
      />
      <div className="space-y-6">
        <Card>
          <CardBody className="grid grid-cols-2 gap-6 lg:grid-cols-4">
            <Stat label="Total debt" value={money(analysis.netWorth.liabilities)} />
            <Stat label="Monthly repayments" value={money(analysis.cashflow.debtRepayments)} />
            <Stat
              label="Debt repayments / gross income"
              value={pct(r.debtToIncome)}
              sub="Guideline: below 30–36%"
              tone={r.debtToIncome > 0.36 ? 'critical' : undefined}
            />
            <Stat label="Housing cost / gross income" value={pct(r.housingToIncome)} sub="Guideline: below 28–30%" tone={r.housingToIncome > 0.3 ? 'critical' : undefined} />
          </CardBody>
        </Card>

        {doc.liabilities.length === 0 ? (
          <EmptyState icon={<CreditCard size={32} strokeWidth={1.5} />} title="No liabilities captured" description="If the client has no debt, you can move on." />
        ) : (
          <div className="space-y-3">
            {doc.liabilities.map((l, i) => {
              const set = (fn: (x: typeof l) => void) => update((d) => fn(d.liabilities[i]));
              const months = monthsToRepay(l.balance, l.interestRate, l.monthlyRepayment);
              const interest = remainingInterest(l.balance, l.interestRate, l.monthlyRepayment);
              return (
                <ItemCard
                  key={l.id}
                  title={l.description || LIABILITY_TYPE[l.type]}
                  badge={<Badge tone={UNSECURED_DEBT.includes(l.type) && l.interestRate >= 0.18 ? 'critical' : 'neutral'}>{money(l.balance)}</Badge>}
                  subtitle={l.monthlyRepayment > 0 ? `Paid off in ${term(months)}` : undefined}
                  onRemove={() => update((d) => void d.liabilities.splice(i, 1))}
                >
                  <Grid cols={4}>
                    <SelectField label="Type" value={l.type} onChange={(v) => set((x) => void (x.type = v))} options={LIABILITY_TYPE} />
                    <TextField label="Description" value={l.description} onChange={(v) => set((x) => void (x.description = v))} />
                    <TextField label="Lender" value={l.lender} onChange={(v) => set((x) => void (x.lender = v))} />
                    {spouse && <SelectField label="Debtor" value={l.owner} onChange={(v) => set((x) => void (x.owner = v))} options={ownerOptions(doc)} />}
                    <MoneyField label="Outstanding balance" value={l.balance} onChange={(v) => set((x) => void (x.balance = v))} />
                    <PercentField label="Interest rate" value={l.interestRate} onChange={(v) => set((x) => void (x.interestRate = v))} hint="Prime is 10.75% (Sept 2026)" />
                    <MoneyField label="Monthly instalment" value={l.monthlyRepayment} onChange={(v) => set((x) => void (x.monthlyRepayment = v))} />
                    {(l.type === 'home-loan' || l.type === 'vehicle-finance') && (
                      <SelectField
                        label="Secured against"
                        value={l.linkedAssetId}
                        onChange={(v) => set((x) => void (x.linkedAssetId = v))}
                        options={doc.assets.map((a) => ({ value: a.id, label: a.description || a.type }))}
                        placeholder="—"
                        optional
                      />
                    )}
                  </Grid>
                  <div className="mt-4 flex flex-wrap gap-x-8 gap-y-3">
                    <Toggle
                      label="Covered by credit life / bond protection"
                      description="Settled by the lender’s policy on death or disability"
                      checked={l.creditLifeCover}
                      onChange={(v) => set((x) => void (x.creditLifeCover = v))}
                    />
                    <Toggle label="Settle on death" checked={l.settleOnDeath} onChange={(v) => set((x) => void (x.settleOnDeath = v))} />
                    <Toggle label="Settle on disability" checked={l.settleOnDisability} onChange={(v) => set((x) => void (x.settleOnDisability = v))} />
                  </div>
                  {l.balance > 0 && l.monthlyRepayment > 0 && (
                    <p className="mt-3 text-xs text-muted">
                      {Number.isFinite(months)
                        ? `At this instalment the debt is repaid in ${term(months)}, costing a further ${money(interest)} in interest.`
                        : 'The instalment does not cover the monthly interest — the balance will grow.'}
                    </p>
                  )}
                </ItemCard>
              );
            })}
          </div>
        )}

        {avalanche.length > 1 && (
          <Callout tone="info" title="Suggested repayment order (avalanche method)">
            Pay minimums on everything and direct extra cash to the highest interest rate first:{' '}
            {avalanche.map((l, i) => (
              <span key={l.id}>
                {i > 0 && ' → '}
                <strong>{l.description || LIABILITY_TYPE[l.type]}</strong> ({pct(l.interestRate, 2)})
              </span>
            ))}
          </Callout>
        )}
      </div>
    </>
  );
}
