'use client';

import { ArrowLeft, Printer } from 'lucide-react';
import Link from 'next/link';
import { REPORT_STYLE } from '@/lib/fna/catalog';
import type { ReportStyle } from '@/lib/fna/types';
import { cx } from '../ui';

/** The report’s screen toolbar — back, style, appendix and print. It never prints. */
export default function ReportToolbar({
  back,
  style,
  onStyle,
  appendix,
  onAppendix,
}: {
  back: { href: string; label: string };
  style: ReportStyle;
  onStyle: (style: ReportStyle) => void;
  appendix: boolean;
  onAppendix: (on: boolean) => void;
}) {
  const dark = style === 'vintage';
  return (
    <div className={cx('no-print sticky top-0 z-20 border-b font-sans backdrop-blur', dark ? 'border-black/30 bg-[#231d19]/95 text-[#efe3cc]' : 'border-line bg-surface/95 text-ink')}>
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-2 px-3 text-sm sm:gap-3 sm:px-4">
        <Link href={back.href} className={cx('inline-flex shrink-0 items-center gap-1.5 font-medium', dark ? 'text-[#efe3cc]/85 hover:text-white' : 'text-ink-2 hover:text-ink')}>
          <ArrowLeft size={15} /> <span className="hidden sm:inline">{back.label}</span>
        </Link>
        <div role="radiogroup" aria-label="Report style" className={cx('inline-flex rounded-lg border p-0.5', dark ? 'border-white/15 bg-black/25' : 'border-line-strong bg-wash')}>
          {(Object.keys(REPORT_STYLE) as ReportStyle[]).map((s) => {
            const on = s === style;
            return (
              <button
                key={s}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => onStyle(s)}
                className={cx(
                  'focus-ring rounded-md px-2.5 py-1 text-[13px] font-medium transition-colors sm:px-3',
                  on && (dark ? 'bg-[#f4ead8] text-[#2a1d14]' : 'bg-surface text-ink shadow-[0_1px_2px_rgba(16,24,40,0.12)]'),
                  !on && (dark ? 'text-[#efe3cc]/70 hover:text-white' : 'text-muted hover:text-ink'),
                )}
              >
                {REPORT_STYLE[s].label}
              </button>
            );
          })}
        </div>
        <label className={cx('flex cursor-pointer items-center gap-2', dark ? 'text-[#efe3cc]/85' : 'text-ink-2')}>
          <input type="checkbox" checked={appendix} onChange={(e) => onAppendix(e.target.checked)} className={cx('h-4 w-4', dark ? 'accent-[#b08a5a]' : 'accent-[var(--color-brand-2)]')} />
          <span>
            Appendix<span className="hidden lg:inline"> with detailed calculations</span>
          </span>
        </label>
        <button
          type="button"
          onClick={() => window.print()}
          title="A4 — choose “Save as PDF” in the print dialog"
          aria-label="Print or save as PDF"
          className={cx(
            'focus-ring inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg px-3 font-medium transition-colors sm:px-3.5',
            dark ? 'border border-[#b08a5a]/60 bg-[#f4ead8] text-[#2a1d14] hover:bg-white' : 'bg-brand text-white hover:bg-brand-2',
          )}
        >
          <Printer size={15} /> <span className="hidden sm:inline">Print / save PDF</span>
          <span className="max-[379px]:hidden sm:hidden">Print</span>
        </button>
      </div>
    </div>
  );
}
