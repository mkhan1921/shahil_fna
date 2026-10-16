/*
 * Colour roles for presentation mode, light and dark. Values come from the
 * validated reference palette (status steps and chart chrome).
 *
 * Roles: the plan is always blue, today is always the muted grey, and what is
 * still short is hatched red. Status colours appear only with an icon and a
 * word.
 */
export const PRESENT_CSS = `
.pv {
  color-scheme: light;
  --pv-page: #f3f2ee;
  --pv-surface: #fcfcfb;
  --pv-raised: #ffffff;
  --pv-ink: #0b0b0b;
  --pv-ink-2: #52514e;
  --pv-muted: #898781;
  --pv-grid: #e1e0d9;
  --pv-axis: #c3c2b7;
  --pv-border: rgba(11, 11, 11, 0.1);
  --pv-plan: #2a78d6;
  --pv-plan-wash: rgba(42, 120, 214, 0.1);
  --pv-today: #898781;
  --pv-left: #dcdbd3;
  --pv-good: #0ca30c;
  --pv-warning: #fab219;
  --pv-critical: #d03b3b;
  --pv-critical-wash: rgba(208, 59, 59, 0.1);
  --pv-critical-stripe: rgba(208, 59, 59, 0.34);
  background: var(--pv-page);
  color: var(--pv-ink);
}
.pv[data-theme='dark'] {
  color-scheme: dark;
  --pv-page: #0d0d0d;
  --pv-surface: #1a1a19;
  --pv-raised: #222221;
  --pv-ink: #f0efec;
  --pv-ink-2: #c3c2b7;
  --pv-muted: #898781;
  --pv-grid: #2c2c2a;
  --pv-axis: #383835;
  --pv-border: rgba(255, 255, 255, 0.1);
  --pv-plan: #3987e5;
  --pv-plan-wash: rgba(57, 135, 229, 0.16);
  --pv-left: #383835;
  --pv-critical-wash: rgba(208, 59, 59, 0.16);
  --pv-critical-stripe: rgba(208, 59, 59, 0.5);
}
.pv .pv-card { background: var(--pv-surface); border: 1px solid var(--pv-border); border-radius: 16px; }
.pv .pv-short { background-color: var(--pv-critical-wash); background-image: repeating-linear-gradient(135deg, var(--pv-critical-stripe) 0 2px, transparent 2px 9px); box-shadow: inset 0 0 0 2px var(--pv-critical); }
.pv .pv-over { background-color: var(--pv-page); background-image: linear-gradient(var(--pv-critical-wash), var(--pv-critical-wash)), repeating-linear-gradient(135deg, var(--pv-critical-stripe) 0 2px, transparent 2px 9px); box-shadow: -2px 0 0 0 var(--pv-page), inset 0 0 0 2px var(--pv-critical); }
.pv .pv-lost { background-image: repeating-linear-gradient(135deg, var(--pv-axis) 0 2px, transparent 2px 7px); box-shadow: inset 0 0 0 2px var(--pv-muted); }
.pv .pv-free { background: var(--pv-left); }
.pv .pv-seg { box-shadow: 2px 0 0 0 var(--pv-page); }
.pv .pv-seg-after { box-shadow: -2px 0 0 0 var(--pv-page), 2px 0 0 0 var(--pv-page); }
.pv .pv-anim { transition: left 360ms cubic-bezier(.2,.7,.2,1), width 360ms cubic-bezier(.2,.7,.2,1), background-color 200ms; }
@keyframes pv-in { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
.pv .pv-in { animation: pv-in 280ms cubic-bezier(.2,.7,.2,1) both; }
.pv input[type='range'].pv-range { -webkit-appearance: none; appearance: none; width: 100%; height: 28px; background: transparent; cursor: pointer; }
.pv input[type='range'].pv-range:focus { outline: none; }
.pv input[type='range'].pv-range::-webkit-slider-runnable-track { height: 8px; border-radius: 999px; background: linear-gradient(to right, var(--pv-plan) 0 var(--fill, 0%), var(--pv-grid) var(--fill, 0%) 100%); }
.pv input[type='range'].pv-range::-moz-range-track { height: 8px; border-radius: 999px; background: linear-gradient(to right, var(--pv-plan) 0 var(--fill, 0%), var(--pv-grid) var(--fill, 0%) 100%); }
.pv input[type='range'].pv-range::-webkit-slider-thumb { -webkit-appearance: none; width: 26px; height: 26px; margin-top: -9px; border-radius: 50%; background: var(--pv-plan); border: 4px solid var(--pv-surface); box-shadow: 0 0 0 1px var(--pv-plan), 0 2px 6px rgba(0,0,0,.25); }
.pv input[type='range'].pv-range::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%; background: var(--pv-plan); border: 4px solid var(--pv-surface); box-shadow: 0 0 0 1px var(--pv-plan); }
.pv input[type='range'].pv-range:focus-visible::-webkit-slider-thumb { box-shadow: 0 0 0 1px var(--pv-plan), 0 0 0 6px var(--pv-plan-wash); }
.pv .pv-tabular { font-variant-numeric: tabular-nums; }
@media (prefers-reduced-motion: reduce) { .pv .pv-anim { transition: none; } .pv .pv-in { animation: none; } }
`;
