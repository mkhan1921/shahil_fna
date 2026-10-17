'use client';

import { produce, type Draft } from 'immer';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FnaDocument } from '@/lib/fna/types';
import { apiReady, createReviewApi, type ReviewApiSettings } from '@/lib/review/api';
import type { Stamp } from '@/lib/review/ops';
import { addActiveTime, applyAction, commitPending, editDocument, markSent, unsentOps, type ReviewAction, type ReviewSession } from '@/lib/review/session';
import { loadApiSettings } from '@/lib/review/listing';
import { loadSession, saveSession } from '@/lib/review/store';
import type { SaveState } from '@/lib/store/hooks';

/** Seconds of inactivity after which review time stops counting (§15.3 review-time metric). */
const IDLE_SECONDS = 120;
const TICK_SECONDS = 5;

/**
 * Where the review stands with the pipeline's API: off (file exchange), idle, sending, unable to
 * reach it (the adviser retries), or it refused some of the changes (they stay in the review and
 * its result file; the adviser is told which).
 */
export type SyncState = { kind: 'off' } | { kind: 'idle' } | { kind: 'sending' } | { kind: 'error'; message: string } | { kind: 'refused'; count: number };

/**
 * A review held in memory, saved on this device after every change, and — when the practice has
 * turned on the review API — sent to the pipeline op by op (§11.2: written to Postgres immediately).
 */
export function useReviewSession(callId: string | null) {
  const [session, setSession] = useState<ReviewSession | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const [api, setApi] = useState<ReviewApiSettings | null>(null);
  const [sync, setSync] = useState<SyncState>({ kind: 'off' });
  const latest = useRef<ReviewSession | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastInput = useRef(Date.now());
  const inflight = useRef<Promise<boolean> | null>(null);

  const stamp = useCallback((): Stamp => ({ reviewerId: latest.current?.reviewerId ?? '', at: new Date().toISOString() }), []);

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const s = latest.current;
    if (!s) return;
    setSaveState('saving');
    try {
      await saveSession(s);
      setSaveState('saved');
    } catch {
      setSaveState('error');
    }
  }, []);

  /**
   * Keep the new state and save it. A decision is written at once: a save deferred past a tab
   * closing is lost, and beforeunload cannot wait for IndexedDB. Only typing in the sheet (a
   * pending edit, not yet an op) is debounced, so the file is not written on every keystroke.
   */
  const set = useCallback(
    (next: ReviewSession) => {
      latest.current = next;
      setSession(next);
      setSaveState('pending');
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      if (next.pending.length) timer.current = setTimeout(() => void flush(), 500);
      else void flush();
    },
    [flush],
  );

  // Load the review and the API settings.
  useEffect(() => {
    let alive = true;
    if (!callId) {
      setSession(null);
      return;
    }
    Promise.all([loadSession(callId), loadApiSettings()])
      .then(([s, settings]) => {
        if (!alive) return;
        setApi(settings);
        setSync(apiReady(settings) ? (s?.refused.length ? { kind: 'refused', count: s.refused.length } : { kind: 'idle' }) : { kind: 'off' });
        // An edit typed just before the page closed becomes its op now.
        const ready = s && s.pending.length && !s.submittedAt ? commitPending(s, { reviewerId: s.reviewerId, at: new Date().toISOString() }) : (s ?? null);
        latest.current = ready;
        setSession(ready);
      })
      .catch((e) => {
        if (!alive) return;
        setError(`This review could not be read from this device${e instanceof Error && e.message ? `: ${e.message}` : ''}.`);
        setSession(null);
      });
    return () => {
      alive = false;
    };
  }, [callId]);

  // Save on tab close and when leaving the page (active time changes without a pending timer).
  useEffect(() => {
    const handler = () => void flush();
    window.addEventListener('beforeunload', handler);
    window.addEventListener('pagehide', handler);
    return () => {
      window.removeEventListener('beforeunload', handler);
      window.removeEventListener('pagehide', handler);
      handler();
    };
  }, [flush]);

  /** Apply a reviewer action; false (with an error to show) when it cannot apply. */
  const dispatch = useCallback(
    (action: ReviewAction): boolean => {
      const s = latest.current;
      if (!s) return false;
      lastInput.current = Date.now();
      try {
        set(applyAction(commitPending(s, stamp()), action, stamp()));
        setError(null);
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return false;
      }
    },
    [set, stamp],
  );

  /** The sheet's update(): the change shows at once; extracted fields become ops when they lose focus. */
  const editSheet = useCallback(
    (recipe: (draft: Draft<FnaDocument>) => void) => {
      const s = latest.current;
      if (!s || s.submittedAt) return;
      lastInput.current = Date.now();
      try {
        set(editDocument(s, produce(s.working, recipe), stamp()));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [set, stamp],
  );

  /** A sheet field lost focus: turn pending edits into ops. */
  const commit = useCallback(() => {
    const s = latest.current;
    if (!s || !s.pending.length) return;
    set(commitPending(s, stamp()));
  }, [set, stamp]);

  const replace = useCallback((next: ReviewSession) => set(next), [set]);

  // Count active review time: visible, and touched within the last two minutes.
  useEffect(() => {
    const touch = () => (lastInput.current = Date.now());
    window.addEventListener('keydown', touch);
    window.addEventListener('pointerdown', touch);
    let ticks = 0;
    const tick = setInterval(() => {
      const s = latest.current;
      if (!s || s.submittedAt || document.visibilityState !== 'visible') return;
      if (Date.now() - lastInput.current > IDLE_SECONDS * 1000) return;
      latest.current = addActiveTime(s, TICK_SECONDS);
      // Time alone is saved every half minute without re-rendering the sheet.
      if (++ticks % 6 === 0 && !timer.current) void saveSession(latest.current).catch(() => setSaveState('error'));
    }, TICK_SECONDS * 1000);
    return () => {
      clearInterval(tick);
      window.removeEventListener('keydown', touch);
      window.removeEventListener('pointerdown', touch);
    };
  }, []);

  /**
   * Send ops the API has not seen yet (API mode only): true once every op has been seen, whether
   * applied or refused. A send already under way is awaited, never started twice. The pipeline
   * applies a batch sent again after a lost reply only once, so a retry is always safe.
   */
  const sendOps = useCallback((): Promise<boolean> => {
    if (inflight.current) return inflight.current;
    const s = latest.current;
    if (!s || !apiReady(api)) return Promise.resolve(false);
    const ops = unsentOps(s);
    if (!ops.length) return Promise.resolve(true);
    const settings = api;
    const run = (async () => {
      setSync({ kind: 'sending' });
      try {
        const reply = await createReviewApi(settings).postOps(s.callId, ops);
        const refused = (reply?.rejected ?? []).filter((r) => ops.some((o) => o.seq === r.seq));
        const now = latest.current ?? s;
        const next = markSent(now, ops[ops.length - 1].seq, refused);
        set(next);
        setSync(next.refused.length ? { kind: 'refused', count: next.refused.length } : { kind: 'idle' });
        return true;
      } catch (e) {
        setSync({ kind: 'error', message: e instanceof Error ? e.message : String(e) });
        return false;
      } finally {
        inflight.current = null;
      }
    })();
    inflight.current = run;
    return run;
  }, [api, set]);

  useEffect(() => {
    if (session && apiReady(api) && unsentOps(session).length && sync.kind !== 'error') void sendOps();
  }, [session, api, sendOps, sync.kind]);

  /** The review as it stands now, including time counted since the last render. */
  const get = useCallback(() => latest.current, []);

  return { session, get, dispatch, editSheet, commit, replace, error, clearError: () => setError(null), saveState, flush, api, sync, sendOps };
}

export type ReviewHandle = ReturnType<typeof useReviewSession>;
