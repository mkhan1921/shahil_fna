'use client';

import { Suspense } from 'react';
import Sheet from '@/components/sheet/Sheet';

export default function ClientPage() {
  return (
    <Suspense fallback={<div className="p-10 text-center text-sm text-muted">Loading…</div>}>
      <Sheet />
    </Suspense>
  );
}
