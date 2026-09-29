/**
 * Estate cost estimators that are not SARS tables.
 *
 * - Executor's remuneration: Administration of Estates Act s51 — max 3.5% of
 *   gross assets plus VAT (handled in analysis via assumptions).
 * - Master's fees: Schedule 2 to the Regulations under the Administration of
 *   Estates Act (GG 41224, effective 1 January 2018).
 * - Conveyancing: Law Society of South Africa guideline tariff effective
 *   1 July 2026 (not binding), plus VAT and deeds office disbursements.
 *   Transfer duty is not payable on inheritance (Transfer Duty Act s9(1)(e)).
 */

/** Master's office fee on the gross value of the estate. */
export const mastersFee = (grossValue: number): number => {
  if (grossValue < 250_000) return 0;
  if (grossValue <= 400_000) return 600;
  const steps = Math.floor((grossValue - 400_000) / 100_000);
  return Math.min(7_000, 600 + steps * 200);
};

/** LSSA guideline conveyancing fee (excl. VAT) for a property value. */
export const conveyancingGuidelineFee = (value: number): number => {
  if (value <= 0) return 0;
  if (value <= 1_000_000) {
    // Guideline below R1m is not published in our source; interpolate from R8,000 at R0.
    return 8_000 + (value / 1_000_000) * (26_275 - 8_000);
  }
  if (value <= 5_000_000) return 26_275 + Math.ceil((value - 1_000_000) / 200_000) * 2_120;
  return 68_675 + Math.ceil((value - 5_000_000) / 1_000_000) * 5_340;
};

/** Deeds office fees and disbursements (estimate). */
export const CONVEYANCING_DISBURSEMENTS = 2_500;

/** Estimated total cost to transfer inherited property: guideline fee + VAT + disbursements. */
export const conveyancingEstimate = (value: number, vatRate = 0.15): number =>
  value > 0 ? conveyancingGuidelineFee(value) * (1 + vatRate) + CONVEYANCING_DISBURSEMENTS : 0;

/** Estimated bond cancellation cost (attorney fee + VAT + deeds office). */
export const BOND_CANCELLATION = 6_000;

/** Advertising (Gazette + newspaper), final tax returns and sundries (estimate). */
export const ESTATE_SUNDRIES = 7_500;
