/**
 * The reviews and annotations on this device, read from small records kept beside them, so the
 * client list and the client sheet can show "call review waiting" without loading a review (or
 * the slot catalog). Also the review API settings, which the client list and the settings page read.
 */
import { deleteSetting, getSetting, listSettings, putSetting } from '../store/db';
import type { AnnotationSession } from './annotate';
import { DEFAULT_API_SETTINGS, type ReviewApiSettings } from './api';

export const reviewHref = (callId: string) => `/review?call=${encodeURIComponent(callId)}`;
export const annotateHref = (key: string) => `/annotate?key=${encodeURIComponent(key)}`;

export const REVIEW_PREFIX = 'review:';
export const SUMMARY_PREFIX = 'review-summary:';
export const ANNOTATION_PREFIX = 'annotation:';
const API = 'review-api';

export interface SessionSummary {
  callId: string;
  docId: string;
  household: string;
  callDate: string;
  createdAt: string;
  submittedAt: string | null;
  openFlags: number;
  /** Reviewable facts, and how many the adviser has decided. */
  total: number;
  decided: number;
}

/** Reviews on this device, newest first. */
export const listReviewSummaries = async (): Promise<SessionSummary[]> =>
  (await listSettings<SessionSummary>(SUMMARY_PREFIX)).map(({ value }) => value).sort((a, b) => b.createdAt.localeCompare(a.createdAt));

/** Remove a review (the client's file is not touched). */
export const deleteSession = async (callId: string) => {
  await deleteSetting(`${REVIEW_PREFIX}${callId}`);
  await deleteSetting(`${SUMMARY_PREFIX}${callId}`);
};

export const annotationKey = (callId: string, annotatorId: string) => `${ANNOTATION_PREFIX}${callId}:${annotatorId}`;

export interface AnnotationSummary {
  key: string;
  callId: string;
  annotator: string;
  role: string;
  facts: number;
  startedAt: string;
  completedAt: string | null;
}

/** Gold annotations on this device, newest first. */
export const listAnnotationSummaries = async (): Promise<AnnotationSummary[]> =>
  (await listSettings<AnnotationSession>(ANNOTATION_PREFIX))
    .map(({ key, value }) => ({
      key,
      callId: value.annotation.call_id,
      annotator: value.annotation.annotator_id,
      role: value.annotation.role,
      facts: value.annotation.facts.length,
      startedAt: value.annotation.started_at,
      completedAt: value.annotation.completed_at,
    }))
    .sort((a, b) => b.startedAt.localeCompare(a.startedAt));

export const deleteAnnotation = (key: string) => deleteSetting(key);

export const loadApiSettings = async (): Promise<ReviewApiSettings> => ({ ...DEFAULT_API_SETTINGS, ...(await getSetting<ReviewApiSettings>(API)) });
export const saveApiSettings = (s: ReviewApiSettings) => putSetting(API, s);
