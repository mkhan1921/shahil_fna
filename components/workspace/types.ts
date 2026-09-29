import type { Draft } from 'immer';
import type { Analysis } from '@/lib/fna/analysis';
import type { FnaDocument, PracticeProfile } from '@/lib/fna/types';

export type StepKey =
  | 'engagement'
  | 'profile'
  | 'income'
  | 'expenses'
  | 'assets'
  | 'liabilities'
  | 'cover'
  | 'goals'
  | 'estate'
  | 'risk'
  | 'assumptions'
  | 'analysis'
  | 'advice'
  | 'report';

export type Update = (recipe: (draft: Draft<FnaDocument>) => void) => void;

export interface StepProps {
  doc: FnaDocument;
  update: Update;
  analysis: Analysis;
  practice: PracticeProfile | null;
  go: (step: StepKey) => void;
}

export const STEPS: { key: StepKey; label: string; group: 'Discover' | 'Analyse' | 'Advise' }[] = [
  { key: 'engagement', label: 'Engagement & consent', group: 'Discover' },
  { key: 'profile', label: 'Client & family', group: 'Discover' },
  { key: 'income', label: 'Income & tax', group: 'Discover' },
  { key: 'expenses', label: 'Monthly budget', group: 'Discover' },
  { key: 'assets', label: 'Assets & retirement funds', group: 'Discover' },
  { key: 'liabilities', label: 'Liabilities', group: 'Discover' },
  { key: 'cover', label: 'Existing cover', group: 'Discover' },
  { key: 'goals', label: 'Goals', group: 'Discover' },
  { key: 'estate', label: 'Estate planning', group: 'Discover' },
  { key: 'risk', label: 'Risk profile', group: 'Discover' },
  { key: 'assumptions', label: 'Assumptions', group: 'Analyse' },
  { key: 'analysis', label: 'Needs analysis', group: 'Analyse' },
  { key: 'advice', label: 'Advice & record', group: 'Advise' },
  { key: 'report', label: 'Report', group: 'Advise' },
];
