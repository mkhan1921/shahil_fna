'use client';

/**
 * Practice settings for call reviews (spec §11.5): who the adviser is in a review, and the opt-in
 * review API. Off by default — reviews work by file exchange — and even when on, the app talks
 * only to an origin this deployment allows (NEXT_PUBLIC_ADVISOR_INTEL_ORIGINS, also in the
 * Content-Security-Policy).
 */
import { AudioLines } from 'lucide-react';
import { useEffect, useState } from 'react';
import { allowedOrigins, DEFAULT_API_SETTINGS, settingsProblems, type ReviewApiSettings } from '@/lib/review/api';
import { loadApiSettings, saveApiSettings } from '@/lib/review/listing';
import { Button, Card, CardBody, CardHeader, Grid, TextField, Toggle } from '../ui';

export default function ReviewApiCard() {
  const [draft, setDraft] = useState<ReviewApiSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const origins = allowedOrigins();

  useEffect(() => {
    loadApiSettings()
      .then(setDraft)
      .catch(() => setDraft({ ...DEFAULT_API_SETTINGS }));
  }, []);

  if (!draft) return null;
  const set = <K extends keyof ReviewApiSettings>(k: K, v: ReviewApiSettings[K]) => {
    setDraft({ ...draft, [k]: v });
    setSaved(false);
  };
  const problems = draft.enabled ? settingsProblems(draft, origins) : [];

  return (
    <Card>
      <CardHeader
        icon={<AudioLines size={18} />}
        title="Call reviews"
        description="How you appear in call reviews, and whether this device talks to the practice’s call intelligence pipeline."
        actions={
          <>
            {saved && <span className="text-sm text-good-ink">Saved</span>}
            <Button
              size="sm"
              onClick={async () => {
                await saveApiSettings({ ...draft, baseUrl: draft.baseUrl.trim(), token: draft.token.trim(), reviewerId: draft.reviewerId.trim() });
                setSaved(true);
              }}
            >
              Save review settings
            </Button>
          </>
        }
      />
      <CardBody className="space-y-4">
        <Grid cols={2}>
          <TextField
            label="Reviewer id"
            value={draft.reviewerId}
            onChange={(v) => set('reviewerId', v)}
            placeholder="e.g. adviser-sipho"
            hint="Stamped on every decision you make in a review. Left blank, the adviser name above is used."
          />
        </Grid>
        <Toggle
          checked={draft.enabled}
          onChange={(v) => set('enabled', v)}
          label="Use the review API"
          description="Off: reviews arrive and go back as files. On: fetch reviews from the pipeline and send each decision as you make it. Client information then goes to the address below."
        />
        {draft.enabled && (
          <Grid cols={2}>
            <TextField label="Review API address" value={draft.baseUrl} onChange={(v) => set('baseUrl', v)} placeholder="https://intel.yourpractice.co.za/api" />
            <TextField label="Bearer token" type="password" value={draft.token} onChange={(v) => set('token', v)} hint="Issued by whoever runs the pipeline. Kept on this device only." />
          </Grid>
        )}
        {problems.length > 0 && (
          <ul className="space-y-0.5 text-xs text-critical-ink">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        <p className="text-xs leading-5 text-muted">
          {origins.length
            ? `This deployment of the app may talk to ${origins.join(', ')}.`
            : 'This deployment of the app allows no review API, so reviews work by file exchange only. Whoever deploys the app can allow one with NEXT_PUBLIC_ADVISOR_INTEL_ORIGINS.'}
        </p>
      </CardBody>
    </Card>
  );
}
