/** Focus the first field of a newly added row once React has rendered it. */
export const focusRow = (id: string) => {
  let tries = 0;
  const attempt = () => {
    const el = document.querySelector<HTMLElement>(`[data-row="${CSS.escape(id)}"] input, [data-row="${CSS.escape(id)}"] select, [data-row="${CSS.escape(id)}"] textarea`);
    if (el) {
      el.focus();
      el.scrollIntoView({ block: 'nearest' });
    } else if (tries++ < 20) requestAnimationFrame(attempt);
  };
  requestAnimationFrame(attempt);
};

const FOCUSABLE = 'input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]):not([tabindex="-1"])';

/**
 * Enter moves to the next field and Shift+Enter to the previous one, like a
 * data-entry form. Textareas keep Enter for new lines (Ctrl+Enter moves on).
 */
export const handleEnterNavigation = (e: React.KeyboardEvent<HTMLElement>) => {
  if (e.key !== 'Enter' || e.altKey || e.metaKey) return;
  const target = e.target as HTMLElement;
  const tag = target.tagName;
  if (tag === 'BUTTON' || tag === 'A') return;
  if (tag === 'TEXTAREA' && !e.ctrlKey) return;
  const root = e.currentTarget;
  const all = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null && el.tagName !== 'BUTTON');
  const i = all.indexOf(target);
  if (i === -1) return;
  e.preventDefault();
  const next = all[e.shiftKey ? i - 1 : i + 1];
  if (next) {
    next.focus();
    if (next instanceof HTMLInputElement && next.type !== 'checkbox' && next.type !== 'date') next.select();
  }
};
