/**
 * Reviews and gold annotations, kept on this device (IndexedDB) beside the client files they belong
 * to. Nothing here leaves the device unless the adviser exports a file or turns on the review API.
 * Listing and deleting live in listing.ts, which the client list loads without the slot catalog.
 */
import { getSetting, putSetting } from '../store/db';
import type { AnnotationSession } from './annotate';
import { householdName } from './labels';
import { REVIEW_PREFIX, SUMMARY_PREFIX, annotationKey, type SessionSummary } from './listing';
import { buildQueue, progress } from './queue';
import { openFlags, type ReviewSession } from './session';

export const summariseSession = (s: ReviewSession): SessionSummary => {
  const p = progress(s, buildQueue(s));
  return {
    callId: s.callId,
    docId: s.docId,
    household: householdName(s.working),
    callDate: s.bundle.transcript.anchor_date || s.bundle.sidecar.anchor_date,
    createdAt: s.createdAt,
    submittedAt: s.submittedAt,
    openFlags: openFlags(s).length,
    total: p.total,
    decided: p.decided,
  };
};

export const saveSession = async (s: ReviewSession) => {
  await putSetting(`${REVIEW_PREFIX}${s.callId}`, s);
  await putSetting(`${SUMMARY_PREFIX}${s.callId}`, summariseSession(s));
};

export const loadSession = (callId: string) => getSetting<ReviewSession>(`${REVIEW_PREFIX}${callId}`);

export const saveAnnotation = (a: AnnotationSession) => putSetting(annotationKey(a.annotation.call_id, a.annotation.annotator_id), a);
export const loadAnnotation = (key: string) => getSetting<AnnotationSession>(key);
