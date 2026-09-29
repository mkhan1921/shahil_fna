'use client';

import { EDUCATION_LEVELS, EMPLOYMENT_TYPE, EXPENSE_CATEGORIES, EXPENSE_ITEMS, MARITAL_REGIME, MARITAL_STATUS, PROVINCES, RELATIONSHIP, SCOPE_AREA, TITLES } from '@/lib/fna/catalog';
import { DISCLAIMERS } from '@/lib/fna/compliance';
import { newDependant, uid } from '@/lib/fna/defaults';
import { ageNextBirthday, ageOn, parseSaId } from '@/lib/fna/idNumber';
import { TAX_YEARS } from '@/lib/fna/tax';
import type { Person, PersonKey, ScopeArea } from '@/lib/fna/types';
import { money, pct } from '@/lib/format';
import { firstNameOf, hasSpouse, people } from '../shared';
import { AddRow, AreaC, Cells, CheckC, DateC, Filler, Item, MoneyC, NullMoneyC, NumC, Pair, PairHead, ReadC, Section, SelectC, Sub, TextC, YesNoC } from '../cells';
import { focusRow } from '../focus';
import type { SectionProps } from '../types';

const GENDER = [
  { value: 'male' as const, label: 'Male' },
  { value: 'female' as const, label: 'Female' },
];

/* ================================================================== */
/* Engagement                                                          */
/* ================================================================== */

export function EngagementSection({ doc, update }: SectionProps) {
  const e = doc.engagement;
  const set = <K extends keyof typeof e>(k: K, v: (typeof e)[K]) => update((d) => void (d.engagement[k] = v));
  return (
    <Section id="engagement" n={1} title="Engagement & consent" right={<span>FAIS s4–s8 · POPIA · FICA</span>}>
      <Cells>
        <DateC label="Meeting date" value={e.meetingDate} onChange={(v) => set('meetingDate', v)} span={2} />
        <SelectC label="Meeting" value={e.meetingType} onChange={(v) => set('meetingType', v)} options={{ 'in-person': 'In person', virtual: 'Video', telephonic: 'Telephone' }} span={2} />
        <SelectC label="Advice type" value={e.adviceType} onChange={(v) => set('adviceType', v)} options={{ comprehensive: 'Comprehensive FNA', limited: 'Limited advice' }} span={2} />
        <SelectC label="PEP / DPIP" value={e.pepStatus} onChange={(v) => set('pepStatus', v)} options={{ no: 'No', yes: 'Yes', unknown: 'Not screened' }} span={2} />
        <TextC label="Reason for meeting" value={e.reasonForAdvice} onChange={(v) => set('reasonForAdvice', v)} span={4} placeholder="Annual review, new baby, new bond…" />
        <AreaC label="Client objectives (own words)" value={e.clientObjectives} onChange={(v) => set('clientObjectives', v)} span={6} need={!e.clientObjectives.trim()} />
        <AreaC label="Concerns & priorities" value={e.clientConcerns} onChange={(v) => set('clientConcerns', v)} span={3} />
        <AreaC label="Other advisers (attorney, accountant…)" value={e.otherAdvisers} onChange={(v) => set('otherAdvisers', v)} span={3} />
      </Cells>
      <Sub>Scope — areas analysed</Sub>
      <Cells>
        {(Object.keys(SCOPE_AREA) as ScopeArea[]).map((k) => (
          <CheckC
            key={k}
            span={3}
            label={SCOPE_AREA[k]}
            checked={e.scope.includes(k)}
            onChange={(on) =>
              update((d) => {
                const s = new Set(d.engagement.scope);
                if (on) s.add(k);
                else s.delete(k);
                d.engagement.scope = [...s];
              })
            }
          />
        ))}
        {e.adviceType === 'limited' && <AreaC label="Limitations agreed (s8(4)) — printed with the limited-advice warning" value={e.limitations} onChange={(v) => set('limitations', v)} span={12} />}
        <CheckC span={4} label="Client declined to give some information" detail="Adds the s8(4)(b) warning" checked={e.clientDeclinedFna} onChange={(v) => set('clientDeclinedFna', v)} />
        {e.clientDeclinedFna && <TextC label="Information not provided & why" value={e.declineReason} onChange={(v) => set('declineReason', v)} span={8} />}
      </Cells>
      <Sub>Disclosures & consents</Sub>
      <Cells>
        <CheckC span={3} label="FSP & rep disclosures given" detail="GCC s4, s5, s7" checked={e.disclosureLetterProvided} onChange={(v) => set('disclosureLetterProvided', v)} need={!e.disclosureLetterProvided} />
        <CheckC span={3} label="Conflict of interest policy disclosed" checked={e.conflictsDisclosed} onChange={(v) => set('conflictsDisclosed', v)} />
        <CheckC span={3} label="Fees & commission explained in rand" checked={e.remunerationDisclosed} onChange={(v) => set('remunerationDisclosed', v)} />
        <CheckC span={3} label="FICA: identity & address verified" checked={e.ficaVerified} onChange={(v) => set('ficaVerified', v)} need={!e.ficaVerified} />
        <CheckC
          span={3}
          label="POPIA consent to process info"
          checked={e.popiaConsent}
          need={!e.popiaConsent}
          onChange={(v) =>
            update((d) => {
              d.engagement.popiaConsent = v;
              if (v && !d.engagement.popiaConsentDate) d.engagement.popiaConsentDate = new Date().toISOString().slice(0, 10);
            })
          }
        />
        <DateC label="Consent date" value={e.popiaConsentDate} onChange={(v) => set('popiaConsentDate', v)} span={2} />
        <CheckC span={4} label="Consent: special personal info (health, criminal)" detail="POPIA s26–27" checked={e.specialInfoConsent} onChange={(v) => set('specialInfoConsent', v)} />
        <CheckC span={3} label="Opted in to direct marketing" detail="POPIA s69" checked={e.marketingOptIn} onChange={(v) => set('marketingOptIn', v)} />
      </Cells>
      {e.adviceType === 'limited' && <div className="border-b border-line bg-warning-soft px-3 py-1.5 text-[11px] leading-4 text-ink-2">{DISCLAIMERS.limited}</div>}
    </Section>
  );
}

/* ================================================================== */
/* People                                                              */
/* ================================================================== */

function PersonCells({ person, set, firstId }: { person: Person; set: (fn: (p: Person) => void) => void; firstId?: string }) {
  const parsed = person.idNumber ? parseSaId(person.idNumber) : null;
  const age = ageOn(person.dateOfBirth);
  const badId = !!parsed && !parsed.valid && person.idNumber.length >= 13;
  const mismatch = !!parsed?.valid && !!person.dateOfBirth && parsed.dateOfBirth !== person.dateOfBirth;
  return (
    <Cells>
      <SelectC label="Title" value={person.title} onChange={(v) => set((p) => void (p.title = v))} options={TITLES.map((t) => ({ value: t, label: t }))} placeholder="—" span={2} />
      <TextC id={firstId} label="First names" value={person.firstName} onChange={(v) => set((p) => void (p.firstName = v))} span={5} need={!person.firstName.trim()} />
      <TextC label="Surname" value={person.surname} onChange={(v) => set((p) => void (p.surname = v))} span={5} need={!person.surname.trim()} />
      <TextC
        label="SA ID number"
        value={person.idNumber}
        inputMode="numeric"
        maxLength={13}
        error={badId}
        hint={badId ? parsed?.error : parsed?.valid ? `✓ ${parsed.citizenship}` : 'fills DOB & gender'}
        onChange={(v) =>
          set((p) => {
            p.idNumber = v.replace(/\D/g, '').slice(0, 13);
            const r = parseSaId(p.idNumber);
            if (r.valid) {
              p.dateOfBirth = r.dateOfBirth!;
              p.gender = r.gender!;
            }
          })
        }
        span={3.75}
      />
      <DateC label="Born" value={person.dateOfBirth} onChange={(v) => set((p) => void (p.dateOfBirth = v))} span={3} error={mismatch} need={!person.dateOfBirth} hint={age !== null ? `${age} · ANB ${ageNextBirthday(person.dateOfBirth)}` : undefined} />
      <SelectC label="Gender" value={person.gender} onChange={(v) => set((p) => void (p.gender = v))} options={GENDER} placeholder="—" span={1.75} need={!person.gender} />
      <YesNoC label="Smoker" value={person.smoker} onChange={(v) => set((p) => void (p.smoker = v))} span={1.5} />
      <TextC label="Passport" value={person.passportNumber} onChange={(v) => set((p) => void (p.passportNumber = v))} span={2} />
      <SelectC label="Employment" value={person.employmentType} onChange={(v) => set((p) => void (p.employmentType = v))} options={EMPLOYMENT_TYPE} span={4} />
      <TextC label="Occupation & duties" value={person.occupation} onChange={(v) => set((p) => void (p.occupation = v))} span={4} />
      <TextC label="Employer / business" value={person.employer} onChange={(v) => set((p) => void (p.employer = v))} span={4} />
      <SelectC label="Qualification" value={person.educationLevel} onChange={(v) => set((p) => void (p.educationLevel = v))} options={EDUCATION_LEVELS.map((x) => ({ value: x, label: x }))} placeholder="—" span={4} />
      <TextC label="Mobile" type="tel" value={person.mobile} onChange={(v) => set((p) => void (p.mobile = v))} span={3} />
      <TextC label="Email" type="email" value={person.email} onChange={(v) => set((p) => void (p.email = v))} span={5} />
      <TextC label="Tax number" value={person.taxNumber} onChange={(v) => set((p) => void (p.taxNumber = v))} span={3} />
      <TextC label="Nationality" value={person.nationality} onChange={(v) => set((p) => void (p.nationality = v))} span={3} />
      <AreaC label="Health & medical history (special info)" value={person.healthNotes} onChange={(v) => set((p) => void (p.healthNotes = v))} span={6} />
      <AreaC label="Hazardous pursuits & travel" value={person.hazardousPursuits} onChange={(v) => set((p) => void (p.hazardousPursuits = v))} span={6} />
    </Cells>
  );
}

export function PeopleSection({ doc, update }: SectionProps) {
  const h = doc.household;
  const partnered = ['married', 'life-partner'].includes(h.maritalStatus);
  const spouse = hasSpouse(doc);
  const children = doc.dependants.filter((d) => d.relationship === 'child');
  return (
    <Section id="people" n={2} title="Client & family" right={<span>{doc.dependants.length} dependant{doc.dependants.length === 1 ? '' : 's'}</span>}>
      <Cells>
        <SelectC
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
          span={2}
        />
        {h.maritalStatus === 'married' && <SelectC label="Marital regime" value={h.maritalRegime} onChange={(v) => update((d) => void (d.household.maritalRegime = v))} options={MARITAL_REGIME} span={3} />}
        {partnered && <DateC label="Date married" value={h.dateOfMarriage} onChange={(v) => update((d) => void (d.household.dateOfMarriage = v))} span={2} />}
        {partnered && <YesNoC label="Analyse spouse" value={h.includeSpouse} onChange={(v) => update((d) => void (d.household.includeSpouse = v))} span={1} />}
        <TextC label="Street address" value={h.physicalAddress} onChange={(v) => update((d) => void (d.household.physicalAddress = v))} span={4} />
        <TextC label="Suburb / city" value={h.city} onChange={(v) => update((d) => void (d.household.city = v))} span={2} />
        <SelectC label="Province" value={h.province} onChange={(v) => update((d) => void (d.household.province = v))} options={PROVINCES.map((p) => ({ value: p, label: p }))} placeholder="—" span={2} />
        <TextC label="Code" value={h.postalCode} onChange={(v) => update((d) => void (d.household.postalCode = v))} span={1} inputMode="numeric" />
      </Cells>
      {h.maritalStatus === 'married' && h.maritalRegime === 'icop' && (
        <div className="border-b border-line bg-brand-soft/50 px-3 py-1 text-[11px] text-ink-2">In community of property: one joint estate — half is attributed to each spouse on death and both are liable for joint debts.</div>
      )}
      {h.maritalStatus === 'life-partner' && (
        <div className="border-b border-line bg-warning-soft px-3 py-1 text-[11px] text-ink-2">Life partners don’t inherit automatically and don’t get the s4(q) deduction or CGT roll-over — wills and nominations are essential.</div>
      )}
      <Pair>
        <div>
          <PairHead right={spouse ? undefined : partnered ? 'Set “Analyse spouse” to Yes to add the spouse' : 'Married or partnered? Change marital status to add a spouse'}>Client</PairHead>
          <PersonCells person={doc.client} set={(fn) => update((d) => fn(d.client))} firstId="client-first-name" />
        </div>
        {spouse && (
          <div>
            <PairHead>{h.maritalStatus === 'life-partner' ? 'Partner' : 'Spouse'}</PairHead>
            <PersonCells person={doc.spouse} set={(fn) => update((d) => fn(d.spouse))} />
          </div>
        )}
      </Pair>

      <Sub right={<span>Education costs in today’s money · {children.length} child{children.length === 1 ? '' : 'ren'}</span>}>Dependants & education plans</Sub>
      {doc.dependants.map((dep, i) => {
        const age = ageOn(dep.dateOfBirth);
        const set = (fn: (x: typeof dep) => void) => update((d) => fn(d.dependants[i]));
        const child = dep.relationship === 'child';
        return (
          <Item key={dep.id} id={dep.id} n={i + 1} lock={4} onRemove={() => update((d) => void d.dependants.splice(i, 1))}>
            <TextC label="Name" value={dep.name} onChange={(v) => set((x) => void (x.name = v))} span={3} />
            <SelectC
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
              span={2}
            />
            <DateC label="Born" value={dep.dateOfBirth} onChange={(v) => set((x) => void (x.dateOfBirth = v))} span={2} hint={age !== null ? `age ${age}` : undefined} />
            <SelectC label="Gender" value={dep.gender} onChange={(v) => set((x) => void (x.gender = v))} options={GENDER} placeholder="—" span={1} />
            {child ? (
              <NumC label="Until age" value={dep.dependencyEndAge} onChange={(v) => set((x) => void (x.dependencyEndAge = v))} suffix="yrs" min={0} max={40} span={1} />
            ) : (
              <>
                <MoneyC label="Support p.m." value={dep.monthlySupport} onChange={(v) => set((x) => void (x.monthlySupport = v))} span={1} />
                <NumC label="For" value={dep.supportYears} onChange={(v) => set((x) => void (x.supportYears = v))} suffix="yrs" min={0} max={60} span={1} />
              </>
            )}
            <YesNoC label="Special" value={dep.specialNeeds} onChange={(v) => set((x) => void (x.specialNeeds = v))} span={1} />
            {child && (
              <>
                <SelectC label="Schooling" value={dep.schoolType} onChange={(v) => set((x) => void (x.schoolType = v))} options={{ none: 'None / done', public: 'Public', private: 'Independent' }} span={2} />
                <MoneyC label="School fees p.a." value={dep.schoolFeesAnnual} onChange={(v) => set((x) => void (x.schoolFeesAnnual = v))} span={2} />
                <YesNoC label="Tertiary" value={dep.tertiary} onChange={(v) => set((x) => void (x.tertiary = v))} span={1} />
                {dep.tertiary && (
                  <>
                    <NumC label="From age" value={dep.tertiaryStartAge} onChange={(v) => set((x) => void (x.tertiaryStartAge = v))} min={15} max={40} span={1} />
                    <NumC label="Years" value={dep.tertiaryYears} onChange={(v) => set((x) => void (x.tertiaryYears = v))} min={1} max={8} span={1} />
                    <MoneyC label="Cost p.a. (today)" value={dep.tertiaryCostAnnual} onChange={(v) => set((x) => void (x.tertiaryCostAnnual = v))} span={2} hint="~R150k" />
                  </>
                )}
                <MoneyC label="Saved for education" value={dep.educationSavings} onChange={(v) => set((x) => void (x.educationSavings = v))} span={1.5} />
                <MoneyC label="Saving p.m." value={dep.educationSavingsMonthly} onChange={(v) => set((x) => void (x.educationSavingsMonthly = v))} span={1.5} />
              </>
            )}
          </Item>
        );
      })}
      <AddRow
        label="Add dependant"
        empty={doc.dependants.length ? undefined : 'Children, parents or others who rely on the household'}
        onAdd={() => {
          const d = newDependant();
          update((x) => void x.dependants.push(d));
          focusRow(d.id);
        }}
      />
    </Section>
  );
}

/* ================================================================== */
/* Income                                                              */
/* ================================================================== */

export function IncomeSection({ doc, update, analysis }: SectionProps) {
  const lives = people(doc);
  return (
    <Section
      id="income"
      n={3} title="Income & tax"
      right={
        <label className="flex items-center gap-1.5">
          Tax year
          <select
            className="rounded border border-line-strong bg-white px-1.5 py-0.5 text-[11.5px]"
            value={doc.assumptions.taxYear}
            onChange={(e) => update((d) => void (d.assumptions.taxYear = e.target.value))}
          >
            {TAX_YEARS().map((y) => (
              <option key={y} value={y}>
                SARS {y}
              </option>
            ))}
          </select>
        </label>
      }
      note="Retirement contributions come from the funds under Assets. Medical aid paid by debit order goes under Budget instead."
    >
      <Pair>
        {lives.map((k: PersonKey) => {
          const inc = doc.income[k];
          const r = analysis.incomes.find((x) => x.key === k)!;
          const set = (fn: (x: typeof inc) => void) => update((d) => fn(d.income[k]));
          return (
            <div key={k}>
              <PairHead right={`Marginal ${pct(r.tax.marginalRate)} · effective ${pct(r.tax.effectiveRate, 1)}`}>{firstNameOf(doc, k)}</PairHead>
              <Cells>
                <MoneyC label="Gross salary / income p.m." value={inc.grossMonthly} onChange={(v) => set((x) => void (x.grossMonthly = v))} span={4} need={k === 'client' && !inc.grossMonthly && !inc.otherTaxableMonthly} />
                <MoneyC label="Annual bonus" value={inc.annualBonus} onChange={(v) => set((x) => void (x.annualBonus = v))} span={4} />
                <MoneyC label="Other taxable p.m." value={inc.otherTaxableMonthly} onChange={(v) => set((x) => void (x.otherTaxableMonthly = v))} span={4} hint="rental, annuity" />
                <MoneyC label="Non-taxable p.m." value={inc.nonTaxableMonthly} onChange={(v) => set((x) => void (x.nonTaxableMonthly = v))} span={4} />
                <MoneyC label="Medical aid via payroll" value={inc.medicalAidPayrollMonthly} onChange={(v) => set((x) => void (x.medicalAidPayrollMonthly = v))} span={4} />
                <NumC label="Medical members" value={inc.medicalSchemeMembers} onChange={(v) => set((x) => void (x.medicalSchemeMembers = Math.round(v)))} min={0} max={12} span={2} hint="s6A" />
                <YesNoC label="UIF" value={inc.uifApplies} onChange={(v) => set((x) => void (x.uifApplies = v))} span={2} />
                <MoneyC label="Other payroll deductions" value={inc.otherPayrollDeductionsMonthly} onChange={(v) => set((x) => void (x.otherPayrollDeductionsMonthly = v))} span={4} />
                <NullMoneyC label="PAYE per payslip" value={inc.payeOverrideMonthly} onChange={(v) => set((x) => void (x.payeOverrideMonthly = v))} span={4} hint="blank = estimate" />
                <ReadC label="Retirement (payroll)" value={money(r.retirementPayrollMonthly)} span={4} />
                <ReadC label="PAYE + UIF" value={money(r.payeMonthly + r.uifMonthly)} span={4} />
                <ReadC label="Take-home p.m." value={money(r.takeHomeMonthly)} span={4} tone="strong" />
                <ReadC label="After-tax incl. bonus p.m." value={money(r.afterTaxMonthly)} span={4} />
                <ReadC label="Taxable income p.a." value={money(r.tax.taxable)} span={4} />
                <ReadC label="Annual tax" value={money(r.tax.annualTax)} span={4} sub={`credits ${money(r.tax.medicalCredit)}`} />
              </Cells>
            </div>
          );
        })}
      </Pair>
    </Section>
  );
}

/* ================================================================== */
/* Budget                                                              */
/* ================================================================== */

export function BudgetSection({ doc, update, analysis }: SectionProps) {
  const cf = analysis.cashflow;
  return (
    <Section
      id="budget"
      n={4} title="Monthly budget"
      right={
        <>
          <span>
            Living <strong className="text-ink tabular">{money(cf.livingExpenses)}</strong>
          </span>
          <span>
            Essential <strong className="text-ink tabular">{money(cf.essentialExpenses)}</strong>
          </span>
          <span>
            {cf.surplus < 0 ? 'Deficit' : 'Surplus'}{' '}
            <strong className={cf.surplus < 0 ? 'text-critical-ink tabular' : 'text-good-ink tabular'}>{money(cf.surplus)}</strong>
          </span>
        </>
      }
      note="Living expenses only — bond and vehicle repayments come from Liabilities, risk premiums from Existing cover, and savings from Assets."
    >
      {EXPENSE_CATEGORIES.map((cat) => {
        const items = EXPENSE_ITEMS.filter((x) => x.category === cat.key);
        const custom = doc.expenses.custom.map((c, idx) => ({ c, idx })).filter(({ c }) => c.category === cat.key);
        const total = cf.byCategory.find((b) => b.key === cat.key)?.amount ?? 0;
        const n = items.length + custom.length;
        const pad = (6 - (n % 6)) % 6;
        return (
          <div key={cat.key}>
            <Sub
              right={
                <>
                  <span className="tabular font-semibold text-ink">{money(total)}</span>
                  <button
                    type="button"
                    tabIndex={-1}
                    className="font-semibold text-brand-2 hover:underline"
                    onClick={() => update((d) => void d.expenses.custom.push({ id: uid(), category: cat.key, label: '', amount: 0, essential: true }))}
                  >
                    + other
                  </button>
                </>
              }
            >
              {cat.label}
            </Sub>
            <Cells>
              {items.map((item) => (
                <MoneyC
                  key={item.key}
                  label={item.label}
                  span={2}
                  value={doc.expenses.items[item.key] ?? 0}
                  onChange={(v) =>
                    update((d) => {
                      if (v) d.expenses.items[item.key] = v;
                      else delete d.expenses.items[item.key];
                    })
                  }
                />
              ))}
              {custom.map(({ c, idx }) => (
                <div key={c.id} className="sheet-cell flex bg-white" style={{ ['--b' as string]: `${(2 / 12) * 100}%`, ['--g' as string]: 2 }}>
                  <input
                    className="w-1/2 min-w-0 border-r border-line bg-transparent px-2 text-[12px] outline-none focus:bg-[#eaf3fb]"
                    placeholder="Item"
                    value={c.label}
                    onChange={(e) => update((d) => void (d.expenses.custom[idx].label = e.target.value))}
                  />
                  <CustomAmount
                    value={c.amount}
                    essential={c.essential}
                    onChange={(v) => update((d) => void (d.expenses.custom[idx].amount = v))}
                    onEssential={(v) => update((d) => void (d.expenses.custom[idx].essential = v))}
                    onRemove={() => update((d) => void d.expenses.custom.splice(idx, 1))}
                  />
                </div>
              ))}
              {pad > 0 && <Filler span={pad * 2} />}
            </Cells>
          </div>
        );
      })}
    </Section>
  );
}

function CustomAmount({
  value,
  essential,
  onChange,
  onEssential,
  onRemove,
}: {
  value: number;
  essential: boolean;
  onChange: (v: number) => void;
  onEssential: (v: boolean) => void;
  onRemove: () => void;
}) {
  return (
    <span className="flex w-1/2 items-center">
      <input type="checkbox" title="Essential expense" checked={essential} onChange={(e) => onEssential(e.target.checked)} className="ml-1 h-3 w-3 shrink-0 accent-[var(--color-brand-2)]" />
      <input
        inputMode="decimal"
        className="w-full min-w-0 bg-transparent px-2 text-right text-[13px] outline-none tabular focus:bg-[#eaf3fb]"
        placeholder="0"
        value={value ? String(value) : ''}
        onChange={(e) => onChange(Number(e.target.value.replace(/[^\d.]/g, '')) || 0)}
      />
      <button type="button" tabIndex={-1} onClick={onRemove} className="px-1 text-faint hover:text-critical-ink" aria-label="Remove item">
        ×
      </button>
    </span>
  );
}
