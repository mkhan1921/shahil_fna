/**
 * The list of call reviews on this device, read from small summaries saved beside each review, so
 * the client list and the client sheet can show "call review waiting" without loading a review.
 */
import { listSettings } from '../store/db';

export const SUMMARY_PREFIX = 'review-summary:';

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
