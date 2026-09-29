'use client';

import { Baby, HeartHandshake, Home, Plus, UserRound } from 'lucide-react';
import { EDUCATION_LEVELS, EMPLOYMENT_TYPE, MARITAL_REGIME, MARITAL_STATUS, PROVINCES, RELATIONSHIP, TITLES } from '@/lib/fna/catalog';
import { newDependant } from '@/lib/fna/defaults';
import { ageNextBirthday, ageOn, parseSaId } from '@/lib/fna/idNumber';
import type { Person, PersonKey } from '@/lib/fna/types';
import {
  Badge,
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  DateField,
  EmptyState,
  Grid,
  ItemCard,
  MoneyField,
  NumberField,
  SelectField,
  StepHeader,
  TextArea,
  TextField,
  Toggle,
} from '../ui';
import type { StepProps } from '../workspace/types';
import { hasSpouse } from './shared';

function PersonCard({ who, person, set, title }: { who: PersonKey; person: Person; set: (fn: (p: Person) => void) => void; title: string }) {
  const parsed = person.idNumber ? parseSaId(person.idNumber) : null;
  const age = ageOn(person.dateOfBirth);
  const anb = ageNextBirthday(person.dateOfBirth);
  const mismatch = parsed?.valid && person.dateOfBirth && parsed.dateOfBirth !== person.dateOfBirth;

  return (
    <Card>
      <CardHeader
        icon={<UserRound size={18} />}
        title={title}
        description={who === 'client' ? 'The main client (life 1).' : 'Spouse or life partner (life 2).'}
        actions={age !== null && <Badge tone="brand">Age {age} · ANB {anb}</Badge>}
      />
      <CardBody>
        <Grid cols={4}>
          <SelectField label="Title" value={person.title} onChange={(v) => set((p) => void (p.title = v))} options={TITLES.map((t) => ({ value: t, label: t }))} placeholder="—" />
          <TextField label="First name(s)" value={person.firstName} onChange={(v) => set((p) => void (p.firstName = v))} />
          <TextField label="Surname" value={person.surname} onChange={(v) => set((p) => void (p.surname = v))} />
          <TextField
            label="SA ID number"
            value={person.idNumber}
            onChange={(v) =>
              set((p) => {
                p.idNumber = v.replace(/[^\d]/g, '').slice(0, 13);
                const r = parseSaId(p.idNumber);
                if (r.valid) {
                  p.dateOfBirth = r.dateOfBirth!;
                  p.gender = r.gender!;
                }
              })
            }
            placeholder="13 digits"
            error={parsed && !parsed.valid && person.idNumber.length >= 13 ? parsed.error : undefined}
            hint={parsed?.valid ? `Valid · ${parsed.citizenship} · ${parsed.gender === 'male' ? 'Male' : 'Female'}` : 'Fills in date of birth and gender'}
          />
          <DateField label="Date of birth" value={person.dateOfBirth} onChange={(v) => set((p) => void (p.dateOfBirth = v))} error={mismatch ? 'Does not match the ID number' : undefined} />
          <SelectField
            label="Gender"
            value={person.gender}
            onChange={(v) => set((p) => void (p.gender = v))}
            options={[
              { value: 'male', label: 'Male' },
              { value: 'female', label: 'Female' },
            ]}
            placeholder="—"
          />
          <TextField label="Passport number" value={person.passportNumber} onChange={(v) => set((p) => void (p.passportNumber = v))} optional />
          <TextField label="Nationality" value={person.nationality} onChange={(v) => set((p) => void (p.nationality = v))} />
          <SelectField label="Employment" value={person.employmentType} onChange={(v) => set((p) => void (p.employmentType = v))} options={EMPLOYMENT_TYPE} />
          <TextField label="Occupation & main duties" value={person.occupation} onChange={(v) => set((p) => void (p.occupation = v))} />
          <TextField label="Employer / business" value={person.employer} onChange={(v) => set((p) => void (p.employer = v))} />
          <SelectField
            label="Highest qualification"
            value={person.educationLevel}
            onChange={(v) => set((p) => void (p.educationLevel = v))}
            options={EDUCATION_LEVELS.map((e) => ({ value: e, label: e }))}
            placeholder="—"
          />
          <TextField label="Mobile" type="tel" value={person.mobile} onChange={(v) => set((p) => void (p.mobile = v))} />
          <TextField label="Email" type="email" value={person.email} onChange={(v) => set((p) => void (p.email = v))} />
          <TextField label="Income tax number" value={person.taxNumber} onChange={(v) => set((p) => void (p.taxNumber = v))} optional />
          <div className="flex items-end pb-2">
            <Toggle label="Smoker" description="Used for underwriting" checked={person.smoker} onChange={(v) => set((p) => void (p.smoker = v))} />
          </div>
        </Grid>
        <Grid cols={2} className="mt-4">
          <TextArea
            label="Health & medical history"
            value={person.healthNotes}
            onChange={(v) => set((p) => void (p.healthNotes = v))}
            rows={2}
            optional
            hint="Special personal information — only record with consent."
          />
          <TextArea
            label="Hazardous pursuits & travel"
            value={person.hazardousPursuits}
            onChange={(v) => set((p) => void (p.hazardousPursuits = v))}
            rows={2}
            optional
            placeholder="e.g. Scuba diving, motor racing, private flying"
          />
        </Grid>
      </CardBody>
    </Card>
  );
}

export default function Profile({ doc, update }: StepProps) {
  const h = doc.household;
  const partnered = ['married', 'life-partner'].includes(h.maritalStatus);
  const includeSpouse = hasSpouse(doc);

  return (
    <>
      <StepHeader
        eyebrow="Step 2"
        title="Client & family"
        description="Personal details, marital regime and dependants. The marital regime determines how the estate is calculated on death."
      />
      <div className="space-y-6">
        <PersonCard who="client" title="Client" person={doc.client} set={(fn) => update((d) => fn(d.client))} />

        <Card>
          <CardHeader icon={<HeartHandshake size={18} />} title="Relationship & household" />
          <CardBody className="space-y-4">
            <Grid cols={3}>
              <SelectField
                label="Marital status"
                value={h.maritalStatus}
                onChange={(v) =>
                  update((d) => {
                    d.household.maritalStatus = v;
                    if (v === 'married' && d.household.maritalRegime === 'na') d.household.maritalRegime = 'anc-accrual';
                    if (v !== 'married') d.household.maritalRegime = 'na';
                    if (['married', 'life-partner'].includes(v)) d.household.includeSpouse = true;
                  })
                }
                options={MARITAL_STATUS}
              />
              {h.maritalStatus === 'married' && (
                <SelectField label="Marital regime" value={h.maritalRegime} onChange={(v) => update((d) => void (d.household.maritalRegime = v))} options={MARITAL_REGIME} />
              )}
              {partnered && <DateField label="Date of marriage / partnership" value={h.dateOfMarriage} onChange={(v) => update((d) => void (d.household.dateOfMarriage = v))} optional />}
            </Grid>
            {partnered && (
              <Toggle
                label={`Include ${h.maritalStatus === 'life-partner' ? 'partner' : 'spouse'} in the analysis`}
                description="Analyse needs for both lives (recommended for a household FNA)."
                checked={h.includeSpouse}
                onChange={(v) => update((d) => void (d.household.includeSpouse = v))}
              />
            )}
            {h.maritalStatus === 'married' && h.maritalRegime === 'icop' && (
              <Callout tone="info" title="In community of property">
                Both spouses share one joint estate. On the first death, half of the joint estate is attributed to the deceased, and both spouses are liable for joint debts.
              </Callout>
            )}
            {h.maritalStatus === 'life-partner' && (
              <Callout tone="warning" title="Life partners have limited automatic rights">
                Unmarried partners do not automatically inherit under the Intestate Succession Act. Wills and beneficiary nominations are essential. The s4(q) estate duty deduction and CGT roll-over apply only to spouses.
              </Callout>
            )}
            <div className="flex items-center gap-2 pt-1 text-[13px] font-medium text-ink-2">
              <Home size={15} /> Residential address
            </div>
            <Grid cols={4}>
              <TextField label="Street address" value={h.physicalAddress} onChange={(v) => update((d) => void (d.household.physicalAddress = v))} className="sm:col-span-2" />
              <TextField label="Suburb / city" value={h.city} onChange={(v) => update((d) => void (d.household.city = v))} />
              <div className="grid grid-cols-2 gap-3">
                <SelectField label="Province" value={h.province} onChange={(v) => update((d) => void (d.household.province = v))} options={PROVINCES.map((p) => ({ value: p, label: p }))} placeholder="—" />
                <TextField label="Code" value={h.postalCode} onChange={(v) => update((d) => void (d.household.postalCode = v))} />
              </div>
            </Grid>
          </CardBody>
        </Card>

        {includeSpouse && (
          <PersonCard
            who="spouse"
            title={h.maritalStatus === 'life-partner' ? 'Partner' : 'Spouse'}
            person={doc.spouse}
            set={(fn) => update((d) => fn(d.spouse))}
          />
        )}

        <Card>
          <CardHeader
            icon={<Baby size={18} />}
            title="Dependants"
            description="Children and anyone else who relies on the household financially. Education plans are captured under Goals."
            actions={
              <Button size="sm" icon={<Plus size={15} />} onClick={() => update((d) => void d.dependants.push(newDependant()))}>
                Add dependant
              </Button>
            }
          />
          <CardBody className="space-y-3">
            {doc.dependants.length === 0 && (
              <EmptyState title="No dependants recorded" description="Add children, parents or others who depend on the client’s income." />
            )}
            {doc.dependants.map((dep, i) => {
              const age = ageOn(dep.dateOfBirth);
              const set = (fn: (x: typeof dep) => void) => update((d) => fn(d.dependants[i]));
              return (
                <ItemCard
                  key={dep.id}
                  title={dep.name || `Dependant ${i + 1}`}
                  badge={<Badge>{RELATIONSHIP[dep.relationship]}</Badge>}
                  subtitle={age !== null ? `Age ${age}` : undefined}
                  onRemove={() => update((d) => void d.dependants.splice(i, 1))}
                >
                  <Grid cols={4}>
                    <TextField label="Full name" value={dep.name} onChange={(v) => set((x) => void (x.name = v))} />
                    <SelectField
                      label="Relationship"
                      value={dep.relationship}
                      onChange={(v) =>
                        set((x) => {
                          x.relationship = v;
                          if (v !== 'child') {
                            x.tertiary = false;
                            x.schoolType = 'none';
                          }
                        })
                      }
                      options={RELATIONSHIP}
                    />
                    <DateField label="Date of birth" value={dep.dateOfBirth} onChange={(v) => set((x) => void (x.dateOfBirth = v))} />
                    <SelectField
                      label="Gender"
                      value={dep.gender}
                      onChange={(v) => set((x) => void (x.gender = v))}
                      options={[
                        { value: 'male', label: 'Male' },
                        { value: 'female', label: 'Female' },
                      ]}
                      placeholder="—"
                    />
                    {dep.relationship === 'child' ? (
                      <NumberField
                        label="Financially dependent until age"
                        value={dep.dependencyEndAge}
                        onChange={(v) => set((x) => void (x.dependencyEndAge = v))}
                        min={0}
                        max={40}
                        hint="Typically 22–25 including tertiary study"
                      />
                    ) : (
                      <>
                        <MoneyField label="Monthly support provided" value={dep.monthlySupport} onChange={(v) => set((x) => void (x.monthlySupport = v))} />
                        <NumberField label="Years of support needed" value={dep.supportYears} onChange={(v) => set((x) => void (x.supportYears = v))} suffix="yrs" min={0} max={60} />
                      </>
                    )}
                    <div className="flex items-end pb-2">
                      <Toggle label="Special needs" description="May require lifelong support" checked={dep.specialNeeds} onChange={(v) => set((x) => void (x.specialNeeds = v))} />
                    </div>
                  </Grid>
                </ItemCard>
              );
            })}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
