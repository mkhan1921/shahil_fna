import type { ReactNode } from 'react';
import type { Analysis, Line, NeedResult, PersonAnalysis, Status } from '@/lib/fna/analysis';
import { ACTION, ASSET_TYPE, BENEFICIARY, LIABILITY_TYPE, MARITAL_REGIME, MARITAL_STATUS, NEED_AREA, POLICY_TYPE, RELATIONSHIP, RETIREMENT_FUND_TYPE, SCOPE_AREA } from '@/lib/fna/catalog';
import { DISCLAIMERS, OMBUDS, REPLACEMENT_FACTORS } from '@/lib/fna/compliance';
import { ageOn } from '@/lib/fna/idNumber';
import { RISK_CATEGORIES, RISK_QUESTIONS } from '@/lib/fna/riskProfile';
import type { FnaDocument, NeedArea, PersonKey, PracticeProfile, Recommendation } from '@/lib/fna/types';
import { dateLong, money, moneyCompact, pct } from '@/lib/format';
import { topSegments } from '../Visuals';
import { Fleuron, Manicule, Ornament, PAT, roman, TONES } from './ink';
import { matters, said, verdict } from './narrative';
import {
  Almanac,
  Balance,
  CatalogueBars,
  CoinGrid,
  CostPaper,
  Dial,
  FamilyTree,
  GapBars,
  Instruments,
  Key,
  NeedColumns,
  RetirementPaper,
  RiskScale,
  Strips,
  VesselRow,
  type AlmanacEvent,
  type ColSeg,
  type VesselDatum,
} from './plates';
import { Abstract, ChapterHead, Gloss, Heading, Keep, Leaders, Leaf, Para, Plate, Signature, VTable, type LeaderRow } from './type';

/*
 * The vintage edition of the client report: the same analysis as the standard
 * report, set as a period financial survey — warm paper, two inks, engraved
 * plates in place of dashboard charts, and each finding restated in plain words.
 */

const fullName = (doc: FnaDocument, k: PersonKey) => [doc[k].title, doc[k].firstName, doc[k].surname].filter(Boolean).join(' ') || (k === 'client' ? 'Client' : 'Spouse');
const first = (doc: FnaDocument, k: PersonKey) => doc[k].firstName || (k === 'client' ? 'Client' : 'Spouse');
const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const baseLabel = (l: string) => l.replace(/\s*\([^)]*\)\s*$/, '').trim();
const PATTERN_YEARS = [1, 2, 3, 4, 5, 10, 15, 20];
/** Recommendations that serve the whole household rather than one life. */
const HOUSEHOLD_AREAS: NeedArea[] = ['education', 'emergency', 'debt'];

/** “the 15th day of October, 2026” */
function periodDate(iso: string) {
  const d = new Date(iso && iso.length === 10 ? `${iso}T00:00:00` : iso || Date.now());
  if (Number.isNaN(d.getTime())) return '';
  const n = d.getDate();
  const suffix = n % 10 === 1 && n !== 11 ? 'st' : n % 10 === 2 && n !== 12 ? 'nd' : n % 10 === 3 && n !== 13 ? 'rd' : 'th';
  return `the ${n}${suffix} day of ${d.toLocaleDateString('en-ZA', { month: 'long' })}, ${d.getFullYear()}`;
}

/** Merge need lines across people by label, so the lives can sit side by side. */
function merged(needs: NeedResult[], pick: (n: NeedResult) => Line[]) {
  const order: string[] = [];
  const map = new Map<string, (number | undefined)[]>();
  needs.forEach((n, i) => {
    for (const l of pick(n)) {
      const key = baseLabel(l.label);
      if (!map.has(key)) {
        map.set(
          key,
          needs.map(() => undefined),
        );
        order.push(key);
      }
      map.get(key)![i] = (map.get(key)![i] ?? 0) + l.amount;
    }
  });
  return order.map((k) => ({ label: k, values: map.get(k)! }));
}

/** A need, line by line, for every life side by side. */
function NeedTable({ names, needs, monthly = false }: { names: string[]; needs: NeedResult[]; monthly?: boolean }) {
  const req = merged(needs, (n) => n.needLines);
  const prov = merged(needs, (n) => n.provisionLines);
  const f = (v?: number) => (v === undefined ? '—' : money(v));
  const rows: ReactNode[][] = [
    ...req.map((r) => [r.label, ...r.values.map(f)]),
    ['Total needed', ...needs.map((n) => money(n.need))],
    ...(prov.length
      ? prov.map((r) => [`Less ${r.label.charAt(0).toLowerCase()}${r.label.slice(1)}`, ...r.values.map((v) => (v === undefined ? '—' : money(-v)))])
      : [['Less cover in place', ...needs.map(() => 'none')]]),
  ];
  const totalRow = req.length;
  return (
    <VTable
      head={[monthly ? 'A month' : 'Lump sum', ...names]}
      align={['l', ...names.map(() => 'r' as const)]}
      rows={rows}
      strong={[totalRow]}
      foot={['Shortfall, or (surplus)', ...needs.map((n) => (n.shortfall > 0.5 ? <span className="vr-red">{money(n.shortfall)}</span> : `(${money(n.provision - n.need)})`))]}
    />
  );
}

/** The executor’s account: what must be paid, against the cash to pay it, balanced as a ledger. */
function ExecutorAccount({ p, name }: { p: PersonAnalysis; name: string }) {
  const e = p.estate;
  const debit = e.lines.filter((l) => l.amount > 0.5);
  const surplus = Math.max(0, e.cashAvailable - e.cashRequired);
  const credit: LeaderRow[] = [{ label: 'Cash and policies paid to the estate', value: money(e.cashAvailable) }];
  if (e.liquidityShortfall > 0.5) credit.push({ label: 'Still to be found (the shortfall)', value: money(e.liquidityShortfall), tone: 'red' });
  const debitRows: LeaderRow[] = debit.map((l) => ({ label: baseLabel(l.label), value: money(l.amount) }));
  if (surplus > 0.5) debitRows.push({ label: 'Balance left over for the heirs', value: money(surplus), tone: 'strong' });
  const total = Math.max(e.cashRequired, e.cashAvailable);
  return (
    <div className="vr-account">
      <div className="vr-account-title">
        The Executor’s Account <i>— on the death of {name}</i>
      </div>
      <div className="vr-account-sides">
        <div>
          <div className="vr-account-side">Cash the executor must pay out</div>
          <Leaders rows={debitRows} />
          <div className="vr-account-total">
            <span>Total</span>
            <span>{money(total)}</span>
          </div>
        </div>
        <div>
          <div className="vr-account-side">Cash the executor can draw on</div>
          <Leaders rows={credit} />
          <div className="vr-account-total">
            <span>Total</span>
            <span>{money(total)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function VintageReport({ doc, analysis, practice, appendix = false }: { doc: FnaDocument; analysis: Analysis; practice: PracticeProfile; appendix?: boolean }) {
  const a = analysis;
  const e = doc.engagement;
  const people = a.people;
  const P = people.map((k) => a.persons[k]!);
  const names = people.map((k) => first(doc, k));
  const couple = people.length > 1;
  const household = people.map((k) => fullName(doc, k)).join(' & ');
  const surnames = [...new Set(people.map((k) => doc[k].surname).filter(Boolean))];
  const houseName = surnames.length === 1 ? `the ${surnames[0]} Household` : `the Household of ${people.map((k) => personNameOf(doc, k)).join(' & ')}`;
  const running = (right: string) => ({ left: cap(houseName), right });
  const firm = practice.practiceName || practice.fspName || '';
  const as = doc.assumptions;
  const cf = a.cashflow;
  const nw = a.netWorth;
  const t = a.table;
  const recs = doc.advice.recommendations;
  const active = recs.filter((r) => r.clientDecision !== 'declined');
  const newCommitments = active.reduce((s, r) => s + (r.premiumMonthly || 0), 0);
  const quotes = active.filter((r) => !r.premiumMonthly && r.action !== 'refer').length;
  const declined = recs.filter((r) => r.clientDecision === 'declined' || r.clientDecision === 'deferred');
  const replacements = recs.filter((r) => r.isReplacement);
  const patterned = recs.filter((r) => r.premiumMonthly > 0 && r.premiumEscalation > 0);
  const year = new Date().getFullYear();
  let plateNo = 0;
  const plate = () => ++plateNo;

  /* ---------------------------------------------------- the vessels */
  const estateStatus = (p: PersonAnalysis): Status => {
    const x = p.estate;
    return x.cashRequired <= 0 ? 'na' : x.liquidityShortfall <= 0 ? 'covered' : x.cashAvailable / x.cashRequired >= 0.6 ? 'partial' : 'shortfall';
  };
  const fromNeed = (n: NeedResult, label: string, key: string): VesselDatum => ({
    key,
    label,
    funded: n.funded,
    status: n.status,
    have: Math.min(n.provision, n.need),
    need: n.need,
    short: n.shortfall,
    monthly: n.monthly,
  });
  const personVessels = (p: PersonAnalysis): VesselDatum[] => [
    fromNeed(p.death, 'Life cover', 'life'),
    fromNeed(p.incomeProtection, 'Income if unable to work', 'ip'),
    fromNeed(p.disability, 'Disability lump sum', 'dis'),
    fromNeed(p.severeIllness, 'Severe illness', 'si'),
    fromNeed(p.funeral, 'Funeral', 'fun'),
    {
      key: 'estate',
      label: 'Cash for the estate',
      funded: p.estate.cashRequired > 0 ? Math.min(1, p.estate.cashAvailable / p.estate.cashRequired) : 0,
      status: estateStatus(p),
      have: Math.min(p.estate.cashAvailable, p.estate.cashRequired),
      need: p.estate.cashRequired,
      short: p.estate.liquidityShortfall,
    },
    {
      key: 'ret',
      label: 'Retirement savings',
      funded: p.retirement.funded,
      status: p.retirement.status,
      have: Math.min(p.retirement.projectedCapitalReal, p.retirement.requiredCapitalReal),
      need: p.retirement.requiredCapitalReal,
      short: Math.max(0, p.retirement.requiredCapitalReal - p.retirement.projectedCapitalReal),
    },
  ];
  const homeVessels: VesselDatum[] = [
    fromNeed(a.emergency, 'Emergency fund', 'em'),
    ...a.education.map((x, i) => ({
      key: `edu${i}`,
      label: `${x.name.split(' ')[0]}’s studies`,
      funded: x.funded,
      status: x.status,
      have: Math.max(0, x.capitalAtStart - x.shortfallAtStart),
      need: x.capitalAtStart,
      short: x.shortfallAtStart,
    })),
    ...a.goals.map((g, i) => ({ key: `goal${i}`, label: g.name, funded: g.funded, status: g.status, have: Math.max(0, g.futureCost - g.shortfall), need: g.futureCost, short: g.shortfall })),
  ];
  const homeRows: VesselDatum[][] = [];
  for (let i = 0; i < homeVessels.length; i += 7) homeRows.push(homeVessels.slice(i, i + 7));
  const allVessels = [...P.flatMap(personVessels), ...homeVessels];
  const opening = verdict(
    allVessels.map((v) => v.status),
    a.goals.length > 0,
  ).split(' ');
  const { attention, good } = matters(doc, a);

  /* ---------------------------------------------------- the household */
  const kids = doc.dependants.filter((d) => d.relationship === 'child');
  const others = doc.dependants.filter((d) => d.relationship !== 'child');
  const ageText = (dob: string) => {
    const n = ageOn(dob);
    return n === null || n === undefined ? '' : `aged ${n}`;
  };
  const owe = new Map<string, number>();
  for (const l of doc.liabilities) owe.set(LIABILITY_TYPE[l.type], (owe.get(LIABILITY_TYPE[l.type]) ?? 0) + l.balance);
  const oweList = [...owe].map(([label, value]) => ({ label, value })).sort((x, y) => y.value - x.value);
  const oweShown = oweList.length > 4 ? [...oweList.slice(0, 3), { label: 'Other debts', value: oweList.slice(3).reduce((s, x) => s + x.value, 0) }] : oweList;

  /* ---------------------------------------------------- the coins */
  const outgo = cf.livingExpenses + cf.debtRepayments + cf.riskPremiums + cf.savingsContributions;
  const per = (n: number) => `${money(n)} a month`;
  const coinParts = [
    { label: 'Living expenses', value: cf.livingExpenses, fill: PAT.cross, sub: per(cf.livingExpenses) },
    { label: 'Debt repayments', value: cf.debtRepayments, fill: PAT.hatch, sub: per(cf.debtRepayments) },
    { label: 'Insurance premiums', value: cf.riskPremiums, fill: PAT.dots, sub: per(cf.riskPremiums) },
    { label: 'Savings & investments', value: cf.savingsContributions, fill: PAT.horiz, sub: per(cf.savingsContributions) },
    cf.surplus >= 0
      ? { label: 'Left over', value: cf.surplus, fill: 'none' as const, sub: per(cf.surplus) }
      : { label: 'Spent beyond income', value: -cf.surplus, fill: PAT.red, red: true, sub: `${money(-cf.surplus)} a month more than comes in` },
  ];

  /* ---------------------------------------------------- the almanac */
  const events: AlmanacEvent[] = [
    ...a.goals.filter((g) => g.years > 0).map((g) => ({ year: year + g.years, label: g.name })),
    ...a.education.filter((x) => x.yearsToTertiary > 0 && x.capitalAtStart > 0).map((x) => ({ year: year + Math.round(x.yearsToTertiary), label: `${x.name.split(' ')[0]} begins studies` })),
    ...P.filter((p) => !p.retirement.retired).map((p) => ({ year: year + Math.round(p.retirement.yearsToRetirement), label: `${p.name.split(' ')[0]} retires at ${p.retirement.retirementAge}` })),
    ...P.filter((p) => p.retirement.depletionAge !== null && p.retirement.depletionAge < p.retirement.planningAge).map((p) => ({
      year: year + Math.round(p.retirement.depletionAge! - p.retirement.currentAge),
      label: `${p.name.split(' ')[0]}’s savings run out`,
      red: true,
    })),
  ];

  /* ---------------------------------------------------- advice */
  const recName = (r: Recommendation) => `${r.productType || NEED_AREA[r.area]}`;
  const amountText = (r: Recommendation) => (r.amount ? (r.area === 'income-protection' ? `${money(r.amount)} a month` : money(r.amount)) : '');
  const forHousehold = (r: Recommendation) => HOUSEHOLD_AREAS.includes(r.area) || !people.includes(r.lifeAssured);
  const recGroups = [
    ...people.map((k) => ({ key: k as string, title: `For ${first(doc, k)}`, items: recs.filter((r) => r.lifeAssured === k && !forHousehold(r)) })),
    { key: 'household', title: 'For the household', items: recs.filter(forHousehold) },
  ].filter((g) => g.items.length);
  /** Articles are numbered in the order they are printed. */
  const articleNo = new Map(recGroups.flatMap((g) => g.items).map((r, i) => [r.id, roman(i + 1)]));
  const riskIdx = (key?: string) => (key ? RISK_CATEGORIES.findIndex((c) => c.key === key) : -1);

  const contents: [string, string][] = [
    ['The Verdict, in Brief', 'Where do we stand?'],
    ['Your Present Situation', 'What do we own, earn and spend?'],
    ['Should the Worst Befall', 'What if one of us died, fell ill or could not work?'],
    ['The Years Ahead', 'Will our savings last? Are the studies paid for?'],
    ['Our Advice', 'What should we do, and what will it cost?'],
    ['Assumptions & Declarations', 'On what basis, and with what protections?'],
  ];

  return (
    <article className="vr-doc">
      {/* ============================================================ Title page */}
      <Leaf className="vr-title-page">
        <div className="vr-title-top">
          {firm && <span>{firm}</span>}
          <span>Private &amp; Confidential</span>
        </div>
        <div className="vr-title-block">
          {practice.logoDataUrl && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={practice.logoDataUrl} alt={firm || 'Practice logo'} className="vr-logo" />
          )}
          <div className="vr-title-kicker">A Financial Survey</div>
          <div className="vr-title-of">of</div>
          <h1 className="vr-title-main">{cap(houseName.replace(/^the /, 'The '))}</h1>
          <Ornament width={210} />
          <p className="vr-title-sub">
            being a needs analysis &amp; record of advice prepared for {household}, setting out what the family has, what it would need should death, disability or illness befall it, and what we
            advise.
          </p>
        </div>
        <nav className="vr-contents" aria-label="Contents">
          <div className="vr-contents-head">Contents</div>
          <Leaders rows={contents.map(([title, q], i) => ({ key: title, label: `${roman(i + 1)}.  ${title}`, value: <i>{q}</i> }))} />
        </nav>
        <div className="vr-imprint">
          <div>
            {doc.household.city ? `${doc.household.city}: p` : 'P'}repared on {periodDate(e.meetingDate)}
          </div>
          <div>
            by {practice.adviserName || 'your adviser'}
            {practice.adviserQualifications ? `, ${practice.adviserQualifications}` : ''}
            {practice.fspName ? ` · ${practice.fspName}` : ''}
            {practice.fspNumber ? ` · Authorised FSP ${practice.fspNumber}` : ''}
          </div>
          <div className="vr-imprint-small">
            {e.adviceType === 'limited' ? 'Limited-scope advice' : 'Comprehensive needs analysis'} · Tax tables: SARS {t.label}
          </div>
        </div>
      </Leaf>

      {/* ============================================================ I. Verdict */}
      <Leaf running={running('Chapter I · The Verdict, in Brief')}>
        <ChapterHead number={1} title="The Verdict, in Brief" sub="where you stand, on a page or two" />
        <Para
          drop
          note={
            <>
              <Gloss term="Provided for">already covered by insurance, savings or assets you hold today.</Gloss>
              <Gloss term="Falls well short">less than 60% of the need is in place.</Gloss>
            </>
          }
        >
          <p>
            <span className="vr-lead">{opening.slice(0, 4).join(' ')}</span> {opening.slice(4).join(' ')}{' '}
            {cf.surplus >= 0
              ? `Day to day, your finances balance: you take home ${money(cf.takeHome)} a month and keep ${money(cf.surplus)} once everything is paid.`
              : `Day to day, your finances are under strain: you take home ${money(cf.takeHome)} a month but spend ${money(-cf.surplus)} more than that.`}{' '}
            The plates that follow show each need as a picture, and the closing chapters set out what we advise and what it would cost.
          </p>
        </Para>

        <div className="vr-register" role="list">
          {[
            ['Net worth', moneyCompact(nw.netWorth), 'what you own less what you owe'],
            ['Take-home pay', money(cf.takeHome), 'a month, after tax'],
            [cf.surplus < 0 ? 'Monthly overspend' : 'Left over', money(Math.abs(cf.surplus)), 'a month, after all commitments'],
            ['Saved', pct(a.ratios.savingsRate), 'of gross income'],
          ].map(([label, value, sub]) => (
            <div key={label} role="listitem">
              <div className="vr-register-label">{label}</div>
              <div className={label === 'Monthly overspend' ? 'vr-register-value vr-red' : 'vr-register-value'}>{value}</div>
              <div className="vr-register-sub">{sub}</div>
            </div>
          ))}
        </div>

        <Plate
          number={plate()}
          title="The state of provision: how full is each vessel?"
          read="Each vessel stands for one need. The dashed line near its rim marks the whole amount needed; the liquid shows how much of it is already provided for. A full vessel is a need met — an empty one is a gap to close."
        >
          {P.map((p, i) => (
            <VesselRow key={p.key} id={`p${i}`} title={p.name.split(' ')[0]} sub={`aged ${Math.floor(p.income.exactAge || p.retirement.currentAge)}`} items={personVessels(p)} />
          ))}
          {homeRows.map((row, i) => (
            <VesselRow key={`h${i}`} id={`h${i}`} title={i === 0 ? 'Household' : ''} sub={i === 0 ? 'shared needs & goals' : undefined} items={row} />
          ))}
        </Plate>

        {attention.length > 0 && (
          <Keep>
            <Heading gloss="the most serious first">Matters that need attention</Heading>
            <ul className="vr-matters">
              {attention.slice(0, 7).map((m) => (
                <li key={m.text} className={m.tone === 'critical' ? 'is-critical' : undefined}>
                  <Manicule red={m.tone === 'critical'} />
                  <span>{m.text}</span>
                </li>
              ))}
            </ul>
          </Keep>
        )}
        {good.length > 0 && (
          <Keep>
            <Heading>Matters in good order</Heading>
            <ul className="vr-matters vr-matters-good">
              {good.slice(0, 4).map((m) => (
                <li key={m.text}>
                  <Fleuron />
                  <span>{m.text}</span>
                </li>
              ))}
            </ul>
          </Keep>
        )}
      </Leaf>

      {/* ============================================================ II. Situation */}
      <Leaf running={running('Chapter II · Your Present Situation')}>
        <ChapterHead number={2} title="Your Present Situation" sub="what you own, what you earn, and where it goes" />
        <Abstract>
          You take home {money(cf.takeHome)} a month. You own {said(nw.assets + nw.retirement)} and owe {said(nw.liabilities)}, which leaves a net worth of {said(nw.netWorth)}.
        </Abstract>

        <Plate number={plate()} title="The household">
          <FamilyTree
            adults={people.map((k) => ({
              name: personNameOf(doc, k),
              sub: [ageText(doc[k].dateOfBirth), doc[k].occupation].filter(Boolean).join(', ') || (k === 'client' ? 'client' : 'spouse'),
            }))}
            union={
              couple && doc.household.maritalStatus === 'married'
                ? `married${doc.household.dateOfMarriage ? ` ${doc.household.dateOfMarriage.slice(0, 4)}` : ''} · ${MARITAL_REGIME[doc.household.maritalRegime].toLowerCase()}`
                : couple
                  ? MARITAL_STATUS[doc.household.maritalStatus].toLowerCase()
                  : undefined
            }
            children={kids.map((d) => ({ name: d.name.split(' ')[0] || 'Child', sub: ageText(d.dateOfBirth) || 'child' }))}
            others={others.map((d) => ({
              name: d.name || RELATIONSHIP[d.relationship],
              sub: `${RELATIONSHIP[d.relationship].toLowerCase()}${ageText(d.dateOfBirth) ? `, ${ageText(d.dateOfBirth)}` : ''}`,
            }))}
          />
        </Plate>

        {(e.reasonForAdvice || e.clientObjectives || e.clientConcerns) && (
          <Para note={<Gloss term="In your words">as recorded at our meeting; tell us if anything has changed.</Gloss>}>
            {e.reasonForAdvice && (
              <p>
                <span className="vr-runin">Why we met.</span> {e.reasonForAdvice}
              </p>
            )}
            {e.clientObjectives && (
              <p>
                <span className="vr-runin">What you want.</span> {e.clientObjectives}
              </p>
            )}
            {e.clientConcerns && (
              <p>
                <span className="vr-runin">What concerns you.</span> {e.clientConcerns}
              </p>
            )}
          </Para>
        )}

        <Keep>
          <Heading gloss="the household’s balance sheet">What you own, and what you owe</Heading>
          <Plate
            number={plate()}
            title="The household in the balance"
            read="Everything you own sits in the left pan, everything you owe in the right. The beam tips toward the heavier side; the plaque on the pillar is the difference — your net worth."
          >
            <Balance own={nw.byGroup.filter((g) => g.amount > 0).map((g) => ({ label: g.label, value: g.amount }))} owe={oweShown} />
          </Plate>
        </Keep>
        <div className="vr-two">
          <div>
            <div className="vr-subhead">Owned</div>
            <Leaders
              rows={[
                ...nw.byGroup.filter((g) => g.amount > 0).map((g) => ({ key: g.key, label: g.label, value: money(g.amount) })),
                { key: 'tot', label: 'Total owned', value: money(nw.assets + nw.retirement), tone: 'strong' as const },
              ]}
            />
          </div>
          <div>
            <div className="vr-subhead">Owed</div>
            <Leaders
              rows={[
                ...doc.liabilities.map((l) => ({ key: l.id, label: `${l.description || LIABILITY_TYPE[l.type]}`, value: money(l.balance), sub: `at ${pct(l.interestRate, 1)} interest` })),
                { key: 'tot', label: 'Total owed', value: money(nw.liabilities), tone: 'strong' as const },
              ]}
            />
          </div>
        </div>

        <Keep>
          <Heading gloss="how much of each salary reaches the bank">From pay-slip to bank account</Heading>
          <Plate number={plate()} title="Gross income, divided into what is deducted and what is paid out">
            <Strips
              rows={a.incomes.map((i) => ({
                label: i.name.split(' ')[0],
                parts: [
                  { label: 'Take-home pay', value: i.takeHomeMonthly, fill: PAT.cross },
                  { label: 'Income tax (PAYE) & UIF', value: i.payeMonthly + i.uifMonthly, fill: PAT.dots },
                  { label: 'Retirement, medical & other deductions', value: i.retirementPayrollMonthly + i.medicalPayrollMonthly + i.otherDeductionsMonthly, fill: PAT.hatch },
                ],
                total: `${money(i.grossMonthly)} gross`,
              }))}
            />
            <Key
              items={[
                { label: 'take-home pay', fill: PAT.cross },
                { label: 'income tax (PAYE) & UIF', fill: PAT.dots },
                { label: 'retirement, medical & other deductions', fill: PAT.hatch },
              ]}
            />
          </Plate>
        </Keep>
        <VTable
          head={['A month', ...names]}
          align={['l', ...names.map(() => 'r' as const)]}
          rows={[
            ['Gross income', ...a.incomes.map((i) => money(i.grossMonthly))],
            ['Less income tax (PAYE) & UIF', ...a.incomes.map((i) => money(-(i.payeMonthly + i.uifMonthly)))],
            ['Less retirement, medical & other deductions', ...a.incomes.map((i) => money(-(i.retirementPayrollMonthly + i.medicalPayrollMonthly + i.otherDeductionsMonthly)))],
            ['Take-home pay', ...a.incomes.map((i) => money(i.takeHomeMonthly))],
            ['Tax on the next rand earned (marginal rate)', ...a.incomes.map((i) => pct(i.tax.marginalRate))],
          ]}
          strong={[3]}
        />

        <Keep>
          <Heading gloss="the month’s take-home pay, divided">Where the money goes</Heading>
          <Plate
            number={plate()}
            title={cf.surplus >= 0 ? 'Of every R100 that comes home' : 'Of every R100 that goes out'}
            read={
              cf.surplus >= 0
                ? 'A tray of a hundred coins stands for one month’s take-home pay. Each coin is one rand in every hundred; the blank coins are what is left over.'
                : 'A tray of a hundred coins stands for one month’s spending. The red coins are spending that the month’s take-home pay does not cover.'
            }
          >
            <CoinGrid parts={coinParts.map((c) => ({ ...c, value: Math.max(0, c.value) }))} />
          </Plate>
        </Keep>
        {cf.byCategory.some((c) => c.amount > 0) && (
          <Plate number={plate()} title="Living expenses, by kind" read={`The month’s living expenses of ${money(cf.livingExpenses)}, largest first.`}>
            <CatalogueBars
              items={[...cf.byCategory]
                .filter((c) => c.amount > 0)
                .sort((x, y) => y.amount - x.amount)
                .map((c) => ({ label: c.label, value: c.amount }))}
            />
          </Plate>
        )}

        <Keep>
          <Heading gloss="five common rules of thumb">Measures of financial health</Heading>
          <Plate
            number={plate()}
            title="Five instruments, each read against its guide"
            read="Each scale is marked with the range generally regarded as healthy (hatched). The pointer is where you stand; a red pointer lies outside the guide."
          >
            <Instruments
              items={[
                { label: 'Debt repayments', value: a.ratios.debtToIncome, display: pct(a.ratios.debtToIncome), good: { max: 0.36 }, guide: 'under 36% of income', scaleMax: 0.6, tick: (v) => pct(v) },
                {
                  label: 'Housing costs',
                  value: a.ratios.housingToIncome,
                  display: pct(a.ratios.housingToIncome),
                  good: { max: 0.3 },
                  guide: 'under 30% of income',
                  scaleMax: 0.5,
                  tick: (v) => pct(v),
                },
                { label: 'Saving', value: a.ratios.savingsRate, display: pct(a.ratios.savingsRate), good: { min: 0.15 }, guide: '15% of income or more', scaleMax: 0.4, tick: (v) => pct(v) },
                {
                  label: 'Insurance premiums',
                  value: a.ratios.premiumsToIncome,
                  display: pct(a.ratios.premiumsToIncome, 1),
                  good: { min: 0.05, max: 0.1 },
                  guide: '5–10% of income',
                  scaleMax: 0.2,
                  tick: (v) => pct(v),
                },
                {
                  label: 'Emergency fund',
                  value: a.ratios.emergencyMonths,
                  display: `${a.ratios.emergencyMonths.toFixed(1)} months`,
                  good: { min: as.emergencyMonths },
                  guide: `${as.emergencyMonths} months of essentials or more`,
                  scaleMax: Math.max(as.emergencyMonths * 2, Math.ceil(a.ratios.emergencyMonths) + 1),
                  tick: (v) => `${Math.round(v)} mo`,
                },
              ]}
            />
          </Plate>
        </Keep>
        <Keep>
          <Heading gloss="policies already in force">Cover you already hold</Heading>
          {doc.policies.filter((p) => people.includes(p.lifeAssured)).length ? (
            <VTable
              head={['Life', 'Benefit', 'Insurer', 'Cover', 'Paid to', 'Premium']}
              align={['l', 'l', 'l', 'r', 'l', 'r']}
              rows={doc.policies
                .filter((p) => people.includes(p.lifeAssured))
                .map((p) => [
                  first(doc, p.lifeAssured),
                  `${POLICY_TYPE[p.type]}${p.isGroup ? ' (employer)' : ''}`,
                  p.insurer || '—',
                  p.type === 'income-protection' || p.type === 'family-income' ? `${money(p.monthlyBenefit)} pm` : money(p.cover),
                  p.type === 'life' ? BENEFICIARY[p.beneficiary].replace(' (no nomination)', '') : '—',
                  p.isGroup ? 'employer' : money(p.premiumMonthly),
                ])}
            />
          ) : (
            <p className="vr-quiet">No existing policies were recorded.</p>
          )}
        </Keep>
      </Leaf>

      {/* ============================================================ III. Protection */}
      <Leaf running={running('Chapter III · Should the Worst Befall')}>
        <ChapterHead number={3} title="Should the Worst Befall" sub="death, disability, severe illness, and the loss of income" />
        <Abstract>
          For each misfortune we worked out what {couple ? 'the family' : 'you'} would need, then subtracted the cover and savings already in place. What remains — drawn in red throughout — is the
          gap.
        </Abstract>
        <Para
          note={
            <>
              <Gloss term="Present value">the lump sum that, invested today, would pay a rising income for the years it is needed.</Gloss>
              <Gloss term="Shortfall">what would be needed, less what is already in place.</Gloss>
            </>
          }
        >
          <p>
            An income your family would need for many years is turned into a single lump sum: the amount that, invested at {pct(as.riskCapitalReturn, 1)} a year, would pay{' '}
            {pct(as.deathIncomeReplacement)} of the lost after-tax income, rising each year with inflation of {pct(as.cpi, 1)}. Debts, education and the costs of winding up the estate are added to it.
          </p>
        </Para>

        <Keep>
          <Heading id="life" gloss="if death occurred today">
            If one of you died
          </Heading>
          {P.some((p) => p.death.need > 0) && (
            <Plate
              number={plate()}
              title="What the family would need, beside what is in place"
              read="The left column of each pair is the need, built up from its parts; the right is what is already in place. The red dashed block is the gap that new cover would have to fill."
            >
              <NeedColumns
                groups={P.map((p) => ({
                  title: p.name.split(' ')[0],
                  sub: `should ${p.name.split(' ')[0]} die`,
                  needed: topSegments(p.death.needLines, 5, 'Other needs').map((s): ColSeg => ({ label: s.label, value: s.value })),
                  held: [...topSegments(p.death.provisionLines, 3, 'Other cover').map((s): ColSeg => ({ label: s.label, value: s.value })), { label: 'The gap', value: p.death.shortfall, gap: true }],
                }))}
              />
            </Plate>
          )}
        </Keep>
        <NeedTable names={names} needs={P.map((p) => p.death)} />
        <p className="vr-small">
          Family income is taken as {pct(as.deathIncomeReplacement)} of after-tax income for {P.map((p) => `${p.name.split(' ')[0]} ${p.deathIncomeYears} years`).join(' and ')}. Retirement-fund death
          benefits are shared out by the fund’s trustees (section 37C of the Pension Funds Act); they are shown after tax and are not available to the executor.
        </p>

        <Keep>
          <Heading id="ip" gloss="a month’s income, after tax">
            If you could not work
          </Heading>
          <Plate
            number={plate()}
            title="Income that would stop, and the part that is insured"
            read="Each bar is the monthly income to protect. The dark part is already insured; the red part would stop if illness or injury kept you from working."
          >
            <GapBars monthly rows={P.map((p) => ({ label: p.name.split(' ')[0], sub: 'monthly income, after tax', need: p.incomeProtection.need, have: p.incomeProtection.provision }))} />
          </Plate>
        </Keep>
        <Keep>
          <Heading id="lump" gloss="lump sums paid on diagnosis or disablement">
            Disability, severe illness and funeral
          </Heading>
          <Plate
            number={plate()}
            title="Each lump-sum need as a bar: the part in place, then the gap"
            read="All bars share one scale, so their lengths can be compared. A bar with no dark part has no cover in place at all."
          >
            <GapBars
              rows={P.flatMap((p) => [
                { label: 'Disability', sub: p.name.split(' ')[0], need: p.disability.need, have: p.disability.provision },
                { label: 'Severe illness', sub: p.name.split(' ')[0], need: p.severeIllness.need, have: p.severeIllness.provision },
                { label: 'Funeral', sub: p.name.split(' ')[0], need: p.funeral.need, have: p.funeral.provision },
              ])}
            />
          </Plate>
        </Keep>
        <NeedTable names={names} needs={P.map((p) => p.disability)} />

        <Keep>
          <Heading id="estate" gloss="what must be paid before anyone inherits">
            Your estate
          </Heading>
          <Para
            note={
              <>
                <Gloss term="Executor">the person who winds up the estate: pays debts and costs, then hands over what remains.</Gloss>
                <Gloss term="Estate duty">a tax of 20% on estates above R3.5 million (25% above R30 million); what passes to a spouse is free of it.</Gloss>
              </>
            }
          >
            <p>
              When someone dies, their bank accounts are frozen and the executor must find cash for debts, fees and taxes before the heirs receive anything. Policies paid directly to a named
              beneficiary do not pass through the estate, so they cannot be used by the executor. Where the cash falls short, assets — sometimes the family home — may have to be sold.
            </p>
          </Para>
        </Keep>
        {P.map((p) => (
          <ExecutorAccount key={p.key} p={p} name={p.name.split(' ')[0]} />
        ))}
        <VTable
          head={['', ...names]}
          align={['l', ...names.map(() => 'r' as const)]}
          rows={[
            ['Assets passing through the executor', ...P.map((p) => money(p.estate.grossEstate))],
            ['Policies paid to nominees (counted for estate duty)', ...P.map((p) => money(p.estate.deemedProperty))],
            ['Net estate', ...P.map((p) => money(p.estate.netEstate))],
            ['Estate duty', ...P.map((p) => money(p.estate.estateDuty))],
            ['Capital gains tax on death', ...P.map((p) => money(p.estate.cgt))],
            [
              'Will',
              ...people.map((k) =>
                doc.estate[k].hasWill === 'yes' ? (
                  `yes${doc.estate[k].willDate ? `, ${doc.estate[k].willDate.slice(0, 4)}` : ''}`
                ) : doc.estate[k].hasWill === 'no' ? (
                  <span className="vr-red">none</span>
                ) : (
                  'unconfirmed'
                ),
              ),
            ],
          ]}
          strong={[2]}
        />
        <p className="vr-small">{DISCLAIMERS.estate}</p>
      </Leaf>

      {/* ============================================================ IV. Years ahead */}
      <Leaf running={running('Chapter IV · The Years Ahead')}>
        <ChapterHead number={4} title="The Years Ahead" sub="retirement, the children’s studies, and the goals you have set" />
        <Abstract>
          {P.filter((p) => !p.retirement.retired)
            .map(
              (p) =>
                `${p.name.split(' ')[0]}’s savings are on course to pay ${pct(p.retirement.targetMonthlyToday > 0 ? p.retirement.sustainableMonthlyToday / p.retirement.targetMonthlyToday : 0)} of the retirement income wanted${p.retirement.additionalMonthly > 0 ? `; saving a further ${money(p.retirement.additionalMonthly)} a month would close the gap` : ''}.`,
            )
            .join(' ') || 'Retirement income is shown below.'}
        </Abstract>
        <Para
          note={
            <>
              <Gloss term="Today’s money">every amount is shown at today’s prices, so it can be compared with what things cost now.</Gloss>
              <Gloss term="Drawdown">the share of savings taken as income each year; about 4–5% is generally regarded as sustainable.</Gloss>
            </>
          }
        >
          <p>
            Savings are assumed to grow at {pct(as.preRetirementReturn, 1)} a year before retirement, with contributions rising {pct(as.salaryEscalation, 1)} a year, and at{' '}
            {pct(as.postRetirementReturn, 1)} a year afterwards. The income wanted rises with inflation and must last until the planning age. These are illustrations, not guarantees.
          </p>
        </Para>

        {P.map((p) => (
          <Plate
            key={p.key}
            number={plate()}
            title={`${p.name.split(' ')[0]}’s retirement savings, year by year`}
            read={
              p.retirement.depletionAge !== null && p.retirement.depletionAge < p.retirement.planningAge
                ? `The curve rises while ${p.name.split(' ')[0]} saves and falls once the income wanted is drawn. Drawn in full, it touches zero at about ${Math.floor(p.retirement.depletionAge)}, before the planning age of ${p.retirement.planningAge}.`
                : `The curve rises while ${p.name.split(' ')[0]} saves and falls once the income wanted is drawn; it stays above zero to the planning age of ${p.retirement.planningAge}.`
            }
          >
            <RetirementPaper r={p.retirement} name={p.name.split(' ')[0]} />
          </Plate>
        ))}

        {P.some((p) => p.retirement.targetMonthlyToday > 0) && (
          <Plate
            number={plate()}
            title="How much of the income wanted could the savings pay?"
            read="Read each dial like a gauge: the needle points to the share of the wanted retirement income that the projected savings could sustain. Below 60% (red band) the gap is serious."
          >
            <div className="vr-dials">
              {P.filter((p) => p.retirement.targetMonthlyToday > 0).map((p) => (
                <div key={p.key} className="vr-dial-cell">
                  <Dial id={p.key} ratio={p.retirement.sustainableMonthlyToday / p.retirement.targetMonthlyToday} caption={p.name.split(' ')[0]} />
                  <div className="vr-dial-name">{p.name.split(' ')[0]}</div>
                  <div className="vr-dial-sub">
                    {money(p.retirement.sustainableMonthlyToday)} of the {money(p.retirement.targetMonthlyToday)} a month wanted
                  </div>
                </div>
              ))}
            </div>
          </Plate>
        )}
        <VTable
          head={['', ...names]}
          align={['l', ...names.map(() => 'r' as const)]}
          rows={[
            ['Retires at / plans to age', ...P.map((p) => `${p.retirement.retirementAge} / ${p.retirement.planningAge}`)],
            ['Retirement savings today', ...P.map((p) => money(p.retirement.currentCapital))],
            ['Contributions a month, with employer’s', ...P.map((p) => money(p.retirement.monthlyContributions))],
            ['Income wanted a month', ...P.map((p) => `${money(p.retirement.targetMonthlyToday)} (${pct(p.retirement.targetReplacementRatio)})`)],
            ['Savings expected at retirement', ...P.map((p) => money(p.retirement.projectedCapitalReal))],
            ['Savings needed at retirement', ...P.map((p) => money(p.retirement.requiredCapitalReal))],
            ['Income the savings could pay a month', ...P.map((p) => money(p.retirement.sustainableMonthlyToday))],
            ['First-year drawdown needed', ...P.map((p) => pct(p.retirement.initialDrawdown, 1))],
            ['Extra saving needed a month (rising yearly)', ...P.map((p) => (p.retirement.additionalMonthly > 0 ? money(p.retirement.additionalMonthly) : 'none'))],
          ]}
          strong={[8]}
        />

        {events.length > 0 && (
          <Keep>
            <Heading gloss="the coming events, on one line of years">An almanac of the years ahead</Heading>
            <Plate number={plate()} title="What falls due, and when" read="Each mark is an event you are planning for; red marks a danger to avoid.">
              <Almanac start={year} events={events} />
            </Plate>
          </Keep>
        )}

        {(a.education.length > 0 || a.goals.length > 0) && (
          <Keep>
            <Heading gloss="what each will cost, and what to set aside">Studies and goals</Heading>
            <Leaders
              rows={[
                ...a.education.map((x) => ({
                  key: x.dependantId,
                  label: `${x.name.split(' ')[0]}’s studies${x.capitalAtStart > 0 && x.yearsToTertiary > 0 ? `, from ${year + Math.round(x.yearsToTertiary)}` : ''}`,
                  value: x.capitalAtStart > 0 ? `${money(x.capitalAtStart)} needed` : '—',
                  sub: (
                    <>
                      <span className={x.status === 'shortfall' ? 'vr-red' : undefined}>{pct(x.funded)} provided for</span> ·{' '}
                      {x.lumpSumRequired > 0 ? `${money(x.lumpSumRequired)} needed now` : x.monthlyRequired > 0 ? `save ${money(x.monthlyRequired)} a month to close the gap` : 'fully funded'} ·
                      education inflation {pct(as.educationInflation, 1)}
                    </>
                  ),
                })),
                ...a.goals.map((g) => ({
                  key: g.id,
                  label: `${g.name}, in ${year + g.years}`,
                  value: `${money(g.futureCost)} needed`,
                  sub: (
                    <>
                      <span className={g.status === 'shortfall' ? 'vr-red' : undefined}>{pct(g.funded)} provided for</span> ·{' '}
                      {g.monthlyRequired > 0 ? `save ${money(g.monthlyRequired)} a month to close the gap` : 'fully funded'}
                    </>
                  ),
                })),
              ]}
            />
          </Keep>
        )}

        <Keep>
          <Heading gloss="how much investment risk suits you">Your investor profile</Heading>
          {a.risk.profile ? (
            <>
              <Plate number={plate()} title="The scale of five">
                <RiskScale cats={RISK_CATEGORIES} profile={riskIdx(a.risk.profile.key)} tolerance={riskIdx(a.risk.tolerance?.key)} capacity={riskIdx(a.risk.capacity?.key)} />
              </Plate>
              <Para note={<Gloss term="Tolerance and capacity">how much loss you are willing to bear, and how much your finances can bear. We take the more cautious of the two.</Gloss>}>
                <p>
                  We recommend a <b>{a.risk.profile.label.toLowerCase()}</b> profile. {a.risk.profile.description} Such a portfolio typically holds {a.risk.profile.equity}, suits a horizon of{' '}
                  {a.risk.profile.horizon}, and aims for {a.risk.profile.target}.
                </p>
              </Para>
            </>
          ) : (
            <p className="vr-quiet">The risk-profile questionnaire was not completed.</p>
          )}
        </Keep>
      </Leaf>

      {/* ============================================================ V. Advice */}
      <Leaf running={running('Chapter V · Our Advice')}>
        <ChapterHead number={5} title="Our Advice" sub="what we recommend, why, and what it would cost" />
        <Abstract>
          {recs.length
            ? `We make ${recs.length === 1 ? 'one recommendation' : `${recs.length} recommendations`}. Those not declined would add ${money(newCommitments)} a month${quotes ? `, before the ${quotes === 1 ? 'premium' : `${quotes} premiums`} still to be quoted by insurers` : ''}.`
            : 'No recommendations have yet been recorded.'}
        </Abstract>
        <Para note={<Gloss term="Record of advice">the written account of our advice that the FAIS General Code of Conduct (section 9) requires us to give you.</Gloss>}>
          <p>
            <span className="vr-runin">Scope.</span> {e.adviceType === 'limited' ? 'Limited advice' : 'A comprehensive needs analysis'} covering{' '}
            {e.scope.map((s) => SCOPE_AREA[s].toLowerCase()).join('; ') || '—'}. We met {e.meetingType === 'in-person' ? 'in person' : e.meetingType === 'virtual' ? 'by video' : 'by telephone'} on{' '}
            {dateLong(e.meetingDate)}.
          </p>
          {doc.advice.summary && <p className="vr-pre">{doc.advice.summary}</p>}
        </Para>
        {e.adviceType === 'limited' && (
          <div className="vr-caution">
            <Manicule red />
            <div>
              {e.limitations && <b>{e.limitations} </b>}
              {DISCLAIMERS.limited}
            </div>
          </div>
        )}
        {e.clientDeclinedFna && (
          <div className="vr-caution">
            <Manicule red />
            <div>
              {e.declineReason && <b>{e.declineReason} </b>}
              {DISCLAIMERS.declined}
            </div>
          </div>
        )}

        {recGroups.map((g) => (
          <div key={g.key} className="vr-articles">
            <Heading>{g.title}</Heading>
            <ol>
              {g.items.map((r) => (
                <li key={r.id}>
                  <div className="vr-article-head">
                    <span className="vr-article-no">{articleNo.get(r.id)}.</span>
                    <span className="vr-article-title">
                      {ACTION[r.action]} — {recName(r)}
                    </span>
                    <span className="vr-dots" aria-hidden />
                    <span className="vr-article-amount">{amountText(r) || <i>by referral</i>}</span>
                  </div>
                  <div className="vr-article-meta">
                    {r.premiumMonthly ? `${money(r.premiumMonthly)} a month` : r.action === 'refer' ? 'fee on referral' : 'premium to be quoted'}
                    {r.provider ? ` · ${r.provider}` : ''} · <span className={r.clientDecision === 'declined' ? 'vr-red' : undefined}>{r.clientDecision}</span>
                  </div>
                  {r.rationale && <p className="vr-article-why">{r.rationale}</p>}
                  {r.clientReason && r.clientDecision !== 'accepted' && <p className="vr-article-why">Your reason: {r.clientReason}</p>}
                  {r.isReplacement && (
                    <div className="vr-caution vr-caution-small">
                      <Manicule red />
                      <div>
                        <b>Replaces {r.replacedPolicy || 'an existing product'}.</b> {r.replacementCostComparison && `Cost: ${r.replacementCostComparison}. `}
                        {r.replacementConsequences && `Consequences: ${r.replacementConsequences}. `}
                        {r.replacementReasons && `Why more suitable: ${r.replacementReasons}. `}
                        Factors discussed:{' '}
                        {REPLACEMENT_FACTORS.filter((f) => r.replacementChecklist.includes(f.key))
                          .map((f) => f.label.toLowerCase())
                          .join('; ') || 'none recorded'}
                        .
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ol>
          </div>
        ))}
        {replacements.length > 0 && <p className="vr-small">{DISCLAIMERS.replacement}</p>}

        {patterned.length > 0 && (
          <>
            <Keep>
              <Heading gloss="premiums and contributions that rise each year">What the advice would cost, year by year</Heading>
              <Plate
                number={plate()}
                title="The monthly cost in each year of the plan"
                read="Each column is one year’s monthly cost, stacked by recommendation. Amounts are shown at the escalation each provider quoted."
              >
                <CostPaper series={patterned.map((r) => ({ label: recName(r), monthly: r.premiumMonthly, escalation: r.premiumEscalation }))} />
                <Key items={patterned.map((r, i) => ({ label: `${articleNo.get(r.id)}. ${recName(r)} (${first(doc, r.lifeAssured)})`, fill: TONES[i % TONES.length] }))} />
              </Plate>
            </Keep>
            <VTable
              head={['Recommendation', ...PATTERN_YEARS.map((y) => `Yr ${y}`)]}
              align={['l', ...PATTERN_YEARS.map(() => 'r' as const)]}
              rows={patterned.map((r) => [
                `${articleNo.get(r.id)}. ${recName(r)} (${first(doc, r.lifeAssured)}, +${pct(r.premiumEscalation, 1)})`,
                ...PATTERN_YEARS.map((y) => money(r.premiumMonthly * Math.pow(1 + r.premiumEscalation, y - 1))),
              ])}
              className="vr-table-tight"
            />
          </>
        )}

        {recs.length > 0 && (
          <Keep>
            <Heading gloss="before and after the recommendations">The monthly budget</Heading>
            <div className="vr-budget">
              <Leaders
                rows={[
                  { label: 'Take-home pay', value: money(cf.takeHome) },
                  { label: 'Less what is spent and saved today', value: money(-outgo) },
                  { label: cf.surplus >= 0 ? 'Left over today' : 'Overspent today', value: money(cf.surplus), tone: cf.surplus < 0 ? 'red' : 'strong' },
                  { label: 'Less the new commitments recommended', value: money(-newCommitments) },
                  {
                    label: cf.surplus - newCommitments >= 0 ? 'Left over after the advice' : 'Overspent after the advice',
                    value: money(cf.surplus - newCommitments),
                    tone: cf.surplus - newCommitments < 0 ? 'red' : 'strong',
                    sub:
                      cf.surplus - newCommitments < 0
                        ? `The recommendations cannot all be paid from monthly income${cf.bonusNet > 0 ? ` (the yearly bonus of ${money(cf.bonusNet)} after tax could fund some)` : ''}; we will agree priorities with you.${quotes ? ' Premiums still to be quoted are not yet included.' : ''}`
                        : quotes
                          ? 'Premiums still to be quoted are not yet included.'
                          : undefined,
                  },
                ]}
              />
            </div>
          </Keep>
        )}

        {(doc.advice.productsConsidered || declined.length > 0 || doc.advice.clientDeviations) && (
          <Para>
            {doc.advice.productsConsidered && (
              <p>
                <span className="vr-runin">Considered, but not recommended.</span> {doc.advice.productsConsidered}
              </p>
            )}
            {(declined.length > 0 || doc.advice.clientDeviations) && (
              <p>
                <span className="vr-runin">Where you chose otherwise.</span> {declined.map((r) => `${recName(r)} for ${first(doc, r.lifeAssured)} (${r.clientDecision})`).join('; ')}
                {declined.length && doc.advice.clientDeviations ? '. ' : ''}
                {doc.advice.clientDeviations} {DISCLAIMERS.departure}
              </p>
            )}
          </Para>
        )}
        <Para>
          <p>
            <span className="vr-runin">Fees and commission.</span> {doc.advice.feesAndCommission || '—'}
          </p>
          <p>
            <span className="vr-runin">Conflicts of interest.</span> {doc.advice.conflictsOfInterest || 'None identified in respect of this advice.'}
            {doc.advice.nextReviewDate && ` We will review this plan with you by ${dateLong(doc.advice.nextReviewDate)}.`}
          </p>
        </Para>
      </Leaf>

      {/* ============================================================ VI. Assumptions & declarations */}
      <Leaf running={running('Chapter VI · Assumptions & Declarations')} className="vr-continue">
        <Keep>
          <ChapterHead number={6} title="Assumptions & Declarations" sub="the basis of the figures, and the protections you have" />
          <div className="vr-two">
            <VTable
              caption="Table of assumptions — economic"
              head={['', 'a year']}
              align={['l', 'r']}
              rows={[
                ['Inflation (CPI)', pct(as.cpi, 1)],
                ['Salary & contribution growth', pct(as.salaryEscalation, 1)],
                ['Return before retirement', pct(as.preRetirementReturn, 1)],
                ['Return after retirement', pct(as.postRetirementReturn, 1)],
                ['Return on capital for dependants', pct(as.riskCapitalReturn, 1)],
                ['Education inflation', pct(as.educationInflation, 1)],
              ]}
            />
            <VTable
              caption="Table of assumptions — needs"
              head={['', 'measured as']}
              align={['l', 'r']}
              rows={[
                ['Family income on death', `${pct(as.deathIncomeReplacement)} of after-tax income`],
                ['Income protection', `${pct(as.incomeProtectionTarget)} of after-tax income`],
                ['Severe illness', `${as.severeIllnessMonths} months’ gross income`],
                ['Emergency fund', `${as.emergencyMonths} months of essentials`],
                ['Tax tables', `SARS ${t.label}`],
              ]}
            />
          </div>
        </Keep>

        <div className="vr-two">
          <div>
            <Heading>Your financial services provider</Heading>
            <Leaders
              rows={[
                { label: 'Provider', value: [practice.fspName, practice.fspNumber && `FSP ${practice.fspNumber}`].filter(Boolean).join(' · ') || '—' },
                { label: 'Representative', value: [practice.adviserName, practice.representativeNumber].filter(Boolean).join(' · ') || '—' },
                { label: 'Supervision', value: practice.underSupervision ? `under ${practice.supervisor || '—'}` : 'none' },
                { label: 'Professional indemnity', value: practice.piCover || 'held' },
                { label: 'Compliance officer', value: [practice.complianceOfficer, practice.complianceOfficerContact].filter(Boolean).join(' · ') || '—' },
                { label: 'Contact', value: [practice.adviserPhone, practice.adviserEmail].filter(Boolean).join(' · ') || '—' },
              ]}
            />
            {practice.categories && <p className="vr-small">Licensed: {practice.categories}</p>}
          </div>
          <div>
            <Heading>Should you wish to complain</Heading>
            <p className="vr-small vr-left">
              Write first to {practice.complaintsContact || practice.complianceOfficerContact || 'the provider'}. If the matter is not resolved within six weeks, you may approach:
            </p>
            <ul className="vr-ombuds">
              {OMBUDS.map((o) => (
                <li key={o.name}>
                  <span className="vr-sc">{o.name}</span>
                  <br />
                  <span>{o.contact}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <Keep>
          <Heading>Disclosures</Heading>
          <div className="vr-cols vr-small">
            <p>{DISCLAIMERS.projections}</p>
            <p>{DISCLAIMERS.pastPerformance}</p>
            <p>{DISCLAIMERS.tax}</p>
            <p>{DISCLAIMERS.nonDisclosure}</p>
            <p>{DISCLAIMERS.popia}</p>
            <p>{DISCLAIMERS.records}</p>
          </div>
        </Keep>
        <Keep>
          <Heading>Declarations</Heading>
          <Para>
            <p>
              <span className="vr-runin">By the client.</span> I confirm that the information I provided is true and complete; that I received the provider’s and representative’s disclosures; that the
              advice, its costs and any replacement consequences were explained in a way I understood; that I received a copy of this record; and that I consent to the processing of my personal
              information as described.
            </p>
            <p>
              <span className="vr-runin">By the adviser.</span> I confirm that I analysed the client’s needs, objectives, financial situation and risk profile, and recommended appropriate products in
              accordance with the FAIS Act and General Code of Conduct.
            </p>
          </Para>
        </Keep>
        <div className="vr-signs">
          {people.map((k) => (
            <Signature
              key={k}
              role={k === 'client' ? 'Signed by the client' : 'Signed by the spouse'}
              name={fullName(doc, k)}
              date={doc.advice.clientSignedAt ? dateLong(doc.advice.clientSignedAt) : undefined}
            />
          ))}
          <Signature role="Signed by the adviser" name={practice.adviserName || '—'} date={doc.advice.adviserSignedAt ? dateLong(doc.advice.adviserSignedAt) : undefined} />
        </div>
        <p className="vr-finis">Finis.</p>
      </Leaf>

      {/* ============================================================ Appendix */}
      {appendix && (
        <Leaf running={running('Appendix · The Workings')}>
          <ChapterHead kicker="Appendix" title="The Workings" sub="line-by-line calculations and the full register, for the adviser’s file" />
          {P.map((p) => (
            <div key={p.key}>
              <Heading>{p.name}</Heading>
              <div className="vr-two">
                {[p.death, p.incomeProtection, p.disability, p.severeIllness, p.funeral].map((n) => (
                  <div key={n.area} className="vr-working">
                    <div className="vr-subhead">{n.title}</div>
                    <Leaders
                      rows={[
                        ...n.needLines.map((l) => ({ label: l.label, value: money(l.amount) })),
                        { label: 'Total needed', value: money(n.need), tone: 'strong' as const },
                        ...n.provisionLines.map((l) => ({ label: `Less ${l.label.charAt(0).toLowerCase()}${l.label.slice(1)}`, value: money(-l.amount) })),
                        {
                          label: n.shortfall > 0.5 ? 'Shortfall' : 'Surplus',
                          value: money(n.shortfall > 0.5 ? n.shortfall : n.provision - n.need),
                          tone: n.shortfall > 0.5 ? ('red' as const) : ('strong' as const),
                        },
                      ]}
                    />
                    {n.notes.length > 0 && <p className="vr-small vr-left">{n.notes.join(' ')}</p>}
                  </div>
                ))}
                <div className="vr-working">
                  <div className="vr-subhead">Estate costs</div>
                  <Leaders rows={p.estate.lines.filter((l) => l.amount > 0).map((l) => ({ label: l.label, value: money(l.amount) }))} />
                </div>
              </div>
            </div>
          ))}
          <Heading>Register of assets</Heading>
          <VTable
            head={['Asset', 'Kind', 'Owner', 'Value', 'Base cost']}
            align={['l', 'l', 'l', 'r', 'r']}
            rows={[
              ...doc.assets.map((x) => [
                x.description || ASSET_TYPE[x.type],
                ASSET_TYPE[x.type],
                x.owner === 'joint' ? 'joint' : first(doc, x.owner),
                money(x.value),
                x.baseCost ? money(x.baseCost) : '—',
              ]),
              ...doc.retirementFunds.map((f) => [f.name || RETIREMENT_FUND_TYPE[f.type], RETIREMENT_FUND_TYPE[f.type], first(doc, f.owner), money(f.value), '—']),
            ]}
          />
          {a.risk.answered > 0 && (
            <>
              <Heading>Risk-profile questionnaire</Heading>
              <VTable head={['Question', 'Answer']} rows={RISK_QUESTIONS.map((q) => [q.question, q.options[doc.riskProfile.answers[q.id]]?.label ?? 'not answered'])} />
            </>
          )}
          <Heading>Sources</Heading>
          <ul className="vr-sources">
            {t.sources.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </Leaf>
      )}
    </article>
  );
}

function personNameOf(doc: FnaDocument, k: PersonKey) {
  return [doc[k].firstName, doc[k].surname].filter(Boolean).join(' ') || (k === 'client' ? 'Client' : 'Spouse');
}
