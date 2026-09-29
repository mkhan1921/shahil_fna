/**
 * Regulatory reference text used in the report. Wording is a starting point
 * aligned with the FAIS General Code of Conduct (BN 80 of 2003 as amended),
 * POPIA and the Long-term Insurance Policyholder Protection Rules — the FSP's
 * compliance officer should approve it before use.
 */

export const REPLACEMENT_FACTORS: { key: string; label: string }[] = [
  { key: 'fees', label: 'Fees and charges of the existing and replacement product' },
  { key: 'terms', label: 'Special terms, exclusions and waiting periods' },
  { key: 'age-health', label: 'Premium impact of changes in age and health' },
  { key: 'tax', label: 'Differences in tax implications' },
  { key: 'investment-risk', label: 'Investment risk' },
  { key: 'penalties', label: 'Penalties and unrecovered expenses on termination' },
  { key: 'liquidity', label: 'Liquidity' },
  { key: 'vested-rights', label: 'Vested rights, guarantees and benefits lost' },
  { key: 'commission', label: 'Commission and fees on both products' },
];

export const OMBUDS = [
  {
    name: 'Office of the FAIS Ombud',
    detail: 'Complaints about financial advice and intermediary services (limit R3.5 million).',
    contact: '012 762 5000 · info@faisombud.co.za · www.faisombud.co.za',
    address: '125 Dallas Avenue, Menlyn Central, Waterkloof Glen, Pretoria 0010',
  },
  {
    name: 'National Financial Ombud Scheme South Africa (NFO)',
    detail: 'Complaints about long-term and short-term insurers, banks and credit providers.',
    contact: '0860 800 900 · info@nfosa.co.za · www.nfosa.co.za',
    address: '110 Oxford Road, Houghton Estate, Johannesburg 2198',
  },
  {
    name: 'Financial Sector Conduct Authority (FSCA)',
    detail: 'Regulator of financial institutions and FSPs.',
    contact: '0800 20 37 22 · info@fsca.co.za · www.fsca.co.za',
    address: 'Riverwalk Office Park, Block B, 41 Matroosberg Road, Ashlea Gardens, Pretoria 0081',
  },
];

export const DISCLAIMERS = {
  projections:
    'Projections and illustrations in this report are based on the assumptions stated in the Assumptions section and are for illustrative purposes only. They are not guaranteed. Actual outcomes depend on market performance, inflation, fees, tax and legislation, and may be materially higher or lower.',
  pastPerformance: 'Past performance is not indicative of future performance.',
  nonDisclosure:
    'You are responsible for the accuracy and completeness of the information you have provided. Misrepresentation or non-disclosure may result in claims being declined or policies being cancelled.',
  tax: 'Tax calculations are estimates based on the tax tables and Budget announcements for the stated tax year, some of which are pending legislation, and may change. They do not constitute tax advice.',
  replacement:
    'Replacing an existing policy may result in the loss of benefits, new waiting periods and exclusions, higher premiums due to changes in age or health, penalties, and different tax treatment. Please consider the comparison provided carefully before cancelling any existing policy.',
  limited:
    'This analysis was limited in scope. Needs that were not analysed are listed in the Scope of Advice section. The advice may therefore have limitations, and you should take particular care to consider whether it is appropriate to your objectives, financial situation and particular needs.',
  declined:
    'You chose not to provide all of the information requested. Our advice may therefore not be appropriate for you, and you should consider carefully whether any product is suitable for your needs.',
  departure:
    'You have chosen not to follow, or to vary, one or more of our recommendations. This carries the risk that your needs, or those of your dependants, will not be adequately provided for. Please consider carefully whether the course of action you have selected is appropriate to your needs, objectives and circumstances.',
  estate:
    'Estate calculations are simplified estimates to illustrate liquidity and duty. They are not a substitute for a will review or fiduciary advice. Values, marital regime and bequests should be confirmed with your attorney or fiduciary specialist.',
  popia:
    'Your personal information, including special personal information such as health information, is processed with your consent for the purpose of providing financial advice and implementing products, and may be shared with product suppliers, reinsurers and underwriters for that purpose. You may request access to, correction of, or deletion of your information at any time.',
  records: 'A copy of this record of advice has been provided to the client. Records are retained for at least five years in accordance with the FAIS Act and FICA.',
};

export const TCF_OUTCOMES = [
  'You can be confident that you are dealing with a firm where the fair treatment of customers is central to its culture.',
  'Products and services are designed to meet the needs of identified customer groups.',
  'You are given clear information and kept appropriately informed before, during and after the point of sale.',
  'Advice is suitable and takes account of your circumstances.',
  'Products perform as you have been led to expect.',
  'You do not face unreasonable post-sale barriers to change products, switch providers, claim or complain.',
];
