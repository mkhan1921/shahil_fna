import type { Draft } from 'immer';
import type { Analysis } from '@/lib/fna/analysis';
import type { FnaDocument, PracticeProfile } from '@/lib/fna/types';

export type Update = (recipe: (draft: Draft<FnaDocument>) => void) => void;

export interface SectionProps {
  doc: FnaDocument;
  update: Update;
  analysis: Analysis;
  practice: PracticeProfile | null;
}
