'use client';

/**
 * Call reviews on the client list (spec §11.5): import what the pipeline handed over — a review
 * package, or a plain client file with its provenance sidecar — load the demo review, fetch a
 * review from the pipeline when the practice has turned the review API on, and pick up reviews and
 * gold annotations in progress on this device.
 */
import { AudioLines, CloudDownload, FileUp, NotebookPen, PlayCircle, Trash2 } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { dateLong, relativeTime } from '@/lib/format';
import { apiReady, createReviewApi, type ReviewApiSettings } from '@/lib/review/api';
import {
  annotateHref,
  annotationKey,
  deleteAnnotation,
  deleteSession,
  listAnnotationSummaries,
  listReviewSummaries,
  loadApiSettings,
  reviewHref,
  type AnnotationSummary,
  type SessionSummary,
} from '@/lib/review/listing';
import type { ReviewBundle } from '@/lib/review/types';
import { getDocument, subscribe } from '@/lib/store/db';
import { usePractice } from '@/lib/store/hooks';
import { importDocument, readTransferFile } from '@/lib/store/transfer';
import Modal from '../Modal';
import { Badge, Button, Callout, Card, Segmented, cx } from '../ui';

/* The review model (the slot catalog, schemas, reducer) loads only when a review is opened. */
const exchange = () => import('@/lib/review/exchange');
const starting = () => import('@/lib/review/start');

/** What the adviser chose, by file format: a review package, or a client file with its sidecar. */
const bundleFromTexts = async (texts: string[]): Promise<ReviewBundle> => {
  const { ImportError, bundleFromFiles, readReviewPackage, readSidecar } = await exchange();
  const files = texts.map((text) => ({ text, file: readTransferFile(text) }));
  const pkg = files.find((f) => f.file?.format === 'advisor-intel-review');
  if (pkg) return readReviewPackage(pkg.text);
  const sidecar = files.find((f) => f.file?.format === 'advisor-intel-sidecar');
  const fna = files.find((f) => f.file?.format === 'shahil-fna');
  if (sidecar && fna) {
    if (fna.file?.encrypted) throw new ImportError('The client file from the pipeline is encrypted: ask for it unencrypted, or import it on its own first.');
    const median = await importDocument(fna.text);
    return bundleFromFiles(median, await readSidecar(sidecar.text), await getDocument(median.id));
  }
  if (fna) throw new ImportError('Choose the provenance sidecar together with the client file: without it there is nothing to review.');
  if (sidecar) throw new ImportError('Choose the client file the pipeline sent together with its sidecar.');
  if (files.some((f) => f.file?.format === 'advisor-intel-review-result')) throw new ImportError('This is a review result: it goes back to the pipeline, not into the app.');
  throw new ImportError('These files are not a review package, or a client file with its sidecar.');
};

export default function CallReviews() {
  const router = useRouter();
  const { practice } = usePractice();
  const [reviews, setReviews] = useState<SessionSummary[]>([]);
  const [annotations, setAnnotations] = useState<AnnotationSummary[]>([]);
  const [api, setApi] = useState<ReviewApiSettings | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [otherRun, setOtherRun] = useState<ReviewBundle | null>(null);
  const [fetching, setFetching] = useState(false);
  const [callId, setCallId] = useState('');
  const [annotating, setAnnotating] = useState(false);
  const [annotator, setAnnotator] = useState('');
  const [role, setRole] = useState<'annotator' | 'adjudicator'>('annotator');
  const [confirmDelete, setConfirmDelete] = useState<{ kind: 'review' | 'annotation'; key: string; label: string } | null>(null);
  const reviewInput = useRef<HTMLInputElement>(null);
  const annotateInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    listReviewSummaries()
      .then(setReviews)
      .catch(() => setReviews([]));
    listAnnotationSummaries()
      .then(setAnnotations)
      .catch(() => setAnnotations([]));
  }, []);

  useEffect(() => {
    load();
    loadApiSettings()
      .then(setApi)
      .catch(() => setApi(null));
    return subscribe(load);
  }, [load]);

  const run = async (label: string, job: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await job();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const open = async (bundle: ReviewBundle, replace = false) => {
    const { reviewerIdOf, startReview } = await starting();
    const out = await startReview(bundle, { reviewerId: reviewerIdOf(api, practice), replace });
    if (out.status === 'other-run') {
      setOtherRun(bundle);
      return;
    }
    router.push(reviewHref(out.callId));
  };

  const demoBundle = async () => {
    const [{ DEMO_PACKAGE_URL }, { ImportError, readReviewPackage }] = await Promise.all([starting(), exchange()]);
    const res = await fetch(DEMO_PACKAGE_URL, { cache: 'no-store' });
    if (!res.ok) throw new ImportError('The demo review could not be loaded.');
    return readReviewPackage(await res.text());
  };

  const readPackage = async (text: string) => (await exchange()).readReviewPackage(text);

  const annotate = async (bundle: ReviewBundle) => {
    const id = annotator.trim();
    if (!id) throw new Error('Enter your annotator id first.');
    const [{ annotationSource, createAnnotation }, { saveAnnotation }] = await Promise.all([import('@/lib/review/annotate'), import('@/lib/review/store')]);
    const session = createAnnotation(annotationSource(bundle), { annotatorId: id, now: new Date().toISOString(), role });
    await saveAnnotation(session);
    router.push(annotateHref(annotationKey(session.annotation.call_id, id)));
  };

  const ready = apiReady(api);

  return (
    <Card className="mb-6">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <AudioLines size={18} className="shrink-0 text-brand-2" aria-hidden />
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-ink">Call reviews</h2>
            <p className="text-xs text-muted">What the call intelligence pipeline heard, proposed on the client’s own sheet for you to check before it reaches the file.</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            ref={reviewInput}
            type="file"
            multiple
            accept=".json,.fna,application/json"
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = '';
              if (files.length) void run('import', async () => open(await bundleFromTexts(await Promise.all(files.map((f) => f.text())))));
            }}
          />
          <Button size="sm" icon={<FileUp size={14} />} onClick={() => reviewInput.current?.click()} disabled={!!busy} title="A review package, or a client file together with its provenance sidecar">
            Import review
          </Button>
          {ready && (
            <Button size="sm" icon={<CloudDownload size={14} />} onClick={() => setFetching(true)} disabled={!!busy}>
              Fetch from pipeline
            </Button>
          )}
          <Button size="sm" icon={<PlayCircle size={14} />} onClick={() => void run('demo', async () => open(await demoBundle()))} disabled={!!busy}>
            {busy === 'demo' ? 'Loading…' : 'Load demo review'}
          </Button>
          <Button size="sm" variant="ghost" icon={<NotebookPen size={14} />} onClick={() => setAnnotating(true)} disabled={!!busy} title="Annotate a call for the evaluation set, without seeing the system's output">
            Annotate a call
          </Button>
        </div>
      </div>

      {error && (
        <Callout tone="critical" className="m-3" title="Could not open the review">
          {error}{' '}
          <button type="button" className="underline" onClick={() => setError(null)}>
            Dismiss
          </button>
        </Callout>
      )}

      {reviews.length === 0 && annotations.length === 0 ? (
        <p className="px-4 py-3 text-[12.5px] text-muted">
          No call reviews on this device. Import the review package the pipeline produced after a call, or load the demo review of the Mokoena family’s annual review.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {reviews.map((r) => (
            <li key={r.callId} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
              <Link href={reviewHref(r.callId)} className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-ink hover:text-brand">{r.household}</span>
                <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                  <span>Call of {dateLong(r.callDate)}</span>
                  <span className="tabular">
                    {r.decided}/{r.total} decided
                  </span>
                  {r.submittedAt ? <Badge tone="good">Submitted {relativeTime(r.submittedAt)}</Badge> : r.openFlags > 0 ? <Badge tone="critical">{r.openFlags} blocking</Badge> : <Badge tone="brand">Ready to submit</Badge>}
                </span>
              </Link>
              <div className="flex items-center gap-1">
                <Button variant="ghost" size="sm" aria-label="Delete review" title="Delete this review from the device" className="hover:text-critical-ink" onClick={() => setConfirmDelete({ kind: 'review', key: r.callId, label: `the review of ${r.household}’s call` })}>
                  <Trash2 size={15} />
                </Button>
                <Link href={reviewHref(r.callId)} className="focus-ring inline-flex h-8 items-center rounded-lg bg-brand px-3 text-[13px] font-medium text-white hover:bg-brand-2">
                  {r.submittedAt ? 'View' : 'Review'}
                </Link>
              </div>
            </li>
          ))}
          {annotations.map((a) => (
            <li key={a.key} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
              <Link href={annotateHref(a.key)} className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 truncate text-sm font-medium text-ink hover:text-brand">
                  <NotebookPen size={14} className="text-muted" aria-hidden /> Gold annotation · {a.callId}
                </span>
                <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted">
                  <span>
                    {a.annotator} ({a.role})
                  </span>
                  <span className="tabular">
                    {a.facts} fact{a.facts === 1 ? '' : 's'}
                  </span>
                  {a.completedAt && <Badge tone="good">Completed</Badge>}
                </span>
              </Link>
              <div className="flex items-center gap-1">
                <Button variant="ghost" size="sm" aria-label="Delete annotation" className="hover:text-critical-ink" onClick={() => setConfirmDelete({ kind: 'annotation', key: a.key, label: `${a.annotator}’s annotation of ${a.callId}` })}>
                  <Trash2 size={15} />
                </Button>
                <Link href={annotateHref(a.key)} className="focus-ring inline-flex h-8 items-center rounded-lg border border-line-strong px-3 text-[13px] font-medium text-ink hover:bg-wash">
                  Open
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Modal
        open={!!otherRun}
        onClose={() => setOtherRun(null)}
        title="A review of this call is already here"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                if (otherRun) router.push(reviewHref(otherRun.call_id));
                setOtherRun(null);
              }}
            >
              Open the one in progress
            </Button>
            <Button
              variant="danger"
              onClick={() =>
                void run('replace', async () => {
                  if (otherRun) await open(otherRun, true);
                  setOtherRun(null);
                })
              }
            >
              Replace it
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          The pipeline ran this call again. Replacing the review on this device discards the decisions made in it; the client’s file is not affected.
        </p>
      </Modal>

      <Modal
        open={fetching}
        onClose={() => setFetching(false)}
        title="Fetch a review from the pipeline"
        footer={
          <Button
            variant="primary"
            disabled={!callId.trim() || !!busy}
            onClick={() =>
              void run('fetch', async () => {
                if (!api) return;
                const bundle = await (await exchange()).checkBundle(await createReviewApi(api).getReview(callId.trim()));
                setFetching(false);
                await open(bundle);
              })
            }
          >
            {busy === 'fetch' ? 'Fetching…' : 'Fetch review'}
          </Button>
        }
      >
        <label className="block text-sm font-medium text-ink-2">
          Call id
          <input autoFocus className="control mt-1" value={callId} onChange={(e) => setCallId(e.target.value)} placeholder="e.g. call-2026-10-15-mokoena" />
        </label>
        <p className="mt-2 text-xs text-muted">Sent to {api?.baseUrl} with the practice’s token. The review is kept on this device; your decisions go back as you make them.</p>
      </Modal>

      <Modal open={annotating} onClose={() => setAnnotating(false)} title="Annotate a call" width="max-w-lg">
        <div className="space-y-3 text-sm text-ink-2">
          <p>
            Annotators record what the client said — status, value and its spread, who said it, and the transcript spans — without seeing what the system extracted. The gold file feeds the
            evaluation set.
          </p>
          <label className="block font-medium">
            Your annotator id
            <input autoFocus className="control mt-1" value={annotator} onChange={(e) => setAnnotator(e.target.value)} placeholder="e.g. annotator-07" />
          </label>
          <Segmented
            size="sm"
            value={role}
            onChange={setRole}
            options={[
              { value: 'annotator', label: 'Annotator' },
              { value: 'adjudicator', label: 'Adjudicator' },
            ]}
          />
          <input
            ref={annotateInput}
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) void run('annotate', async () => annotate(await readPackage(await file.text())));
            }}
          />
          <div className={cx('flex flex-wrap gap-2', !annotator.trim() && 'opacity-60')}>
            <Button icon={<FileUp size={14} />} disabled={!annotator.trim() || !!busy} onClick={() => annotateInput.current?.click()}>
              Choose the call’s review package
            </Button>
            <Button variant="ghost" disabled={!annotator.trim() || !!busy} onClick={() => void run('annotate', async () => annotate(await demoBundle()))}>
              Use the demo call
            </Button>
          </div>
          <p className="text-xs text-muted">Only the transcript, the recording and the client’s file before the call are loaded; the package’s extracted facts are left out.</p>
          {error && <p className="text-xs text-critical-ink">{error}</p>}
        </div>
      </Modal>

      <Modal
        open={!!confirmDelete}
        onClose={() => setConfirmDelete(null)}
        title="Delete from this device?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              icon={<Trash2 size={15} />}
              onClick={() =>
                void run('delete', async () => {
                  if (confirmDelete?.kind === 'review') await deleteSession(confirmDelete.key);
                  else if (confirmDelete) await deleteAnnotation(confirmDelete.key);
                  setConfirmDelete(null);
                })
              }
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          <strong>{confirmDelete?.label}</strong> will be removed from this device. The client’s file is not changed.
        </p>
      </Modal>
    </Card>
  );
}
