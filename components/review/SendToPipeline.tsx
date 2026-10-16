'use client';

/**
 * Send a client's file to the call intelligence pipeline (POST /clients/{id}/fna, spec §11.5), so
 * the next call with the client starts from it. Shown only when the practice has turned on the
 * review API; otherwise the file goes to the pipeline as an export, like any other.
 */
import { CloudUpload } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { FnaDocument } from '@/lib/fna/types';
import { apiReady, createReviewApi, type ReviewApiSettings } from '@/lib/review/api';
import { loadApiSettings } from '@/lib/review/listing';

type State = { kind: 'idle' } | { kind: 'sending' } | { kind: 'sent' } | { kind: 'error'; message: string };

export default function SendToPipeline({ doc, flush }: { doc: FnaDocument; flush: () => Promise<void> }) {
  const [api, setApi] = useState<ReviewApiSettings | null>(null);
  const [state, setState] = useState<State>({ kind: 'idle' });

  useEffect(() => {
    loadApiSettings()
      .then(setApi)
      .catch(() => setApi(null));
  }, []);

  if (!apiReady(api)) return null;
  const send = async () => {
    await flush();
    setState({ kind: 'sending' });
    try {
      await createReviewApi(api).sendClientFna(doc.id, doc);
      setState({ kind: 'sent' });
    } catch (e) {
      setState({ kind: 'error', message: e instanceof Error ? e.message : String(e) });
    }
  };
  const label = state.kind === 'sending' ? 'Sending…' : state.kind === 'sent' ? 'Sent to pipeline' : state.kind === 'error' ? 'Not sent: retry' : 'Send to pipeline';
  return (
    <button
      type="button"
      tabIndex={-1}
      onClick={() => void send()}
      disabled={state.kind === 'sending'}
      title={state.kind === 'error' ? state.message : 'Send this client file to the call intelligence pipeline, so the next call starts from it'}
      aria-label={label}
      className={`inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[13px] font-medium hover:bg-wash disabled:opacity-50 ${state.kind === 'error' ? 'text-critical-ink' : 'text-ink-2'}`}
    >
      <CloudUpload size={15} /> <span className="hidden sm:inline">{label}</span>
    </button>
  );
}
