'use client';

import { Gauge } from 'lucide-react';
import { RISK_CATEGORIES, RISK_QUESTIONS } from '@/lib/fna/riskProfile';
import { Badge, Callout, Card, CardBody, CardHeader, StepHeader, TextArea, cx } from '../ui';
import type { StepProps } from '../workspace/types';

export default function Risk({ doc, update, analysis }: StepProps) {
  const r = analysis.risk;
  return (
    <>
      <StepHeader
        eyebrow="Step 10"
        title="Risk profile"
        description="Measures willingness (tolerance) and ability (capacity) to take investment risk. The recommended profile is the lower of the two, as suitability requires under GCC s8(1)(a)."
      />
      <div className="grid gap-6 xl:grid-cols-[1fr_20rem]">
        <div className="space-y-4">
          {RISK_QUESTIONS.map((q, qi) => (
            <Card key={q.id}>
              <CardBody>
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div className="text-sm font-medium text-ink">
                    <span className="mr-2 text-muted tabular">{qi + 1}.</span>
                    {q.question}
                  </div>
                  <Badge tone={q.dimension === 'capacity' ? 'brand' : 'neutral'}>{q.dimension === 'capacity' ? 'Capacity' : 'Tolerance'}</Badge>
                </div>
                <div role="radiogroup" aria-label={q.question} className="grid gap-2">
                  {q.options.map((o, oi) => {
                    const active = doc.riskProfile.answers[q.id] === oi;
                    return (
                      <button
                        key={o.label}
                        type="button"
                        role="radio"
                        aria-checked={active}
                        onClick={() => update((d) => void (d.riskProfile.answers[q.id] = oi))}
                        className={cx(
                          'focus-ring flex items-center gap-3 rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                          active ? 'border-brand-2 bg-brand-soft text-ink' : 'border-line hover:border-line-strong hover:bg-wash',
                        )}
                      >
                        <span className={cx('flex h-4 w-4 shrink-0 items-center justify-center rounded-full border', active ? 'border-brand-2' : 'border-line-strong')}>
                          {active && <span className="h-2 w-2 rounded-full bg-brand-2" />}
                        </span>
                        {o.label}
                      </button>
                    );
                  })}
                </div>
              </CardBody>
            </Card>
          ))}
          <Card>
            <CardBody>
              <TextArea
                label="Adviser notes on risk profile and product knowledge"
                value={doc.riskProfile.notes}
                onChange={(v) => update((d) => void (d.riskProfile.notes = v))}
                placeholder="e.g. Client understands market volatility from previous RA experience; prefers not to hold more than 60% equities."
              />
            </CardBody>
          </Card>
        </div>
        <div className="xl:sticky xl:top-20 xl:self-start">
          <Card>
            <CardHeader icon={<Gauge size={18} />} title="Result" description={`${r.answered} of ${r.total} answered`} />
            <CardBody className="space-y-4">
              <div className="space-y-1.5">
                {RISK_CATEGORIES.map((c) => {
                  const active = r.profile?.key === c.key;
                  return (
                    <div key={c.key} className={cx('flex items-center justify-between rounded-md px-3 py-2 text-sm', active ? 'bg-brand text-white' : 'text-ink-2')}>
                      <span className="font-medium">{c.label}</span>
                      <span className={cx('text-xs', active ? 'text-white/80' : 'text-muted')}>{c.equity.split(' growth')[0]}</span>
                    </div>
                  );
                })}
              </div>
              {r.profile ? (
                <div className="space-y-1 text-sm text-ink-2">
                  <p>{r.profile.description}</p>
                  <p className="text-xs text-muted">
                    Typical target {r.profile.target} over {r.profile.horizon}.
                  </p>
                  <p className="pt-1 text-xs text-muted">
                    Tolerance: {r.tolerance?.label} · Capacity: {r.capacity?.label}
                  </p>
                </div>
              ) : (
                <p className="text-sm text-muted">Answer the questions to calculate the profile.</p>
              )}
              {r.mismatch && (
                <Callout tone="warning" title="Tolerance and capacity differ">
                  Discuss with the client: the portfolio should follow the lower (capacity-constrained) profile.
                </Callout>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
