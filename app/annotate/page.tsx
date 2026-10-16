'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import AnnotateWorkspace from '@/components/review/AnnotateWorkspace';

function AnnotateRoute() {
  const params = useSearchParams();
  return <AnnotateWorkspace annotationKey={params?.get('key') ?? null} />;
}

/** Annotate mode for the evaluation set: /annotate?key=<annotation key> (spec §15.2). */
export default function AnnotatePage() {
  return (
    <Suspense fallback={<div className="p-10 text-center text-sm text-muted">Loading…</div>}>
      <AnnotateRoute />
    </Suspense>
  );
}
