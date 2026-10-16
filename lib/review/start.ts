/**
 * Starting a review on this device (spec §11.5): from a review package file, a plain shahil-fna
 * file with its sidecar, the demo package or the review API. The review updates the client's file
 * when it is submitted; until then nothing the call proposed touches that file.
 */
import { normaliseDocument } from '../fna/defaults';
import type { FnaDocument, PracticeProfile } from '../fna/types';
import { getDocument, saveDocument } from '../store/db';
import type { ReviewApiSettings } from './api';
import { createSession } from './session';
import { loadSession, saveSession } from './store';
import type { ReviewBundle } from './types';

/** Where the demo package is served from (public/demo, written by pipeline/scripts/make_review_fixture.py). */
export const DEMO_PACKAGE_URL = '/demo/review_package.json';

/** Who is reviewing, stamped on every op: the API reviewer id, else the practice's adviser. */
export const reviewerIdOf = (api: ReviewApiSettings | null | undefined, practice: PracticeProfile | null | undefined): string => {
  if (api?.reviewerId.trim()) return api.reviewerId.trim();
  const name = practice?.adviserName.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return name ? `adviser-${name}` : 'adviser';
};

export interface StartPlan {
  /** The client file the review updates when it is submitted. */
  docId: string;
  /** The prior file to store first, when the client is not on this device yet. */
  saveClient: FnaDocument | null;
  /** The client's file as it is on this device, so changes made since the export are carried in. */
  local: FnaDocument | null;
}

/** Which client file a review belongs to: the prior file's (stored now if it is not here), or a new client's. */
export const planStart = (bundle: ReviewBundle, local: FnaDocument | null): StartPlan => {
  const prior = bundle.prior_document;
  if (!prior) return { docId: bundle.median_document.id, saveClient: null, local: null };
  return { docId: prior.id, saveClient: local ? null : normaliseDocument(structuredClone(prior)), local };
};

export type StartOutcome = { callId: string; status: 'created' | 'existing' | 'other-run' };

/**
 * Store a review of this bundle. A review of the same call already on this device is kept unless
 * `replace` is set: the same run reopens it; another run of the pipeline is reported so the adviser
 * can choose.
 */
export const startReview = async (bundle: ReviewBundle, o: { reviewerId: string; replace?: boolean; now?: string }): Promise<StartOutcome> => {
  const existing = await loadSession(bundle.call_id);
  if (existing && !o.replace) return { callId: bundle.call_id, status: existing.runId === bundle.run_id ? 'existing' : 'other-run' };
  const local = bundle.prior_document ? await getDocument(bundle.prior_document.id) : null;
  const plan = planStart(bundle, local);
  if (plan.saveClient) await saveDocument(plan.saveClient);
  const session = createSession(bundle, { docId: plan.docId, reviewerId: o.reviewerId, now: o.now ?? new Date().toISOString(), local: plan.local });
  await saveSession(session);
  return { callId: bundle.call_id, status: 'created' };
};
