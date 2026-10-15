/*
 * Stylesheet for the vintage edition. It is rendered in a <style> element by
 * the page, so its @page rules apply only while the vintage report is open.
 */

/** Paper grain: a seamless tile of faint flecks and fibres (public/vintage/paper-grain.png). */
const GRAIN = "url('/vintage/paper-grain.png')";

/** The lighter centre and darker edge of an old sheet. */
const AGE = 'radial-gradient(ellipse 80% 70% at 50% 45%, rgba(255,250,238,0.35) 0%, rgba(255,250,238,0) 60%), radial-gradient(ellipse at 50% 50%, rgba(0,0,0,0) 62%, rgba(126,86,38,0.13) 100%)';

const cssString = (s: string) =>
  `"${s
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/[\r\n]+/g, ' ')}"`;

export function vintageCss({ fontFamily, runningTitle }: { fontFamily: string; runningTitle: string }) {
  const face = `${fontFamily}, 'EB Garamond', Garamond, 'Times New Roman', serif`;
  return `
@page {
  size: A4;
  margin: 16mm 0 15mm;
  background: #f4ead8 ${GRAIN};
  @top-center { content: ${cssString(runningTitle)}; font-family: ${face}; font-style: italic; font-size: 8.5pt; color: #574333; }
  @bottom-center { content: "— " counter(page) " —"; font-family: ${face}; font-size: 9pt; color: #2a1d14; }
}
@page :first {
  margin: 0;
  @top-center { content: none; }
  @bottom-center { content: none; }
}

.vr {
  --vr-ink: #2a1d14;
  --vr-ink-2: #574333;
  --vr-ink-3: #76604c;
  --vr-red: #9a2b1d;
  --vr-paper: #f4ead8;
  --vr-faint: #c9b597;
  font-family: ${face};
  color: var(--vr-ink);
  background: #2f2823 radial-gradient(ellipse at 50% 0%, #4a3f36 0%, #2f2823 70%) fixed;
  font-size: 10.6pt;
  line-height: 1.42;
  font-variant-numeric: oldstyle-nums proportional-nums;
  font-kerning: normal;
  hyphens: auto;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}
.vr-doc { counter-reset: leaf; padding: 26px 0 64px; }
.vr * { box-sizing: border-box; }
.vr b, .vr strong { font-weight: 600; }

/* ------------------------------------------------------------- the leaf */
.vr-leaf {
  counter-increment: leaf;
  position: relative;
  width: 210mm;
  min-height: 297mm;
  margin: 0 auto 30px;
  padding: 16mm 20mm 20mm;
  background-color: var(--vr-paper);
  background-image: ${GRAIN}, ${AGE};
  box-shadow: 0 1px 1px rgba(0,0,0,.25), 0 14px 38px rgba(0,0,0,.45);
}
.vr-leaf::after {
  content: "— " counter(leaf) " —";
  position: absolute; left: 0; right: 0; bottom: 9mm;
  text-align: center; font-size: 9pt;
}
.vr-title-page::after { content: none; }
.vr-runhead {
  display: flex; justify-content: space-between; gap: 6mm;
  font-style: italic; font-size: 8.5pt; color: var(--vr-ink-2);
  border-bottom: 0.5pt solid var(--vr-ink); padding-bottom: 1.6mm; margin-bottom: 9mm;
}

/* ------------------------------------------------------------- title page */
.vr-title-page { display: flex; flex-direction: column; align-items: center; text-align: center; }
.vr-title-top { display: flex; justify-content: space-between; width: 100%; font-variant-caps: all-small-caps; letter-spacing: .14em; font-size: 9.5pt; color: var(--vr-ink-2); }
.vr-title-block { margin-top: 30mm; display: flex; flex-direction: column; align-items: center; }
.vr-title-kicker { font-variant-caps: small-caps; letter-spacing: .2em; font-size: 15pt; }
.vr-title-of { font-style: italic; font-size: 13pt; margin: 2mm 0 1mm; }
.vr-title-main { font-weight: 400; font-size: 31pt; line-height: 1.08; margin: 0 0 6mm; font-variant-caps: small-caps; letter-spacing: .05em; max-width: 160mm; }
.vr-title-sub { font-style: italic; font-size: 11.5pt; line-height: 1.45; max-width: 112mm; margin: 6mm auto 0; }
.vr-contents { width: 128mm; margin: 22mm auto 0; text-align: left; }
.vr-contents-head { text-align: center; font-variant-caps: all-small-caps; letter-spacing: .2em; font-size: 10pt; margin-bottom: 3mm; }
.vr-contents .vr-leader { margin-bottom: 2.2mm; }
.vr-contents dt { font-variant-caps: small-caps; letter-spacing: .03em; }
.vr-contents dd { font-size: 9.5pt; color: var(--vr-ink-2); }
.vr-imprint { margin-top: auto; padding-top: 16mm; font-size: 10pt; line-height: 1.55; }
.vr-imprint > div:first-child { font-variant-caps: small-caps; letter-spacing: .08em; }
.vr-imprint-small { font-size: 8.5pt; color: var(--vr-ink-2); font-style: italic; }

/* ------------------------------------------------------------- headings */
.vr-chapter { text-align: center; margin: 0 0 7mm; }
.vr-chapter-no { font-variant-caps: all-small-caps; letter-spacing: .2em; font-size: 10pt; }
.vr-chapter h2 { font-weight: 400; font-size: 22pt; line-height: 1.12; margin: 1mm 0 0; }
.vr-chapter-sub { font-style: italic; font-size: 11pt; margin: 1.2mm 0 4mm; color: var(--vr-ink-2); }
.vr-rule3 { display: flex; justify-content: center; gap: 2.4mm; }
.vr-rule3 span { display: block; height: 0.9pt; background: var(--vr-ink); width: 25mm; }
.vr-rule3 span:nth-child(2) { width: 9mm; }
.vr-keep { break-inside: avoid; page-break-inside: avoid; }
.vr-heading { text-align: center; margin: 8mm 0 3.5mm; break-after: avoid; page-break-after: avoid; }
.vr-heading h3 { font-weight: 400; font-variant-caps: small-caps; letter-spacing: .1em; font-size: 12.5pt; margin: 0; }
.vr-heading p { margin: .4mm 0 0; font-style: italic; font-size: 9.8pt; color: var(--vr-ink-2); }
.vr-subhead { font-variant-caps: all-small-caps; letter-spacing: .12em; font-size: 9.5pt; border-bottom: .5pt solid var(--vr-ink); padding-bottom: .6mm; margin-bottom: 1.6mm; }

/* ------------------------------------------------------------- text */
.vr-para { display: grid; grid-template-columns: minmax(0, 1fr) 37mm; column-gap: 7mm; margin: 0 0 3.5mm; }
.vr-text p { margin: 0; text-align: justify; text-indent: 1.3em; }
.vr-text p:first-child { text-indent: 0; }
.vr-text p + p { margin-top: .4mm; }
.vr-drop > p:first-child::first-letter { float: left; font-size: 3.7em; line-height: .8; padding: .06em .07em 0 0; margin-top: .03em; font-variant-numeric: lining-nums; }
.vr-lead { font-variant-caps: small-caps; letter-spacing: .05em; }
.vr-runin { font-variant-caps: small-caps; letter-spacing: .06em; font-weight: 600; }
.vr-pre { white-space: pre-line; }
.vr-margin { font-size: 8.3pt; line-height: 1.32; color: var(--vr-ink-2); padding-top: .4mm; }
.vr-margin p { margin: 0 0 2.4mm; }
.vr-margin i { color: var(--vr-ink); }
.vr-abstract { border-top: .9pt solid var(--vr-ink); border-bottom: .9pt solid var(--vr-ink); padding: 2.4mm 1mm; margin: 0 0 5mm; font-style: italic; font-size: 11pt; line-height: 1.45; text-align: center; }
.vr-abstract-label { font-style: normal; font-variant-caps: all-small-caps; letter-spacing: .12em; }
.vr-small { font-size: 8.6pt; line-height: 1.38; color: var(--vr-ink-2); text-align: justify; margin: 2mm 0 0; hyphens: auto; }
.vr-left { text-align: left; }
.vr-quiet { font-style: italic; color: var(--vr-ink-2); }
.vr-red { color: var(--vr-red); }
.vr-sc { font-variant-caps: small-caps; letter-spacing: .05em; }
.vr-finis { text-align: center; font-style: italic; margin: 12mm 0 0; font-size: 11pt; }

/* ------------------------------------------------------------- leaders */
.vr-leaders { margin: 0; }
.vr-leader { margin: 0 0 1.1mm; break-inside: avoid; }
.vr-leader-line { display: flex; align-items: last baseline; }
.vr-leader dt { flex: 0 1 auto; min-width: 0; }
.vr-leader dd { flex: none; margin: 0; padding-left: .35em; font-variant-numeric: lining-nums tabular-nums; text-align: right; }
.vr-dots { position: relative; align-self: stretch; flex: 1 1 1.5em; min-width: 1.5em; color: var(--vr-ink-3); }
.vr-dots::after { position: absolute; left: .3em; right: 0; bottom: 0; overflow: hidden; white-space: nowrap; content: ". . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . ."; letter-spacing: .1em; }
.vr-leader-sub { font-size: 8.8pt; font-style: italic; color: var(--vr-ink-2); padding-left: 1.2em; line-height: 1.3; }
.vr-leader.is-red dd, .vr-leader.is-red dt { color: var(--vr-red); }
.vr-leader.is-strong dd, .vr-leader.is-strong dt { font-weight: 600; }

/* ------------------------------------------------------------- tables */
.vr-table { width: 100%; border-collapse: collapse; font-size: 9.5pt; line-height: 1.3; font-variant-numeric: lining-nums tabular-nums; border-top: 1.1pt solid var(--vr-ink); border-bottom: 1.1pt solid var(--vr-ink); margin: 2mm 0 3mm; break-inside: avoid; }
.vr-table caption { caption-side: top; text-align: left; font-variant-caps: all-small-caps; letter-spacing: .12em; font-size: 9.5pt; padding-bottom: 1.2mm; }
.vr-table th { font-weight: 400; font-variant-caps: all-small-caps; letter-spacing: .08em; text-align: left; padding: 1.1mm 1.8mm; border-bottom: .6pt solid var(--vr-ink); }
.vr-table td { padding: .65mm 1.8mm; vertical-align: top; }
.vr-table td:first-child, .vr-table th:first-child { padding-left: 0; border-right: .5pt solid var(--vr-ink); }
.vr-table td:last-child, .vr-table th:last-child { padding-right: 0; }
.vr-table tbody tr:nth-child(5n):not(:last-child) td { border-bottom: .4pt solid var(--vr-faint); }
.vr-table .is-r { text-align: right; }
.vr-table .is-c { text-align: center; }
.vr-table tr.is-strong td { font-weight: 600; }
.vr-table tr.is-red td { color: var(--vr-red); }
.vr-table tfoot td { border-top: .6pt solid var(--vr-ink); font-weight: 600; padding-top: 1.1mm; padding-bottom: 1.1mm; }
.vr-table-tight { font-size: 8.6pt; }

/* ------------------------------------------------------------- plates */
.vr-plate { margin: 4mm 0 6mm; break-inside: avoid; page-break-inside: avoid; }
.vr-plate-head { text-align: center; margin-bottom: 3.2mm; }
.vr-plate-no { display: block; font-variant-caps: all-small-caps; letter-spacing: .2em; font-size: 10pt; }
.vr-plate-title { display: block; font-style: italic; font-size: 11pt; }
.vr-plate-body > svg + svg { margin-top: 1.5mm; }
.vr-plate-read { margin: 2.6mm auto 0; max-width: 140mm; font-size: 8.8pt; line-height: 1.36; color: var(--vr-ink-2); text-align: center; }
.vr-plate-read i { color: var(--vr-ink); }
.vr-svg { display: block; width: 100%; height: auto; overflow: visible; font-family: ${face}; }
.vr-svg .sc { font-variant-caps: all-small-caps; letter-spacing: .07em; }
.vr-key { list-style: none; display: flex; flex-wrap: wrap; justify-content: center; gap: 1mm 6mm; padding: 0; margin: 2.4mm 0 0; font-size: 9pt; }
.vr-key li { display: flex; align-items: center; }
.vr-key .vr-swatch { flex: none; }
.vr-swatch { width: 14px; height: 10px; vertical-align: -1px; margin-right: 5px; }
.vr-ornament { display: block; margin: 0 auto; height: auto; }
.vr-manicule { width: 25px; height: 12.5px; flex: none; margin-top: .28em; }
.vr-fleuron { width: 11px; height: 11px; flex: none; margin: .32em 7px 0 8px; }
.vr-dials { display: flex; justify-content: center; gap: 10mm; }
.vr-dial-cell { width: 74mm; text-align: center; }
.vr-dial-name { font-variant-caps: small-caps; letter-spacing: .08em; font-size: 11.5pt; margin-top: 1mm; }
.vr-dial-sub { font-size: 9pt; font-style: italic; color: var(--vr-ink-2); }

/* ------------------------------------------------------------- matters */
.vr-matters { list-style: none; padding: 0; margin: 0 4mm; }
.vr-matters li { display: flex; gap: 3mm; margin: 0 0 2.2mm; break-inside: avoid; text-align: justify; }
.vr-matters-good li { gap: 0; }

/* ------------------------------------------------------------- register */
.vr-register { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1.1pt solid var(--vr-ink); border-bottom: 1.1pt solid var(--vr-ink); margin: 3mm 0 5mm; }
.vr-register > div { padding: 2mm 2mm 2.2mm; text-align: center; }
.vr-register > div + div { border-left: .5pt solid var(--vr-ink); }
.vr-register-label { font-variant-caps: all-small-caps; letter-spacing: .12em; font-size: 9.5pt; }
.vr-register-value { font-size: 17pt; line-height: 1.2; }
.vr-register-sub { font-size: 8.4pt; font-style: italic; color: var(--vr-ink-2); }

/* ------------------------------------------------------------- ledgers & accounts */
.vr-two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 9mm; align-items: start; }
.vr-two > div > .vr-heading:first-child { margin-top: 2mm; }
.vr-account { margin: 3mm 0 5mm; break-inside: avoid; border-top: 1.1pt solid var(--vr-ink); border-bottom: 1.1pt solid var(--vr-ink); }
.vr-account-title { text-align: center; font-variant-caps: small-caps; letter-spacing: .08em; font-size: 11pt; padding: 1.4mm 0; border-bottom: .5pt solid var(--vr-ink); }
.vr-account-title i { font-variant-caps: normal; letter-spacing: 0; font-size: 10pt; }
.vr-account-sides { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
.vr-account-sides > div { padding: 1.6mm 4mm 0; display: flex; flex-direction: column; font-size: 9.6pt; }
.vr-account-sides > div:first-child { padding-left: 0; border-right: .5pt solid var(--vr-ink); }
.vr-account-sides > div:last-child { padding-right: 0; }
.vr-account-side { font-variant-caps: all-small-caps; letter-spacing: .1em; font-size: 9pt; color: var(--vr-ink-2); margin-bottom: 1.2mm; }
.vr-account-total { margin-top: auto; display: flex; justify-content: space-between; font-weight: 600; border-top: .6pt solid var(--vr-ink); border-bottom: 2.4pt double var(--vr-ink); padding: .8mm 0; margin-bottom: 1.6mm; font-variant-numeric: lining-nums tabular-nums; }
.vr-budget { max-width: 120mm; margin: 0 auto 4mm; }
.vr-working { break-inside: avoid; font-size: 9.4pt; margin-bottom: 3mm; }

/* ------------------------------------------------------------- advice */
.vr-articles ol { list-style: none; margin: 0; padding: 0; }
.vr-articles li { margin: 0 0 3.2mm; break-inside: avoid; }
.vr-article-head { display: flex; align-items: last baseline; }
.vr-article-no { flex: none; width: 2.6em; font-variant-numeric: lining-nums; }
.vr-article-title { flex: 0 1 auto; font-weight: 600; }
.vr-article-amount { flex: none; padding-left: .35em; font-variant-numeric: lining-nums tabular-nums; }
.vr-article-meta { padding-left: 2.6em; font-size: 9.2pt; font-style: italic; color: var(--vr-ink-2); }
.vr-article-why { padding-left: 2.6em; margin: .6mm 0 0; font-size: 9.4pt; line-height: 1.38; text-align: justify; color: var(--vr-ink-2); }
.vr-caution { display: flex; gap: 3mm; margin: 2mm 0 3mm; padding: 2mm 3mm; border: .6pt solid var(--vr-red); font-size: 9.4pt; line-height: 1.38; break-inside: avoid; }
.vr-caution-small { margin-left: 2.6em; font-size: 8.8pt; }

/* ------------------------------------------------------------- declarations */
.vr-cols { columns: 2; column-gap: 8mm; column-rule: .5pt solid var(--vr-ink); text-align: left; }
.vr-cols p { margin: 0 0 1.6mm; }
.vr-ombuds { list-style: none; padding: 0; margin: 2mm 0 0; font-size: 9pt; line-height: 1.35; }
.vr-ombuds li { margin-bottom: 2mm; }
.vr-signs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9mm; margin-top: 9mm; break-inside: avoid; }
.vr-sign-role { font-variant-caps: all-small-caps; letter-spacing: .12em; font-size: 9pt; }
.vr-sign-line { height: 15mm; border-bottom: .6pt solid var(--vr-ink); }
.vr-sign-name { font-style: italic; font-size: 11pt; margin-top: 1mm; }
.vr-sign-date { font-size: 8.4pt; color: var(--vr-ink-2); }
.vr-sources { font-size: 8.4pt; color: var(--vr-ink-2); padding-left: 4mm; word-break: break-all; }

/* ------------------------------------------------------------- narrow screens */
@media screen and (max-width: 860px) {
  .vr-doc { padding: 0 0 24px; }
  .vr-leaf { width: auto; min-height: 0; margin: 0 0 10px; padding: 22px 16px 40px; box-shadow: none; }
  .vr-leaf::after { bottom: 12px; }
  .vr-title-page { min-height: 92vh; }
  .vr-title-block { margin-top: 48px; }
  .vr-title-main { font-size: 26pt; }
  .vr-contents { width: 100%; }
  .vr-para { grid-template-columns: 1fr; }
  .vr-margin { border-left: 1px solid var(--vr-faint); padding-left: 10px; margin-top: 6px; }
  .vr-plate-body { overflow-x: auto; }
  .vr-plate-body > svg { min-width: 560px; }
  .vr-plate-body:has(> svg)::before { content: 'swipe sideways to see the whole plate →'; display: block; position: sticky; left: 0; font-style: italic; font-size: 8.5pt; color: var(--vr-ink-2); margin-bottom: 4px; }
  .vr-two, .vr-account-sides { grid-template-columns: 1fr; }
  .vr-account-sides > div:first-child { border-right: 0; padding-right: 0; }
  .vr-account-sides > div:last-child { padding-left: 0; }
  .vr-register { grid-template-columns: 1fr 1fr; }
  .vr-register > div:nth-child(3) { border-left: 0; }
  .vr-dials { flex-direction: column; align-items: center; }
  .vr-signs { grid-template-columns: 1fr; }
  .vr-cols { columns: 1; }
  .vr-runhead span:first-child { display: none; }
  .vr-table-tight { display: block; overflow-x: auto; }
}

/* ------------------------------------------------------------- print */
@media print {
  /* The paper is the page itself (see @page), so it runs under the margins too. */
  html, body { background: transparent !important; }
  .vr { background: none; font-size: 10.3pt; min-height: 0; }
  .vr-doc { padding: 0; }
  .vr-leaf { width: auto; min-height: 0; margin: 0; padding: 0 19mm; box-shadow: none; background: none; break-before: page; }
  .vr-leaf::after, .vr-runhead { display: none; }
  /* The closing chapter follows on, rather than leaving a near-empty page. */
  .vr-continue { break-before: auto; margin-top: 14mm; }
  .vr-title-page { break-before: auto; height: 297mm; padding: 20mm 19mm 18mm; }
  .vr-title-block { margin-top: 34mm; }
  .vr-chapter { margin-top: 2mm; }
  * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
`;
}
