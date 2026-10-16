'use client';

import { produce, type Draft } from 'immer';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FnaDocument } from '@/lib/fna/types';
import { apiReady, createReviewApi, type ReviewApiSettings } from '@/lib/review/api';
import type { Stamp } from '@/lib/review/ops';
import { addActiveTime, applyAction, commitPending, editDocument, markSent, unsentOps, type ReviewAction, type ReviewSession } from '@/lib/review/session';
import { loadApiSettings, loadSession, saveSession } from '@/lib/review/store';
import type { SaveState } from '@/lib/store/hooks';

/** Seconds of inactivity after which review time stops counting (§15.3 review-time metric). */
const IDLE_SECONDS = 120;
const TICK_SECONDS = 5;

export type SyncState = { kind: 'off' } | { kind: 'idle' } | { kind: 'sending' } | { kind: 'error'; message: string };

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
  const sending = useRef(false);

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

  const set = useCallback(
    (next: ReviewSession) => {
      latest.current = next;
      setSession(next);
      setSaveState('pending');
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void flush(), 500);
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
    Promise.all([loadSession(callId), loadApiSettings()]).then(([s, settings]) => {
      if (!alive) return;
      setApi(settings);
      setSync(apiReady(settings) ? { kind: 'idle' } : { kind: 'off' });
      // An edit typed just before the page closed becomes its op now.
      const ready = s && s.pending.length && !s.submittedAt ? commitPending(s, { reviewerId: s.reviewerId, at: new Date().toISOString() }) : (s ?? null);
      latest.current = ready;
      setSession(ready);
    });
    return () => {
      alive = false;
    };
  }, [callId]);

  // Save on tab close and when leaving the page.
  useEffect(() => {
    const handler = () => {
      if (timer.current) void flush();
    };
    window.addEventListener('beforeunload', handler);
    return () => {
      window.removeEventListener('beforeunload', handler);
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

  /** Send ops the API has not accepted yet (API mode only): true once none are left unsent. */
  const sendOps = useCallback(async (): Promise<boolean> => {
    const s = latest.current;
    if (!s || !apiReady(api)) return false;
    if (sending.current) return false;
    const ops = unsentOps(s);
    if (!ops.length) return true;
    sending.current = true;
    setSync({ kind: 'sending' });
    try {
      const reply = await createReviewApi(api).postOps(s.callId, ops);
      const refused = reply?.rejected ?? [];
      if (refused.length) throw new Error(`the pipeline refused ${refused.length} change${refused.length === 1 ? '' : 's'}: ${refused[0].reason}`);
      const now = latest.current;
      if (now) set(markSent(now, ops[ops.length - 1].seq));
      setSync({ kind: 'idle' });
      return true;
    } catch (e) {
      setSync({ kind: 'error', message: e instanceof Error ? e.message : String(e) });
      return false;
    } finally {
      sending.current = false;
    }
  }, [api, set]);

  useEffect(() => {
    if (session && apiReady(api) && unsentOps(session).length && sync.kind !== 'error') void sendOps();
  }, [session, api, sendOps, sync.kind]);

  /** The review as it stands now, including time counted since the last render. */
  const get = useCallback(() => latest.current, []);

  return { session, get, dispatch, editSheet, commit, replace, error, clearError: () => setError(null), saveState, flush, api, sync, sendOps };
}

export type ReviewHandle = ReturnType<typeof useReviewSession>;
