import type { NextConfig } from 'next';

/*
 * The Advisor Call Intelligence review API and its call recordings are the only other origins the
 * app may talk to, and only those this deployment names (space- or comma-separated). Left unset,
 * the app connects to nothing but itself; recordings the adviser opens from disk play as blob: URLs.
 */
const reviewOrigins = (process.env.NEXT_PUBLIC_ADVISOR_INTEL_ORIGINS ?? '')
  .split(/[\s,]+/)
  .map((s) => s.trim().replace(/\/+$/, ''))
  .filter((s) => /^https?:\/\/[^\s'";]+$/.test(s));
const withReviewOrigins = (sources: string) => [sources, ...reviewOrigins].join(' ');

const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'no-referrer' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), interest-cohort=()' },
  { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains; preload' },
  {
    key: 'Content-Security-Policy',
    value: [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      `connect-src ${withReviewOrigins("'self'")}`,
      `media-src ${withReviewOrigins("'self' blob:")}`,
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "object-src 'none'",
    ].join('; '),
  },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
  async redirects() {
    // The vintage style was first previewed at its own address; it is now a style of /report.
    return [{ source: '/report/vintage', destination: '/report?style=vintage', permanent: false }];
  },
};

export default nextConfig;
