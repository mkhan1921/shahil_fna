'use client';

import { Scale, ScrollText } from 'lucide-react';
import { ageOn } from '@/lib/fna/idNumber';
import { money } from '@/lib/format';
import {
  Callout,
  Card,
  CardBody,
  CardHeader,
  DateField,
  Grid,
  MoneyField,
  Row,
  SegmentedField,
  StepHeader,
  TextArea,
  TextField,
  Toggle,
} from '../ui';
import type { StepProps } from '../workspace/types';
import { firstNameOf, people } from './shared';

export default function Estate({ doc, update, analysis }: StepProps) {
  const minors = doc.dependants.some((d) => d.relationship === 'child' && (ageOn(d.dateOfBirth) ?? 0) < 18);
  const accrual = doc.household.maritalStatus === 'married' && doc.household.maritalRegime === 'anc-accrual';
  const widowed = doc.household.maritalStatus === 'widowed';

  return (
    <>
      <StepHeader
        eyebrow="Step 9"
        title="Estate planning"
        description="Wills, guardianship and the costs of winding up each estate. Estate duty, executor’s fees and capital gains tax on death are calculated from the assets captured."
      />
      <div className="space-y-6">
        {people(doc).map((k) => {
          const e = doc.estate[k];
          const r = analysis.persons[k]?.estate;
          const set = (fn: (x: typeof e) => void) => update((d) => fn(d.estate[k]));
          return (
            <Card key={k}>
              <CardHeader icon={<ScrollText size={18} />} title={`${firstNameOf(doc, k)}’s estate`} />
              <div className="grid lg:grid-cols-[1fr_22rem]">
                <CardBody className="space-y-5 border-line lg:border-r">
                  <SegmentedField
                    label="Valid will in place?"
                    value={e.hasWill}
                    onChange={(v) => set((x) => void (x.hasWill = v))}
                    options={[
                      { value: 'yes', label: 'Yes' },
                      { value: 'no', label: 'No' },
                      { value: 'unsure', label: 'Unsure' },
                    ]}
                  />
                  {e.hasWill === 'yes' && (
                    <Grid cols={3}>
                      <DateField label="Date signed" value={e.willDate} onChange={(v) => set((x) => void (x.willDate = v))} />
                      <TextField label="Where it is kept" value={e.willLocation} onChange={(v) => set((x) => void (x.willLocation = v))} />
                      <TextField label="Executor nominated" value={e.executor} onChange={(v) => set((x) => void (x.executor = v))} />
                    </Grid>
                  )}
                  {e.hasWill === 'no' && (
                    <Callout tone="critical" title="Intestate succession">
                      Without a will, the estate devolves under the Intestate Succession Act 81 of 1987, the Master appoints an executor, and minors’ inheritances are paid into the
                      Guardian’s Fund.
                    </Callout>
                  )}
                  {minors && (
                    <SegmentedField
                      label="Guardian nominated for minor children?"
                      value={e.guardianNominated}
                      onChange={(v) => set((x) => void (x.guardianNominated = v))}
                      options={[
                        { value: 'yes', label: 'Yes' },
                        { value: 'no', label: 'No' },
                        { value: 'na', label: 'Not applicable' },
                      ]}
                    />
                  )}
                  <Toggle label="Has, or is a beneficiary of, a trust" checked={e.hasTrust} onChange={(v) => set((x) => void (x.hasTrust = v))} />
                  {e.hasTrust && <TextArea label="Trust details" value={e.trustNotes} onChange={(v) => set((x) => void (x.trustNotes = v))} rows={2} />}
                  <Grid cols={2}>
                    <MoneyField label="Funeral costs" value={e.funeralCost} onChange={(v) => set((x) => void (x.funeralCost = v))} hint="Typical 2026 range R20k–R80k" />
                    <MoneyField label="Cash bequests in the will" value={e.cashBequests} onChange={(v) => set((x) => void (x.cashBequests = v))} />
                    {accrual && (
                      <MoneyField
                        label="Net estate at start of marriage (CPI-adjusted)"
                        value={e.accrualCommencementValue}
                        onChange={(v) => set((x) => void (x.accrualCommencementValue = v))}
                        hint="As declared in the antenuptial contract"
                      />
                    )}
                    {widowed && (
                      <MoneyField
                        label="Unused abatement from late spouse (s4A)"
                        value={e.portedAbatement}
                        onChange={(v) => set((x) => void (x.portedAbatement = v))}
                        hint="Up to R3.5m; total abatement max R7m"
                      />
                    )}
                    <MoneyField
                      label="Extra monthly need on death (today)"
                      value={e.additionalIncomeNeedMonthly}
                      onChange={(v) => set((x) => void (x.additionalIncomeNeedMonthly = v))}
                      hint="e.g. childcare to replace a stay-at-home parent"
                    />
                    <MoneyField
                      label="Other capital needed on death"
                      value={e.additionalCapitalNeed}
                      onChange={(v) => set((x) => void (x.additionalCapitalNeed = v))}
                      hint="e.g. business debt, surety, special-needs trust"
                    />
                  </Grid>
                  <TextArea label="Estate planning notes" value={e.notes} onChange={(v) => set((x) => void (x.notes = v))} rows={2} optional />
                </CardBody>
                {r && (
                  <div className="bg-wash/60 px-5 py-5">
                    <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-ink">
                      <Scale size={15} /> If {firstNameOf(doc, k)} died today
                    </div>
                    <Row label="Gross estate" value={money(r.grossEstate)} />
                    <Row label="Deemed property (policies)" value={money(r.deemedProperty)} muted />
                    <Row label="Net estate" value={money(r.netEstate)} muted />
                    <Row label="Spousal deduction s4(q)" value={money(r.spouseDeduction)} muted />
                    <Row label="Abatement" value={money(r.abatement)} muted />
                    <Row label="Estate duty" value={money(r.estateDuty)} />
                    <Row label="CGT on death" value={money(r.cgt)} />
                    <Row label="Executor’s fees" value={money(r.executorFees)} />
                    <div className="my-1 border-t border-line" />
                    <Row label="Cash required" value={money(r.cashRequired)} />
                    <Row label="Cash available in estate" value={money(r.cashAvailable)} />
                    <Row
                      label="Liquidity shortfall"
                      value={<span className={r.liquidityShortfall > 0 ? 'text-critical-ink' : 'text-good-ink'}>{r.liquidityShortfall > 0 ? money(r.liquidityShortfall) : 'None'}</span>}
                      strong
                    />
                  </div>
                )}
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
