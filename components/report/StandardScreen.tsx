'use client';

import type { ReactNode } from 'react';
import { personName, type Analysis } from '@/lib/fna/analysis';
import type { FnaDocument, PracticeProfile } from '@/lib/fna/types';
import Report from './Report';
import { usePrintOnLoad } from './usePrintOnLoad';

const cssString = (s: string) =>
  `"${s
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/[\r\n]+/g, ' ')}"`;

/** The standard report as it appears on screen and in print. */
export default function StandardScreen({
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
      {toolbar}
      <Report doc={doc} analysis={analysis} practice={practice} appendix={appendix} />
    </div>
  );
}
