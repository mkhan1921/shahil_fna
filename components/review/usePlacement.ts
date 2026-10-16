'use client';

import { useLayoutEffect, useState, type CSSProperties } from 'react';

const GAP = 6;
const MARGIN = 12;
const WIDTH = 432;
/** The app header is sticky above the sheet. */
const HEADER = 56;
const PHONE = 640;

/**
 * Where a popover sits: just below (or above) its field in the sheet, kept clear of the review
 * rail, following the field as the page scrolls; docked at the bottom right when the field is not
 * on screen (a forecast-only fact has no field), and as a bottom sheet on a phone.
 */
export const placeNear = (anchor: DOMRect | null, viewport: { width: number; height: number }, railLeft: number | null): CSSProperties => {
  const { width: vw, height: vh } = viewport;
  if (vw < PHONE) return { left: MARGIN, right: MARGIN, bottom: MARGIN, width: 'auto', maxHeight: '60vh' };
  const limit = Math.min(vw, railLeft ?? vw) - MARGIN;
  const width = Math.max(280, Math.min(WIDTH, limit - MARGIN));
  if (!anchor || anchor.bottom < HEADER || anchor.top > vh) return { left: Math.max(MARGIN, limit - width), bottom: MARGIN, width, maxHeight: vh - HEADER - 2 * MARGIN };
  const left = Math.max(MARGIN, Math.min(anchor.left, limit - width));
  const below = vh - anchor.bottom - GAP - MARGIN;
  const above = anchor.top - GAP - HEADER;
  if (below >= 320 || below >= above) return { left, top: anchor.bottom + GAP, width, maxHeight: Math.max(180, below) };
  return { left, bottom: vh - anchor.top + GAP, width, maxHeight: Math.max(180, above) };
};

const measure = (path: string | null): CSSProperties => {
  const rail = document.querySelector<HTMLElement>('[data-review-rail]');
  const railLeft = rail && rail.offsetParent ? rail.getBoundingClientRect().left : null;
  const field = path ? document.querySelector<HTMLElement>(`[data-path="${CSS.escape(path)}"]`) : null;
  return placeNear(field ? field.getBoundingClientRect() : null, { width: window.innerWidth, height: window.innerHeight }, railLeft);
};

export function usePlacement(path: string | null, open: boolean): CSSProperties {
  const [style, setStyle] = useState<CSSProperties>({ visibility: 'hidden' });
  useLayoutEffect(() => {
    if (!open) return;
    let frame = 0;
    const place = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setStyle(measure(path)));
    };
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [path, open]);
  return style;
}
