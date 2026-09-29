'use client';

import { RotateCcw, SlidersHorizontal } from 'lucide-react';
import { DEFAULT_ASSUMPTIONS } from '@/lib/fna/defaults';
import { realRate } from '@/lib/fna/finance';
import { TAX_YEARS, getTaxTable } from '@/lib/fna/tax';
import { pct } from '@/lib/format';
import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Grid,
  MoneyField,
  NumberField,
  PercentField,
  SelectField,
  StepHeader,
  Toggle,
} from '../ui';
import type { StepProps } from '../workspace/types';

export default function Assumptions({ doc, update, practice }: StepProps) {
  const a = doc.assumptions;
  const set = <K extends keyof typeof a>(k: K, v: (typeof a)[K]) => update((d) => void (d.assumptions[k] = v));
  const base = practice?.defaultAssumptions ?? DEFAULT_ASSUMPTIONS;
  const real = (n: number) => `CPI ${n - a.cpi >= 0 ? '+' : '−'} ${pct(Math.abs(n - a.cpi), 1)} · real ${pct(realRate(n, a.cpi), 1)}`;
  const table = getTaxTable(a.taxYear);

  return (
    <>
      <StepHeader
        eyebrow="Step 11"
        title="Assumptions"
        description="Every projection depends on these assumptions. They are disclosed in the report as required by GCC s7A — adjust them to the client’s circumstances and your practice’s house view."
        actions={
          <Button icon={<RotateCcw size={15} />} onClick={() => update((d) => void (d.assumptions = { ...base }))}>
            Reset to practice defaults
          </Button>
        }
      />
      <div className="space-y-6">
        <Card>
          <CardHeader icon={<SlidersHorizontal size={18} />} title="Economic assumptions" />
          <CardBody>
            <Grid cols={3}>
              <SelectField label="Tax tables" value={a.taxYear} onChange={(v) => set('taxYear', v)} options={TAX_YEARS().map((y) => ({ value: y, label: `${y} (${getTaxTable(y).period})` }))} />
              <PercentField label="Inflation (CPI)" value={a.cpi} onChange={(v) => set('cpi', v)} decimals={1} hint="SARB target 3% ±1%; CPI 4.4% (Aug 2026). 4.5% is prudent." />
              <PercentField label="Salary & contribution escalation" value={a.salaryEscalation} onChange={(v) => set('salaryEscalation', v)} decimals={1} hint={real(a.salaryEscalation)} />
              <PercentField label="Pre-retirement return" value={a.preRetirementReturn} onChange={(v) => set('preRetirementReturn', v)} decimals={1} hint={real(a.preRetirementReturn)} />
              <PercentField label="Post-retirement return" value={a.postRetirementReturn} onChange={(v) => set('postRetirementReturn', v)} decimals={1} hint={real(a.postRetirementReturn)} />
              <PercentField label="Return on capital for dependants" value={a.riskCapitalReturn} onChange={(v) => set('riskCapitalReturn', v)} decimals={1} hint={real(a.riskCapitalReturn)} />
              <PercentField label="Education inflation" value={a.educationInflation} onChange={(v) => set('educationInflation', v)} decimals={1} hint="2026: school fees +6.2%, tertiary +4.2%" />
            </Grid>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Needs assumptions" />
          <CardBody className="space-y-5">
            <Grid cols={3}>
              <PercentField
                label="Family income need on death"
                value={a.deathIncomeReplacement}
                onChange={(v) => set('deathIncomeReplacement', v)}
                decimals={0}
                hint="% of the deceased’s after-tax income (typically 70–80%)"
              />
              <SelectField
                label="Income needed on death until"
                value={a.deathIncomeTerm}
                onChange={(v) => set('deathIncomeTerm', v)}
                options={{
                  auto: 'Automatic (youngest independent; dependent spouse to retirement)',
                  'youngest-independent': 'Youngest dependant is independent',
                  'spouse-retirement': 'Surviving spouse retires',
                  fixed: 'Fixed number of years',
                }}
              />
              {a.deathIncomeTerm === 'fixed' && (
                <NumberField label="Years of income" value={a.deathIncomeFixedYears} onChange={(v) => set('deathIncomeFixedYears', v)} min={0} max={60} suffix="yrs" />
              )}
              <PercentField label="Income protection target" value={a.incomeProtectionTarget} onChange={(v) => set('incomeProtectionTarget', v)} decimals={0} hint="% of after-tax income (insurers cap at ~100%)" />
              <MoneyField label="Disability adaptations lump sum" value={a.disabilityAdjustments} onChange={(v) => set('disabilityAdjustments', v)} hint="Home/vehicle modification, equipment, care" />
              <NumberField label="Severe illness need" value={a.severeIllnessMonths} onChange={(v) => set('severeIllnessMonths', v)} min={0} max={60} suffix="months" hint="Months of gross income (12–36; default 24 = 2× annual)" />
              <NumberField label="Emergency fund" value={a.emergencyMonths} onChange={(v) => set('emergencyMonths', v)} min={0} max={24} suffix="months" hint="3–6 months; 6+ for variable income" />
              <PercentField label="Executor’s fee (excl. VAT)" value={a.executorFeeRate} onChange={(v) => set('executorFeeRate', v)} decimals={2} hint="Maximum 3.5% of gross assets" />
              <PercentField label="VAT" value={a.vatRate} onChange={(v) => set('vatRate', v)} decimals={0} />
            </Grid>
            <Toggle
              label="Include lost retirement contributions in the disability need"
              description="Capitalises member and employer contributions that stop on disability"
              checked={a.disabilityIncludeRetirement}
              onChange={(v) => set('disabilityIncludeRetirement', v)}
            />
          </CardBody>
        </Card>

        <Callout tone="info" title={`Tax basis: ${table.label}`}>
          Brackets, rebates, medical tax credits, the {pct(table.retirementDeduction.rate, 1)} / R{table.retirementDeduction.cap.toLocaleString('en-ZA')} retirement
          deduction, CGT exclusions (R{table.cgt.deathExclusion.toLocaleString('en-ZA')} in the year of death; R{table.cgt.primaryResidence.toLocaleString('en-ZA')} primary
          residence), the R{table.estateDuty.abatement.toLocaleString('en-ZA')} estate duty abatement and lump-sum tax tables all follow the {table.label} SARS tables.
        </Callout>
      </div>
    </>
  );
}
