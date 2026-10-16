/*
 * The planning session's look: a printed financial survey on cream paper
 * (the vintage report's ink, rules and EB Garamond), with modern charts set
 * into it as plates. Charts follow a Highcharts-style system: Inter type,
 * whisper gridlines, one action colour (the plan, in blue) against warm greys,
 * red and green only for what is short or met, and a dark tooltip.
 *
 * Chart colours were checked with the palette validator on both surfaces
 * (plan blue with the status red, light and dark).
 */
export const PRESENT_CSS = `
.pv {
  color-scheme: light;
  --pv-page: #f4ead8;
  --pv-paper: url('/vintage/paper-grain.png'), radial-gradient(ellipse 80% 60% at 50% 30%, rgba(255, 250, 238, 0.5) 0%, rgba(255, 250, 238, 0) 65%);
  --pv-surface: #fbf6ea;
  --pv-raised: #fffdf8;
  --pv-ink: #2a1d14;
  --pv-ink-2: #574333;
  --pv-muted: #85725c;
  --pv-faint: #c9b597;
  --pv-grid: rgba(42, 29, 20, 0.09);
  --pv-axis: #b5a282;
  --pv-border: rgba(42, 29, 20, 0.2);
  --pv-plan: #1f66b8;
  --pv-plan-wash: rgba(31, 102, 184, 0.1);
  --pv-today: #a3957f;
  --pv-soft: #d6cab2;
  --pv-good: #4c7f2e;
  --pv-warning: #b98226;
  --pv-critical: #a8382b;
  --pv-critical-wash: rgba(168, 56, 43, 0.1);
  --pv-tip: rgba(33, 24, 17, 0.96);
  --pv-tip-ink: #fbf6ea;
  --pv-head-h: 108px;
  font-family: var(--vr-font, 'EB Garamond'), 'EB Garamond', Garamond, Georgia, 'Times New Roman', serif;
  background-color: var(--pv-page);
  background-image: var(--pv-paper);
  color: var(--pv-ink);
  font-size: 17px;
  line-height: 1.5;
  font-variant-numeric: lining-nums;
  font-kerning: normal;
}
.pv[data-theme='dark'] {
  color-scheme: dark;
  --pv-page: #17120d;
  --pv-paper: radial-gradient(ellipse 80% 60% at 50% 20%, rgba(255, 236, 200, 0.05) 0%, rgba(255, 236, 200, 0) 70%);
  --pv-surface: #211a13;
  --pv-raised: #2a2119;
  --pv-ink: #efe5d1;
  --pv-ink-2: #cdbd9f;
  --pv-muted: #9a8a72;
  --pv-faint: #5c4e3d;
  --pv-grid: rgba(239, 229, 209, 0.08);
  --pv-axis: #5c4e3d;
  --pv-border: rgba(239, 229, 209, 0.16);
  --pv-plan: #3f86d4;
  --pv-plan-wash: rgba(63, 134, 212, 0.16);
  --pv-today: #8c7f6c;
  --pv-soft: #4d4235;
  --pv-good: #7fa65c;
  --pv-warning: #c9922c;
  --pv-critical: #d4553f;
  --pv-critical-wash: rgba(212, 85, 63, 0.16);
  --pv-tip: rgba(250, 244, 232, 0.97);
  --pv-tip-ink: #211a13;
}
.pv * { box-sizing: border-box; }
.pv b, .pv strong { font-weight: 600; }
.pv .pv-sans { font-family: var(--font-sans), ui-sans-serif, system-ui, sans-serif; }
.pv .pv-sc { font-variant-caps: all-small-caps; letter-spacing: 0.12em; }
.pv .pv-num { font-variant-numeric: lining-nums tabular-nums; }

/* ------------------------------------------------------------ masthead and contents bar */
.pv .pv-head { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 30; background-color: var(--pv-page); background-image: var(--pv-paper); border-bottom: 3px double var(--pv-ink); }
.pv .pv-head-row { display: flex; align-items: center; gap: 12px; height: 56px; padding-inline: max(16px, env(safe-area-inset-left, 0px)) max(16px, env(safe-area-inset-right, 0px)); }
.pv .pv-head-title { min-width: 0; flex: 1; text-align: center; }
.pv .pv-head-title > div:first-child { font-variant-caps: all-small-caps; letter-spacing: 0.2em; font-size: 13px; color: var(--pv-ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pv .pv-head-title > div:last-child { font-size: 18px; line-height: 1.15; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pv .pv-icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 38px; height: 38px; border-radius: 999px; color: var(--pv-ink-2); }
.pv .pv-icon-btn:hover, .pv .pv-icon-btn[aria-pressed='true'] { background: var(--pv-plan-wash); color: var(--pv-ink); }
.pv .pv-nav { display: flex; gap: 2px; overflow-x: auto; scrollbar-width: none; padding: 0 max(12px, env(safe-area-inset-left, 0px)) 6px; height: 46px; align-items: center; mask-image: linear-gradient(to right, transparent 0, #000 16px, #000 calc(100% - 16px), transparent 100%); }
.pv .pv-nav::-webkit-scrollbar { display: none; }
.pv .pv-nav a { flex: none; display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 4px; font-variant-caps: all-small-caps; letter-spacing: 0.1em; font-size: 16px; color: var(--pv-ink-2); text-decoration: none; border-bottom: 2px solid transparent; }
.pv .pv-nav a:hover { color: var(--pv-ink); background: var(--pv-plan-wash); }
.pv .pv-nav a[aria-current='true'] { color: var(--pv-ink); border-bottom-color: var(--pv-ink); border-radius: 4px 4px 0 0; font-weight: 600; }
.pv .pv-nav .pv-dot { width: 7px; height: 7px; border-radius: 99px; flex: none; }

/* ------------------------------------------------------------ the page */
.pv .pv-main { max-width: 1140px; margin: 0 auto; padding-inline: max(16px, env(safe-area-inset-left, 0px)) max(16px, env(safe-area-inset-right, 0px)); padding-bottom: 120px; }
.pv .pv-section { scroll-margin-top: calc(var(--pv-head-h) + env(safe-area-inset-top, 0px) + 8px); padding-block: clamp(36px, 7vh, 72px); }
.pv .pv-section + .pv-section { border-top: 1px solid var(--pv-faint); }
.pv .pv-kicker { display: block; text-align: center; font-variant-caps: all-small-caps; letter-spacing: 0.22em; font-size: 15px; color: var(--pv-ink-2); }
.pv .pv-rule3 { display: flex; justify-content: center; gap: 9px; margin: 10px 0 14px; }
.pv .pv-rule3 span { display: block; height: 1.3px; width: 90px; background: var(--pv-ink); }
.pv .pv-rule3 span:nth-child(2) { width: 30px; }
.pv .pv-title { margin: 0 auto; max-width: 21em; text-align: center; font-weight: 400; font-size: clamp(30px, min(4.2vw, 7vh), 52px); line-height: 1.08; letter-spacing: -0.005em; text-wrap: balance; }
.pv .pv-answer { margin: 16px auto 0; max-width: 40em; text-align: center; font-style: italic; font-size: clamp(19px, 2.1vw, 25px); line-height: 1.4; color: var(--pv-ink-2); text-wrap: balance; }
.pv .pv-answer b { font-style: normal; color: var(--pv-ink); white-space: nowrap; }
.pv .pv-lead { font-style: normal; font-variant-caps: all-small-caps; letter-spacing: 0.08em; font-weight: 600; color: var(--pv-ink); }
.pv .pv-answer svg { display: inline-block; vertical-align: -0.12em; margin-right: 6px; }
.pv .pv-person { display: flex; justify-content: center; margin-top: 16px; }

/* ------------------------------------------------------------ the ledger of figures */
.pv .pv-register { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-top: 1.4px solid var(--pv-ink); border-bottom: 1.4px solid var(--pv-ink); margin: clamp(22px, 4vh, 36px) 0 0; }
.pv .pv-register > div { padding: 10px 12px 12px; text-align: center; min-width: 0; }
.pv .pv-register > div + div { border-left: 0.6px solid var(--pv-ink); }
.pv .pv-register-label { font-variant-caps: all-small-caps; letter-spacing: 0.12em; font-size: 15px; color: var(--pv-ink-2); }
.pv .pv-register-value { font-size: clamp(24px, 2.6vw, 34px); line-height: 1.15; font-variant-numeric: lining-nums tabular-nums; }
.pv .pv-register-sub { font-size: 14.5px; font-style: italic; color: var(--pv-ink-2); }
.pv .pv-register .is-bad .pv-register-value { color: var(--pv-critical); }
.pv .pv-register .is-good .pv-register-value { color: var(--pv-good); }
.pv .pv-register .is-plan .pv-register-value { color: var(--pv-plan); }

/* ------------------------------------------------------------ plates (charts) */
.pv .pv-plate { background: var(--pv-surface); border: 1px solid var(--pv-border); border-radius: 3px; padding: 18px 20px 16px; box-shadow: 0 1px 0 rgba(42, 29, 20, 0.05), 0 14px 32px -22px rgba(42, 29, 20, 0.45); min-width: 0; }
.pv .pv-plate-title { font-family: var(--font-sans), system-ui, sans-serif; font-size: 15.5px; font-weight: 600; line-height: 1.35; color: var(--pv-ink); margin: 0; text-wrap: balance; }
.pv .pv-plate-title b { font-weight: 700; }
.pv .pv-plate-sub { font-family: var(--font-sans), system-ui, sans-serif; font-size: 13px; line-height: 1.4; color: var(--pv-ink-2); margin: 2px 0 14px; }
.pv .pv-chart { position: relative; font-family: var(--font-sans), system-ui, sans-serif; font-size: 12.5px; color: var(--pv-ink-2); font-variant-numeric: tabular-nums; }
.pv .pv-legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 12px; font-family: var(--font-sans), system-ui, sans-serif; font-size: 12.5px; color: var(--pv-ink-2); }
.pv .pv-legend span { display: inline-flex; align-items: center; gap: 6px; }
.pv .pv-legend i { display: inline-block; width: 12px; height: 12px; border-radius: 2px; }
.pv .pv-legend i.is-line { height: 3px; border-radius: 2px; width: 16px; }
.pv .pv-tip { position: absolute; z-index: 40; pointer-events: none; min-width: 170px; max-width: 280px; padding: 8px 11px; border-radius: 8px; background: var(--pv-tip); color: var(--pv-tip-ink); font-family: var(--font-sans), system-ui, sans-serif; font-size: 12.5px; line-height: 1.45; box-shadow: 0 10px 28px rgba(0, 0, 0, 0.28); }
.pv .pv-tip-head { font-weight: 600; margin-bottom: 2px; }
.pv .pv-tip-row { display: flex; align-items: center; gap: 7px; }
.pv .pv-tip-row i { width: 8px; height: 8px; border-radius: 99px; flex: none; }
.pv .pv-tip-muted { opacity: 0.75; }

/* bridge (horizontal waterfall) */
.pv .pv-bridge { --label-w: clamp(118px, 34%, 250px); }
.pv .pv-brow { display: grid; grid-template-columns: var(--label-w) minmax(0, 1fr); align-items: center; min-height: 30px; }
.pv .pv-brow.is-total, .pv .pv-brow.is-result { border-top: 1px solid var(--pv-grid); }
.pv .pv-blabel { padding: 4px 10px 4px 0; text-align: right; line-height: 1.25; color: var(--pv-ink-2); }
.pv .pv-brow.is-total .pv-blabel, .pv .pv-brow.is-result .pv-blabel { color: var(--pv-ink); font-weight: 600; }
.pv .pv-btrack { position: relative; height: 30px; margin-right: 76px; }
.pv .pv-grid-v { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--pv-grid); }
.pv .pv-grid-v.is-zero { background: var(--pv-axis); }
.pv .pv-bar { position: absolute; top: 6px; bottom: 6px; border-radius: 2px; transition: left 0.36s cubic-bezier(.2,.7,.2,1), width 0.36s cubic-bezier(.2,.7,.2,1), opacity 0.2s, transform 0.85s cubic-bezier(.2,.7,.2,1); transform-origin: left center; }
.pv .pv-bar.is-neg { transform-origin: right center; }
.pv .pv-chart:not(.is-in) .pv-bar { transform: scaleX(0); }
.pv .pv-bval { position: absolute; top: 50%; transform: translateY(-50%); white-space: nowrap; color: var(--pv-ink); font-weight: 500; transition: left 0.36s cubic-bezier(.2,.7,.2,1); }
.pv .pv-conn { position: absolute; top: 24px; height: 12px; width: 1px; background: var(--pv-axis); transition: left 0.36s cubic-bezier(.2,.7,.2,1); }
.pv .pv-baxis { display: grid; grid-template-columns: var(--label-w) minmax(0, 1fr); margin-top: 4px; }
.pv .pv-baxis > div { position: relative; height: 18px; margin-right: 76px; }
.pv .pv-baxis > div > span { position: absolute; transform: translateX(-50%); white-space: nowrap; font-size: 11.5px; color: var(--pv-muted); }
.pv .pv-target { position: absolute; top: -2px; bottom: -2px; border-left: 2px dashed var(--pv-ink-2); }
.pv .pv-rows:has(.pv-brow:hover) .pv-brow:not(:hover) .pv-bar { opacity: 0.4; }
.pv .k-add { background: var(--pv-today); }
.pv .k-less { background: var(--pv-soft); box-shadow: inset 0 0 0 1px var(--pv-axis); }
.pv .k-total { background: var(--pv-ink); }
.pv .k-plan { background: var(--pv-plan); }
.pv .k-bad { background: var(--pv-critical); }
.pv .k-good { background: var(--pv-good); }

/* columns, bullets, dumbbells */
.pv .pv-cols { position: relative; height: 210px; margin-left: 52px; border-bottom: 1px solid var(--pv-axis); }
.pv .pv-col { position: absolute; bottom: 0; display: flex; flex-direction: column-reverse; gap: 2px; transform-origin: bottom center; transition: transform 0.85s cubic-bezier(.2,.7,.2,1), opacity 0.2s; }
.pv .pv-chart:not(.is-in) .pv-col { transform: scaleY(0); }
.pv .pv-col > i { display: block; border-radius: 2px 2px 0 0; }
.pv .pv-cols:has(.pv-col:hover) .pv-col:not(:hover) { opacity: 0.45; }
.pv .pv-grid-h { position: absolute; left: 0; right: 0; height: 1px; background: var(--pv-grid); }
.pv .pv-ytick { position: absolute; right: calc(100% + 8px); transform: translateY(50%); font-size: 11.5px; color: var(--pv-muted); white-space: nowrap; }
.pv .pv-xtick { position: absolute; top: 4px; transform: translateX(-50%); font-size: 11.5px; color: var(--pv-muted); white-space: nowrap; }
.pv .pv-bullet { display: grid; grid-template-columns: var(--label-w, clamp(118px, 30%, 230px)) minmax(0, 1fr) auto; gap: 4px 14px; align-items: center; padding: 10px 0; }
.pv .pv-bullet + .pv-bullet { border-top: 1px solid var(--pv-grid); }
.pv .pv-btrack2 { position: relative; height: 28px; }
.pv .pv-ghost { position: absolute; top: 4px; bottom: 4px; left: 0; background: var(--pv-soft); border-radius: 2px; }
.pv .pv-fill { position: absolute; top: 9px; bottom: 9px; left: 0; border-radius: 2px; transform-origin: left center; transition: width 0.36s cubic-bezier(.2,.7,.2,1), transform 0.85s cubic-bezier(.2,.7,.2,1); }
.pv .pv-chart:not(.is-in) .pv-fill { transform: scaleX(0); }
.pv .pv-mark { position: absolute; top: 0; bottom: 0; width: 3px; margin-left: -1.5px; background: var(--pv-ink); border-radius: 2px; }
.pv .pv-dumb { display: grid; grid-template-columns: var(--label-w, clamp(140px, 30%, 240px)) minmax(0, 1fr) 58px; align-items: center; min-height: 28px; gap: 0 12px; }
.pv .pv-dumb-group { margin-top: 10px; padding-top: 6px; border-top: 1px solid var(--pv-grid); font-variant-caps: all-small-caps; letter-spacing: 0.12em; font-size: 13px; color: var(--pv-ink-2); }
.pv .pv-dumb-track { position: relative; height: 22px; }
.pv .pv-dumb-line { position: absolute; top: 10px; height: 3px; background: var(--pv-plan-wash); border-radius: 2px; transition: left 0.36s, width 0.36s; }
.pv .pv-dumb-dot { position: absolute; top: 4px; width: 14px; height: 14px; margin-left: -7px; border-radius: 99px; border: 2px solid var(--pv-surface); transition: left 0.36s cubic-bezier(.2,.7,.2,1); }

/* ------------------------------------------------------------ what the question must address */
.pv .pv-address h3 { margin: 0 0 6px; font-weight: 400; font-variant-caps: all-small-caps; letter-spacing: 0.14em; font-size: 17px; border-bottom: 0.8px solid var(--pv-ink); padding-bottom: 3px; }
.pv .pv-address h3 + ol { margin-top: 8px; }
.pv .pv-address ol { list-style: none; margin: 0 0 22px; padding: 0; }
.pv .pv-address li { display: grid; grid-template-columns: 2.1em minmax(0, 1fr); margin-bottom: 11px; }
.pv .pv-address .pv-no { font-variant-caps: all-small-caps; letter-spacing: 0.04em; color: var(--pv-ink-2); padding-top: 1px; }
.pv .pv-leader { display: flex; align-items: last baseline; gap: 0; }
.pv .pv-leader > span:first-child { font-variant-caps: small-caps; letter-spacing: 0.04em; font-weight: 600; }
.pv .pv-leader > span:last-child:not(:first-child) { flex: none; padding-left: 0.35em; font-variant-numeric: lining-nums tabular-nums; }
.pv .pv-dots { flex: 1 1 1.5em; min-width: 1.5em; align-self: stretch; position: relative; color: var(--pv-muted); }
.pv .pv-dots::after { content: '. . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .'; position: absolute; left: 0.3em; right: 0; bottom: 0.15em; overflow: hidden; white-space: nowrap; letter-spacing: 0.1em; }
.pv .pv-address p { margin: 1px 0 0; font-size: 16px; line-height: 1.42; color: var(--pv-ink-2); text-wrap: pretty; }
.pv .pv-basis { display: flex; gap: 10px; margin-top: 6px; font-style: italic; font-size: 15px; line-height: 1.45; color: var(--pv-ink-2); }
.pv .pv-basis p { margin: 0 0 4px; }

/* ------------------------------------------------------------ levers */
.pv .pv-levers-head { text-align: center; margin: clamp(26px, 5vh, 44px) 0 14px; }
.pv .pv-card { background: var(--pv-surface); border: 1px solid var(--pv-border); border-radius: 3px; }
.pv .pv-tabs { display: inline-flex; border: 1px solid var(--pv-ink); border-radius: 3px; overflow: hidden; }
.pv .pv-tabs button { padding: 4px 14px; font-variant-caps: all-small-caps; letter-spacing: 0.1em; font-size: 16px; color: var(--pv-ink-2); }
.pv .pv-tabs button + button { border-left: 1px solid var(--pv-ink); }
.pv .pv-tabs button[aria-checked='true'] { background: var(--pv-ink); color: var(--pv-page); }
.pv input[type='range'].pv-range { -webkit-appearance: none; appearance: none; width: 100%; height: 28px; background: transparent; cursor: pointer; }
.pv input[type='range'].pv-range:focus { outline: none; }
.pv input[type='range'].pv-range::-webkit-slider-runnable-track { height: 4px; border-radius: 999px; background: linear-gradient(to right, var(--pv-plan) 0 var(--fill, 0%), var(--pv-faint) var(--fill, 0%) 100%); }
.pv input[type='range'].pv-range::-moz-range-track { height: 4px; border-radius: 999px; background: linear-gradient(to right, var(--pv-plan) 0 var(--fill, 0%), var(--pv-faint) var(--fill, 0%) 100%); }
.pv input[type='range'].pv-range::-webkit-slider-thumb { -webkit-appearance: none; width: 24px; height: 24px; margin-top: -10px; border-radius: 50%; background: var(--pv-surface); border: 2px solid var(--pv-ink); box-shadow: inset 0 0 0 4px var(--pv-surface), inset 0 0 0 12px var(--pv-plan), 0 1px 3px rgba(0, 0, 0, 0.25); }
.pv input[type='range'].pv-range::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%; background: var(--pv-plan); border: 2px solid var(--pv-ink); }
.pv input[type='range'].pv-range:focus-visible::-webkit-slider-thumb { box-shadow: inset 0 0 0 4px var(--pv-surface), inset 0 0 0 12px var(--pv-plan), 0 0 0 5px var(--pv-plan-wash); }

/* ------------------------------------------------------------ the foot */
.pv .pv-foot { position: fixed; left: 0; right: 0; bottom: 0; z-index: 30; background-color: var(--pv-page); background-image: var(--pv-paper); border-top: 3px double var(--pv-ink); padding-bottom: env(safe-area-inset-bottom, 0px); }
.pv .pv-foot-row { max-width: 1140px; margin: 0 auto; display: flex; align-items: center; justify-content: center; gap: 10px; min-height: 52px; padding-inline: max(16px, env(safe-area-inset-left, 0px)) max(16px, env(safe-area-inset-right, 0px)); font-size: 16.5px; }

.pv .pv-button { display: inline-flex; align-items: center; gap: 8px; padding: 10px 18px; border-radius: 3px; background: var(--pv-ink); color: var(--pv-page); font-variant-caps: all-small-caps; letter-spacing: 0.12em; font-size: 17px; }
.pv .pv-button:disabled { opacity: 0.4; cursor: not-allowed; }
.pv .pv-link-btn { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border: 1px solid var(--pv-border); border-radius: 3px; font-size: 15.5px; color: var(--pv-ink-2); }
.pv .pv-link-btn:hover { border-color: var(--pv-ink); color: var(--pv-ink); }
.pv :focus-visible { outline: 2px solid var(--pv-plan); outline-offset: 2px; }

@keyframes pv-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
.pv .pv-in { animation: pv-in 260ms cubic-bezier(.2,.7,.2,1) both; }

@media (max-width: 720px) {
  .pv { font-size: 16px; --pv-head-h: 100px; }
  .pv .pv-register { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .pv .pv-register > div:nth-child(3) { border-left: 0; }
  .pv .pv-register > div:nth-child(n + 3) { border-top: 0.6px solid var(--pv-ink); }
  .pv .pv-plate { padding: 14px 12px 12px; }
  .pv .pv-btrack, .pv .pv-baxis > div { margin-right: 64px; }
  .pv .pv-bullet { grid-template-columns: minmax(0, 1fr) auto; }
  .pv .pv-bullet .pv-btrack2 { grid-column: 1 / -1; grid-row: 2; }
  .pv .pv-dumb { grid-template-columns: 112px minmax(0, 1fr) 46px; }
  .pv .pv-nav a { font-size: 15px; padding: 6px 8px; }
  .pv .pv-baxis > div > span:nth-child(even) { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .pv .pv-bar, .pv .pv-col, .pv .pv-fill, .pv .pv-bval, .pv .pv-conn, .pv .pv-dumb-dot, .pv .pv-dumb-line { transition: none; }
  .pv .pv-chart:not(.is-in) .pv-bar, .pv .pv-chart:not(.is-in) .pv-col, .pv .pv-chart:not(.is-in) .pv-fill { transform: none; }
  .pv .pv-in { animation: none; }
}
`;
