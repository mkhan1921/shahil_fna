'use client';

import { FileSignature, Plus, Sparkles, Wand2 } from 'lucide-react';
import { ACTION, NEED_AREA } from '@/lib/fna/catalog';
import { DISCLAIMERS, REPLACEMENT_FACTORS } from '@/lib/fna/compliance';
import { newRecommendation } from '@/lib/fna/defaults';
import { draftRecommendations } from '@/lib/fna/recommend';
import type { ClientDecision, Recommendation } from '@/lib/fna/types';
import { money } from '@/lib/format';
import {
  Badge,
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Checkbox,
  DateField,
  EmptyState,
  Grid,
  ItemCard,
  MoneyField,
  PercentField,
  SegmentedField,
  SelectField,
  Stat,
  StepHeader,
  TextArea,
  TextField,
  Toggle,
} from '../ui';
import type { StepProps } from '../workspace/types';
import { firstNameOf, personOptions } from './shared';

const DECISION_TONE: Record<ClientDecision, 'neutral' | 'good' | 'critical' | 'warning'> = {
  pending: 'neutral',
  accepted: 'good',
  declined: 'critical',
  deferred: 'warning',
};

export const RISK_AREAS = ['life', 'disability', 'income-protection', 'severe-illness', 'funeral'];

export default function Advice({ doc, update, analysis }: StepProps) {
  const adv = doc.advice;
  const recs = adv.recommendations;
  const accepted = recs.filter((r) => r.clientDecision === 'accepted' || r.clientDecision === 'pending');
  const newPremiums = accepted.reduce((s, r) => s + (r.premiumMonthly || 0), 0);
  const surplus = analysis.cashflow.surplus;

  const draft = () => {
    const drafts = draftRecommendations(doc, analysis);
    update((d) => {
      for (const r of drafts) {
        const exists = d.advice.recommendations.some((x) => x.area === r.area && x.lifeAssured === r.lifeAssured && x.productType === r.productType);
        if (!exists) d.advice.recommendations.push(r);
      }
    });
  };

  return (
    <>
      <StepHeader
        eyebrow="Step 13"
        title="Advice & record of advice"
        description="Record what was recommended, why it is suitable, what was considered, and what the client decided — the content required by GCC section 9."
        actions={
          <Button variant="primary" icon={<Wand2 size={15} />} onClick={draft}>
            Draft from analysis
          </Button>
        }
      />
      <div className="space-y-6">
        <Card>
          <CardBody className="grid grid-cols-2 gap-6 lg:grid-cols-4">
            <Stat label="Recommendations" value={recs.length} sub={`${recs.filter((r) => r.clientDecision === 'accepted').length} accepted`} />
            <Stat label="New premiums & contributions" value={`${money(newPremiums)} p.m.`} sub="Accepted and pending" />
            <Stat label="Current monthly surplus" value={money(surplus)} tone={surplus < 0 ? 'critical' : undefined} />
            <Stat
              label="After recommendations"
              value={money(surplus - newPremiums)}
              tone={surplus - newPremiums < 0 ? 'critical' : 'good'}
              sub={surplus - newPremiums < 0 ? 'Requires budget changes or phasing' : 'Affordable from surplus'}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Summary of advice" description="Plain-language summary of the client’s situation, needs identified and the basis of your recommendations." />
          <CardBody>
            <TextArea
              label="Advice summary"
              value={adv.summary}
              onChange={(v) => update((d) => void (d.advice.summary = v))}
              rows={5}
              placeholder="e.g. Thabo and Lerato’s main risks are loss of income and premature death while the children are young. We prioritised income protection and life cover, funded by…"
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Recommendations"
            description="Premiums are indicative until underwritten quotes are obtained. Attach the product quotations to the client file."
            actions={
              <Button size="sm" icon={<Plus size={15} />} onClick={() => update((d) => void d.advice.recommendations.push(newRecommendation()))}>
                Add
              </Button>
            }
          />
          <CardBody className="space-y-3">
            {recs.length === 0 && (
              <EmptyState
                icon={<Sparkles size={30} strokeWidth={1.5} />}
                title="No recommendations yet"
                description="Draft recommendations from the analysis, then edit products, premiums and reasons."
                action={
                  <Button variant="primary" icon={<Wand2 size={15} />} onClick={draft}>
                    Draft from analysis
                  </Button>
                }
              />
            )}
            {recs.map((r, i) => (
              <RecommendationCard key={r.id} r={r} i={i} doc={doc} update={update} />
            ))}
          </CardBody>
        </Card>

        <Card>
          <CardHeader icon={<FileSignature size={18} />} title="Record of advice" />
          <CardBody className="space-y-4">
            <TextArea
              label="Products considered but not recommended, and why"
              value={adv.productsConsidered}
              onChange={(v) => update((d) => void (d.advice.productsConsidered = v))}
              placeholder="GCC s9(1)(b): e.g. Considered a whole-of-life policy from Insurer X; not recommended because the premium pattern is less affordable over 20 years."
            />
            <TextArea
              label="Where the client departed from the advice"
              value={adv.clientDeviations}
              onChange={(v) => update((d) => void (d.advice.clientDeviations = v))}
              rows={2}
              hint="Declined recommendations are listed automatically with the GCC s8(4)(c) warning."
            />
            <TextArea label="Fees and commission (in rand)" value={adv.feesAndCommission} onChange={(v) => update((d) => void (d.advice.feesAndCommission = v))} rows={3} />
            <TextArea
              label="Conflicts of interest"
              value={adv.conflictsOfInterest}
              onChange={(v) => update((d) => void (d.advice.conflictsOfInterest = v))}
              rows={2}
              placeholder="e.g. None. / The FSP received more than 30% of its remuneration from Insurer X in the past 12 months."
            />
            <Grid cols={3}>
              <DateField label="Next review date" value={adv.nextReviewDate} onChange={(v) => update((d) => void (d.advice.nextReviewDate = v))} />
              <DateField label="Client signed" value={adv.clientSignedAt} onChange={(v) => update((d) => void (d.advice.clientSignedAt = v))} optional />
              <DateField label="Adviser signed" value={adv.adviserSignedAt} onChange={(v) => update((d) => void (d.advice.adviserSignedAt = v))} optional />
            </Grid>
            <Checkbox
              label="A copy of this record of advice was provided to the client (GCC s9(2))"
              checked={doc.engagement.copyProvided}
              onChange={(v) => update((d) => void (d.engagement.copyProvided = v))}
            />
            <Toggle
              label="Mark advice as given"
              description="Changes the client status to ‘Advice given’."
              checked={doc.status === 'advice-given'}
              onChange={(v) => update((d) => void (d.status = v ? 'advice-given' : 'in-progress'))}
            />
          </CardBody>
        </Card>
      </div>
    </>
  );
}

function RecommendationCard({ r, i, doc, update }: { r: Recommendation; i: number; doc: StepProps['doc']; update: StepProps['update'] }) {
  const set = (fn: (x: Recommendation) => void) => update((d) => fn(d.advice.recommendations[i]));
  const isRisk = RISK_AREAS.includes(r.area);
  const monthlyBenefit = r.area === 'income-protection';
  const lives = personOptions(doc);
  return (
    <ItemCard
      title={`${NEED_AREA[r.area]}${r.productType ? ` — ${r.productType}` : ''}`}
      badge={
        <>
          <Badge tone="brand">{ACTION[r.action]}</Badge>
          <Badge tone={DECISION_TONE[r.clientDecision]}>{r.clientDecision === 'pending' ? 'Decision pending' : r.clientDecision}</Badge>
        </>
      }
      subtitle={lives.length > 1 ? firstNameOf(doc, r.lifeAssured) : undefined}
      onRemove={() => update((d) => void d.advice.recommendations.splice(i, 1))}
    >
      <Grid cols={4}>
        <SelectField label="Need" value={r.area} onChange={(v) => set((x) => void (x.area = v))} options={NEED_AREA} />
        <SelectField label="Action" value={r.action} onChange={(v) => set((x) => void (x.action = v))} options={ACTION} />
        {lives.length > 1 && <SelectField label="For" value={r.lifeAssured} onChange={(v) => set((x) => void (x.lifeAssured = v))} options={lives} />}
        <TextField label="Product type" value={r.productType} onChange={(v) => set((x) => void (x.productType = v))} />
        <TextField label="Product supplier" value={r.provider} onChange={(v) => set((x) => void (x.provider = v))} />
        <TextField label="Product name" value={r.productName} onChange={(v) => set((x) => void (x.productName = v))} optional />
        <MoneyField
          label={monthlyBenefit ? 'Monthly benefit' : isRisk ? 'Cover amount' : 'Amount / contribution'}
          value={r.amount}
          onChange={(v) => set((x) => void (x.amount = v))}
        />
        <MoneyField label="Premium / contribution p.m." value={r.premiumMonthly} onChange={(v) => set((x) => void (x.premiumMonthly = v))} />
        <PercentField label="Premium escalation p.a." value={r.premiumEscalation} onChange={(v) => set((x) => void (x.premiumEscalation = v))} decimals={1} />
      </Grid>
      <div className="mt-4 space-y-4">
        <TextArea label="Why this is suitable (GCC s9(1)(c))" value={r.rationale} onChange={(v) => set((x) => void (x.rationale = v))} rows={3} />
        <Toggle
          label="This replaces an existing product"
          description="Triggers the replacement disclosures of GCC s8(1)(d) and PPR rule 19"
          checked={r.isReplacement}
          onChange={(v) => set((x) => void (x.isReplacement = v))}
        />
        {r.isReplacement && (
          <div className="space-y-4 rounded-lg border border-warning/40 bg-warning-soft/40 p-4">
            <TextField label="Product being replaced" value={r.replacedPolicy} onChange={(v) => set((x) => void (x.replacedPolicy = v))} placeholder="Insurer, policy number, cover and premium" />
            <div>
              <div className="mb-2 text-[13px] font-medium text-ink-2">Factors compared and discussed with the client</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {REPLACEMENT_FACTORS.map((f) => (
                  <Checkbox
                    key={f.key}
                    label={f.label}
                    checked={r.replacementChecklist.includes(f.key)}
                    onChange={(on) =>
                      set((x) => {
                        const s = new Set(x.replacementChecklist);
                        if (on) s.add(f.key);
                        else s.delete(f.key);
                        x.replacementChecklist = [...s];
                      })
                    }
                  />
                ))}
              </div>
            </div>
            <TextArea label="Cost comparison" value={r.replacementCostComparison} onChange={(v) => set((x) => void (x.replacementCostComparison = v))} rows={2} />
            <TextArea label="Consequences of replacement" value={r.replacementConsequences} onChange={(v) => set((x) => void (x.replacementConsequences = v))} rows={2} placeholder="Lost benefits, new waiting periods and exclusions, re-underwriting, penalties" />
            <TextArea label="Why replacement is more suitable than keeping or modifying the existing product" value={r.replacementReasons} onChange={(v) => set((x) => void (x.replacementReasons = v))} rows={2} />
            <Callout tone="warning">{DISCLAIMERS.replacement}</Callout>
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-[auto_1fr]">
          <SegmentedField
            label="Client decision"
            value={r.clientDecision}
            onChange={(v) => set((x) => void (x.clientDecision = v))}
            options={[
              { value: 'pending', label: 'Pending' },
              { value: 'accepted', label: 'Accepted' },
              { value: 'deferred', label: 'Deferred' },
              { value: 'declined', label: 'Declined' },
            ]}
          />
          {r.clientDecision !== 'pending' && r.clientDecision !== 'accepted' && (
            <TextField label="Client’s reason" value={r.clientReason} onChange={(v) => set((x) => void (x.clientReason = v))} />
          )}
        </div>
      </div>
    </ItemCard>
  );
}
