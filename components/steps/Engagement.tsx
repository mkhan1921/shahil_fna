'use client';

import { ClipboardCheck, Handshake, ShieldCheck } from 'lucide-react';
import { SCOPE_AREA } from '@/lib/fna/catalog';
import { DISCLAIMERS } from '@/lib/fna/compliance';
import type { ScopeArea } from '@/lib/fna/types';
import {
  Callout,
  Card,
  CardBody,
  CardHeader,
  Checkbox,
  DateField,
  Grid,
  SegmentedField,
  SelectField,
  StepHeader,
  TextArea,
} from '../ui';
import type { StepProps } from '../workspace/types';

export default function Engagement({ doc, update }: StepProps) {
  const e = doc.engagement;
  const set = <K extends keyof typeof e>(k: K, v: (typeof e)[K]) => update((d) => void (d.engagement[k] = v));

  return (
    <>
      <StepHeader
        eyebrow="Step 1"
        title="Engagement & consent"
        description="Record the basis of the engagement, the scope of advice and the disclosures and consents required by FAIS, POPIA and FICA before gathering information."
      />
      <div className="space-y-6">
        <Card>
          <CardHeader icon={<Handshake size={18} />} title="Meeting" description="When and how the needs analysis was conducted." />
          <CardBody>
            <Grid cols={3}>
              <DateField label="Date of meeting" value={e.meetingDate} onChange={(v) => set('meetingDate', v)} />
              <SelectField
                label="Meeting type"
                value={e.meetingType}
                onChange={(v) => set('meetingType', v)}
                options={{ 'in-person': 'In person', virtual: 'Virtual (video)', telephonic: 'Telephonic' }}
              />
              <SelectField
                label="Politically exposed person?"
                value={e.pepStatus}
                onChange={(v) => set('pepStatus', v)}
                options={{ no: 'No', yes: 'Yes — DPIP / FPPO', unknown: 'Not yet screened' }}
              />
            </Grid>
            <Grid cols={1} className="mt-4">
              <TextArea
                label="Reason for the engagement"
                value={e.reasonForAdvice}
                onChange={(v) => set('reasonForAdvice', v)}
                placeholder="e.g. Annual review; birth of a child; new home loan; approaching retirement"
                rows={2}
              />
              <TextArea
                label="Client’s objectives in their own words"
                value={e.clientObjectives}
                onChange={(v) => set('clientObjectives', v)}
                placeholder="What would the client like to achieve? What does financial security mean to them?"
              />
              <TextArea label="Concerns and priorities" value={e.clientConcerns} onChange={(v) => set('clientConcerns', v)} rows={2} optional />
              <TextArea
                label="Other advisers (attorney, accountant, other FSPs)"
                value={e.otherAdvisers}
                onChange={(v) => set('otherAdvisers', v)}
                rows={2}
                optional
              />
            </Grid>
          </CardBody>
        </Card>

        <Card>
          <CardHeader icon={<ClipboardCheck size={18} />} title="Scope of advice" description="FAIS General Code of Conduct s8(4): if the analysis is limited, the client must be told of its limitations." />
          <CardBody className="space-y-5">
            <SegmentedField
              label="Type of advice"
              value={e.adviceType}
              onChange={(v) => set('adviceType', v)}
              options={[
                { value: 'comprehensive', label: 'Comprehensive FNA' },
                { value: 'limited', label: 'Limited advice' },
              ]}
            />
            <div>
              <div className="mb-2 text-[13px] font-medium text-ink-2">Areas included in this analysis</div>
              <div className="grid gap-2.5 sm:grid-cols-2">
                {(Object.keys(SCOPE_AREA) as ScopeArea[]).map((k) => (
                  <Checkbox
                    key={k}
                    label={SCOPE_AREA[k]}
                    checked={e.scope.includes(k)}
                    onChange={(on) =>
                      update((d) => {
                        const s = new Set(d.engagement.scope);
                        if (on) s.add(k);
                        else s.delete(k);
                        d.engagement.scope = [...s];
                      })
                    }
                  />
                ))}
              </div>
            </div>
            {e.adviceType === 'limited' && (
              <>
                <TextArea
                  label="Limitations agreed with the client"
                  value={e.limitations}
                  onChange={(v) => set('limitations', v)}
                  placeholder="e.g. Client requested advice on income protection only; retirement and estate planning were not analysed."
                />
                <Callout tone="warning" title="Limited advice warning (printed in the report)">
                  {DISCLAIMERS.limited}
                </Callout>
              </>
            )}
            <Checkbox
              label="Client declined to provide some of the information requested"
              description="The report will include the warning required by GCC s8(4)(b)."
              checked={e.clientDeclinedFna}
              onChange={(v) => set('clientDeclinedFna', v)}
            />
            {e.clientDeclinedFna && (
              <TextArea label="Information not provided, and reason" value={e.declineReason} onChange={(v) => set('declineReason', v)} rows={2} />
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader icon={<ShieldCheck size={18} />} title="Disclosures & consents" description="Confirm each item before advice is given. Outstanding items are flagged on the analysis and report." />
          <CardBody className="space-y-4">
            <Checkbox
              label="FSP and representative disclosures provided (GCC s4, s5 and s7)"
              description="Licence number, categories, PI cover, compliance officer, complaints process and supervision status."
              checked={e.disclosureLetterProvided}
              onChange={(v) => set('disclosureLetterProvided', v)}
            />
            <Checkbox
              label="Conflict of interest policy disclosed (GCC s3(1)(c))"
              checked={e.conflictsDisclosed}
              onChange={(v) => set('conflictsDisclosed', v)}
            />
            <Checkbox
              label="Remuneration, fees and commission explained in rand terms"
              checked={e.remunerationDisclosed}
              onChange={(v) => set('remunerationDisclosed', v)}
            />
            <div className="rounded-lg border border-line bg-wash/60 p-4">
              <Checkbox
                label="POPIA consent obtained to process personal information"
                description={DISCLAIMERS.popia}
                checked={e.popiaConsent}
                onChange={(v) =>
                  update((d) => {
                    d.engagement.popiaConsent = v;
                    if (v && !d.engagement.popiaConsentDate) d.engagement.popiaConsentDate = new Date().toISOString().slice(0, 10);
                  })
                }
              />
              <div className="mt-3 pl-7">
                <Checkbox
                  label="Consent to process special personal information (health, criminal behaviour) — POPIA s26–27"
                  checked={e.specialInfoConsent}
                  onChange={(v) => set('specialInfoConsent', v)}
                />
              </div>
              {e.popiaConsent && (
                <div className="mt-3 max-w-xs pl-7">
                  <DateField label="Consent date" value={e.popiaConsentDate} onChange={(v) => set('popiaConsentDate', v)} />
                </div>
              )}
            </div>
            <Checkbox label="Client identity and address verified (FICA)" checked={e.ficaVerified} onChange={(v) => set('ficaVerified', v)} />
            <Checkbox
              label="Client opted in to electronic direct marketing (POPIA s69)"
              checked={e.marketingOptIn}
              onChange={(v) => set('marketingOptIn', v)}
            />
          </CardBody>
        </Card>
      </div>
    </>
  );
}
