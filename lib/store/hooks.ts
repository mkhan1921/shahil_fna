'use client';

import { produce, type Draft } from 'immer';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { analyse, type Analysis } from '../fna/analysis';
import type { FnaDocument, PracticeProfile } from '../fna/types';
import { getDocument, getPractice, listDocuments, saveDocument, savePractice, subscribe } from './db';

export type SaveState = 'idle' | 'pending' | 'saving' | 'saved' | 'error';

export const useDocument = (id: string | null) => {
  const [doc, setDoc] = useState<FnaDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef<FnaDocument | null>(null);

  useEffect(() => {
    let alive = true;
    if (!id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    getDocument(id)
      .then((d) => {
        if (!alive) return;
        latest.current = d;
        setDoc(d);
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [id]);

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const d = latest.current;
    if (!d) return;
    setSaveState('saving');
    try {
      await saveDocument(d);
      setSaveState('saved');
    } catch {
      setSaveState('error');
    }
  }, []);

  const update = useCallback(
    (recipe: (draft: Draft<FnaDocument>) => void) => {
      setDoc((prev) => {
        if (!prev) return prev;
        const next = produce(prev, (draft) => {
          recipe(draft);
          draft.updatedAt = new Date().toISOString();
          if (draft.status === 'draft') draft.status = 'in-progress';
        });
        latest.current = next;
        return next;
      });
      setSaveState('pending');
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void flush(), 600);
    },
    [flush],
  );

  // Save on tab close / navigation.
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

  const analysis: Analysis | null = useMemo(() => (doc ? analyse(doc) : null), [doc]);

  return { doc, analysis, loading, update, saveState, flush };
};

export const useDocumentList = () => {
  const [docs, setDocs] = useState<FnaDocument[] | null>(null);
  const refresh = useCallback(() => {
    listDocuments()
      .then(setDocs)
      .catch(() => setDocs([]));
  }, []);
  useEffect(() => {
    refresh();
    return subscribe(refresh);
  }, [refresh]);
  return { docs, refresh };
};

export const usePractice = () => {
  const [practice, setPractice] = useState<PracticeProfile | null>(null);
  useEffect(() => {
    const load = () => getPractice().then(setPractice);
    load();
    return subscribe(load);
  }, []);
  const update = useCallback(async (next: PracticeProfile) => {
    setPractice(next);
    await savePractice(next);
  }, []);
  return { practice, update };
};
