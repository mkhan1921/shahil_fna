# Rebuild notes (v2, September 2026)

Version 1 was replaced entirely. This file records what was wrong with v1 and what replaced it.

## Critical problems in v1

1. **Client data was exposed and did not persist.**
   - Client records, including ID numbers, health, criminal history and finances, were written as plain JSON files to `data/` on the server. Those files were committed to a **public** GitHub repository.
   - `/api/clients` and `/api/load-client` had no authentication, so anyone could list and download every client.
   - On Vercel the filesystem is read-only and ephemeral, so saving failed in production anyway.
2. **The PDF export could not work in production.** It used Puppeteer, which needs a bundled Chromium that Vercel serverless functions do not have.
3. **There was no needs analysis.** The app collected data but never calculated life, disability, income protection, severe illness, estate or education needs. The "insurance review" was a set of free-text boxes.
4. **The retirement maths was wrong in ways that overstated readiness.**
   - It assumed a 9.6% withdrawal rate, which is unsustainable (ASISA suggests 4–5%).
   - It applied a 10% nominal return to a target in today's money that was never inflated.
   - It applied a one-third lump sum to all capital.
   - It assumed annuity income of 0.5% a month.
5. **The tax was wrong.** Tax tables labelled "2025" did not match any SARS year. The retirement deduction had no 27.5% or cap limit. Medical credits counted every family member, whether or not they were on the scheme.
6. **Goals and debt maths were wrong.** Goals used a flat 7% inflation and ignored investment growth. Debt payoff time ignored interest.
7. **The records were not compliant.** There were no FSP disclosures, no POPIA consent, no FICA, no record of advice, no replacement disclosures, no limited-advice warnings, no assumptions disclosure and no complaints or ombud details.
8. **Housekeeping.** Test artefacts (videos, PDFs, screenshots), a stray `-p` directory and self-congratulatory "audit" documents claiming the app was "production ready" were committed.

## What replaced it

- **Local-first architecture.** Data is stored in IndexedDB in the adviser's browser, with an optional AES-256-GCM encrypted export and import. There is no server storage of personal information. Security headers (CSP, HSTS, frame denial) are set in `next.config.ts`.
- **A tested calculation engine** in `lib/fna`, with 37 tests. They include worked SARS examples, estate duty, lump-sum tax, Master's fee and LSSA conveyancing reference points, and time-value-of-money identities.
- **FAIS-aligned workflow.** It covers disclosures, consents, risk profiling (tolerance vs capacity), recommendations with rationale, replacement factors, client decisions and signatures.
- **A browser-rendered A4 report** (about 10 pages for a couple), with running footers, page numbers and an optional appendix.
- **Research-backed defaults.** Budget 2026 (25 Feb 2026) tables, the SARB 3% inflation target, and prime at 10.75% from 25 Sept 2026. Sources are listed on `/methodology`.

## Follow-up for the repository owner

The v1 `data/*.json` files remain in the public repository's git history. They appear to be test data ("John TestUser", "Test User"). If any real client data was ever committed, purge it from history (for example with `git filter-repo`) and treat it as a POPIA incident.
