'use client';

import { Plus, ShoppingBasket, Trash2 } from 'lucide-react';
import { EXPENSE_CATEGORIES, EXPENSE_ITEMS } from '@/lib/fna/catalog';
import { uid } from '@/lib/fna/defaults';
import type { ExpenseCategory } from '@/lib/fna/types';
import { money, pct } from '@/lib/format';
import { AllocationBar } from '../charts';
import { Button, Callout, Card, CardBody, CardHeader, MoneyField, StepHeader, Toggle } from '../ui';
import type { StepProps } from '../workspace/types';

export default function Expenses({ doc, update, analysis }: StepProps) {
  const cf = analysis.cashflow;
  const catTotal = (c: ExpenseCategory) => cf.byCategory.find((b) => b.key === c)?.amount ?? 0;

  return (
    <>
      <StepHeader
        eyebrow="Step 4"
        title="Monthly budget"
        description="Living expenses only. Debt repayments, life & risk premiums and savings contributions come from their own sections, so nothing is counted twice."
      />
      <div className="space-y-6">
        <Card>
          <CardHeader title="Where the money goes" description={`Take-home pay of ${money(cf.takeHome)} per month`} />
          <CardBody>
            <AllocationBar
              total={cf.takeHome}
              segments={[
                { label: 'Living expenses', value: cf.livingExpenses },
                { label: 'Debt repayments', value: cf.debtRepayments },
                { label: 'Risk premiums', value: cf.riskPremiums },
                { label: 'Savings & investments', value: cf.savingsContributions },
                { label: 'Unallocated surplus', value: Math.max(0, cf.surplus) },
              ]}
            />
            {cf.surplus < 0 && (
              <Callout tone="critical" className="mt-4" title={`Outgoings exceed take-home pay by ${money(-cf.surplus)} per month`}>
                The shortfall is being funded from savings, bonuses or credit. Review discretionary spending before adding new commitments.
              </Callout>
            )}
          </CardBody>
        </Card>

        <div className="grid gap-6 xl:grid-cols-2">
          {EXPENSE_CATEGORIES.map((cat) => {
            const items = EXPENSE_ITEMS.filter((i) => i.category === cat.key);
            const custom = doc.expenses.custom.map((c, idx) => ({ c, idx })).filter(({ c }) => c.category === cat.key);
            const total = catTotal(cat.key);
            return (
              <Card key={cat.key}>
                <CardHeader
                  icon={<ShoppingBasket size={17} />}
                  title={cat.label}
                  actions={
                    <span className="text-sm font-semibold text-ink tabular">
                      {money(total)}
                      {cf.takeHome > 0 && total > 0 && <span className="ml-1.5 text-xs font-normal text-muted">{pct(total / cf.takeHome)}</span>}
                    </span>
                  }
                />
                <CardBody className="space-y-3">
                  <div className="grid gap-3 sm:grid-cols-2">
                    {items.map((item) => (
                      <MoneyField
                        key={item.key}
                        label={item.label}
                        hint={item.hint}
                        value={doc.expenses.items[item.key] ?? 0}
                        onChange={(v) =>
                          update((d) => {
                            if (v) d.expenses.items[item.key] = v;
                            else delete d.expenses.items[item.key];
                          })
                        }
                      />
                    ))}
                  </div>
                  {custom.map(({ c, idx }) => (
                    <div key={c.id} className="grid grid-cols-[1fr_9rem_auto] items-end gap-3 rounded-lg border border-line bg-wash/50 p-3">
                      <label className="min-w-0">
                        <span className="mb-1.5 block text-[13px] font-medium text-ink-2">Description</span>
                        <input className="control" value={c.label} onChange={(e) => update((d) => void (d.expenses.custom[idx].label = e.target.value))} />
                      </label>
                      <MoneyField label="Amount" value={c.amount} onChange={(v) => update((d) => void (d.expenses.custom[idx].amount = v))} />
                      <Button variant="ghost" size="sm" aria-label="Remove" onClick={() => update((d) => void d.expenses.custom.splice(idx, 1))} className="mb-1 text-muted hover:text-critical-ink">
                        <Trash2 size={15} />
                      </Button>
                      <div className="col-span-3">
                        <Toggle label="Essential" checked={c.essential} onChange={(v) => update((d) => void (d.expenses.custom[idx].essential = v))} />
                      </div>
                    </div>
                  ))}
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Plus size={14} />}
                    onClick={() => update((d) => void d.expenses.custom.push({ id: uid(), category: cat.key, label: '', amount: 0, essential: true }))}
                  >
                    Add other {cat.label.toLowerCase()} item
                  </Button>
                </CardBody>
              </Card>
            );
          })}
        </div>

        <Card>
          <CardBody className="grid gap-6 sm:grid-cols-3">
            <div>
              <div className="text-xs text-muted">Total living expenses</div>
              <div className="mt-1 text-xl font-semibold tabular">{money(cf.livingExpenses)}</div>
            </div>
            <div>
              <div className="text-xs text-muted">Of which essential</div>
              <div className="mt-1 text-xl font-semibold tabular">{money(cf.essentialExpenses)}</div>
              <div className="text-xs text-muted">Used for the emergency fund target</div>
            </div>
            <div>
              <div className="text-xs text-muted">{cf.surplus < 0 ? 'Monthly deficit' : 'Unallocated surplus'}</div>
              <div className={`mt-1 text-xl font-semibold tabular ${cf.surplus < 0 ? 'text-critical-ink' : ''}`}>{money(cf.surplus)}</div>
              <div className="text-xs text-muted">After debt, premiums and savings</div>
            </div>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
