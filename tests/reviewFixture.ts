/** Shared loading of the review fixtures and schemas for the review overlay's tests. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { JsonSchema } from '@/lib/review/jsonSchema';
import type { ReviewPackage } from '@/lib/review/types';

export const ROOT = join(import.meta.dirname, '..');

export const readJson = <T = unknown>(...parts: string[]): T => JSON.parse(readFileSync(join(ROOT, ...parts), 'utf8')) as T;

/** The demo review package for the sample household (pipeline/scripts/make_review_fixture.py). */
export const loadPackage = (): ReviewPackage => readJson<ReviewPackage>('tests', 'fixtures', 'review_package.json');

export const schema = (name: string): JsonSchema => readJson<JsonSchema>('lib', 'review', 'schema', `${name}.json`);

/** The call date: the reviews' analyses run as at the call, as the pipeline's do. */
export const ANCHOR = new Date('2026-10-15T12:00:00');

export const STAMP = { reviewerId: 'adviser-sipho', at: '2026-10-16T09:30:00Z' };

/** Fact ids in the fixture. */
export const F = {
  income: 'income.{p}.grossMonthly#client',
  spouseIncome: 'income.{p}.grossMonthly#spouse',
  bonus: 'income.{p}.annualBonus#client',
  retirementAge: 'retirement.{p}.retirementAge#client',
  target: 'retirement.{p}.targetPercent#client',
  schoolFees: 'dependants[].schoolFeesAnnual#person.neo_mokoena_1',
  schoolType: 'dependants[].schoolType#person.kagiso_mokoena_2',
  tertiaryCost: 'dependants[].tertiaryCostAnnual#person.kagiso_mokoena_2',
  neoTertiaryCost: 'dependants[].tertiaryCostAnnual#person.neo_mokoena_1',
  studyOccurs: 'event[].occurs#goal.kagiso_overseas_study',
  ra: 'retirementFunds[].value#account.retirement_annuity_provi_2',
  bond: 'liabilities[].balance#liability.home_loan_1',
  disabilityType: 'policies[].type#policy.group_disability_new',
  disabilityCover: 'policies[].cover#policy.group_disability_new',
  disabilityDefinition: 'policies[].definition#policy.group_disability_new',
  krugerType: 'assets[].type#property.krugerrands_new',
  krugerValue: 'assets[].value#property.krugerrands_new',
  spouseWill: 'estate.{p}.hasWill#spouse',
  groceries: 'expenses.items[]#groceries',
  smoker: '{p}.smoker#client',
  mothersSupport: 'dependants[].monthlySupport#person.mmabatho_mokoena_3',
  policyRelationship: 'dependants[].relationship#person.kagiso_mokoena_2',
} as const;
