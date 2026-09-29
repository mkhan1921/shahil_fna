'use client';

import { Landmark, PiggyBank, Plus } from 'lucide-react';
import { ASSET_BEHAVIOUR, ASSET_TYPE, PRE_RETIREMENT_FUNDS, RETIREMENT_FUND_TYPE } from '@/lib/fna/catalog';
import { newAsset, newRetirementFund } from '@/lib/fna/defaults';
import { getTaxTable } from '@/lib/fna/tax';
import type { AssetType, Earmark } from '@/lib/fna/types';
import { money, moneyCompact } from '@/lib/format';
import { BarList } from '../charts';
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
  StepHeader,
  TextField,
  Toggle,
} from '../ui';
import type { StepProps } from '../workspace/types';
import { hasSpouse, ownerOptions, personOptions } from './shared';

const EARMARK: Record<Earmark, string> = {
  general: 'General / no specific goal',
  emergency: 'Emergency fund',
  retirement: 'Retirement',
  education: 'Education',
  goal: 'Other goal',
};

export default function Assets({ doc, update, analysis }: StepProps) {
  const nw = analysis.netWorth;
  const table = getTaxTable(doc.assumptions.taxYear);
  const spouse = hasSpouse(doc);

  return (
    <>
      <StepHeader
        eyebrow="Step 5"
        title="Assets & retirement funds"
        description="What the household owns. Ownership, base cost and who inherits each asset drive the estate, capital gains tax and liquidity calculations."
      />
      <div className="space-y-6">
        <Card>
          <CardBody className="grid gap-6 lg:grid-cols-[16rem_1fr]">
            <div className="space-y-4">
              <div>
                <div className="text-xs text-muted">Net worth</div>
                <div className="mt-1 text-3xl font-semibold tracking-tight">{moneyCompact(nw.netWorth)}</div>
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <div className="text-xs text-muted">Assets</div>
                  <div className="font-medium tabular">{moneyCompact(nw.assets + nw.retirement)}</div>
                </div>
                <div>
                  <div className="text-xs text-muted">Liabilities</div>
                  <div className="font-medium tabular">{moneyCompact(nw.liabilities)}</div>
                </div>
              </div>
            </div>
            <div>
              <div className="mb-3 text-[13px] font-medium text-ink-2">Composition of assets</div>
              {nw.byGroup.length ? <BarList items={nw.byGroup.map((g) => ({ label: g.label, value: g.amount }))} format={moneyCompact} /> : <p className="text-sm text-muted">No assets captured yet.</p>}
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            icon={<PiggyBank size={18} />}
            title="Retirement funds"
            description="Pension, provident, retirement annuity, preservation funds and annuities. Values include the two-pot savings, retirement and vested components."
            actions={
              <Button size="sm" icon={<Plus size={15} />} onClick={() => update((d) => void d.retirementFunds.push(newRetirementFund()))}>
                Add fund
              </Button>
            }
          />
          <CardBody className="space-y-3">
            {doc.retirementFunds.length === 0 && <EmptyState title="No retirement funds captured" description="Add employer funds, retirement annuities, preservation funds and living annuities." />}
            {doc.retirementFunds.map((f, i) => {
              const set = (fn: (x: typeof f) => void) => update((d) => fn(d.retirementFunds[i]));
              const pre = PRE_RETIREMENT_FUNDS.includes(f.type);
              const employer = f.type === 'pension' || f.type === 'provident';
              return (
                <ItemCard
                  key={f.id}
                  title={f.name || RETIREMENT_FUND_TYPE[f.type]}
                  badge={<Badge tone="brand">{money(f.value)}</Badge>}
                  subtitle={spouse ? ownerOptions(doc).find((o) => o.value === f.owner)?.label : undefined}
                  onRemove={() => update((d) => void d.retirementFunds.splice(i, 1))}
                >
                  <Grid cols={4}>
                    <SelectField label="Type" value={f.type} onChange={(v) => set((x) => void (x.type = v))} options={RETIREMENT_FUND_TYPE} />
                    {spouse && <SelectField label="Member" value={f.owner} onChange={(v) => set((x) => void (x.owner = v))} options={personOptions(doc)} />}
                    <TextField label="Provider / administrator" value={f.provider} onChange={(v) => set((x) => void (x.provider = v))} />
                    <TextField label="Fund / product name" value={f.name} onChange={(v) => set((x) => void (x.name = v))} />
                    <MoneyField label="Current value" value={f.value} onChange={(v) => set((x) => void (x.value = v))} />
                    {pre && (
                      <MoneyField
                        label="Savings pot balance"
                        value={f.savingsPot}
                        onChange={(v) => set((x) => void (x.savingsPot = v))}
                        hint="Accessible once per tax year (min R2,000), taxed at marginal rate"
                        optional
                      />
                    )}
                    {pre && (
                      <MoneyField
                        label={employer ? 'Member contribution p.m.' : 'Contribution p.m.'}
                        value={f.employeeMonthly}
                        onChange={(v) => set((x) => void (x.employeeMonthly = v))}
                      />
                    )}
                    {employer && <MoneyField label="Employer contribution p.m." value={f.employerMonthly} onChange={(v) => set((x) => void (x.employerMonthly = v))} hint="Retirement funding portion only" />}
                    {f.type === 'living-annuity' && (
                      <PercentField
                        label="Drawdown rate"
                        value={f.drawdownRate}
                        onChange={(v) => set((x) => void (x.drawdownRate = v))}
                        hint={`Legal range 2.5%–17.5% · ${money((f.value * f.drawdownRate) / 12)} p.m.`}
                      />
                    )}
                  </Grid>
                  <div className="mt-4 flex flex-wrap gap-x-8 gap-y-3">
                    {pre && (
                      <Toggle
                        label="Deducted via payroll"
                        description={f.viaPayroll ? 'Reduces take-home pay' : 'Paid by debit order from take-home pay'}
                        checked={f.viaPayroll}
                        onChange={(v) => set((x) => void (x.viaPayroll = v))}
                      />
                    )}
                    <Toggle label="Beneficiaries nominated" description="Guides trustees under s37C" checked={f.beneficiariesNominated} onChange={(v) => set((x) => void (x.beneficiariesNominated = v))} />
                  </div>
                </ItemCard>
              );
            })}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            icon={<Landmark size={18} />}
            title="Assets"
            description="Property, vehicles, cash, investments and business interests."
            actions={
              <Button size="sm" icon={<Plus size={15} />} onClick={() => update((d) => void d.assets.push(newAsset()))}>
                Add asset
              </Button>
            }
          />
          <CardBody className="space-y-3">
            {doc.assets.length === 0 && <EmptyState title="No assets captured" description="Add the home, vehicles, bank accounts, investments and other assets." />}
            {doc.assets.map((a, i) => {
              const set = (fn: (x: typeof a) => void) => update((d) => fn(d.assets[i]));
              const b = ASSET_BEHAVIOUR[a.type];
              const showBase = !b.cgtExempt;
              const investment = ['unit-trust', 'shares', 'tfsa', 'endowment', 'offshore', 'money-market', 'cash', 'crypto'].includes(a.type);
              return (
                <ItemCard
                  key={a.id}
                  title={a.description || ASSET_TYPE[a.type]}
                  badge={<Badge tone="brand">{money(a.value)}</Badge>}
                  subtitle={b.liquid ? 'Liquid' : 'Illiquid'}
                  onRemove={() => update((d) => void d.assets.splice(i, 1))}
                >
                  <Grid cols={4}>
                    <SelectField label="Type" value={a.type} onChange={(v: AssetType) => set((x) => void (x.type = v))} options={ASSET_TYPE} />
                    <TextField label="Description" value={a.description} onChange={(v) => set((x) => void (x.description = v))} />
                    {spouse && <SelectField label="Owner" value={a.owner} onChange={(v) => set((x) => void (x.owner = v))} options={ownerOptions(doc)} />}
                    <MoneyField label="Market value" value={a.value} onChange={(v) => set((x) => void (x.value = v))} />
                    {showBase && (
                      <MoneyField
                        label="Base cost (for CGT)"
                        value={a.baseCost}
                        onChange={(v) => set((x) => void (x.baseCost = v))}
                        hint="Purchase price plus improvements"
                      />
                    )}
                    {investment && <TextField label="Provider" value={a.provider} onChange={(v) => set((x) => void (x.provider = v))} optional />}
                    {investment && <MoneyField label="Monthly contribution" value={a.monthlyContribution} onChange={(v) => set((x) => void (x.monthlyContribution = v))} />}
                    {investment && <SelectField label="Earmarked for" value={a.earmark} onChange={(v) => set((x) => void (x.earmark = v))} options={EARMARK} />}
                    {(b.group === 'property' || b.group === 'business') && (
                      <MoneyField label="Monthly income from asset" value={a.monthlyIncome} onChange={(v) => set((x) => void (x.monthlyIncome = v))} hint="e.g. rental; include it under taxable income too" optional />
                    )}
                  </Grid>
                  {spouse && (
                    <div className="mt-4">
                      <Toggle
                        label="Inherited by the surviving spouse"
                        description="Qualifies for the s4(q) estate duty deduction and CGT roll-over"
                        checked={a.bequeathToSpouse}
                        onChange={(v) => set((x) => void (x.bequeathToSpouse = v))}
                      />
                    </div>
                  )}
                  {a.type === 'tfsa' && a.monthlyContribution * 12 > table.tfsa.annual && (
                    <Callout tone="warning" className="mt-3">
                      Contributions of {money(a.monthlyContribution * 12)} a year exceed the {money(table.tfsa.annual)} annual TFSA limit — excess contributions attract a 40% penalty.
                    </Callout>
                  )}
                </ItemCard>
              );
            })}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
