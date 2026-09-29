# Shahil FNA — Financial Needs Analysis for South African advisers

A financial needs analysis (FNA) and record-of-advice tool for South African financial advisers. It uses SARS 2026/27 tax tables, is aligned with FAIS, and keeps client data on the adviser's device.

## What it does

- **Guided discovery in 14 steps.** Engagement and consent (FAIS, POPIA and FICA), client and family, income and tax, budget, assets and retirement funds, liabilities, existing cover, goals, estate, risk profile, assumptions, analysis, advice and report.
- **Needs analysis for one or two lives.**
  - Life cover (capital needs)
  - Income protection
  - Lump-sum disability
  - Severe illness
  - Funeral
  - Estate liquidity, estate duty and CGT on death
  - Retirement projection, including the drawdown sustainability test
  - Education funding and other goals
  - Emergency fund
  - Cash flow and debt ratios
- **Automatic findings** that flag risks (critical, warning or information) and outstanding compliance items.
- **Drafted recommendations** generated from the analysis, each with a rationale. It computes the after-tax cost of retirement annuity (RA) contributions using the s11F deduction headroom.
- **Record of advice.** Captures replacement disclosures (GCC s8(1)(d), all nine factors), client decisions, and a 20-year premium pattern (GCC s7(1)(c)(xiv)). A departure-from-advice warning is added automatically.
- **Client report.** A concise A4 report of about 8–10 pages: summary, situation, protection needs side by side, estate, retirement, record of advice, disclosures and signatures. An optional appendix adds line-by-line calculations. Save it as a PDF from the browser.
- **Privacy-first storage.**
  - Client files are stored only in the browser (IndexedDB), and nothing is sent to a server.
  - Exports can be encrypted with AES-256-GCM (password-derived key, PBKDF2-SHA256, 310k iterations). Use them for backups and FAIS/FICA five-year retention.

## Calculation basis

All calculations are pure functions in `lib/fna/` and are covered by tests in `tests/engine.test.ts`.

| Area | Basis |
|---|---|
| Income tax | SARS 2026/27 brackets, rebates, thresholds and s6A medical credits (2025/26 also available). The s11F retirement deduction is 27.5%, capped at R430,000. UIF ceiling R17,712. |
| Lump sums | Retirement/death table and withdrawal table. Death benefits are taxed as if the deceased retired immediately before death. |
| Estate | Executor's fees of 3.5% + VAT. Master's fees per the 2018 tariff. LSSA 2026 conveyancing guideline. |
| Estate duty | 20% / 25% after the R3.5m abatement. Includes s4(q) spousal deductions, s4A portability and s3(3)(a) deemed property. Accrual claims and in-community-of-property (ICOP) half-shares are handled. |
| CGT on death | R440,000 exclusion, R3m primary residence exclusion, 40% inclusion. Spousal roll-over. |
| Capital needs | Income needs use the present value of a monthly income paid in advance that escalates with CPI, discounted at the real rate. |
| Retirement | Monthly projection with contributions that escalate each year. Required capital is funded to the planning age. The drawdown is tested against the 2.5–17.5% legal limits and the ~4–5% sustainable level. The solver for extra contributions is exact. |

See `/methodology` in the app for the full method, default assumptions and sources.

## Development

```bash
npm install
npm run dev        # http://localhost:3000
npm test           # calculation test suite (vitest)
npm run typecheck
npm run build
```

Stack: Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4 and Immer. It has no backend and no database.

## Compliance note

Disclaimer and declaration wording in `lib/fna/compliance.ts` is a starting point. The FSP's compliance officer should approve it. The tool supports the adviser's professional judgement; it does not replace it.
