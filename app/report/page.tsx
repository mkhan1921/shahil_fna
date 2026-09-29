'use client';

import { ArrowLeft, Printer } from 'lucide-react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useMemo, useState } from 'react';
import Report from '@/components/report/Report';
import { Button } from '@/components/ui';
import { analyse, personName } from '@/lib/fna/analysis';
import { defaultPractice } from '@/lib/fna/defaults';
import type { FnaDocument, PracticeProfile } from '@/lib/fna/types';
import { getDocument, getPractice } from '@/lib/store/db';

const cssString = (s: string) => `"${s.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/[\r\n]+/g, ' ')}"`;

function ReportView() {
  const params = useSearchParams();
  const id = params?.get('id') ?? null;
  const autoPrint = params?.get('print') === '1';
  const [appendix, setAppendix] = useState(params?.get('appendix') === '1');
  const [doc, setDoc] = useState<FnaDocument | null | undefined>(undefined);
  const [practice, setPractice] = useState<PracticeProfile>(defaultPractice());

  useEffect(() => {
    if (!id) {
      setDoc(null);
      return;
    }
    Promise.all([getDocument(id), getPractice()]).then(([d, p]) => {
      setDoc(d);
      setPractice(p);
    });
  }, [id]);

  const analysis = useMemo(() => (doc ? analyse(doc) : null), [doc]);

  useEffect(() => {
    if (!doc) return;
    const who = personName(doc.client, 'Client');
    document.title = `FNA — ${who} — ${doc.engagement.meetingDate || new Date().toISOString().slice(0, 10)}`;
    if (autoPrint) {
      const t = setTimeout(() => window.print(), 600);
      return () => clearTimeout(t);
    }
  }, [doc, autoPrint]);

  if (doc === undefined) return <div className="p-16 text-center text-sm text-muted">Preparing report…</div>;
  if (!doc || !analysis)
    return (
      <div className="p-16 text-center text-sm text-muted">
        Client not found on this device.{' '}
        <Link className="text-brand underline" href="/">
          Back to clients
        </Link>
      </div>
    );

  const footerLeft = [practice.fspName || practice.practiceName, practice.fspNumber && `Authorised FSP ${practice.fspNumber}`].filter(Boolean).join(' · ') || 'Financial Needs Analysis';
  const footerMid = `${personName(doc.client, 'Client')} — confidential`;

  return (
    <div className="report-root min-h-screen bg-[#e9edf1] print:bg-white">
      <style>{`
        @page {
          @bottom-left { content: ${cssString(footerLeft)}; font-size: 7.5pt; font-family: system-ui, sans-serif; color: #6b7a8c; }
          @bottom-center { content: ${cssString(footerMid)}; font-size: 7.5pt; font-family: system-ui, sans-serif; color: #98a4b3; }
          @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 7.5pt; font-family: system-ui, sans-serif; color: #6b7a8c; }
        }
        .report { width: 210mm; }
        .report > section { padding: 0; }
        @media screen {
          .report { padding: 18mm 16mm; box-shadow: 0 10px 40px rgba(15, 30, 50, 0.12); margin: 24px auto 64px; border-radius: 4px; }
          .report > section + section { margin-top: 18mm; }
          .report .print-break { margin-top: 14mm; }
        }
        @media screen and (max-width: 900px) {
          .report { width: 100%; padding: 20px 16px; margin: 0; border-radius: 0; box-shadow: none; }
          .report .grid-cols-8 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
        }
        @media print {
          .report { width: auto; }
          .report > section + section:not(.print-break) { margin-top: 9mm; }
        }
      `}</style>
      <div className="no-print sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-3 px-4">
          <Link href={`/client?id=${doc.id}&s=report`} className="inline-flex items-center gap-1.5 text-sm font-medium text-ink-2 hover:text-ink">
            <ArrowLeft size={15} /> Back to FNA
          </Link>
          <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-2">
            <input type="checkbox" checked={appendix} onChange={(e) => setAppendix(e.target.checked)} className="h-4 w-4 accent-[var(--color-brand-2)]" />
            Include detailed appendix
          </label>
          <div className="hidden text-sm text-muted lg:block">A4 · “Save as PDF” in the print dialog</div>
          <Button variant="primary" icon={<Printer size={15} />} onClick={() => window.print()}>
            Print / save PDF
          </Button>
        </div>
      </div>
      <Report doc={doc} analysis={analysis} practice={practice} appendix={appendix} />
    </div>
  );
}

export default function ReportPage() {
  return (
    <Suspense fallback={<div className="p-16 text-center text-sm text-muted">Preparing report…</div>}>
      <ReportView />
    </Suspense>
  );
}
