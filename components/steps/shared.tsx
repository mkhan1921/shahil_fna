'use client';

import type { Owner, PersonKey, FnaDocument } from '@/lib/fna/types';
import { personName } from '@/lib/fna/analysis';

export const hasSpouse = (doc: FnaDocument) =>
  doc.household.includeSpouse && ['married', 'life-partner'].includes(doc.household.maritalStatus);

export const nameOf = (doc: FnaDocument, key: PersonKey) =>
  personName(doc[key], key === 'client' ? 'Client' : doc.household.maritalStatus === 'life-partner' ? 'Partner' : 'Spouse');

export const firstNameOf = (doc: FnaDocument, key: PersonKey) =>
  doc[key].firstName || (key === 'client' ? 'Client' : doc.household.maritalStatus === 'life-partner' ? 'Partner' : 'Spouse');

export const ownerOptions = (doc: FnaDocument, allowJoint = true): { value: Owner; label: string }[] => {
  const opts: { value: Owner; label: string }[] = [{ value: 'client', label: firstNameOf(doc, 'client') }];
  if (hasSpouse(doc)) {
    opts.push({ value: 'spouse', label: firstNameOf(doc, 'spouse') });
    if (allowJoint) opts.push({ value: 'joint', label: 'Joint' });
  }
  return opts;
};

export const personOptions = (doc: FnaDocument): { value: PersonKey; label: string }[] =>
  ownerOptions(doc, false) as { value: PersonKey; label: string }[];

export const people = (doc: FnaDocument): PersonKey[] => (hasSpouse(doc) ? ['client', 'spouse'] : ['client']);
