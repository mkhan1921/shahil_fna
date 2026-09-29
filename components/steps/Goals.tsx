'use client';

import { GraduationCap, Plus, Sunset, Target } from 'lucide-react';
import { newGoal } from '@/lib/fna/defaults';
import { ageOn } from '@/lib/fna/idNumber';
import { money, pct } from '@/lib/format';
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Grid,
  ItemCard,
  MoneyField,
  NumberField,
  PercentField,
  Row,
  SegmentedField,
  SelectField,
  StatusPill,
  StepHeader,
  TextField,
  Toggle,
} from '../ui';
import type { StepProps } from '../workspace/types';
import { firstNameOf, ownerOptions, people } from './shared';

export default function Goals({ doc, update, analysis }: StepProps) {
  const children = doc.dependants.map((d, i) => ({ d, i })).filter(({ d }) => d.relationship === 'child');

  return (
    <>
      <StepHeader eyebrow="Step 8" title="Goals" description="Retirement, education and other financial goals. Targets are captured in today’s money; the analysis inflates them." />
      <div className="space-y-6">
        {people(doc).map((k) => {
          const g = doc.retirement[k];
          const r = analysis.persons[k]?.retirement;
          const set = (fn: (x: typeof g) => void) => update((d) => fn(d.retirement[k]));
          return (
            <Card key={k}>
              <CardHeader icon={<Sunset size={18} />} title={`Retirement — ${firstNameOf(doc, k)}`} actions={r && <StatusPill status={r.status} />} />
              <div className="grid lg:grid-cols-[1fr_20rem]">
                <CardBody className="border-line lg:border-r">
                  <Grid cols={2}>
                    <NumberField label="Retirement age" value={g.retirementAge} onChange={(v) => set((x) => void (x.retirementAge = v))} min={40} max={80} />
                    <NumberField
                      label="Plan income to age"
                      value={g.planningAge}
                      onChange={(v) => set((x) => void (x.planningAge = v))}
                      min={70}
                      max={110}
                      hint="Longevity planning: 90 (men) / 95 (women) is prudent"
                    />
                    <SegmentedField
                      label="Target income"
                      value={g.targetMode}
                      onChange={(v) => set((x) => void (x.targetMode = v))}
                      options={[
                        { value: 'percent', label: '% of income' },
                        { value: 'amount', label: 'Rand amount' },
                      ]}
                    />
                    {g.targetMode === 'percent' ? (
                      <PercentField label="Replacement ratio (of gross income)" value={g.targetPercent} onChange={(v) => set((x) => void (x.targetPercent = v))} decimals={0} hint="75% is the common SA benchmark" />
                    ) : (
                      <MoneyField label="Income needed (today’s money, p.m.)" value={g.targetMonthly} onChange={(v) => set((x) => void (x.targetMonthly = v))} />
                    )}
                    <MoneyField
                      label="Other retirement income (today’s money, p.m.)"
                      value={g.otherIncomeMonthly}
                      onChange={(v) => set((x) => void (x.otherIncomeMonthly = v))}
                      hint="e.g. rental income, a defined-benefit pension"
                    />
                  </Grid>
                </CardBody>
                {r && (
                  <div className="space-y-0.5 bg-wash/60 px-5 py-5">
                    <Row label="Target income (today)" value={`${money(r.targetMonthlyToday)} p.m.`} />
                    <Row label="Capital today" value={money(r.currentCapital)} />
                    <Row label="Contributions" value={`${money(r.monthlyContributions)} p.m.`} />
                    <Row label="Projected at retirement" value={money(r.projectedCapitalReal)} muted />
                    <Row label="Required at retirement" value={money(r.requiredCapitalReal)} muted />
                    <div className="my-1 border-t border-line" />
                    <Row label="Income replacement" value={pct(r.replacementRatio)} strong />
                    <Row label="Extra saving needed" value={r.additionalMonthly > 0 ? `${money(r.additionalMonthly)} p.m.` : '—'} strong />
                    <p className="pt-2 text-[11px] leading-4 text-muted">Capital shown in today’s money. See Needs analysis for the full projection.</p>
                  </div>
                )}
              </div>
            </Card>
          );
        })}

        <Card>
          <CardHeader icon={<GraduationCap size={18} />} title="Education" description="School fees and tertiary study for each child. Costs are in today’s money and inflate at the education inflation assumption." />
          <CardBody className="space-y-3">
            {children.length === 0 && <EmptyState title="No children recorded" description="Add children under Client & family to plan education funding." />}
            {children.map(({ d, i }) => {
              const e = analysis.education.find((x) => x.dependantId === d.id);
              const set = (fn: (x: typeof d) => void) => update((draft) => fn(draft.dependants[i]));
              const age = ageOn(d.dateOfBirth);
              return (
                <ItemCard
                  key={d.id}
                  title={d.name || 'Child'}
                  subtitle={age !== null ? `Age ${age}` : 'Add date of birth'}
                  badge={e && e.capitalAtStart > 0 ? <StatusPill status={e.status} /> : undefined}
                >
                  <Grid cols={4}>
                    <SelectField
                      label="Schooling"
                      value={d.schoolType}
                      onChange={(v) => set((x) => void (x.schoolType = v))}
                      options={{ none: 'Not at school / done', public: 'Public school', private: 'Independent school' }}
                    />
                    {d.schoolType !== 'none' && (
                      <MoneyField label="School fees per year" value={d.schoolFeesAnnual} onChange={(v) => set((x) => void (x.schoolFeesAnnual = v))} hint="Include levies, transport and uniforms" />
                    )}
                    <div className="flex items-end pb-2 sm:col-span-2">
                      <Toggle label="Plans tertiary education" checked={d.tertiary} onChange={(v) => set((x) => void (x.tertiary = v))} />
                    </div>
                    {d.tertiary && (
                      <>
                        <NumberField label="Starts at age" value={d.tertiaryStartAge} onChange={(v) => set((x) => void (x.tertiaryStartAge = v))} min={16} max={30} />
                        <NumberField label="Years of study" value={d.tertiaryYears} onChange={(v) => set((x) => void (x.tertiaryYears = v))} min={1} max={8} />
                        <MoneyField
                          label="Cost per year (today)"
                          value={d.tertiaryCostAnnual}
                          onChange={(v) => set((x) => void (x.tertiaryCostAnnual = v))}
                          hint="2026 guide: ~R70k tuition + R80–110k residence & living"
                        />
                        <MoneyField label="Saved so far" value={d.educationSavings} onChange={(v) => set((x) => void (x.educationSavings = v))} />
                        <MoneyField
                          label="Saving per month"
                          value={d.educationSavingsMonthly}
                          onChange={(v) => set((x) => void (x.educationSavingsMonthly = v))}
                          hint="Only if not already listed under Assets"
                        />
                      </>
                    )}
                  </Grid>
                  {e && e.capitalAtStart > 0 && (
                    <div className="mt-4 grid gap-3 rounded-lg bg-wash p-3 text-sm sm:grid-cols-4">
                      <div>
                        <div className="text-xs text-muted">Needed when study starts</div>
                        <div className="font-semibold tabular">{money(e.capitalAtStart)}</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted">Savings projected</div>
                        <div className="font-semibold tabular">{money(e.projectedSavings)}</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted">Shortfall (today’s value)</div>
                        <div className="font-semibold tabular">{money(e.shortfallToday)}</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted">{e.lumpSumRequired > 0 ? 'Needed now' : 'Save monthly (escalating)'}</div>
                        <div className="font-semibold tabular">{e.lumpSumRequired > 0 ? money(e.lumpSumRequired) : e.monthlyRequired > 0 ? money(e.monthlyRequired) : '—'}</div>
                      </div>
                    </div>
                  )}
                </ItemCard>
              );
            })}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            icon={<Target size={18} />}
            title="Other goals"
            description="Vehicle replacement, home deposit, travel, business start-up, a wedding…"
            actions={
              <Button size="sm" icon={<Plus size={15} />} onClick={() => update((d) => void d.goals.push(newGoal()))}>
                Add goal
              </Button>
            }
          />
          <CardBody className="space-y-3">
            {doc.goals.length === 0 && <EmptyState title="No other goals" description="Capture lifestyle and capital goals to test affordability." />}
            {doc.goals.map((g, i) => {
              const r = analysis.goals.find((x) => x.id === g.id);
              const set = (fn: (x: typeof g) => void) => update((d) => fn(d.goals[i]));
              return (
                <ItemCard
                  key={g.id}
                  title={g.name || 'Goal'}
                  badge={<Badge tone={g.priority === 'high' ? 'critical' : g.priority === 'medium' ? 'warning' : 'neutral'}>{g.priority} priority</Badge>}
                  onRemove={() => update((d) => void d.goals.splice(i, 1))}
                >
                  <Grid cols={4}>
                    <TextField label="Goal" value={g.name} onChange={(v) => set((x) => void (x.name = v))} />
                    <MoneyField label="Cost in today’s money" value={g.cost} onChange={(v) => set((x) => void (x.cost = v))} />
                    <NumberField label="Target year" value={g.targetYear} onChange={(v) => set((x) => void (x.targetYear = Math.round(v)))} min={2000} max={2100} />
                    <SelectField
                      label="Priority"
                      value={g.priority}
                      onChange={(v) => set((x) => void (x.priority = v))}
                      options={{ high: 'High', medium: 'Medium', low: 'Low' }}
                    />
                    <MoneyField label="Saved so far" value={g.existingSavings} onChange={(v) => set((x) => void (x.existingSavings = v))} />
                    <MoneyField label="Saving per month" value={g.monthlyContribution} onChange={(v) => set((x) => void (x.monthlyContribution = v))} />
                    {ownerOptions(doc).length > 1 && <SelectField label="For" value={g.owner} onChange={(v) => set((x) => void (x.owner = v))} options={ownerOptions(doc)} />}
                  </Grid>
                  {r && (
                    <p className="mt-3 text-sm text-ink-2">
                      Future cost <strong className="tabular">{money(r.futureCost)}</strong> in {r.years} year{r.years === 1 ? '' : 's'}; projected savings{' '}
                      <strong className="tabular">{money(r.projected)}</strong>.{' '}
                      {r.shortfall > 0 ? (
                        <>
                          Save <strong className="tabular">{money(r.monthlyRequired)}</strong> more per month to reach it.
                        </>
                      ) : (
                        'On track.'
                      )}
                    </p>
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
