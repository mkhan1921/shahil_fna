"""Estate cost estimators that are not SARS tables: a port of ``lib/fna/estate.ts`` (§12.7).

- Executor's remuneration: Administration of Estates Act s51, at most 3.5% of gross assets plus VAT
  (the rate is an assumption on the document).
- Master's fees: Schedule 2 to the Regulations under the Administration of Estates Act (GG 41224,
  effective 1 January 2018).
- Conveyancing: Law Society of South Africa guideline tariff effective 1 July 2026 (not binding), plus
  VAT and deeds office disbursements. Transfer duty is not payable on inheritance (Transfer Duty Act s9(1)(e)).
"""

from __future__ import annotations

import math

CONVEYANCING_DISBURSEMENTS = 2_500  # deeds office fees and disbursements (estimate)
BOND_CANCELLATION = 6_000  # attorney fee + VAT + deeds office (estimate)
ESTATE_SUNDRIES = 7_500  # advertising (Gazette + newspaper), final tax returns and sundries (estimate)


def masters_fee(gross_value: float) -> float:
    """Master's office fee on the gross value of the estate."""
    if gross_value < 250_000:
        return 0.0
    if gross_value <= 400_000:
        return 600.0
    steps = math.floor((gross_value - 400_000) / 100_000)
    return float(min(7_000, 600 + steps * 200))


def conveyancing_guideline_fee(value: float) -> float:
    """LSSA guideline conveyancing fee (excl. VAT) for a property value."""
    if value <= 0:
        return 0.0
    if value <= 1_000_000:
        # The guideline below R1m is not published in the source; interpolated from R8,000 at R0.
        return 8_000 + (value / 1_000_000) * (26_275 - 8_000)
    if value <= 5_000_000:
        return float(26_275 + math.ceil((value - 1_000_000) / 200_000) * 2_120)
    return float(68_675 + math.ceil((value - 5_000_000) / 1_000_000) * 5_340)


def conveyancing_estimate(value: float, vat_rate: float = 0.15) -> float:
    """Estimated cost to transfer inherited property: guideline fee + VAT + disbursements."""
    return conveyancing_guideline_fee(value) * (1 + vat_rate) + CONVEYANCING_DISBURSEMENTS if value > 0 else 0.0
