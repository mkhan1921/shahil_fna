const zar = new Intl.NumberFormat('en-ZA', { maximumFractionDigits: 0 });

/** R 1 234 567 */
export const money = (n: number | null | undefined, opts: { sign?: boolean; dash?: boolean } = {}): string => {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  if (opts.dash && Math.round(n) === 0) return '—';
  const neg = n < 0;
  const body = `R${zar.format(Math.abs(Math.round(n)))}`;
  if (neg) return `−${body}`;
  return opts.sign && n > 0 ? `+${body}` : body;
};

/** R1.2m, R845k — for tight spaces. */
export const moneyCompact = (n: number): string => {
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  const sign = n < 0 ? '−' : '';
  if (abs >= 1e9) return `${sign}R${(abs / 1e9).toFixed(1)}bn`;
  if (abs >= 1e6) return `${sign}R${(abs / 1e6).toFixed(abs >= 1e7 ? 1 : 2)}m`;
  if (abs >= 1e4) return `${sign}R${Math.round(abs / 1e3)}k`;
  return `${sign}R${zar.format(Math.round(abs))}`;
};

export const pct = (n: number, digits = 0): string => (Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : '—');

export const num = (n: number, digits = 0): string =>
  Number.isFinite(n) ? new Intl.NumberFormat('en-ZA', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n) : '—';

export const dateLong = (iso: string | Date | null | undefined): string => {
  if (!iso) return '—';
  const d = typeof iso === 'string' ? new Date(iso.length === 10 ? `${iso}T00:00:00` : iso) : iso;
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-ZA', { day: 'numeric', month: 'long', year: 'numeric' });
};

export const relativeTime = (iso: string): string => {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.round(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} day${d === 1 ? '' : 's'} ago`;
  return dateLong(iso);
};

export const years = (n: number): string => `${num(n, n % 1 === 0 ? 0 : 1)} yr${n === 1 ? '' : 's'}`;
