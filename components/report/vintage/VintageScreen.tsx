'use client';

import type { ReactNode } from 'react';
import type { Analysis } from '@/lib/fna/analysis';
import type { FnaDocument, PracticeProfile } from '@/lib/fna/types';
import { usePrintOnLoad } from '../usePrintOnLoad';
import { garamond } from './font';
import { vintageCss } from './styles';
import VintageReport from './VintageReport';

/** The vintage report as it appears on screen and in print. */
export default function VintageScreen({
  doc,
  analysis,
  practice,
  appendix,
  autoPrint,
  toolbar,
}: {
  doc: FnaDocument;
  analysis: Analysis;
  practice: PracticeProfile;
  appendix: boolean;
  autoPrint: boolean;
  toolbar: ReactNode;
}) {
  usePrintOnLoad(autoPrint);
  const surnames = [...new Set(analysis.people.map((k) => doc[k].surname).filter(Boolean))];
  const runningTitle = surnames.length === 1 ? `A Financial Survey of the ${surnames[0]} Household` : 'A Financial Survey';
  return (
    <div className={`vr ${garamond.variable}`}>
      <style>{vintageCss({ fontFamily: garamond.style.fontFamily, runningTitle })}</style>
      {toolbar}
      <VintageReport doc={doc} analysis={analysis} practice={practice} appendix={appendix} />
    </div>
  );
}
