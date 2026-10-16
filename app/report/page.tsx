'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useMemo, useState } from 'react';
import ReportToolbar from '@/components/report/ReportToolbar';
import StandardScreen from '@/components/report/StandardScreen';
import { analyse, personName } from '@/lib/fna/analysis';
import { asReportStyle } from '@/lib/fna/catalog';
import { defaultPractice } from '@/lib/fna/defaults';
import { draftRecommendations } from '@/lib/fna/recommend';
import { sampleDocument } from '@/lib/fna/sample';
import type { FnaDocument, PracticeProfile, ReportStyle } from '@/lib/fna/types';
import { getDocument, getPractice } from '@/lib/store/db';

/* The vintage style, its typeface and its plates load only when it is chosen. */
const VintageScreen = dynamic(() => import('@/components/report/vintage/VintageScreen'), {
  loading: () => <div className="p-16 text-center text-sm text-muted">Setting the type…</div>,
});

function ReportView() {
  const params = useSearchParams();
  const router = useRouter();
  const id = params?.get('id') ?? null;
  const autoPrint = params?.get('print') === '1';
  // ?sample (or ?sample=1) previews the built-in sample family without saving it to this device.
  const sampleParam = params?.get('sample');
  const sample = sampleParam !== null && sampleParam !== undefined && sampleParam !== '0';
  const [appendix, setAppendix] = useState(params?.get('appendix') === '1');
  const [doc, setDoc] = useState<FnaDocument | null | undefined>(undefined);
  const [practice, setPractice] = useState<PracticeProfile>(defaultPractice());

  useEffect(() => {
    if (sample) {
      getPractice().then((p) => {
        const d = sampleDocument();
        // Show a complete record of advice: the sample carries the drafted recommendations.
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
  // The style chosen in Settings, unless this link asks for the other one.
  const style = asReportStyle(params?.get('style'), practice.reportStyle);

  useEffect(() => {
    if (!doc) return;
    const who = personName(doc.client, 'Client');
    document.title = `FNA — ${who} — ${doc.engagement.meetingDate || new Date().toISOString().slice(0, 10)}`;
  }, [doc]);

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

  const switchStyle = (next: ReportStyle) => {
    const q = new URLSearchParams(params?.toString() ?? '');
    q.set('style', next);
    q.delete('print');
    router.replace(`/report?${q.toString()}`, { scroll: false });
  };

  const toolbar = (
    <ReportToolbar
      back={sample ? { href: '/', label: 'Back to clients' } : { href: `/client?id=${doc.id}&s=report`, label: 'Back to FNA' }}
      style={style}
      onStyle={switchStyle}
      appendix={appendix}
      onAppendix={setAppendix}
    />
  );
  const screen = { doc, analysis, practice, appendix, autoPrint, toolbar };
  return style === 'vintage' ? <VintageScreen {...screen} /> : <StandardScreen {...screen} />;
}

export default function ReportPage() {
  return (
    <Suspense fallback={<div className="p-16 text-center text-sm text-muted">Preparing report…</div>}>
      <ReportView />
    </Suspense>
  );
}
