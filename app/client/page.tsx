'use client';

import { Suspense } from 'react';
import Workspace from '@/components/workspace/Workspace';

export default function ClientPage() {
  return (
    <Suspense fallback={<div className="p-10 text-center text-sm text-muted">Loading…</div>}>
      <Workspace />
    </Suspense>
  );
}
