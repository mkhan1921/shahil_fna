'use client';

import { Receipt, Wallet } from 'lucide-react';
import { TAX_YEARS, getTaxTable } from '@/lib/fna/tax';
import type { PersonKey } from '@/lib/fna/types';
import { money, pct } from '@/lib/format';
import {
  Callout,
  Card,
  CardBody,
  CardHeader,
  Grid,
  MoneyField,
  NullableMoneyField,
  NumberField,
  Row,
  SelectField,
  StepHeader,
  Toggle,
} from '../ui';
import type { StepProps } from '../workspace/types';
import { nameOf, people } from './shared';

export default function Income({ doc, update, analysis, go }: StepProps) {
  const table = getTaxTable(doc.assumptions.taxYear);
  return (
    <>
      <StepHeader
        eyebrow="Step 3"
        title="Income & tax"
        description={`Gross income and payroll deductions. PAYE, UIF and medical tax credits are estimated from the SARS ${table.label} tables (${table.period}); override PAYE with the payslip figure where available.`}
        actions={
          <div className="w-44">
            <SelectField
              label="Tax year"
              value={doc.assumptions.taxYear}
              onChange={(v) => update((d) => void (d.assumptions.taxYear = v))}
              options={TAX_YEARS().map((y) => ({ value: y, label: `${y} tax year` }))}
            />
          </div>
        }
      />
      <div className="space-y-6">
        {people(doc).map((k: PersonKey) => {
          const inc = doc.income[k];
          const r = analysis.incomes.find((i) => i.key === k);
          const set = (fn: (x: typeof inc) => void) => update((d) => fn(d.income[k]));
          const salaried = !['self-employed', 'retired', 'unemployed', 'student'].includes(doc[k].employmentType);
          return (
            <Card key={k}>
              <CardHeader icon={<Wallet size={18} />} title={nameOf(doc, k)} description={[doc[k].occupation, doc[k].employer].filter(Boolean).join(' · ') || 'Monthly income and payroll deductions'} />
              <div className="grid lg:grid-cols-[1fr_22rem]">
                <CardBody className="border-line lg:border-r">
                  <Grid cols={2}>
                    <MoneyField
                      label={salaried ? 'Gross monthly salary' : 'Gross monthly income'}
                      value={inc.grossMonthly}
                      onChange={(v) => set((x) => void (x.grossMonthly = v))}
                      hint="Basic salary plus fixed taxable allowances"
                    />
                    <MoneyField label="Annual bonus / 13th cheque" value={inc.annualBonus} onChange={(v) => set((x) => void (x.annualBonus = v))} />
                    <MoneyField
                      label="Other taxable income (monthly)"
                      value={inc.otherTaxableMonthly}
                      onChange={(v) => set((x) => void (x.otherTaxableMonthly = v))}
                      hint="Rental, commission, side income, annuity income"
                    />
                    <MoneyField
                      label="Non-taxable income (monthly)"
                      value={inc.nonTaxableMonthly}
                      onChange={(v) => set((x) => void (x.nonTaxableMonthly = v))}
                      hint="e.g. maintenance received, disability benefits"
                    />
                    <MoneyField
                      label="Medical aid via payroll (monthly)"
                      value={inc.medicalAidPayrollMonthly}
                      onChange={(v) => set((x) => void (x.medicalAidPayrollMonthly = v))}
                      hint="If paid by debit order instead, capture it under Budget"
                    />
                    <NumberField
                      label="People on this person’s medical scheme"
                      value={inc.medicalSchemeMembers}
                      onChange={(v) => set((x) => void (x.medicalSchemeMembers = Math.round(v)))}
                      min={0}
                      max={12}
                      hint="Main member + dependants, for s6A tax credits. 0 if none."
                    />
                    <MoneyField
                      label="Other payroll deductions (monthly)"
                      value={inc.otherPayrollDeductionsMonthly}
                      onChange={(v) => set((x) => void (x.otherPayrollDeductionsMonthly = v))}
                      hint="Union fees, staff loans, group risk premiums"
                    />
                    <NullableMoneyField
                      label="PAYE per payslip (monthly)"
                      value={inc.payeOverrideMonthly}
                      onChange={(v) => set((x) => void (x.payeOverrideMonthly = v))}
                      hint="Leave blank to use the estimate"
                      optional
                    />
                  </Grid>
                  <div className="mt-4">
                    <Toggle label="UIF applies" description="1% of remuneration up to the R17,712 ceiling" checked={inc.uifApplies} onChange={(v) => set((x) => void (x.uifApplies = v))} />
                  </div>
                  <p className="mt-4 text-xs text-muted">
                    Retirement fund contributions are captured once, per fund, under{' '}
                    <button type="button" className="font-medium text-brand underline underline-offset-2" onClick={() => go('assets')}>
                      Assets &amp; retirement funds
                    </button>
                    , and flow into the tax calculation automatically.
                  </p>
                </CardBody>
                {r && (
                  <div className="bg-wash/60 px-5 py-5">
                    <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-ink">
                      <Receipt size={15} /> Estimated payslip
                    </div>
                    <Row label="Gross income" value={money(r.grossMonthly)} />
                    <Row label="PAYE" value={`−${money(r.payeMonthly)}`} muted />
                    <Row label="UIF" value={`−${money(r.uifMonthly)}`} muted />
                    <Row label="Retirement (payroll)" value={`−${money(r.retirementPayrollMonthly)}`} muted />
                    <Row label="Medical aid (payroll)" value={`−${money(r.medicalPayrollMonthly)}`} muted />
                    {r.otherDeductionsMonthly > 0 && <Row label="Other deductions" value={`−${money(r.otherDeductionsMonthly)}`} muted />}
                    {r.nonTaxableMonthly > 0 && <Row label="Non-taxable income" value={`+${money(r.nonTaxableMonthly)}`} muted />}
                    <div className="my-1 border-t border-line" />
                    <Row label="Take-home pay" value={money(r.takeHomeMonthly)} strong />
                    <div className="mt-4 space-y-0.5 border-t border-line pt-3">
                      <Row label="Taxable income (annual)" value={money(r.tax.taxable)} muted className="text-xs" />
                      <Row label="Retirement deduction (s11F)" value={money(r.tax.retirementDeduction)} muted className="text-xs" />
                      <Row label="Medical tax credits (s6A)" value={money(r.tax.medicalCredit)} muted className="text-xs" />
                      <Row label="Annual tax incl. bonus" value={money(r.tax.annualTax)} muted className="text-xs" />
                      <Row label="Marginal / effective rate" value={`${pct(r.tax.marginalRate)} / ${pct(r.tax.effectiveRate, 1)}`} muted className="text-xs" />
                      <Row label="After-tax income (incl. bonus) p.m." value={money(r.afterTaxMonthly)} className="text-xs" />
                    </div>
                  </div>
                )}
              </div>
            </Card>
          );
        })}
        <Callout tone="info" title="Why after-tax income matters">
          Since March 2015 income protection premiums are not tax-deductible and benefits are tax-free, so income protection needs are measured against after-tax income.
          Life cover income needs use the same after-tax basis.
        </Callout>
      </div>
    </>
  );
}
