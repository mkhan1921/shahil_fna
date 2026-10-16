'use client';

import { AudioLines, Download, FileText, Presentation } from 'lucide-react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { listReviewSummaries, reviewHref, type SessionSummary } from '@/lib/review/listing';
import { subscribe } from '@/lib/store/db';
import { useDocument, usePractice } from '@/lib/store/hooks';
import AppShell from '../AppShell';
import ExportDialog from '../ExportDialog';
import { EmptyState } from '../ui';
import LiveSummary from './LiveSummary';
import SheetFrame, { SaveIndicator, goToSection } from './SheetFrame';

const LEGACY_STEP: Record<string, string> = { profile: 'people', expenses: 'budget' };

/** Call reviews of this client that are not yet submitted. */
const useOpenReviews = (docId: string | null) => {
  const [reviews, setReviews] = useState<SessionSummary[]>([]);
  useEffect(() => {
    if (!docId) return;
    const load = () =>
      listReviewSummaries()
        .then((all) => setReviews(all.filter((r) => r.docId === docId && !r.submittedAt)))
        .catch(() => setReviews([]));
    load();
    return subscribe(load);
  }, [docId]);
  return reviews;
};

export default function Sheet() {
  const params = useSearchParams();
  const id = params?.get('id') ?? null;
  const initial = params?.get('s') ?? null;
  const { doc, analysis, loading, update, saveState, flush } = useDocument(id);
  const { practice } = usePractice();
  const [exporting, setExporting] = useState(false);
  const reviews = useOpenReviews(doc?.id ?? null);

  // Deep link (?s=section) and focus the first empty name on a new client.
  useEffect(() => {
    if (!doc) return;
    const target = initial ? (LEGACY_STEP[initial] ?? initial) : null;
    if (target && document.getElementById(target)) requestAnimationFrame(() => goToSection(target));
    else if (!doc.client.firstName) {
      document.getElementById('client-first-name')?.focus();
    }
  }, [doc?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading)
    return (
      <AppShell wide>
        <div className="p-16 text-center text-sm text-muted">Loading client…</div>
      </AppShell>
    );
  if (!doc || !analysis)
    return (
      <AppShell>
        <div className="mx-auto max-w-lg p-10">
          <EmptyState
            title="Client not found"
            description="This client file is not stored in this browser. Import it from an export file."
            action={
              <Link href="/" className="text-sm font-medium text-brand underline">
                Back to clients
              </Link>
            }
          />
        </div>
      </AppShell>
    );

  return (
    <>
      <SheetFrame
        doc={doc}
        analysis={analysis}
        update={update}
        practice={practice}
        rail={(go) => <LiveSummary analysis={analysis} go={go} />}
        actions={
          <div className="flex items-center gap-2">
            <SaveIndicator state={saveState} />
            {reviews.map((r) => (
              <Link
                key={r.callId}
                href={reviewHref(r.callId)}
                onClick={() => void flush()}
                tabIndex={-1}
                title="A call review of this client is waiting: open it to check the proposed changes"
                className="inline-flex h-8 items-center gap-1 rounded-lg bg-warning-soft px-2 text-[13px] font-medium text-warning-ink hover:bg-warning/20"
              >
                <AudioLines size={15} /> <span className="hidden sm:inline">Call review</span>
                {r.openFlags > 0 && <span className="tabular">· {r.openFlags}</span>}
              </Link>
            ))}
            <button type="button" tabIndex={-1} onClick={() => setExporting(true)} className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[13px] font-medium text-ink-2 hover:bg-wash">
              <Download size={15} /> <span className="hidden sm:inline">Export</span>
            </button>
            <Link
              href={`/present?id=${doc.id}`}
              onClick={() => void flush()}
              tabIndex={-1}
              title="Planning session: present the needs and try trade-offs with the client"
              className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[13px] font-medium text-ink-2 hover:bg-wash"
            >
              <Presentation size={15} /> <span className="hidden sm:inline">Present</span>
            </Link>
            <Link href={`/report?id=${doc.id}`} target="_blank" tabIndex={-1} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-brand px-3 text-[13px] font-medium text-white hover:bg-brand-2">
              <FileText size={15} /> Report
            </Link>
          </div>
        }
      />
      <ExportDialog doc={doc} open={exporting} onClose={() => setExporting(false)} />
    </>
  );
}
