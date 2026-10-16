'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import ReviewWorkspace from '@/components/review/ReviewWorkspace';

function ReviewRoute() {
  const params = useSearchParams();
  return <ReviewWorkspace callId={params?.get('call') ?? null} />;
}

/** A call review: /review?call=<call id> (spec §11). */
export default function ReviewPage() {
  return (
    <Suspense fallback={<div className="p-10 text-center text-sm text-muted">Loading…</div>}>
      <ReviewRoute />
    </Suspense>
  );
}
