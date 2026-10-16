# Shahil FNA — Financial Needs Analysis for South African advisers

A financial needs analysis (FNA) and record-of-advice tool for South African financial advisers. It uses SARS 2026/27 tax tables, is aligned with FAIS, and keeps client data on the adviser's device.

## What it does

- **One fast, dense data-entry sheet.** Every section is on a single scrolling page:
  - engagement and consent (FAIS, POPIA and FICA)
  - client and spouse side by side, and dependants with education plans
  - income and tax, and budget
  - assets and retirement funds, liabilities, and existing cover
  - goals, estate, risk profile and assumptions
  - the live needs analysis, advice and report

  Tab or Enter moves to the next field and Shift goes back. Yes/No and lists are chosen by typing. Adding a row puts the cursor in it. A section nav tracks your position and completeness, and a live summary rail updates as you type.
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
  - Two styles, chosen in Settings and switchable on any report before printing:
    - **Standard**: clean and modern, in the practice's accent colour.
    - **Vintage**: set as a period financial survey on cream paper. It opens with the verdict in plain words, shows each need as an engraved-style plate with a note on how to read it, and explains terms in the margin. It runs to about twice the length of the Standard report.
- **Planning session.** A presentation mode for working through the plan with the client on a laptop, tablet, phone or TV (`Present` on the client list or the client file). It is one scrolling page set in the vintage style, with charts drawn in a clean modern style.
  - A bar along the top lists each question and shows a coloured dot for its answer. Tap a question to jump to it. The bar shows where you are as you scroll.
  - It opens with the client's questions, such as "If one of you died, would the family be OK?", "Can you each retire when you want?" and "Can you afford the plan?". Each shows today's answer in a few words. A chart shows how much of each need is funded today and with the plan.
  - Each question has its own section:
    - The answer in one sentence, and four key figures, such as what is needed, what is in place today, what the plan adds and what is still short.
    - A bridge chart of what the scenario is made of. For example, the cover needed on death adds up debts, estate costs, the funeral, an adjustment fund, education capital and income for dependants. It then takes off the cover, fund benefits and assets already in place, and the plan's changes.
    - A list of what the answer must address. It explains each item in plain words, with its figure, and adds the matters the numbers cannot settle, such as beneficiaries, a will and guardians, waiting periods, definitions of disability, and tax.
    - The basis of the calculation, and only the levers that change that answer.
  - Retirement shows savings by age, today and with the plan, and when the money runs out. It can also test the plan with investment returns 1–2% lower or 1% higher. Studies and goals show the costs year by year against what is set aside.
  - The levers include cover amounts and premiums, retirement age, income and saving, saving for studies and goals, goal dates, emergency savings, spending, and debts settled from the bonus. The analysis re-runs as a lever moves. A line at the foot of the page keeps the plan's monthly cost and what is left over in view.
  - "What have we agreed?" lists the changes by question, with their cost. The plan is saved beside the client file, never inside it, until the adviser adds it to the record of advice. The recommendations are then added as "pending" for the client to accept.
  - Light and dark themes, full screen, and keyboard shortcuts (← → between sections, F, H, T) are built in.
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
