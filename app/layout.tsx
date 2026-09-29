import type { Metadata, Viewport } from 'next';
import { Inter, Source_Serif_4 } from 'next/font/google';
import './globals.css';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' });
const serif = Source_Serif_4({ subsets: ['latin'], variable: '--font-serif-face', display: 'swap', weight: ['400', '600', '700'] });

export const metadata: Metadata = {
  title: { default: 'Shahil FNA — Financial Needs Analysis', template: '%s · Shahil FNA' },
  description:
    'Financial needs analysis for South African financial advisers: SARS 2026/27 tax, life, disability, severe illness, estate liquidity, retirement and education needs, with a FAIS-aligned record of advice.',
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: '#0f3d5e',
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-ZA" className={`${inter.variable} ${serif.variable}`}>
      <body className="min-h-screen font-sans antialiased">{children}</body>
    </html>
  );
}
