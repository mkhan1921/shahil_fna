/**
 * The review queue (spec §10.3, §11.1, §11.3): blocking flags first, then the facts sorted by
 * review_priority into "Needs you", "Check quickly" and a collapsed group of auto-accepted facts
 * that barely move the plan. In a spot-check review every auto-accepted fact is shown under
 * "Needs you" so the conformal guarantee is checked against real decisions.
 *
 * The keyboard flow matches the sheet: Enter accepts and moves to the next undecided item, Tab moves
 * on without deciding, Shift goes back.
 */
import { formatIn } from './dist';
import { derivationText } from './labels';
import { isDecided, isReviewItem, openFlags, type ReviewFact, type ReviewSession } from './session';
import type { BlockingFlag, FlagKind } from './types';

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

/** The queue entry a fact is reviewed under: its flag's entry while a flag is open on it, else its own. */
export const entryOf = (s: ReviewSession, order: string[], factId: string): string | null =>
  order.find((e) => e.startsWith('flag:') && entryFact(s, e)?.factId === factId) ?? (order.includes(factId) ? factId : null);

/**
 * Where the queue goes after a decision on `factId` made at `from`: the next undecided entry,
 * wrapping round to the first; never the fact just decided (it is still undecided in `s`, the
 * review as it was before the decision). Null when nothing is left to decide.
 */
export const nextUndecided = (s: ReviewSession, order: string[], from: string | null, factId: string | null): string | null => {
  const skip = (e: string | null) => e !== null && (e === from || (factId !== null && entryFact(s, e)?.factId === factId));
  let e = nextEntry(s, order, from, { undecided: true });
  while (skip(e)) e = nextEntry(s, order, e, { undecided: true });
  if (e) return e;
  e = nextEntry(s, order, null, { undecided: true });
  while (skip(e)) e = nextEntry(s, order, e, { undecided: true });
  return e;
};

export type QueueKey = 'accept' | 'next' | 'prev' | 'edit' | 'reject' | 'unknown' | 'play' | 'close' | 'dismiss' | 'acceptQuick';

/**
 * The queue's keys: Enter accepts and moves on, Tab moves on, Shift goes back, E edits, R rejects,
 * U marks unknown, P plays, D dismisses the flag in view, A accepts every "Check quickly" item.
 */
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
    case 'd':
    case 'D':
      return 'dismiss';
    case 'a':
    case 'A':
      return 'acceptQuick';
    case 'Escape':
      return 'close';
    default:
      return null;
  }
};

/** What a queue key does in a review, as the workspace carries it out. */
export type FlowStep =
  | { do: 'select'; entry: string }
  | { do: 'accept'; factId: string; next: string | null }
  | { do: 'clearFlag'; flagId: string; next: string | null }
  | { do: 'dismissFlag'; flagId: string }
  | { do: 'acceptGroup'; group: 'quick'; factIds: string[] }
  | { do: 'confirmTerm'; factId: string }
  | { do: 'panel'; panel: 'edit' | 'reject'; factId: string }
  | { do: 'unknown'; factId: string; next: string | null }
  | { do: 'play'; factId: string }
  | { do: 'close' };

const termOpen = (f: ReviewFact) => !!f.termResolution && f.termResolution.status !== 'confirmed' && f.termResolution.status !== 'corrected';

/**
 * The keyboard flow of §11.1 on the queue entry `current`: Enter accepts and moves to the next
 * undecided entry (an unknown term opens its confirmation instead; a flag on the whole call is
 * marked checked); Tab and the arrows move without deciding; E and R open the editor or the reject
 * reasons; U marks the fact unknown; P plays its first quote; D opens the dismissal of the flag in
 * view; A accepts everything left under "Check quickly"; Escape closes the popover. Null when the
 * key should keep its usual meaning — Tab past either end leaves the queue.
 */
export const flowStep = (s: ReviewSession, order: string[], current: string | null, key: QueueKey, popoverOpen = false): FlowStep | null => {
  const f = current ? entryFact(s, current) : undefined;
  const open = !s.submittedAt;
  switch (key) {
    case 'next':
    case 'prev': {
      const n = nextEntry(s, order, current, { back: key === 'prev' });
      return n === null ? null : { do: 'select', entry: n };
    }
    case 'accept': {
      if (!open) return null;
      if (current === null) {
        const first = nextEntry(s, order, null, { undecided: true });
        return first ? { do: 'select', entry: first } : null;
      }
      const flag = current.startsWith('flag:') ? s.flags.find((x) => `flag:${x.flag_id}` === current) : undefined;
      if (flag && !f) return { do: 'clearFlag', flagId: flag.flag_id, next: nextUndecided(s, order, current, null) };
      if (!f) return null;
      if (flag?.kind === 'unresolved_term' && termOpen(f)) return { do: 'confirmTerm', factId: f.factId };
      return { do: 'accept', factId: f.factId, next: nextUndecided(s, order, current, f.factId) };
    }
    case 'edit':
    case 'reject':
      return f && open ? { do: 'panel', panel: key, factId: f.factId } : null;
    case 'unknown':
      return f && open ? { do: 'unknown', factId: f.factId, next: nextUndecided(s, order, current, f.factId) } : null;
    case 'dismiss': {
      const flag = current?.startsWith('flag:') ? s.flags.find((x) => `flag:${x.flag_id}` === current) : undefined;
      return flag && !flag.cleared && open ? { do: 'dismissFlag', flagId: flag.flag_id } : null;
    }
    case 'acceptQuick': {
      if (!open) return null;
      const ids = buildQueue(s)
        .quick.filter((x) => !isDecided(x))
        .map((x) => x.factId);
      return ids.length ? { do: 'acceptGroup', group: 'quick', factIds: ids } : null;
    }
    case 'play':
      return f?.evidence.length ? { do: 'play', factId: f.factId } : null;
    case 'close':
      return popoverOpen ? { do: 'close' } : null;
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

/* ------------------------------------------------------------------ */
/* Badges on the sheet (§11.1)                                         */
/* ------------------------------------------------------------------ */

export type BadgeKind = 'new' | 'changed' | 'confirmed' | 'assumed' | 'unknown' | 'removed';

export interface FieldBadge {
  kind: BadgeKind;
  /** Short badge text. */
  label: string;
  /** What the badge means for this field, for its tooltip and screen readers. */
  title: string;
  /** The value before the call, struck through beside a change. */
  prior: string | null;
  decision: 'accepted' | 'edited' | 'rejected' | 'auto' | null;
  flagged: boolean;
}

/** The value before the call in the field's own unit ("R95,000", "65"). */
export const priorShown = (f: ReviewFact): string | null => (f.prior && typeof f.prior.point === 'number' ? formatIn(f.prior, f.prior.point) : f.priorText);

const kindOf = (f: ReviewFact): [BadgeKind, string] => {
  if (f.origin === 'reviewer') return ['new', 'Added'];
  if (f.change === 'removed') return ['removed', 'Removed'];
  if (f.status === 'contradicted') return ['unknown', 'Contradicted'];
  if (f.status === 'explicitly_unknown') return ['unknown', 'Unknown'];
  if (f.source === 'fna_default') return ['assumed', 'Assumed'];
  if (f.source === 'crm' && f.status === 'not_discussed') return ['assumed', 'From CRM'];
  if (f.change === 'changed') return ['changed', 'Changed'];
  if (f.change === 'confirmed') return ['confirmed', 'Confirmed'];
  if (f.change === 'new') return ['new', 'New'];
  return ['confirmed', 'Kept'];
};

/** How the sheet badges a fact: new, changed (with the prior value struck), confirmed or assumed, and the reviewer's decision. */
export const badgeOf = (s: ReviewSession, f: ReviewFact): FieldBadge | null => {
  if (!isReviewItem(f)) return null;
  const [kind, label] = kindOf(f);
  const value = f.quantity ? f.valueText : f.status === 'explicitly_unknown' ? 'not known' : 'not discussed';
  const prior = priorShown(f);
  const what: Record<BadgeKind, string> = {
    new: f.origin === 'reviewer' ? `Added by you: ${value}` : `New from the call: ${value}`,
    changed: `Changed in the call: ${prior ?? 'blank'} → ${value}`,
    confirmed: label === 'Kept' ? `The call kept ${value}` : `Confirmed in the call: ${value}`,
    assumed: f.source === 'crm' ? `From the CRM, not discussed in the call: ${value}` : `Not discussed: the FNA default ${value} is assumed`,
    unknown: f.status === 'contradicted' ? `The call contradicts itself: ${value}` : 'The client said they do not know',
    removed: `Removed in the call (was ${prior ?? 'blank'})`,
  };
  const confidence = f.origin === 'pipeline' && f.source !== 'fna_default' ? ` · ${pBand(f.pCorrect)} confidence` : '';
  const derived = f.derivation ? ` · ${derivationText(f.derivation).replace(/^\w/, (c) => c.toLowerCase())}` : '';
  const decision = f.review.status === 'accepted' || f.review.status === 'edited' || f.review.status === 'rejected' ? f.review.status : f.autoAccepted ? 'auto' : null;
  return {
    kind,
    label,
    title: `${what[kind]}${derived}${confidence}`,
    prior: kind === 'changed' || kind === 'removed' ? prior : null,
    decision,
    flagged: s.flags.some((x) => !x.cleared && x.fact_id === f.factId),
  };
};

/** The badge on the sheet field at a concrete path (null where no reviewable fact stands). */
export const badgeAt = (s: ReviewSession, path: string): FieldBadge | null => {
  const id = s.pathIndex[path];
  const f = id ? s.facts[id] : undefined;
  return f ? badgeOf(s, f) : null;
};
