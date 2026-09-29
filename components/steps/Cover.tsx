'use client';

import { Plus, ShieldHalf } from 'lucide-react';
import { BENEFICIARY, DISABILITY_DEFINITION, POLICY_TYPE } from '@/lib/fna/catalog';
import { newPolicy } from '@/lib/fna/defaults';
import type { PersonKey, Policy, PolicyType } from '@/lib/fna/types';
import { money, moneyCompact } from '@/lib/format';
import {
  Badge,
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  DateField,
  EmptyState,
  Grid,
  ItemCard,
  MoneyField,
  NumberField,
  PercentField,
  SelectField,
  StepHeader,
  TextField,
  Toggle,
} from '../ui';
import type { StepProps } from '../workspace/types';
import { firstNameOf, people } from './shared';

const summary = (p: Policy) =>
  p.type === 'income-protection' || p.type === 'family-income' ? `${money(p.monthlyBenefit)} p.m.` : money(p.cover);

export default function Cover({ doc, update }: StepProps) {
  const lives = people(doc);
  const totals = (k: PersonKey, t: PolicyType) =>
    doc.policies.filter((p) => p.lifeAssured === k && p.type === t).reduce((s, p) => s + (t === 'income-protection' || t === 'family-income' ? p.monthlyBenefit : p.cover), 0);

  return (
    <>
      <StepHeader
        eyebrow="Step 7"
        title="Existing cover"
        description="Personal policies and employer group benefits. Beneficiary nominations determine whether proceeds provide estate liquidity or pay dependants directly."
      />
      <div className="space-y-6">
        <Card>
          <CardHeader icon={<ShieldHalf size={18} />} title="Cover summary" />
          <CardBody className="overflow-x-auto p-0">
            <table className="w-full min-w-[36rem] text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-muted">
                  <th className="px-5 py-2.5 font-medium">Benefit</th>
                  {lives.map((k) => (
                    <th key={k} className="px-5 py-2.5 text-right font-medium">
                      {firstNameOf(doc, k)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="tabular">
                {(Object.keys(POLICY_TYPE) as PolicyType[]).map((t) => (
                  <tr key={t} className="border-b border-line last:border-0">
                    <td className="px-5 py-2.5 text-ink-2">{POLICY_TYPE[t]}</td>
                    {lives.map((k) => {
                      const v = totals(k, t);
                      return (
                        <td key={k} className="px-5 py-2.5 text-right text-ink">
                          {v ? (t === 'income-protection' || t === 'family-income' ? `${money(v)} p.m.` : moneyCompact(v)) : '—'}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>

        {lives.map((k) => {
          const list = doc.policies.map((p, i) => ({ p, i })).filter(({ p }) => p.lifeAssured === k);
          return (
            <Card key={k}>
              <CardHeader
                title={`Policies on ${firstNameOf(doc, k)}’s life`}
                actions={
                  <Button size="sm" icon={<Plus size={15} />} onClick={() => update((d) => void d.policies.push(newPolicy(k)))}>
                    Add policy
                  </Button>
                }
              />
              <CardBody className="space-y-3">
                {list.length === 0 && <EmptyState title="No policies recorded" description="Add personal policies and group benefits from the employer’s benefit statement." />}
                {list.map(({ p, i }) => {
                  const set = (fn: (x: Policy) => void) => update((d) => fn(d.policies[i]));
                  const monthly = p.type === 'income-protection' || p.type === 'family-income';
                  return (
                    <ItemCard
                      key={p.id}
                      title={`${POLICY_TYPE[p.type]}${p.insurer ? ` — ${p.insurer}` : ''}`}
                      badge={
                        <>
                          <Badge tone="brand">{summary(p)}</Badge>
                          {p.isGroup && <Badge>Group</Badge>}
                        </>
                      }
                      onRemove={() => update((d) => void d.policies.splice(i, 1))}
                    >
                      <Grid cols={4}>
                        <SelectField label="Benefit type" value={p.type} onChange={(v) => set((x) => void (x.type = v))} options={POLICY_TYPE} />
                        <TextField label="Insurer / scheme" value={p.insurer} onChange={(v) => set((x) => void (x.insurer = v))} />
                        <TextField label="Policy number" value={p.policyNumber} onChange={(v) => set((x) => void (x.policyNumber = v))} optional />
                        {lives.length > 1 && (
                          <SelectField
                            label="Life assured"
                            value={p.lifeAssured}
                            onChange={(v) => set((x) => void (x.lifeAssured = v))}
                            options={lives.map((l) => ({ value: l, label: firstNameOf(doc, l) }))}
                          />
                        )}
                        {monthly ? (
                          <MoneyField label="Monthly benefit" value={p.monthlyBenefit} onChange={(v) => set((x) => void (x.monthlyBenefit = v))} />
                        ) : (
                          <MoneyField label="Cover amount" value={p.cover} onChange={(v) => set((x) => void (x.cover = v))} />
                        )}
                        {p.type === 'income-protection' && (
                          <>
                            <NumberField label="Waiting period" value={p.waitingPeriodMonths} onChange={(v) => set((x) => void (x.waitingPeriodMonths = v))} suffix="months" min={0} max={24} />
                            <NumberField
                              label="Benefit paid to age"
                              value={p.benefitToAge}
                              onChange={(v) => set((x) => void (x.benefitToAge = v))}
                              min={0}
                              max={100}
                              hint="0 = temporary benefit only (e.g. 24 months)"
                            />
                          </>
                        )}
                        {p.type === 'family-income' && (
                          <NumberField label="Paid for" value={p.benefitTermYears} onChange={(v) => set((x) => void (x.benefitTermYears = v))} suffix="years" min={0} max={60} />
                        )}
                        {p.type === 'life' && (
                          <SelectField label="Beneficiary" value={p.beneficiary} onChange={(v) => set((x) => void (x.beneficiary = v))} options={BENEFICIARY} />
                        )}
                        {(p.type === 'disability-lump' || p.type === 'income-protection') && (
                          <SelectField label="Disability definition" value={p.definition} onChange={(v) => set((x) => void (x.definition = v))} options={DISABILITY_DEFINITION} />
                        )}
                        {!p.isGroup && <MoneyField label="Premium p.m." value={p.premiumMonthly} onChange={(v) => set((x) => void (x.premiumMonthly = v))} />}
                        {!p.isGroup && <PercentField label="Premium escalation p.a." value={p.premiumEscalation} onChange={(v) => set((x) => void (x.premiumEscalation = v))} decimals={1} />}
                        {!p.isGroup && <PercentField label="Cover escalation p.a." value={p.coverEscalation} onChange={(v) => set((x) => void (x.coverEscalation = v))} decimals={1} />}
                        {!p.isGroup && <DateField label="Inception date" value={p.inceptionDate} onChange={(v) => set((x) => void (x.inceptionDate = v))} optional />}
                      </Grid>
                      <div className="mt-4 flex flex-wrap gap-x-8 gap-y-3">
                        <Toggle label="Employer group benefit" description="Premium paid via employer; lost on leaving employment" checked={p.isGroup} onChange={(v) => set((x) => void (x.isGroup = v))} />
                        {(p.type === 'severe-illness' || p.type === 'disability-lump') && (
                          <Toggle label="Accelerated" description="A claim reduces the life cover" checked={p.accelerated} onChange={(v) => set((x) => void (x.accelerated = v))} />
                        )}
                      </div>
                      <div className="mt-3">
                        <TextField label="Notes" value={p.notes} onChange={(v) => set((x) => void (x.notes = v))} placeholder="e.g. 3× annual salary; exclusions; loadings" optional />
                      </div>
                    </ItemCard>
                  );
                })}
              </CardBody>
            </Card>
          );
        })}

        <Callout tone="info" title="Group benefits">
          Employer group life and disability benefits usually end when employment ends and may not be convertible. The analysis counts them, but
          recommendations should consider whether personal cover is needed to protect against job changes.
        </Callout>
      </div>
    </>
  );
}
