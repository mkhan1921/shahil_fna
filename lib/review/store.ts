/**
 * Reviews, gold annotations and the review API settings, kept on this device (IndexedDB) beside the
 * client files they belong to. Nothing here leaves the device unless the adviser exports a file or
 * turns on the review API.
 */
import { deleteSetting, getSetting, listSettings, putSetting } from '../store/db';
import type { AnnotationSession } from './annotate';
import { DEFAULT_API_SETTINGS, type ReviewApiSettings } from './api';
import { SUMMARY_PREFIX, type SessionSummary } from './listing';
import { buildQueue, progress } from './queue';
import { openFlags, type ReviewSession } from './session';

const REVIEW = 'review:';
const ANNOTATION = 'annotation:';
const API = 'review-api';

const household = (s: ReviewSession) => {
  const d = s.working;
  const names = [d.client.firstName, d.household.includeSpouse ? d.spouse.firstName : ''].filter(Boolean);
  return names.length ? `${names.join(' & ')} ${d.client.surname}`.trim() : 'Unnamed client';
};

export const summariseSession = (s: ReviewSession): SessionSummary => {
  const p = progress(s, buildQueue(s));
  return {
    callId: s.callId,
    docId: s.docId,
    household: household(s),
    callDate: s.bundle.transcript.anchor_date || s.bundle.sidecar.anchor_date,
    createdAt: s.createdAt,
    submittedAt: s.submittedAt,
    openFlags: openFlags(s).length,
    total: p.total,
    decided: p.decided,
  };
};

export const saveSession = async (s: ReviewSession) => {
  await putSetting(`${REVIEW}${s.callId}`, s);
  await putSetting(`${SUMMARY_PREFIX}${s.callId}`, summariseSession(s));
};

export const loadSession = (callId: string) => getSetting<ReviewSession>(`${REVIEW}${callId}`);

export const deleteSession = async (callId: string) => {
  await deleteSetting(`${REVIEW}${callId}`);
  await deleteSetting(`${SUMMARY_PREFIX}${callId}`);
};

export const annotationKey = (callId: string, annotatorId: string) => `${ANNOTATION}${callId}:${annotatorId}`;
export const saveAnnotation = (a: AnnotationSession) => putSetting(annotationKey(a.annotation.call_id, a.annotation.annotator_id), a);
export const loadAnnotation = (key: string) => getSetting<AnnotationSession>(key);
export const deleteAnnotation = (key: string) => deleteSetting(key);
export const listAnnotations = async () => listSettings<AnnotationSession>(ANNOTATION);

export const loadApiSettings = async (): Promise<ReviewApiSettings> => ({ ...DEFAULT_API_SETTINGS, ...(await getSetting<ReviewApiSettings>(API)) });
export const saveApiSettings = (s: ReviewApiSettings) => putSetting(API, s);
