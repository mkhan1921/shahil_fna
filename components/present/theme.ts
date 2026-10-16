/*
 * The planning session's look, taken from the vintage edition of the report:
 * leaves of cream paper on a dark desk, EB Garamond, chapter heads with the
 * printer's three-part rule, figures in ruled registers and dot-leader
 * accounts, and plates engraved in hatching and stipple rather than filled
 * with colour. Sepia is what is there and red is what is short; the plan is
 * written in a third ink, blue, so the changes stand out on every plate.
 *
 * Sizes are for a screen across a desk or a phone in the hand, so they run
 * larger than the printed report's, and long text is set in roman, not
 * italic or spaced capitals.
 */
const GRAIN = "url('/vintage/paper-grain.png')";
const AGE = 'radial-gradient(ellipse 80% 70% at 50% 45%, rgba(255,250,238,0.35) 0%, rgba(255,250,238,0) 60%), radial-gradient(ellipse at 50% 50%, rgba(0,0,0,0) 62%, rgba(126,86,38,0.13) 100%)';
const DOTS = '. '.repeat(160);

/* Tones, as CSS so bars and their keys always match. */
const HATCH = (ink: string, angle = 135, gap = 4.5) => `repeating-linear-gradient(${angle}deg, ${ink} 0 1px, transparent 1px ${gap}px)`;

export const PRESENT_CSS = `
.pv {
  color-scheme: light;
  --pv-desk: #2f2823;
  --pv-bar: #1d1814;
  --pv-bar-ink: #f1e7d3;
  --pv-bar-ink-2: #c4b394;
  --pv-paper: #f4ead8;
  --pv-slip: #fbf6ea;
  --pv-ink: #2a1d14;
  --pv-ink-2: #574333;
  --pv-ink-3: #76604c;
  --pv-faint: #c9b597;
  --pv-red: #9a2b1d;
  --pv-plan: #1f4f8f;
  --pv-plan-wash: rgba(31, 79, 143, 0.1);
  --pv-graph: #f6e9d3;
  --pv-grid-minor: #edd5b7;
  --pv-grid-major: #d8a776;
  --pv-head-h: 100px;
  font-family: var(--vr-font, 'EB Garamond'), 'EB Garamond', Garamond, Georgia, 'Times New Roman', serif;
  background: var(--pv-desk) radial-gradient(ellipse at 50% 0%, #4a3f36 0%, #2f2823 70%) fixed;
  color: var(--pv-ink);
  font-size: 19px;
  line-height: 1.55;
  font-variant-numeric: lining-nums;
  font-kerning: normal;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
}
.pv * { box-sizing: border-box; }
.pv b, .pv strong { font-weight: 600; }
.pv button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; }
.pv .pv-sc { font-variant-caps: all-small-caps; letter-spacing: 0.08em; }
.pv .pv-num { font-variant-numeric: lining-nums tabular-nums; }
.pv .pv-red { color: var(--pv-red); }
.pv .pv-blue { color: var(--pv-plan); }
.pv :focus-visible { outline: 2px solid var(--pv-plan); outline-offset: 2px; }
.pv .vr-manicule { width: 30px; height: 15px; flex: none; }
.pv .vr-fleuron { width: 15px; height: 15px; flex: none; }
.pv .vr-ornament { display: block; margin: 0 auto; height: auto; }
.pv .vr-rule3 { display: flex; justify-content: center; gap: 9px; }
.pv .vr-rule3 span { display: block; height: 1.2px; width: 96px; background: var(--pv-ink); }
.pv .vr-rule3 span:nth-child(2) { width: 34px; }

/* ------------------------------------------------------------ the desk: bars top and bottom */
.pv .pv-top { position: sticky; top: 0; z-index: 30; background: var(--pv-bar); color: var(--pv-bar-ink); padding-top: env(safe-area-inset-top, 0px); box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35); }
.pv .pv-top-row { display: flex; align-items: center; gap: 8px; height: 54px; max-width: 1120px; margin: 0 auto; padding-inline: max(10px, env(safe-area-inset-left, 0px)) max(10px, env(safe-area-inset-right, 0px)); }
.pv .pv-top-title { flex: 1; min-width: 0; text-align: center; line-height: 1.2; }
.pv .pv-top-title small { display: block; font-variant-caps: all-small-caps; letter-spacing: 0.16em; font-size: 14px; color: var(--pv-bar-ink-2); }
.pv .pv-top-title span { display: block; font-size: 19px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pv .pv-icon { display: inline-flex; align-items: center; justify-content: center; width: 42px; height: 42px; border-radius: 999px; color: var(--pv-bar-ink-2); flex: none; }
.pv .pv-icon:hover, .pv .pv-icon[aria-pressed='true'] { color: var(--pv-bar-ink); background: rgba(255, 255, 255, 0.08); }
.pv .pv-nav { display: flex; overflow-x: auto; scrollbar-width: none; height: 46px; max-width: 1400px; margin: 0 auto; padding: 0 max(6px, env(safe-area-inset-left, 0px)); mask-image: linear-gradient(to right, transparent 0, #000 14px, #000 calc(100% - 14px), transparent 100%); }
.pv .pv-nav::-webkit-scrollbar { display: none; }
.pv .pv-nav > a:first-child { margin-left: auto; }
.pv .pv-nav > a:last-child { margin-right: auto; }
.pv .pv-nav a { flex: none; display: inline-flex; align-items: center; gap: 8px; padding: 0 12px; font-size: 18px; color: var(--pv-bar-ink-2); text-decoration: none; border-bottom: 2px solid transparent; }
.pv .pv-nav a:hover { color: var(--pv-bar-ink); }
.pv .pv-nav a[aria-current='true'] { color: var(--pv-bar-ink); border-bottom-color: var(--pv-bar-ink); }
.pv .pv-dot { width: 8px; height: 8px; border-radius: 99px; flex: none; }
.pv .pv-foot { position: fixed; left: 0; right: 0; bottom: 0; z-index: 30; background: var(--pv-bar); color: var(--pv-bar-ink); padding-bottom: env(safe-area-inset-bottom, 0px); box-shadow: 0 -6px 18px rgba(0, 0, 0, 0.35); }
.pv .pv-foot-row { max-width: 1120px; margin: 0 auto; display: flex; align-items: center; justify-content: center; gap: 14px; min-height: 56px; padding-inline: max(14px, env(safe-area-inset-left, 0px)) max(14px, env(safe-area-inset-right, 0px)); font-size: 18px; }
.pv button.pv-foot-row { width: 100%; text-align: center; }
.pv .pv-foot-row > span:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pv .pv-foot-see { flex: none; border: 1px solid var(--pv-bar-ink-2); padding: 3px 14px; }
.pv button.pv-foot-row:hover .pv-foot-see { background: rgba(255, 255, 255, 0.08); }
.pv .pv-foot .pv-muted { color: var(--pv-bar-ink-2); }
.pv .pv-foot .is-over { color: #f0a08a; }


/* ------------------------------------------------------------ leaves of paper */
.pv .pv-desk { padding: 30px max(14px, env(safe-area-inset-left, 0px)) 120px max(14px, env(safe-area-inset-right, 0px)); }
.pv .pv-leaf {
  position: relative;
  max-width: 1000px;
  margin: 0 auto 30px;
  padding: 60px clamp(18px, 6vw, 76px) 76px;
  background-color: var(--pv-paper);
  background-image: ${GRAIN}, ${AGE};
  box-shadow: 0 1px 1px rgba(0, 0, 0, 0.25), 0 14px 38px rgba(0, 0, 0, 0.45);
  scroll-margin-top: calc(var(--pv-head-h) + env(safe-area-inset-top, 0px) + 14px);
}
.pv .pv-leaf::after { content: '— ' attr(data-folio) ' —'; position: absolute; left: 0; right: 0; bottom: 22px; text-align: center; font-size: 16px; color: var(--pv-ink-2); }

/* ------------------------------------------------------------ chapter heads and the short of it */
.pv .pv-chapter { text-align: center; }
.pv .pv-chapter-no { font-variant-caps: all-small-caps; letter-spacing: 0.2em; font-size: 17px; color: var(--pv-ink-2); }
.pv .pv-chapter h2 { font-weight: 400; font-size: clamp(30px, 4.4vw, 46px); line-height: 1.12; margin: 6px auto 0; max-width: 17em; text-wrap: balance; }
.pv .pv-chapter-sub { font-style: italic; font-size: clamp(19px, 2.1vw, 22px); color: var(--pv-ink-2); margin: 8px 0 16px; }
.pv .pv-person { display: flex; justify-content: center; margin-top: 20px; }
.pv .pv-abstract { margin: 26px 0 0; padding: 14px 4px 15px; border-top: 1.4px solid var(--pv-ink); border-bottom: 1.4px solid var(--pv-ink); text-align: center; font-size: clamp(20px, 2.3vw, 24px); line-height: 1.45; text-wrap: balance; }
.pv .pv-abstract-label { font-variant-caps: all-small-caps; letter-spacing: 0.12em; font-size: 0.8em; color: var(--pv-ink-2); }
.pv .pv-abstract .pv-lead { font-weight: 600; }
.pv .pv-abstract b { font-weight: 600; white-space: nowrap; }
.pv .pv-abstract .vr-manicule, .pv .pv-abstract .vr-fleuron { display: inline-block; vertical-align: -0.04em; margin: 0 0.3em 0 0.2em; }
.pv .pv-abstract .vr-manicule { width: 34px; height: 17px; }

/* ------------------------------------------------------------ the register of figures */
.pv .pv-register { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1.4px solid var(--pv-ink); }
.pv .pv-register > div { padding: 13px 10px 14px; text-align: center; min-width: 0; }
.pv .pv-register > div + div { border-left: 0.8px solid var(--pv-ink); }
.pv .pv-register-label { font-variant-caps: all-small-caps; letter-spacing: 0.1em; font-size: 17px; color: var(--pv-ink-2); }
.pv .pv-register-value { font-size: clamp(27px, 3.1vw, 36px); line-height: 1.15; font-variant-numeric: lining-nums tabular-nums; }
.pv .pv-register-sub { font-size: 16px; font-style: italic; color: var(--pv-ink-2); }
.pv .pv-register .is-bad .pv-register-value { color: var(--pv-red); }
.pv .pv-register .is-plan .pv-register-value { color: var(--pv-plan); }

/* ------------------------------------------------------------ headings, ledgers, matters */
.pv .pv-heading { text-align: center; margin: 52px 0 16px; }
.pv .pv-heading h3 { font-weight: 400; font-variant-caps: small-caps; letter-spacing: 0.08em; font-size: clamp(22px, 2.4vw, 25px); margin: 0; }
.pv .pv-heading p { margin: 2px 0 0; font-style: italic; font-size: 18px; color: var(--pv-ink-2); }
.pv .pv-subhead { font-variant-caps: all-small-caps; letter-spacing: 0.1em; font-size: 18px; border-bottom: 0.8px solid var(--pv-ink); padding-bottom: 3px; margin: 0 0 12px; }
.pv .pv-leaders { margin: 0; }
.pv .pv-leader { margin: 0 0 14px; }
.pv .pv-leader-line { display: flex; align-items: last baseline; }
.pv .pv-leader dt { flex: 0 1 auto; min-width: 0; }
.pv .pv-leader dd { flex: none; margin: 0; padding-left: 0.35em; font-variant-numeric: lining-nums tabular-nums; text-align: right; }
.pv .pv-dots { position: relative; align-self: stretch; flex: 1 1 1.5em; min-width: 1.2em; color: var(--pv-ink-3); }
.pv .pv-dots::after { position: absolute; left: 0.3em; right: 0; bottom: 0.12em; overflow: hidden; white-space: nowrap; content: '${DOTS}'; letter-spacing: 0.1em; }
.pv .pv-gloss { margin: 3px 0 0; font-size: 17px; line-height: 1.45; color: var(--pv-ink-2); }
.pv .pv-leader.is-plan dt, .pv .pv-leader.is-plan dd { color: var(--pv-plan); font-weight: 600; }
.pv .pv-leader.is-short dt, .pv .pv-leader.is-short dd { color: var(--pv-red); font-weight: 600; }
.pv .pv-leader.is-over dt, .pv .pv-leader.is-over dd { font-weight: 600; }
.pv .pv-account { margin: 48px 0 0; border-top: 1.4px solid var(--pv-ink); border-bottom: 1.4px solid var(--pv-ink); }
.pv .pv-account-title { text-align: center; font-variant-caps: small-caps; letter-spacing: 0.06em; font-size: clamp(21px, 2.3vw, 24px); padding: 9px 8px; border-bottom: 0.8px solid var(--pv-ink); }
.pv .pv-account-title i { font-variant-caps: normal; letter-spacing: 0; color: var(--pv-ink-2); }
.pv .pv-account-sides { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
.pv .pv-account-sides > div { display: flex; flex-direction: column; padding: 14px 24px 0; min-width: 0; }
.pv .pv-account-sides > div:first-child { padding-left: 0; border-right: 0.8px solid var(--pv-ink); }
.pv .pv-account-sides > div:last-child { padding-right: 0; }
.pv .pv-account-side { font-variant-caps: all-small-caps; letter-spacing: 0.1em; font-size: 17px; color: var(--pv-ink-2); margin-bottom: 10px; }
.pv .pv-account-total { margin-top: auto; display: flex; justify-content: space-between; font-weight: 600; border-top: 0.8px solid var(--pv-ink); border-bottom: 3px double var(--pv-ink); padding: 5px 0; margin-bottom: 14px; font-variant-numeric: lining-nums tabular-nums; }
.pv .pv-schedule { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 48px; margin-top: 48px; }
.pv .pv-schedule.is-one { grid-template-columns: minmax(0, 1fr); max-width: 44em; margin-inline: auto; }
.pv .pv-matters { list-style: none; padding: 0; margin: 0 auto; max-width: 44em; }
.pv .pv-matters li { display: flex; gap: 14px; margin: 0 0 14px; }
.pv .pv-matters .vr-manicule { margin-top: 0.42em; }
.pv .pv-runin { font-variant-caps: small-caps; letter-spacing: 0.04em; font-weight: 600; }
.pv .pv-basis { margin: 30px auto 0; max-width: 44em; font-size: 16.5px; line-height: 1.5; color: var(--pv-ink-2); }
.pv .pv-basis .pv-runin { color: var(--pv-ink); }
.pv .pv-note { margin: 18px auto 0; max-width: 40em; text-align: center; font-style: italic; color: var(--pv-ink-2); }

/* ------------------------------------------------------------ plates */
.pv .pv-plate { margin: 52px 0 0; }
.pv .pv-plate-head { display: block; text-align: center; margin-bottom: 16px; }
.pv .pv-plate-no { display: block; font-variant-caps: all-small-caps; letter-spacing: 0.2em; font-size: 16px; color: var(--pv-ink-2); }
.pv .pv-plate-title { display: block; font-style: italic; font-size: clamp(20px, 2.2vw, 23px); line-height: 1.3; text-wrap: balance; }
.pv .pv-plate-read { margin: 16px auto 0; max-width: 40em; font-size: 16.5px; line-height: 1.5; color: var(--pv-ink-2); text-align: center; text-wrap: pretty; }
.pv .pv-plate-read i { color: var(--pv-ink); }
.pv .pv-key { list-style: none; display: flex; flex-wrap: wrap; justify-content: center; gap: 6px 22px; padding: 0; margin: 16px 0 0; font-size: 17px; }
.pv .pv-key li { display: inline-flex; align-items: center; gap: 8px; }
.pv .pv-sw { display: inline-block; width: 24px; height: 14px; flex: none; }
.pv .pv-sw.is-sm { width: 16px; height: 11px; }
.pv .pv-sw.is-line { height: 0; border: 0; background: none; }
.pv .pv-sw.l-ink { border-top: 2.5px solid var(--pv-ink); }
.pv .pv-sw.l-plan { border-top: 3px solid var(--pv-plan); }
.pv .pv-sw.l-dash { border-top: 2px dashed var(--pv-ink); }
.pv .pv-sw.l-dot { border-top: 2px dotted var(--pv-ink-2); }
.pv .pv-sw.is-cross { width: auto; height: auto; font-style: normal; font-weight: 700; font-size: 1.15em; line-height: 1; }
.pv .pv-sw.is-cross::before { content: '×'; }
.pv .pv-sw.x-red { color: var(--pv-red); }
.pv .pv-sw.x-ink { color: var(--pv-ink); }
.pv .t-add { background-color: var(--pv-slip); background-image: ${HATCH('var(--pv-ink)')}; border: 1px solid var(--pv-ink); }
.pv .t-total { background-color: var(--pv-slip); background-image: ${HATCH('var(--pv-ink)', 45)}, ${HATCH('var(--pv-ink)')}; border: 1.2px solid var(--pv-ink); }
.pv .t-less { background-color: var(--pv-slip); background-image: radial-gradient(circle, var(--pv-ink) 0.95px, transparent 1.25px); background-size: 4.2px 4.2px; border: 1px solid var(--pv-ink); }
.pv .t-plan { background-color: rgba(31, 79, 143, 0.14); background-image: ${HATCH('var(--pv-plan)', 90, 4)}; border: 1.4px solid var(--pv-plan); }
.pv .t-bad { background-color: rgba(154, 43, 29, 0.06); background-image: ${HATCH('var(--pv-red)')}; border: 1.4px dashed var(--pv-red); }
.pv .t-good { background-color: var(--pv-slip); background-image: radial-gradient(circle, var(--pv-ink) 0.8px, transparent 1.1px); background-size: 7px 7px; border: 1.4px solid var(--pv-ink); }
.pv .t-box, .pv .pv-strack > i.pv-box { background: var(--pv-slip); border: 1.2px solid var(--pv-ink); }
.pv .t-c1 { background-color: var(--pv-slip); background-image: ${HATCH('var(--pv-ink)')}; border: 1px solid var(--pv-ink); }
.pv .t-c2 { background-color: var(--pv-slip); background-image: radial-gradient(circle, var(--pv-ink) 0.95px, transparent 1.25px); background-size: 4.2px 4.2px; border: 1px solid var(--pv-ink); }
.pv .t-c3 { background-color: var(--pv-slip); background-image: ${HATCH('var(--pv-ink)', 45)}, ${HATCH('var(--pv-ink)')}; border: 1px solid var(--pv-ink); }
.pv .t-c4 { background-color: var(--pv-slip); background-image: ${HATCH('var(--pv-ink)', 0, 3.6)}; border: 1px solid var(--pv-ink); }
.pv .pv-chart { position: relative; font-size: 17px; color: var(--pv-ink); font-variant-numeric: lining-nums tabular-nums; }
.pv .pv-tip { position: absolute; z-index: 40; pointer-events: none; min-width: 190px; max-width: 300px; padding: 9px 14px 10px; background: var(--pv-slip); color: var(--pv-ink); border: 1px solid var(--pv-ink); box-shadow: 0 10px 24px rgba(42, 29, 20, 0.3); font-size: 17px; line-height: 1.4; }
.pv .pv-tip-head { font-variant-caps: all-small-caps; letter-spacing: 0.08em; font-size: 16px; color: var(--pv-ink-2); margin-bottom: 3px; }
.pv .pv-tip-row { display: flex; align-items: center; gap: 9px; }
.pv .pv-tip-muted { font-style: italic; color: var(--pv-ink-2); font-size: 16px; }

/* bridge: a ledger drawn as a picture, one line per part */
.pv .pv-bridge { --label-w: clamp(136px, 36%, 290px); }
.pv .pv-brow { display: grid; grid-template-columns: var(--label-w) minmax(0, 1fr); align-items: center; min-height: 40px; }
.pv .pv-brow.is-total, .pv .pv-brow.is-result { border-top: 0.8px solid var(--pv-ink); }
.pv .pv-blabel { padding: 5px 14px 5px 0; text-align: right; line-height: 1.25; }
.pv .pv-brow.is-total .pv-blabel, .pv .pv-brow.is-result .pv-blabel { font-weight: 600; }
.pv .pv-btrack { position: relative; height: 40px; margin-right: 92px; }
.pv .pv-grid-v { position: absolute; top: 0; bottom: 0; border-left: 1px dotted var(--pv-faint); }
.pv .pv-grid-v.is-zero { border-left: 1px solid var(--pv-ink); }
.pv .pv-b { position: absolute; top: 9px; bottom: 9px; transition: left 0.36s cubic-bezier(.2,.7,.2,1), width 0.36s cubic-bezier(.2,.7,.2,1), opacity 0.2s, transform 0.8s cubic-bezier(.2,.7,.2,1); transform-origin: left center; }
.pv .pv-b.is-neg { transform-origin: right center; }
.pv .pv-chart:not(.is-in) .pv-b { transform: scaleX(0); }
.pv .pv-bval { position: absolute; top: 50%; transform: translateY(-50%); white-space: nowrap; transition: left 0.36s cubic-bezier(.2,.7,.2,1); }
.pv .pv-brow.is-total .pv-bval, .pv .pv-brow.is-result .pv-bval { font-weight: 600; }
.pv .pv-bval.is-bad { color: var(--pv-red); }
.pv .pv-bval.is-plan { color: var(--pv-plan); font-weight: 600; }
.pv .pv-conn { position: absolute; top: 31px; height: 18px; border-left: 1px solid var(--pv-ink-3); transition: left 0.36s cubic-bezier(.2,.7,.2,1); }
.pv .pv-baxis { display: grid; grid-template-columns: var(--label-w) minmax(0, 1fr); margin-top: 6px; }
.pv .pv-baxis > div { position: relative; height: 22px; margin-right: 92px; }
.pv .pv-baxis > div > span { position: absolute; transform: translateX(-50%); white-space: nowrap; font-size: 15.5px; color: var(--pv-ink-2); }
.pv .pv-target { position: absolute; top: -4px; bottom: -4px; border-left: 2px dashed var(--pv-ink); }
@media (hover: hover) {
  .pv .pv-rows:has(.pv-brow:hover) .pv-brow:not(:hover) .pv-b { opacity: 0.35; }
  .pv .pv-cols:has(.pv-col:hover) .pv-col:not(:hover) { opacity: 0.4; }
}

/* graph paper and columns */
.pv .pv-svg { display: block; overflow: visible; }
.pv .pv-svg text { fill: var(--pv-ink); font-family: inherit; }
.pv .pv-cols { position: relative; height: 250px; margin-left: 62px; border-bottom: 1.2px solid var(--pv-ink); }
.pv .pv-col { position: absolute; bottom: 0; display: flex; flex-direction: column-reverse; gap: 2px; transform-origin: bottom center; transition: transform 0.8s cubic-bezier(.2,.7,.2,1), opacity 0.2s, height 0.36s; }
.pv .pv-chart:not(.is-in) .pv-col { transform: scaleY(0); }
.pv .pv-col > i { display: block; min-height: 1px; }
.pv .pv-grid-h { position: absolute; left: 0; right: 0; border-top: 1px dotted var(--pv-faint); }
.pv .pv-ytick { position: absolute; right: calc(100% + 9px); transform: translateY(-50%); font-size: 15.5px; color: var(--pv-ink-2); white-space: nowrap; }
.pv .pv-xtick { position: absolute; top: 6px; transform: translateX(-50%); font-size: 15.5px; color: var(--pv-ink-2); white-space: nowrap; }

/* strips: each need as a box to be filled */
.pv .pv-strips { --label-w: clamp(150px, 32%, 260px); }
.pv .pv-strip { display: grid; grid-template-columns: var(--label-w) minmax(0, 1fr) auto; gap: 4px 16px; align-items: center; padding: 11px 0; width: 100%; text-align: left; }
.pv .pv-strip + .pv-strip { border-top: 1px dotted var(--pv-faint); }
.pv button.pv-strip:hover .pv-strip-label b { text-decoration: underline; text-underline-offset: 3px; }
.pv .pv-strip-label { min-width: 0; line-height: 1.3; }
.pv .pv-strip-label b { display: block; font-weight: 600; }
.pv .pv-strip-label span { display: block; font-size: 16px; font-style: italic; color: var(--pv-ink-2); }
.pv .pv-strack { position: relative; height: 26px; }
.pv .pv-strack > i { position: absolute; top: 0; bottom: 0; transition: left 0.36s cubic-bezier(.2,.7,.2,1), width 0.36s cubic-bezier(.2,.7,.2,1); }
.pv .pv-strack > i.pv-box { left: 0; }
.pv .pv-strip-end { text-align: right; white-space: nowrap; font-variant-numeric: lining-nums tabular-nums; }
.pv .pv-strip-end.is-short { color: var(--pv-red); }
.pv .pv-strip-end small { display: block; font-size: 15px; font-style: italic; color: var(--pv-ink-2); }
.pv .pv-strip-group { margin-top: 18px; font-variant-caps: all-small-caps; letter-spacing: 0.14em; font-size: 17px; color: var(--pv-ink-2); border-bottom: 0.8px solid var(--pv-ink); }

/* ------------------------------------------------------------ the title leaf */
.pv .pv-title-leaf { text-align: center; }
.pv .pv-title-top > span:only-child { margin: 0 auto; }
.pv .pv-title-top { display: flex; justify-content: space-between; gap: 12px; font-variant-caps: all-small-caps; letter-spacing: 0.12em; font-size: 16px; color: var(--pv-ink-2); }
.pv .pv-title-kicker { margin-top: 44px; font-variant-caps: small-caps; letter-spacing: 0.16em; font-size: clamp(20px, 2.4vw, 24px); }
.pv .pv-title-of { font-style: italic; font-size: 20px; margin: 4px 0; }
.pv .pv-title-main { font-weight: 400; font-size: clamp(34px, 5.2vw, 54px); line-height: 1.08; margin: 0 auto 20px; font-variant-caps: small-caps; letter-spacing: 0.03em; max-width: 15em; text-wrap: balance; }
.pv .pv-title-sub { font-style: italic; font-size: clamp(19px, 2.1vw, 22px); line-height: 1.45; max-width: 28em; margin: 20px auto 0; color: var(--pv-ink-2); text-wrap: balance; }
.pv .pv-imprint { margin: 18px 0 0; font-size: 17px; color: var(--pv-ink-2); }
.pv .pv-verdict { max-width: 34em; margin: 34px auto 0; font-size: clamp(20px, 2.3vw, 23px); line-height: 1.5; text-wrap: balance; }
.pv .pv-contents { text-align: left; max-width: 46em; margin: 40px auto 0; }
.pv .pv-contents-head { text-align: center; font-variant-caps: all-small-caps; letter-spacing: 0.2em; font-size: 17px; border-bottom: 0.8px solid var(--pv-ink); padding-bottom: 4px; }
.pv .pv-contents ol { list-style: none; margin: 0; padding: 0; }
.pv .pv-contents li + li { border-top: 1px dotted var(--pv-faint); }
.pv .pv-contents li > button { display: block; width: 100%; text-align: left; padding: 11px 0; }
.pv .pv-contents li > button:hover .pv-q-text { text-decoration: underline; text-underline-offset: 3px; }
.pv .pv-q { display: grid; grid-template-columns: 2.6em minmax(0, 1fr); align-items: baseline; }
.pv .pv-q-no { font-variant-caps: all-small-caps; letter-spacing: 0.04em; color: var(--pv-ink-2); }
.pv .pv-q-text { font-size: 20px; line-height: 1.35; }
.pv .pv-q-status { display: flex; flex-wrap: wrap; gap: 4px 22px; margin: 4px 0 0 2.6em; font-size: 17px; }
.pv .pv-status { display: inline-flex; align-items: center; gap: 8px; }
.pv .pv-status.is-short { color: var(--pv-red); }
.pv .pv-status .vr-manicule { width: 26px; height: 13px; }
.pv .pv-status .vr-fleuron { width: 14px; height: 14px; }
.pv .pv-mark-na { color: var(--pv-ink-3); }
.pv .pv-was { font-style: italic; color: var(--pv-ink-2); font-size: 0.94em; }
.pv .pv-begin { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 10px; margin-top: 40px; }
.pv .pv-begin > span { font-style: italic; color: var(--pv-ink-2); margin-right: 4px; }

/* ------------------------------------------------------------ what was agreed */
.pv .pv-articles { max-width: 46em; margin: 44px auto 0; }
.pv .pv-articles > div + div { margin-top: 26px; }
.pv .pv-articles ol { list-style: none; margin: 0; padding: 0; }
.pv .pv-article { display: grid; grid-template-columns: 2.2em minmax(0, 1fr) auto; gap: 0 8px; align-items: start; margin: 0 0 12px; }
.pv .pv-article-no { font-variant-numeric: lining-nums; color: var(--pv-ink-2); padding-top: 1px; }
.pv .pv-article .pv-leader { margin: 0; }
.pv .pv-article dd { color: var(--pv-plan); font-weight: 600; }
.pv .pv-strike { width: 36px; height: 36px; margin-top: -5px; display: inline-flex; align-items: center; justify-content: center; color: var(--pv-ink-3); border-radius: 99px; }
.pv .pv-strike:hover { color: var(--pv-red); background: rgba(154, 43, 29, 0.08); }
.pv .pv-plan-actions { display: flex; flex-direction: column; align-items: center; gap: 4px; margin-top: 40px; }
.pv .pv-plan-actions .pv-begin { margin-top: 12px; }

/* ------------------------------------------------------------ levers */
.pv .pv-levers { margin-top: 8px; }
.pv .pv-lever-grid { display: grid; gap: 16px; max-width: 640px; margin: 0 auto; align-items: start; }
.pv .pv-lever-grid.is-2 { max-width: none; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.pv .pv-lever-grid.is-3 { max-width: none; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.pv .pv-lever-grid > .is-wide { grid-column: 1 / -1; }
.pv .pv-slip { background: var(--pv-slip); border: 1px solid var(--pv-ink); padding: 18px 20px 20px; }
.pv .pv-lever-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.pv .pv-lever-label { font-size: 19px; font-weight: 600; }
.pv .pv-lever-value { font-size: 26px; line-height: 1.2; font-variant-numeric: lining-nums tabular-nums; white-space: nowrap; }
.pv .pv-hint { margin: 6px 0 0; font-size: 16.5px; line-height: 1.45; color: var(--pv-ink-2); }
.pv .pv-range-wrap { position: relative; margin-top: 6px; }
.pv .pv-range-wrap.has-marker { padding-bottom: 30px; }
.pv input[type='range'].pv-range { -webkit-appearance: none; appearance: none; display: block; width: 100%; height: 30px; background: transparent; cursor: pointer; margin: 0; }
.pv input[type='range'].pv-range:focus { outline: none; }
.pv input[type='range'].pv-range::-webkit-slider-runnable-track { height: 4px; background: linear-gradient(to right, var(--pv-plan) 0 var(--fill, 0%), var(--pv-ink-3) var(--fill, 0%) 100%); background-size: 100% 100%; }
.pv input[type='range'].pv-range::-moz-range-track { height: 4px; background: linear-gradient(to right, var(--pv-plan) 0 var(--fill, 0%), var(--pv-ink-3) var(--fill, 0%) 100%); }
.pv input[type='range'].pv-range::-webkit-slider-thumb { -webkit-appearance: none; width: 26px; height: 26px; margin-top: -11px; border-radius: 50%; background: var(--pv-slip); border: 2px solid var(--pv-ink); box-shadow: inset 0 0 0 5px var(--pv-slip), inset 0 0 0 13px var(--pv-plan); }
.pv input[type='range'].pv-range::-moz-range-thumb { width: 20px; height: 20px; border-radius: 50%; background: var(--pv-plan); border: 2px solid var(--pv-ink); }
.pv input[type='range'].pv-range:focus-visible::-webkit-slider-thumb { box-shadow: inset 0 0 0 5px var(--pv-slip), inset 0 0 0 13px var(--pv-plan), 0 0 0 5px var(--pv-plan-wash); }
.pv .pv-marker { position: absolute; top: 26px; display: flex; flex-direction: column; align-items: center; transform: translateX(-50%); font-size: 15.5px; font-style: italic; color: var(--pv-ink-2); line-height: 1; white-space: nowrap; }
.pv .pv-marker::before { content: ''; width: 0; height: 0; border-left: 5px solid transparent; border-right: 5px solid transparent; border-bottom: 7px solid var(--pv-ink); margin-bottom: 4px; }
.pv .pv-marker:hover { color: var(--pv-ink); text-decoration: underline; }
.pv .pv-blank-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 8px; margin-top: 12px; font-size: 17px; color: var(--pv-ink-2); }
.pv .pv-blank { display: inline-flex; align-items: baseline; gap: 3px; color: var(--pv-ink); }
.pv .pv-blank input { width: 8.5em; border: 0; border-bottom: 1.4px dotted var(--pv-ink); background: transparent; font: inherit; font-size: 18px; color: var(--pv-ink); text-align: right; padding: 0 2px; font-variant-numeric: lining-nums tabular-nums; outline: none; }
.pv .pv-blank input:focus { border-bottom-style: solid; border-bottom-color: var(--pv-plan); }
.pv .pv-blank input::placeholder { color: var(--pv-ink-3); font-style: italic; }
.pv .pv-tabs { display: inline-flex; border: 1px solid var(--pv-ink); background: var(--pv-slip); }
.pv .pv-tabs button { padding: 5px 18px; min-height: 42px; font-size: 18px; color: var(--pv-ink); }
.pv .pv-tabs button + button { border-left: 1px solid var(--pv-ink); }
.pv .pv-tabs button[aria-checked='true'] { background: var(--pv-ink); color: var(--pv-paper); }
.pv .pv-tabs.is-sm button { padding: 3px 12px; min-height: 38px; font-size: 17px; }
.pv .pv-stress { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 8px 12px; margin-top: 6px; font-style: italic; font-size: 18px; color: var(--pv-ink-2); }
.pv .pv-stress .pv-tabs { font-style: normal; }
.pv .pv-check { display: flex; align-items: flex-start; gap: 12px; padding: 10px 12px; border: 1px solid var(--pv-faint); cursor: pointer; }
.pv .pv-check + .pv-check { margin-top: 8px; }
.pv .pv-check.is-on { border-color: var(--pv-plan); background: var(--pv-plan-wash); }
.pv .pv-check input { margin-top: 6px; width: 18px; height: 18px; accent-color: var(--pv-plan); flex: none; }
.pv .pv-check-label { display: block; font-weight: 600; }
.pv .pv-check-detail { display: block; font-size: 16px; color: var(--pv-ink-2); }
.pv .pv-stepper { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.pv .pv-stepper-ctl { display: flex; align-items: center; gap: 8px; }
.pv .pv-step { width: 42px; height: 42px; display: inline-flex; align-items: center; justify-content: center; border: 1px solid var(--pv-ink) !important; color: var(--pv-ink); }
.pv .pv-step:hover { background: var(--pv-plan-wash); }
.pv .pv-step:disabled { opacity: 0.3; cursor: default; }

/* ------------------------------------------------------------ buttons, notices, the dialog */
.pv .pv-button { display: inline-flex; align-items: center; gap: 9px; padding: 11px 22px; background: var(--pv-ink); color: var(--pv-paper); font-size: 19px; }
.pv .pv-button:hover { background: #000; }
.pv .pv-button:disabled { opacity: 0.45; cursor: not-allowed; }
.pv .pv-button-2 { display: inline-flex; align-items: center; gap: 7px; padding: 7px 15px; border: 1px solid var(--pv-ink); color: var(--pv-ink); font-size: 18px; }
.pv .pv-button-2:hover { background: var(--pv-plan-wash); }
.pv .pv-notice { position: fixed; bottom: calc(72px + env(safe-area-inset-bottom, 0px)); left: 50%; transform: translateX(-50%); z-index: 40; width: min(38rem, calc(100% - 2rem)); display: flex; align-items: flex-start; gap: 12px; padding: 14px 16px; background: var(--pv-slip); border: 1px solid var(--pv-ink); box-shadow: 0 12px 30px rgba(0, 0, 0, 0.35); font-size: 18px; }
.pv .pv-notice a { color: var(--pv-plan); font-weight: 600; text-decoration: underline; text-underline-offset: 2px; }
.pv .pv-dialog { position: fixed; inset: 0; z-index: 50; display: flex; align-items: center; justify-content: center; padding: 16px; }
.pv .pv-dialog-back { position: absolute; inset: 0; background: rgba(20, 15, 11, 0.6); }
.pv .pv-dialog-card { position: relative; width: 100%; max-width: 34rem; max-height: calc(100vh - 32px); overflow-y: auto; padding: 26px 26px 22px; background-color: var(--pv-paper); background-image: ${GRAIN}; border: 1px solid var(--pv-ink); box-shadow: 0 24px 60px rgba(0, 0, 0, 0.5); }
.pv .pv-dialog-card h2 { margin: 0; font-weight: 400; font-variant-caps: small-caps; letter-spacing: 0.05em; font-size: 24px; line-height: 1.25; }
.pv .pv-dialog-card ul { list-style: none; margin: 16px 0 0; padding: 0; }
.pv .pv-dialog-card li { display: flex; gap: 10px; margin-bottom: 8px; }
.pv .pv-dialog-card li .vr-manicule { margin-top: 0.4em; }

@media (max-width: 860px) {
  .pv .pv-lever-grid.is-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
  .pv { font-size: 18px; --pv-head-h: 100px; }
  .pv .pv-top-title small { display: none; }
  .pv .pv-nav a { font-size: 17px; padding: 0 10px; }
  .pv .pv-desk { padding: 10px 0 110px; }
  .pv .pv-leaf { margin-bottom: 10px; padding: 34px 16px 64px; box-shadow: none; }
  .pv .pv-register { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .pv .pv-register > div:nth-child(3) { border-left: 0; }
  .pv .pv-register > div:nth-child(n + 3) { border-top: 0.8px solid var(--pv-ink); }
  .pv .pv-account-sides, .pv .pv-schedule { grid-template-columns: minmax(0, 1fr); }
  .pv .pv-account-sides > div:first-child { border-right: 0; padding-right: 0; }
  .pv .pv-account-sides > div:last-child { padding-left: 0; border-top: 0.8px solid var(--pv-ink); }
  .pv .pv-btrack, .pv .pv-baxis > div { margin-right: 74px; }
  .pv .pv-baxis > div > span.is-alt { display: none; }
  .pv .pv-bridge { --label-w: 40%; }
  .pv .pv-blabel { padding-right: 10px; font-size: 16.5px; }
  .pv .pv-strip { grid-template-columns: minmax(0, 1fr) auto; }
  .pv .pv-strip .pv-strack { grid-column: 1 / -1; grid-row: 2; }
  .pv .pv-lever-grid.is-2, .pv .pv-lever-grid.is-3 { grid-template-columns: minmax(0, 1fr); }
  .pv .pv-cols { margin-left: 54px; height: 220px; }
  .pv .pv-slip { padding: 16px 14px 18px; }
  .pv .pv-foot-row { font-size: 17px; gap: 10px; }
  .pv .pv-foot-see { display: none; }
}
@media (min-width: 721px) {
  .pv .pv-matters li > div, .pv .pv-basis { text-align: justify; hyphens: auto; }
}
@media (prefers-reduced-motion: reduce) {
  .pv .pv-b, .pv .pv-col, .pv .pv-strack > i, .pv .pv-bval, .pv .pv-conn { transition: none; }
  .pv .pv-chart:not(.is-in) .pv-b, .pv .pv-chart:not(.is-in) .pv-col { transform: none; }
}
`;
