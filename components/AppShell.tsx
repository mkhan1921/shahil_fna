'use client';

import { BookOpen, Building2, LockKeyhole, Users } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState, type ReactNode } from 'react';
import { apiReady } from '@/lib/review/api';
import { loadApiSettings } from '@/lib/review/listing';
import { subscribe } from '@/lib/store/db';
import { usePractice } from '@/lib/store/hooks';
import { cx } from './ui';

const NAV = [
  { href: '/', label: 'Clients', icon: Users },
  { href: '/settings', label: 'Practice', icon: Building2 },
  { href: '/methodology', label: 'Methodology', icon: BookOpen },
];

export function Logo({ practiceName, logo }: { practiceName?: string; logo?: string }) {
  return (
    <Link href="/" className="flex min-w-0 items-center gap-2.5">
      {logo ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={logo} alt="" className="h-8 w-auto max-w-[120px] object-contain" />
      ) : (
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand text-[13px] font-bold text-white">
          FNA
        </span>
      )}
      <span className="min-w-0 leading-tight">
        <span className="block truncate text-[14px] font-semibold text-ink">{practiceName || 'Shahil FNA'}</span>
        <span className="block truncate text-[11px] text-muted">Financial needs analysis</span>
      </span>
    </Link>
  );
}

/** Whether call reviews are being sent to the practice's pipeline (the opt-in review API is on). */
const useReviewSync = () => {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let alive = true;
    const check = () =>
      loadApiSettings()
        .then((s) => alive && setOn(apiReady(s)))
        .catch(() => alive && setOn(false));
    check();
    const stop = subscribe(check);
    return () => {
      alive = false;
      stop();
    };
  }, []);
  return on;
};

export default function AppShell({ children, wide = false, actions }: { children: ReactNode; wide?: boolean; actions?: ReactNode }) {
  const pathname = usePathname() ?? '/';
  const { practice } = usePractice();
  const syncing = useReviewSync();
  return (
    <div className="min-h-screen">
      <header className="no-print sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur">
        <div className={cx('mx-auto flex h-12 items-center gap-4 px-4 sm:px-6', wide ? 'max-w-[1600px]' : 'max-w-7xl')}>
          <Logo practiceName={practice?.practiceName} logo={practice?.logoDataUrl} />
          <nav className="ml-2 hidden items-center gap-1 md:flex" aria-label="Main">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = href === '/' ? pathname === '/' || pathname.startsWith('/client') : pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={cx(
                    'focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] font-medium transition-colors',
                    active ? 'bg-brand-soft text-brand' : 'text-ink-2 hover:bg-wash hover:text-ink',
                  )}
                >
                  <Icon size={15} aria-hidden />
                  {label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            {actions}
            {syncing ? (
              <span
                className="hidden items-center gap-1.5 rounded-full border border-warning/40 bg-warning-soft px-2.5 py-1 text-[11px] font-medium text-warning-ink lg:inline-flex"
                title="Client files stay in this browser. Call reviews are sent to the practice's review API, as set in Practice settings."
              >
                <LockKeyhole size={12} aria-hidden /> Reviews sync to the pipeline
              </span>
            ) : (
              <span
                className="hidden items-center gap-1.5 rounded-full border border-line bg-wash px-2.5 py-1 text-[11px] font-medium text-muted lg:inline-flex"
                title="Client data is stored only in this browser (IndexedDB). Nothing is sent to a server."
              >
                <LockKeyhole size={12} aria-hidden /> Data stays on this device
              </span>
            )}
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto border-t border-line px-3 py-1.5 md:hidden" aria-label="Main">
          {NAV.map(({ href, label }) => (
            <Link key={href} href={href} className="rounded-md px-2.5 py-1 text-[13px] font-medium text-ink-2 hover:bg-wash">
              {label}
            </Link>
          ))}
        </nav>
      </header>
      {children}
    </div>
  );
}
