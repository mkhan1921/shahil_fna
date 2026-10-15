'use client';

import { ArrowLeft, Printer } from 'lucide-react';
import { EB_Garamond } from 'next/font/google';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useMemo, useState } from 'react';
import { vintageCss } from '@/components/report/vintage/styles';
import VintageReport from '@/components/report/vintage/VintageReport';
import { analyse, personName } from '@/lib/fna/analysis';
import { defaultPractice } from '@/lib/fna/defaults';
import { draftRecommendations } from '@/lib/fna/recommend';
import { sampleDocument } from '@/lib/fna/sample';
import type { FnaDocument, PracticeProfile } from '@/lib/fna/types';
import { getDocument, getPractice } from '@/lib/store/db';

const garamond = EB_Garamond({ subsets: ['latin'], style: ['normal', 'italic'], variable: '--vr-font', display: 'swap' });

/** Experimental: the client report set as a period financial survey. */
function VintageView() {
  const params = useSearchParams();
  const id = params?.get('id') ?? null;
  const autoPrint = params?.get('print') === '1';
  const sampleParam = params?.get('sample');
  const sample = sampleParam !== null && sampleParam !== undefined && sampleParam !== '0';
  const [appendix, setAppendix] = useState(params?.get('appendix') === '1');
  const [doc, setDoc] = useState<FnaDocument | null | undefined>(undefined);
  const [practice, setPractice] = useState<PracticeProfile>(defaultPractice());

  useEffect(() => {
    if (sample) {
      getPractice().then((p) => {
        const d = sampleDocument();
        if (!d.advice.recommendations.length) d.advice.recommendations = draftRecommendations(d, analyse(d));
        setDoc(d);
        setPractice(p);
      });
      return;
    }
    if (!id) {
      setDoc(null);
      return;
    }
    Promise.all([getDocument(id), getPractice()]).then(([d, p]) => {
      setDoc(d);
      setPractice(p);
    });
  }, [id, sample]);

  const analysis = useMemo(() => (doc ? analyse(doc) : null), [doc]);

  useEffect(() => {
    if (!doc) return;
    document.title = `Financial Survey — ${personName(doc.client, 'Client')}`;
    if (autoPrint) {
      const t = setTimeout(() => window.print(), 900);
      return () => clearTimeout(t);
    }
  }, [doc, autoPrint]);

  if (doc === undefined) return <div className="p-16 text-center text-sm text-muted">Setting the type…</div>;
  if (!doc || !analysis)
    return (
      <div className="p-16 text-center text-sm text-muted">
        Client not found on this device.{' '}
        <Link className="text-brand underline" href="/">
          Back to clients
        </Link>
      </div>
    );

  const surnames = [...new Set(analysis.people.map((k) => doc[k].surname).filter(Boolean))];
  const runningTitle = surnames.length === 1 ? `A Financial Survey of the ${surnames[0]} Household` : 'A Financial Survey';
  const query = sample ? 'sample=1' : `id=${doc.id}`;

  return (
    <div className={`vr ${garamond.variable}`}>
      <style>{vintageCss({ fontFamily: garamond.style.fontFamily, runningTitle })}</style>
      <div className="no-print sticky top-0 z-20 border-b border-black/30 bg-[#231d19]/95 text-[#efe3cc] backdrop-blur" style={{ fontFamily: 'var(--font-sans)' }}>
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-3 px-4 text-sm">
          <Link href={sample ? '/' : `/client?id=${doc.id}&s=report`} className="inline-flex items-center gap-1.5 font-medium text-[#efe3cc]/85 hover:text-white">
            <ArrowLeft size={15} /> {sample ? 'Clients' : 'Back to FNA'}
          </Link>
          <Link href={`/report?${query}`} className="hidden text-[#efe3cc]/70 underline-offset-4 hover:text-white hover:underline sm:inline">
            Standard edition
          </Link>
          <label className="flex cursor-pointer items-center gap-2 text-[#efe3cc]/85">
            <input type="checkbox" checked={appendix} onChange={(ev) => setAppendix(ev.target.checked)} className="h-4 w-4 accent-[#b08a5a]" />
            Appendix
          </label>
          <button
            type="button"
            onClick={() => window.print()}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-[#b08a5a]/60 bg-[#f4ead8] px-3 font-medium text-[#2a1d14] hover:bg-white"
          >
            <Printer size={15} /> Print / save PDF
          </button>
        </div>
      </div>
      <VintageReport doc={doc} analysis={analysis} practice={practice} appendix={appendix} />
    </div>
  );
}

export default function VintageReportPage() {
  return (
    <Suspense fallback={<div className="p-16 text-center text-sm text-muted">Setting the type…</div>}>
      <VintageView />
    </Suspense>
  );
}
