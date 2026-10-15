import { EB_Garamond } from 'next/font/google';

/** EB Garamond, self-hosted by next/font and loaded only with the vintage report. */
export const garamond = EB_Garamond({ subsets: ['latin'], style: ['normal', 'italic'], variable: '--vr-font', display: 'swap', preload: false });
