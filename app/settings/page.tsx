'use client';

import { Building2, ImagePlus, Palette, RotateCcw, SlidersHorizontal, UserRound } from 'lucide-react';
import { useEffect, useState } from 'react';
import AppShell from '@/components/AppShell';
import { Button, Callout, Card, CardBody, CardHeader, Grid, PercentField, SelectField, TextArea, TextField, Toggle } from '@/components/ui';
import { DEFAULT_ASSUMPTIONS } from '@/lib/fna/defaults';
import { TAX_YEARS } from '@/lib/fna/tax';
import type { PracticeProfile } from '@/lib/fna/types';
import { usePractice } from '@/lib/store/hooks';

export default function SettingsPage() {
  const { practice, update } = usePractice();
  const [draft, setDraft] = useState<PracticeProfile | null>(null);
  const [saved, setSaved] = useState(false);
  const [msg, alertMsg] = useState<string | null>(null);

  useEffect(() => {
    if (practice && !draft) setDraft(practice);
  }, [practice, draft]);

  if (!draft) return <AppShell><div className="p-16 text-center text-sm text-muted">Loading…</div></AppShell>;

  const set = <K extends keyof PracticeProfile>(k: K, v: PracticeProfile[K]) => {
    setDraft((d) => (d ? { ...d, [k]: v } : d));
    setSaved(false);
  };
  const setA = <K extends keyof PracticeProfile['defaultAssumptions']>(k: K, v: PracticeProfile['defaultAssumptions'][K]) => {
    setDraft((d) => (d ? { ...d, defaultAssumptions: { ...d.defaultAssumptions, [k]: v } } : d));
    setSaved(false);
  };

  const onLogo = async (file: File) => {
    if (file.size > 600_000) {
      alertMsg('Please use a logo smaller than 600 KB.');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => set('logoDataUrl', String(reader.result));
    reader.readAsDataURL(file);
  };
  const save = async () => {
    await update(draft);
    setSaved(true);
  };

  return (
    <AppShell>
      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Practice settings</h1>
            <p className="mt-1 text-sm text-muted">These details appear on every report and satisfy the provider and representative disclosures of the FAIS General Code of Conduct (s4–s5).</p>
          </div>
          <div className="flex items-center gap-3">
            {saved && <span className="text-sm text-good-ink">Saved</span>}
            <Button variant="primary" onClick={save}>
              Save settings
            </Button>
          </div>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader icon={<Building2 size={18} />} title="Financial services provider" />
            <CardBody>
              <Grid cols={2}>
                <TextField label="Practice / trading name" value={draft.practiceName} onChange={(v) => set('practiceName', v)} />
                <TextField label="FSP legal name" value={draft.fspName} onChange={(v) => set('fspName', v)} />
                <TextField label="FSP licence number" value={draft.fspNumber} onChange={(v) => set('fspNumber', v)} placeholder="e.g. 12345" />
                <TextField label="Company registration number" value={draft.companyRegistration} onChange={(v) => set('companyRegistration', v)} optional />
                <TextField label="Physical address" value={draft.address} onChange={(v) => set('address', v)} />
                <TextField label="Website" value={draft.website} onChange={(v) => set('website', v)} optional />
                <TextField label="Key individual" value={draft.keyIndividual} onChange={(v) => set('keyIndividual', v)} />
                <TextField label="Professional indemnity / fidelity cover" value={draft.piCover} onChange={(v) => set('piCover', v)} placeholder="e.g. Held with Insurer X, R5m limit" />
                <TextField label="Compliance officer" value={draft.complianceOfficer} onChange={(v) => set('complianceOfficer', v)} />
                <TextField label="Compliance officer contact" value={draft.complianceOfficerContact} onChange={(v) => set('complianceOfficerContact', v)} />
                <TextField label="Complaints contact" value={draft.complaintsContact} onChange={(v) => set('complaintsContact', v)} placeholder="complaints@yourpractice.co.za" />
                <TextField label="Conflict of interest policy (link)" value={draft.conflictPolicyUrl} onChange={(v) => set('conflictPolicyUrl', v)} optional />
              </Grid>
              <Grid cols={1} className="mt-4">
                <TextArea label="Licensed categories and subcategories" value={draft.categories} onChange={(v) => set('categories', v)} rows={2} />
                <TextArea
                  label="Product suppliers represented"
                  value={draft.productSuppliers}
                  onChange={(v) => set('productSuppliers', v)}
                  rows={2}
                  hint="Disclose any supplier from which the FSP earned more than 30% of remuneration in the past 12 months, or in which it holds more than 10% of shares (GCC s4)."
                />
              </Grid>
            </CardBody>
          </Card>

          <Card>
            <CardHeader icon={<UserRound size={18} />} title="Representative" />
            <CardBody>
              <Grid cols={2}>
                <TextField label="Adviser name" value={draft.adviserName} onChange={(v) => set('adviserName', v)} />
                <TextField label="Qualifications" value={draft.adviserQualifications} onChange={(v) => set('adviserQualifications', v)} placeholder="e.g. CFP®, BCom (Hons)" />
                <TextField label="Representative number" value={draft.representativeNumber} onChange={(v) => set('representativeNumber', v)} optional />
                <TextField label="Email" type="email" value={draft.adviserEmail} onChange={(v) => set('adviserEmail', v)} />
                <TextField label="Phone" type="tel" value={draft.adviserPhone} onChange={(v) => set('adviserPhone', v)} />
              </Grid>
              <div className="mt-4 space-y-3">
                <Toggle label="Representative is working under supervision" checked={draft.underSupervision} onChange={(v) => set('underSupervision', v)} />
                {draft.underSupervision && <TextField label="Supervisor name" value={draft.supervisor} onChange={(v) => set('supervisor', v)} />}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader icon={<Palette size={18} />} title="Report branding" />
            <CardBody className="flex flex-wrap items-center gap-6">
              <div className="flex h-20 w-48 items-center justify-center rounded-lg border border-dashed border-line-strong bg-wash">
                {draft.logoDataUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={draft.logoDataUrl} alt="Logo" className="max-h-16 max-w-[11rem] object-contain" />
                ) : (
                  <span className="text-xs text-muted">No logo</span>
                )}
              </div>
              <div className="space-y-2">
                <label className="focus-ring inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3.5 text-sm font-medium hover:bg-wash">
                  <ImagePlus size={15} /> Upload logo
                  <input type="file" accept="image/png,image/jpeg,image/svg+xml,image/webp" className="sr-only" onChange={(e) => e.target.files?.[0] && onLogo(e.target.files[0])} />
                </label>
                {draft.logoDataUrl && (
                  <Button variant="ghost" size="sm" onClick={() => set('logoDataUrl', '')}>
                    Remove logo
                  </Button>
                )}
                {msg && <p className="text-xs text-critical-ink">{msg}</p>}
              </div>
              <label className="flex items-center gap-3 text-sm">
                <input type="color" value={draft.brandColor} onChange={(e) => set('brandColor', e.target.value)} className="h-9 w-12 cursor-pointer rounded border border-line-strong" />
                <span>
                  <span className="block font-medium">Accent colour</span>
                  <span className="text-xs text-muted">Used for headings in the report</span>
                </span>
              </label>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              icon={<SlidersHorizontal size={18} />}
              title="Default assumptions for new clients"
              description="Your house view. Each client’s assumptions can still be changed individually."
              actions={
                <Button size="sm" icon={<RotateCcw size={14} />} onClick={() => set('defaultAssumptions', { ...DEFAULT_ASSUMPTIONS })}>
                  Research defaults
                </Button>
              }
            />
            <CardBody>
              <Grid cols={3}>
                <SelectField label="Tax tables" value={draft.defaultAssumptions.taxYear} onChange={(v) => setA('taxYear', v)} options={TAX_YEARS().map((y) => ({ value: y, label: y }))} />
                <PercentField label="Inflation (CPI)" value={draft.defaultAssumptions.cpi} onChange={(v) => setA('cpi', v)} decimals={1} />
                <PercentField label="Salary escalation" value={draft.defaultAssumptions.salaryEscalation} onChange={(v) => setA('salaryEscalation', v)} decimals={1} />
                <PercentField label="Pre-retirement return" value={draft.defaultAssumptions.preRetirementReturn} onChange={(v) => setA('preRetirementReturn', v)} decimals={1} />
                <PercentField label="Post-retirement return" value={draft.defaultAssumptions.postRetirementReturn} onChange={(v) => setA('postRetirementReturn', v)} decimals={1} />
                <PercentField label="Return on capital for dependants" value={draft.defaultAssumptions.riskCapitalReturn} onChange={(v) => setA('riskCapitalReturn', v)} decimals={1} />
                <PercentField label="Education inflation" value={draft.defaultAssumptions.educationInflation} onChange={(v) => setA('educationInflation', v)} decimals={1} />
                <PercentField label="Family income on death" value={draft.defaultAssumptions.deathIncomeReplacement} onChange={(v) => setA('deathIncomeReplacement', v)} decimals={0} />
                <PercentField label="Income protection target" value={draft.defaultAssumptions.incomeProtectionTarget} onChange={(v) => setA('incomeProtectionTarget', v)} decimals={0} />
              </Grid>
            </CardBody>
          </Card>

          <Callout tone="info" title="Where is my data?">
            Practice settings and client files are stored in this browser on this device only (IndexedDB). They are never uploaded. Use Export on the Clients page to back up
            client files, and repeat this setup on each device you use.
          </Callout>

          <div className="flex justify-end">
            <Button variant="primary" onClick={save}>
              Save settings
            </Button>
          </div>
        </div>
      </main>
    </AppShell>
  );
}
