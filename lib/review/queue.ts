/**
 * The review queue (spec §10.3, §11.1, §11.3): blocking flags first, then the facts sorted by
 * review_priority into "Needs you", "Check quickly" and a collapsed group of auto-accepted facts
 * that barely move the plan. In a spot-check review every auto-accepted fact is shown under
 * "Needs you" so the conformal guarantee is checked against real decisions.
 *
 * The keyboard flow matches the sheet: Enter accepts and moves to the next undecided item, Tab moves
 * on without deciding, Shift goes back.
 */
import type { BlockingFlag, FlagKind } from './types';
import { isDecided, isReviewItem, openFlags, type ReviewFact, type ReviewSession } from './session';

/** Facts below half a point of success probability, and auto-accepted, are collapsed (§10.3). */
export const COLLAPSE_PRIORITY = 0.005;
/** From two points of success probability a fact needs the adviser, whatever its confidence. */
export const NEEDS_PRIORITY = 0.02;

export type PBand = 'high' | 'medium' | 'low';
/** p_correct badge bands (§11.1): high ≥ 0.85, low < 0.6. */
export const pBand = (p: number): PBand => (p >= 0.85 ? 'high' : p < 0.6 ? 'low' : 'medium');

export type QueueGroup = 'needs' | 'quick' | 'collapsed';

export const GROUP_LABEL: Record<QueueGroup, string> = { needs: 'Needs you', quick: 'Check quickly', collapsed: 'Auto-accepted' };

export const FLAG_LABEL: Record<FlagKind, string> = {
  tier_a_missing: 'Missing',
  tier_a_contradicted: 'Contradicted',
  status_flip: 'Flips a need',
  scale_ambiguous: 'Scale unclear',
  second_pass_disagrees: 'Re-listen disagrees',
  hard_constraint: 'Impossible value',
  unresolved_term: 'Unknown term',
  role_review: 'Speaker roles',
};

/** Which group a fact falls in. */
export const groupOf = (f: ReviewFact, spotCheck: boolean): QueueGroup => {
  if (f.autoAccepted) {
    if (spotCheck) return 'needs';
    return f.priority < COLLAPSE_PRIORITY ? 'collapsed' : 'quick';
  }
  if (f.source === 'fna_default') return f.tier === 'A' ? 'needs' : 'quick';
  if (f.status === 'contradicted' || f.priority >= 1) return 'needs';
  const band = pBand(f.pCorrect);
  if (band === 'low' || f.priority >= NEEDS_PRIORITY) return 'needs';
  if (f.tier === 'A' && (band !== 'high' || f.attribution === 'advisor_suggested')) return 'needs';
  return 'quick';
};

/** Highest priority first; ties: tier A first, then the fact id, so the order is stable. */
const byPriority = (a: ReviewFact, b: ReviewFact) => b.priority - a.priority || a.tier.localeCompare(b.tier) || a.factId.localeCompare(b.factId);

export interface QueueFlag {
  flag: BlockingFlag;
  fact: ReviewFact | null;
}

export interface Queue {
  flags: QueueFlag[];
  needs: ReviewFact[];
  quick: ReviewFact[];
  collapsed: ReviewFact[];
}

export const buildQueue = (s: ReviewSession): Queue => {
  const flags = openFlags(s).map((flag) => ({ flag, fact: flag.fact_id ? (s.facts[flag.fact_id] ?? null) : null }));
  const flagged = new Set(flags.map((f) => f.flag.fact_id).filter(Boolean));
  const items = Object.values(s.facts).filter((f) => isReviewItem(f) && !flagged.has(f.factId));
  const q: Queue = { flags, needs: [], quick: [], collapsed: [] };
  for (const f of items) q[groupOf(f, s.spotCheck)].push(f);
  q.needs.sort(byPriority);
  q.quick.sort(byPriority);
  q.collapsed.sort(byPriority);
  return q;
};

/** Queue entries in keyboard order: flags (by their fact), then the groups; the collapsed group only when open. */
export const queueOrder = (q: Queue, showCollapsed: boolean): string[] => [
  ...q.flags.map((f) => `flag:${f.flag.flag_id}`),
  ...q.needs.map((f) => f.factId),
  ...q.quick.map((f) => f.factId),
  ...(showCollapsed ? q.collapsed.map((f) => f.factId) : []),
];

/** The fact behind a queue entry. */
export const entryFact = (s: ReviewSession, entry: string): ReviewFact | undefined => {
  if (!entry.startsWith('flag:')) return s.facts[entry];
  const flag = s.flags.find((f) => `flag:${f.flag_id}` === entry);
  return flag?.fact_id ? s.facts[flag.fact_id] : undefined;
};

const decidedEntry = (s: ReviewSession, entry: string) => {
  if (entry.startsWith('flag:')) return !!s.flags.find((f) => `flag:${f.flag_id}` === entry)?.cleared;
  const f = s.facts[entry];
  return !f || isDecided(f);
};

/** The next entry after `current` (or the first), optionally skipping decided ones; null at the end. */
export const nextEntry = (s: ReviewSession, order: string[], current: string | null, o: { undecided?: boolean; back?: boolean } = {}): string | null => {
  const seq = o.back ? [...order].reverse() : order;
  const start = current === null ? -1 : seq.indexOf(current);
  for (let i = start + 1; i < seq.length; i++) if (!o.undecided || !decidedEntry(s, seq[i])) return seq[i];
  return null;
};

export type QueueKey = 'accept' | 'next' | 'prev' | 'edit' | 'reject' | 'unknown' | 'play' | 'close';

/** The queue's keys: Enter accepts and moves on, Tab moves on, Shift goes back, E edits, R rejects, U marks unknown, P plays. */
export const queueKey = (e: { key: string; shiftKey?: boolean; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean }): QueueKey | null => {
  if (e.altKey || e.ctrlKey || e.metaKey) return null;
  switch (e.key) {
    case 'Enter':
      return e.shiftKey ? 'prev' : 'accept';
    case 'Tab':
      return e.shiftKey ? 'prev' : 'next';
    case 'ArrowDown':
    case 'j':
      return 'next';
    case 'ArrowUp':
    case 'k':
      return 'prev';
    case 'e':
    case 'E':
      return 'edit';
    case 'r':
    case 'R':
      return 'reject';
    case 'u':
    case 'U':
      return 'unknown';
    case 'p':
    case 'P':
      return 'play';
    case 'Escape':
      return 'close';
    default:
      return null;
  }
};

export interface Progress {
  total: number;
  decided: number;
  needs: number;
  needsDecided: number;
}

/** How far the review has come: every reviewable fact the adviser has decided, and "Needs you" in particular. */
export const progress = (s: ReviewSession, q: Queue = buildQueue(s)): Progress => {
  const all = [...q.flags.map((f) => f.fact).filter((f): f is ReviewFact => !!f), ...q.needs, ...q.quick];
  const needs = [...q.flags.map((f) => f.fact).filter((f): f is ReviewFact => !!f), ...q.needs];
  return { total: all.length, decided: all.filter(isDecided).length, needs: needs.length, needsDecided: needs.filter(isDecided).length };
};
