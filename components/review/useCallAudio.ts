'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { seekTime, stopTime } from '@/lib/review/transcript';
import type { Evidence } from '@/lib/review/types';

/**
 * The call recording: the pipeline's audio URL, or a file the adviser opens from disk (played from
 * a blob: URL, so it never leaves the device). Playing a quote starts 2 s before it and stops just
 * after it (§11.1).
 */
export function useCallAudio(url: string | null) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const stopAt = useRef<number | null>(null);
  const [localUrl, setLocalUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const src = localUrl ?? url;

  useEffect(() => {
    if (!src) return;
    const a = new Audio();
    a.preload = 'metadata';
    a.src = src;
    const onTime = () => {
      if (stopAt.current !== null && a.currentTime >= stopAt.current) {
        a.pause();
        stopAt.current = null;
      }
    };
    const onPause = () => setPlaying(null);
    const onError = () => setError('The recording could not be played. Open the recording from disk instead.');
    a.addEventListener('timeupdate', onTime);
    a.addEventListener('pause', onPause);
    a.addEventListener('ended', onPause);
    a.addEventListener('error', onError);
    audio.current = a;
    setError(null);
    return () => {
      a.pause();
      a.removeEventListener('timeupdate', onTime);
      a.removeEventListener('pause', onPause);
      a.removeEventListener('ended', onPause);
      a.removeEventListener('error', onError);
      audio.current = null;
    };
  }, [src]);

  useEffect(() => () => void (localUrl && URL.revokeObjectURL(localUrl)), [localUrl]);

  /** Play a quote from t_start − 2 s; playing the same quote again stops it. */
  const play = useCallback(
    (e: Pick<Evidence, 't_start' | 't_end' | 'segment_id' | 'char_start'>) => {
      const a = audio.current;
      if (!a) return;
      const key = `${e.segment_id}:${e.char_start}`;
      if (playing === key && !a.paused) {
        a.pause();
        return;
      }
      const start = () => {
        a.currentTime = seekTime(e);
        stopAt.current = stopTime(e);
        setPlaying(key);
        a.play().catch(() => {
          setPlaying(null);
          setError('The browser would not start the recording. Press play again.');
        });
      };
      // Seeking needs the recording's metadata: wait for it the first time.
      if (a.readyState >= 1) start();
      else {
        a.addEventListener('loadedmetadata', start, { once: true });
        a.load();
      }
    },
    [playing],
  );

  const openFile = useCallback((file: File) => {
    setLocalUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return URL.createObjectURL(file);
    });
  }, []);

  return { available: !!src, local: !!localUrl, play, playing, openFile, error };
}

export type CallAudio = ReturnType<typeof useCallAudio>;
