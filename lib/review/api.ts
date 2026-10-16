/**
 * The opt-in client for the pipeline's review API (spec §11.5, §17 default: by M5).
 *
 * Nothing is sent anywhere unless the adviser turns it on in Practice settings with a base URL and
 * a bearer token, and the deployment allows that origin (NEXT_PUBLIC_ADVISOR_INTEL_ORIGINS, which
 * next.config.ts also adds to the Content-Security-Policy). Until then the same review works by
 * file exchange.
 *
 *   GET  /calls/{call_id}/review                 → ReviewBundle
 *   POST /calls/{call_id}/ops                    ← StateOp[]
 *   POST /calls/{call_id}/submit                 ← ReviewResult (submitted)
 *   GET  /calls/{call_id}/fna                    → the reviewed shahil-fna file + sidecar
 *   POST /calls/{call_id}/followups/{id}/answer  ← {text, source}
 *   POST /clients/{client_id}/fna                ← a plain shahil-fna file, to seed the next call
 */
import type { FnaDocument } from '../fna/types';
import type { ReviewBundle, ReviewResult, Sidecar, StateOp } from './types';

export interface ReviewApiSettings {
  enabled: boolean;
  /** e.g. https://intel.practice.co.za/api */
  baseUrl: string;
  token: string;
  /** Stamped on every reviewer op. */
  reviewerId: string;
}

export const DEFAULT_API_SETTINGS: ReviewApiSettings = { enabled: false, baseUrl: '', token: '', reviewerId: '' };

/** Origins this deployment lets the app talk to (and plays recordings from), set when it is built. */
export const allowedOrigins = (raw: string | undefined = process.env.NEXT_PUBLIC_ADVISOR_INTEL_ORIGINS): string[] =>
  (raw ?? '')
    .split(/[\s,]+/)
    .map((s) => s.trim().replace(/\/+$/, ''))
    .filter(Boolean);

export const originOf = (url: string): string | null => {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
};

/** Why these settings cannot be used (empty when the client may call the API). */
export const settingsProblems = (s: ReviewApiSettings, origins: string[] = allowedOrigins()): string[] => {
  const out: string[] = [];
  if (!s.enabled) return ['The review API is off.'];
  const origin = originOf(s.baseUrl);
  if (!s.baseUrl.trim()) out.push('Enter the base URL of the review API.');
  else if (!origin) out.push('The base URL is not a valid address.');
  else {
    const u = new URL(s.baseUrl);
    const local = u.hostname === 'localhost' || u.hostname === '127.0.0.1';
    if (u.protocol !== 'https:' && !(local && u.protocol === 'http:')) out.push('Use https, so client information is encrypted on the way.');
    if (!origins.includes(origin)) out.push(origins.length ? `This deployment only allows ${origins.join(', ')}.` : 'This deployment does not allow a review API: whoever deploys the app sets NEXT_PUBLIC_ADVISOR_INTEL_ORIGINS.');
  }
  if (!s.token.trim()) out.push('Enter the bearer token the practice was issued.');
  if (!s.reviewerId.trim()) out.push('Enter your reviewer id.');
  return out;
};

export const apiReady = (s: ReviewApiSettings | null | undefined, origins?: string[]): s is ReviewApiSettings => !!s && settingsProblems(s, origins).length === 0;

export class ReviewApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
  ) {
    super(message);
  }
}

/** What POST /ops reports back: the ops it applied and any it refused. */
export interface OpsReply {
  applied?: number[];
  rejected?: { seq: number; reason: string }[];
  [key: string]: unknown;
}

/** GET /fna returns the reviewed file and its sidecar. */
export interface ReviewedFna {
  file: { format: 'shahil-fna'; version: 1; document: FnaDocument; [key: string]: unknown };
  sidecar: Sidecar | null;
}

const TIMEOUT_MS = 30_000;

export const createReviewApi = (settings: ReviewApiSettings, fetchImpl: typeof fetch = (...a) => fetch(...a)) => {
  const base = settings.baseUrl.replace(/\/+$/, '');
  const url = (...parts: string[]) => `${base}/${parts.map(encodeURIComponent).join('/')}`;

  const call = async <T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> => {
    const problems = settingsProblems(settings);
    if (problems.length) throw new ReviewApiError(problems[0], null);
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), TIMEOUT_MS);
    let res: Response;
    try {
      res = await fetchImpl(path, {
        method,
        headers: { Authorization: `Bearer ${settings.token}`, Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: 'omit',
        cache: 'no-store',
        referrerPolicy: 'no-referrer',
        signal: abort.signal,
      });
    } catch (e) {
      throw new ReviewApiError(abort.signal.aborted ? 'The review API did not answer in time.' : `Could not reach the review API${e instanceof Error && e.message ? `: ${e.message}` : ''}.`, null);
    } finally {
      clearTimeout(timer);
    }
    const text = await res.text();
    let json: unknown = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      json = null;
    }
    if (!res.ok) {
      const detail = json && typeof json === 'object' && 'detail' in json ? String((json as { detail: unknown }).detail) : text.slice(0, 200);
      throw new ReviewApiError(`The review API refused the request (${res.status})${detail ? `: ${detail}` : ''}.`, res.status);
    }
    return json as T;
  };

  return {
    getReview: (callId: string) => call<ReviewBundle>('GET', url('calls', callId, 'review')),
    postOps: (callId: string, ops: StateOp[]) => call<OpsReply | null>('POST', url('calls', callId, 'ops'), ops),
    submit: (callId: string, result: ReviewResult) => call<unknown>('POST', url('calls', callId, 'submit'), result),
    getFna: async (callId: string): Promise<ReviewedFna> => {
      const reply = await call<Record<string, unknown>>('GET', url('calls', callId, 'fna'));
      if (reply?.format === 'shahil-fna') return { file: reply as ReviewedFna['file'], sidecar: null };
      if (reply && typeof reply.file === 'object') return { file: reply.file as ReviewedFna['file'], sidecar: (reply.sidecar as Sidecar) ?? null };
      throw new ReviewApiError('The review API sent something other than a client file.', null);
    },
    answerFollowup: (callId: string, followupId: string, answer: { text: string; source: 'followup' | 'advisor_note' | 'client_email' }) =>
      call<unknown>('POST', url('calls', callId, 'followups', followupId, 'answer'), answer),
    sendClientFna: (clientId: string, doc: FnaDocument) =>
      call<unknown>('POST', url('clients', clientId, 'fna'), { format: 'shahil-fna', version: 1, encrypted: false, exportedAt: new Date().toISOString(), document: doc }),
  };
};

export type ReviewApi = ReturnType<typeof createReviewApi>;
