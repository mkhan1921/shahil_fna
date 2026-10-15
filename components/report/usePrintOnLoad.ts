'use client';

import { useEffect } from 'react';

/** For “print” links: open the print dialog once the report and its fonts have rendered. */
export function usePrintOnLoad(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const t = setTimeout(() => {
      document.fonts.ready.then(() => {
        if (!cancelled) window.print();
      });
    }, 500);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [enabled]);
}
