'use client';

/**
 * A call review (spec §11): the adviser's own sheet showing the median document, every
 * fact-backed field badged, a popover per field, the review queue and impact strip in the rail,
 * and a top bar with the call's quality, follow-ups and Submit — blocked while any flag is open.
 * Everything the reviewer does is a state op, saved on this device as it happens and, when the
 * practice has turned on the review API, sent to the pipeline.
 */
import { AudioLines, CheckCircle2, CloudOff, Download, FileText, ListChecks, Loader2, MessageCircleQuestion, Plus, RefreshCw, Send, X } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { analyse, type Analysis } from '@/lib/fna/analysis';
import { dateLong } from '@/lib/format';
import { apiReady, createReviewApi } from '@/lib/review/api';
import { slotById } from '@/lib/review/catalog';
import { reviewResultBlob, reviewResultName, reviewedFileName } from '@/lib/review/exchange';
import { bundleDate, compareNeeds, movedNeeds } from '@/lib/review/impact';
import { factTitle, householdName } from '@/lib/review/labels';
import { badgeAt, buildQueue, entryFact, entryOf, flowStep, nextUndecided, progress, queueKey, queueOrder, type QueueGroup } from '@/lib/review/queue';
import { canSubmit, commitPending, factAt, isDecided, isReviewItem, openFlags, submit, toResult, unsentOps, type ReviewAction, type ReviewSession } from '@/lib/review/session';
import { getDocument, saveDocument } from '@/lib/store/db';
import { usePractice } from '@/lib/store/hooks';
import { downloadBlob, exportDocument } from '@/lib/store/transfer';
import AppShell from '../AppShell';
import SheetFrame, { SaveIndicator } from '../sheet/SheetFrame';
import { Button, Callout, EmptyState } from '../ui';
import { AddFactDialog, FollowupDialog, SubmitDialog, TermDialog, TranscriptDialog, type SubmitChoices } from './dialogs';
import FieldPopover, { STATUS_LABEL, type Panel } from './FieldPopover';
import { OverlayProvider, type OverlayApi } from './overlay';
import { ImpactStrip, ReviewQueue } from './ReviewRail';
import { useCallAudio } from './useCallAudio';
import { usePlacement } from './usePlacement';
import { useReviewSession, type ReviewHandle, type SyncState } from './useReviewSession';

type Dialog = 'followups' | 'transcript' | 'add' | 'submit';

function HeaderButton({ icon, label, onClick, title, disabled }: { icon: ReactNode; label: ReactNode; onClick: () => void; title: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      tabIndex={-1}
      onClick={onClick}
      title={title}
      aria-label={title}
      disabled={disabled}
      className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[13px] font-medium text-ink-2 hover:bg-wash disabled:opacity-50"
    >
      {icon} <span className="hidden xl:inline">{label}</span>
    </button>
  );
}

function SyncIndicator({ sync, onRetry }: { sync: SyncState; onRetry: () => void }) {
  if (sync.kind === 'off' || sync.kind === 'idle') return null;
  if (sync.kind === 'sending')
    return (
      <span className="inline-flex items-center gap-1 text-[11.5px] text-muted">
        <Loader2 size={12} className="animate-spin" /> Sending
      </span>
    );
  return (
    <button type="button" tabIndex={-1} onClick={onRetry} title={sync.message} className="inline-flex items-center gap-1 text-[11.5px] text-critical-ink hover:underline">
      <CloudOff size={12} /> Not sent · retry <RefreshCw size={11} />
    </button>
  );
}

export default function ReviewWorkspace({ callId }: { callId: string | null }) {
  const handle = useReviewSession(callId);
  if (handle.session === undefined)
    return (
      <AppShell wide>
        <div className="p-16 text-center text-sm text-muted">Loading the review…</div>
      </AppShell>
    );
  if (!handle.session)
    return (
      <AppShell>
        <div className="mx-auto max-w-lg p-10">
          <EmptyState
            title="Review not found"
            description="This call review is not stored in this browser. Import its review package from the client list."
            action={
              <Link href="/" className="text-sm font-medium text-brand underline">
                Back to clients
              </Link>
            }
          />
        </div>
      </AppShell>
    );
  return <Workspace handle={handle} session={handle.session} />;
}

function Workspace({ handle, session }: { handle: ReviewHandle; session: ReviewSession }) {
  const { dispatch: rawDispatch, editSheet, commit, error, clearError, saveState, api, sync, sendOps, replace, flush, get } = handle;
  const { practice } = usePractice();
  const audio = useCallAudio(session.bundle.audio_url);
  const asAt = useMemo(() => bundleDate(session.bundle), [session.bundle]);
  const analysis = useMemo(() => analyse(session.working, asAt), [session.working, asAt]);
  const queue = useMemo(() => buildQueue(session), [session]);
  const [showCollapsed, setShowCollapsed] = useState(false);
  const order = useMemo(() => queueOrder(queue, showCollapsed), [queue, showCollapsed]);
  const [current, setCurrent] = useState<string | null>(null);
  const [popover, setPopover] = useState<{ factId: string; panel: Panel; from: 'queue' | 'sheet' } | null>(null);
  const [last, setLast] = useState<{ label: string; before: Analysis } | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [termFact, setTermFact] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const [docExists, setDocExists] = useState(false);
  const [localChanged, setLocalChanged] = useState(false);
  const sheetEdit = useRef<Analysis | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const audioInput = useRef<HTMLInputElement>(null);

  const done = !!session.submittedAt;
  const fact = popover ? session.facts[popover.factId] : undefined;
  const placement = usePlacement(fact?.path ?? null, !!fact);
  const names = { client: session.working.client.firstName || 'Client', spouse: session.working.spouse.firstName || 'Spouse' };
  const household = householdName(session.working);
  const flags = openFlags(session);
  const p = progress(session, queue);
  const ready = apiReady(api);
  const lastChange = useMemo(() => (last ? { label: last.label, deltas: movedNeeds(compareNeeds(last.before, analysis)) } : null), [last, analysis]);

  useEffect(() => {
    let alive = true;
    getDocument(session.docId).then((d) => {
      if (!alive) return;
      setDocExists(!!d);
      setLocalChanged(!!d && !session.submittedAt && d.updatedAt > session.createdAt);
    });
    return () => {
      alive = false;
    };
  }, [session.docId, session.createdAt, session.submittedAt, dialog]);

  /* ---------------------------------------------------------------- */
  /* Decisions                                                         */
  /* ---------------------------------------------------------------- */

  const describe = useCallback(
    (a: ReviewAction): string | null => {
      const t = (id: string) => (session.facts[id] ? factTitle(session, session.facts[id]) : id);
      switch (a.type) {
        case 'accept':
          return `Accepted ${t(a.factId)}`;
        case 'acceptMany':
          return `Accepted ${a.factIds.length} fact${a.factIds.length === 1 ? '' : 's'}`;
        case 'edit':
          return `Edited ${t(a.factId)}`;
        case 'setStatus':
          return `Marked ${t(a.factId)} ${STATUS_LABEL[a.status].toLowerCase()}`;
        case 'reject':
          return `Rejected ${t(a.factId)}`;
        case 'removeRow':
          return `Removed the row of ${t(a.factId)}`;
        case 'reattribute':
          return `Moved ${t(a.factId)}`;
        case 'addFact':
          return `Added ${slotById(a.slotId)?.label ?? 'a fact'}`;
        case 'clearFlag': {
          const flag = session.flags.find((f) => f.flag_id === a.flagId);
          return `Cleared the flag on ${flag?.fact_id ? t(flag.fact_id) : 'the call'}`;
        }
        case 'confirmTerm':
          return `Confirmed the term in ${t(a.factId)}`;
        default:
          return null;
      }
    },
    [session],
  );

  /** Apply an action, remembering the analysis before it for the impact strip. */
  const dispatch = useCallback(
    (a: ReviewAction): boolean => {
      const label = describe(a);
      const before = analysis;
      const ok = rawDispatch(a);
      if (ok && label) setLast({ label, before });
      return ok;
    },
    [analysis, describe, rawDispatch],
  );

  const scrollToField = (path: string) =>
    requestAnimationFrame(() => document.querySelector(`[data-path="${CSS.escape(path)}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }));

  /** Keep the keyboard flow going: back to the queue (or the popover when the rail is hidden). */
  const focusFlow = () =>
    requestAnimationFrame(() => {
      const list = listRef.current;
      if (list && list.offsetParent) list.focus({ preventScroll: true });
      else popRef.current?.focus({ preventScroll: true });
    });

  const select = useCallback(
    (entry: string | null) => {
      setCurrent(entry);
      const f = entry ? entryFact(session, entry) : undefined;
      if (f) {
        setPopover({ factId: f.factId, panel: 'view', from: 'queue' });
        scrollToField(f.path);
      } else setPopover(null);
    },
    [session],
  );

  const openFromSheet = useCallback(
    (factId: string, panel: Panel = 'view') => {
      setPopover({ factId, panel, from: 'sheet' });
      setCurrent(entryOf(session, order, factId));
      requestAnimationFrame(() => popRef.current?.focus({ preventScroll: true }));
    },
    [session, order],
  );

  const openFact = useCallback(
    (factId: string) => {
      const f = session.facts[factId];
      if (!f) return;
      setPopover({ factId, panel: 'view', from: 'queue' });
      setCurrent(entryOf(session, order, factId));
      scrollToField(f.path);
    },
    [session, order],
  );

  /** After a decision on a fact: from the queue, move to the next undecided entry; from the sheet, close. */
  const afterDecision = (factId: string) => {
    if (popover?.from === 'sheet' && popover.factId === factId) {
      setPopover(null);
      return;
    }
    const from = current && entryFact(session, current)?.factId === factId ? current : entryOf(session, order, factId);
    const next = nextUndecided(session, order, from, factId);
    if (next) select(next);
    else {
      setPopover(null);
      setCurrent(from);
    }
    focusFlow();
  };

  const acceptGroup = (g: QueueGroup) => {
    const facts = (g === 'needs' ? queue.needs : g === 'quick' ? queue.quick : queue.collapsed).filter((f) => !isDecided(f));
    if (facts.length) dispatch({ type: 'acceptMany', factIds: facts.map((f) => f.factId) });
  };

  const clearFlag = (flagId: string, resolution: 'accepted' | 'dismissed', note?: string) => {
    const ok = dispatch({ type: 'clearFlag', flagId, resolution, note });
    if (ok && current === `flag:${flagId}`) {
      const next = nextUndecided(session, order, current, null);
      select(next);
      focusFlow();
    }
    return ok;
  };

  /** The queue's keys (spec §11.1), in the queue and in the popover. */
  const onQueueKey = (e: KeyboardEvent<HTMLElement>) => {
    const target = e.target as HTMLElement;
    if (target !== e.currentTarget && target.closest('input, textarea, select, button, a, [contenteditable="true"]')) return;
    const k = queueKey(e);
    const step = k ? flowStep(session, order, current, k, !!popover) : null;
    if (!step) return; // Tab leaves the queue at its ends
    e.preventDefault();
    switch (step.do) {
      case 'select':
        select(step.entry);
        return;
      case 'accept':
        if (dispatch({ type: 'accept', factId: step.factId })) afterDecision(step.factId);
        return;
      case 'clearFlag':
        clearFlag(step.flagId, 'accepted');
        return;
      case 'confirmTerm':
        setTermFact(step.factId);
        return;
      case 'panel':
        setPopover({ factId: step.factId, panel: step.panel, from: popover?.from ?? 'queue' });
        return;
      case 'unknown':
        if (dispatch({ type: 'setStatus', factId: step.factId, status: 'explicitly_unknown' })) afterDecision(step.factId);
        return;
      case 'play': {
        const f = session.facts[step.factId];
        audio.play([...f.evidence].sort((a, b) => a.t_start - b.t_start)[0]);
        return;
      }
      case 'close':
        setPopover(null);
        focusFlow();
        return;
    }
  };

  /* ---------------------------------------------------------------- */
  /* The sheet                                                         */
  /* ---------------------------------------------------------------- */

  const overlay = useMemo<OverlayApi>(
    () => ({
      mode: 'review',
      markAt: (path) => badgeAt(session, path),
      currentPath: fact?.path ?? null,
      openAt: (path) => {
        const f = factAt(session, path);
        if (f && isReviewItem(f)) openFromSheet(f.factId);
      },
    }),
    [session, fact?.path, openFromSheet],
  );

  const update = useCallback(
    (recipe: Parameters<typeof editSheet>[0]) => {
      if (done) return;
      if (!sheetEdit.current) sheetEdit.current = analysis;
      editSheet(recipe);
    },
    [analysis, done, editSheet],
  );

  const onSheetBlur = () => {
    if (!get()?.pending.length && !sheetEdit.current) return;
    commit();
    if (sheetEdit.current) {
      setLast({ label: 'Your edit in the sheet', before: sheetEdit.current });
      sheetEdit.current = null;
    }
  };

  const onSheetKey = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === 'F2' || (e.altKey && e.key === 'Enter')) {
      const path = (e.target as HTMLElement).closest('[data-path]')?.getAttribute('data-path');
      const f = path ? factAt(session, path) : undefined;
      if (f && isReviewItem(f)) {
        e.preventDefault();
        openFromSheet(f.factId);
      }
    } else if (e.key === 'Escape' && popover) {
      e.preventDefault();
      setPopover(null);
    }
  };

  /* ---------------------------------------------------------------- */
  /* Files and submitting                                              */
  /* ---------------------------------------------------------------- */

  const downloadResult = (submitted: boolean) => {
    const s = get() ?? session;
    const at = submitted && s.submittedAt ? s.submittedAt : new Date().toISOString();
    const result = toResult(s, { submitted, at });
    downloadBlob(reviewResultBlob(result), reviewResultName(s.working, result, at));
  };

  const downloadFile = async () => {
    const s = get() ?? session;
    const at = s.submittedAt ?? new Date().toISOString();
    const doc = { ...s.working, id: s.docId };
    downloadBlob(await exportDocument(doc), reviewedFileName(doc, at, false));
  };

  const runSubmit = async (c: SubmitChoices) => {
    setSubmitting({ busy: true, error: null });
    const at = new Date().toISOString();
    try {
      let now = get() ?? session;
      if (now.pending.length) {
        now = commitPending(now, { reviewerId: now.reviewerId, at });
        replace(now);
      }
      const reviewed = submit(now, at);
      const result = toResult(reviewed, { submitted: true, at });
      let sentSeq = now.sentSeq;
      if (c.sendToApi && ready) {
        const sent = await sendOps();
        const after = get() ?? now;
        if (!sent || unsentOps(after).length) throw new Error('Your decisions could not all be sent to the pipeline. Check the connection and submit again.');
        sentSeq = after.sentSeq;
        await createReviewApi(api).submit(reviewed.callId, result);
      }
      const doc = { ...structuredClone(reviewed.working), id: reviewed.docId, updatedAt: at };
      await saveDocument(doc);
      replace({ ...reviewed, sentSeq });
      await flush();
      if (c.downloadResult) downloadBlob(reviewResultBlob(result), reviewResultName(doc, result, at));
      if (c.downloadFile) downloadBlob(await exportDocument(doc), reviewedFileName(doc, at, false));
      setSubmitting({ busy: false, error: null });
      setPopover(null);
    } catch (e) {
      setSubmitting({ busy: false, error: e instanceof Error ? e.message : String(e) });
    }
  };

  const answerFollowup = ready
    ? async (followupId: string, text: string) => {
        await createReviewApi(api).answerFollowup(session.callId, followupId, { text, source: 'followup' });
      }
    : undefined;

  /* ---------------------------------------------------------------- */
  /* Layout                                                            */
  /* ---------------------------------------------------------------- */

  const quality = session.bundle.quality ?? session.bundle.transcript.quality;
  const openFollowups = session.followups.filter((q) => q.status === 'open').length;
  const submittable = canSubmit(session);
  const submitTitle = done ? 'Submitted' : flags.length ? `Clear the ${flags.length} blocking flag${flags.length === 1 ? '' : 's'} first` : session.pending.length ? 'Finish the field you are editing first' : 'Submit the review';

  const actions = (
    <div className="flex items-center gap-1.5">
      <SaveIndicator state={saveState} />
      <SyncIndicator sync={sync} onRetry={() => void sendOps()} />
      <HeaderButton icon={<FileText size={15} />} label="Transcript" onClick={() => setDialog('transcript')} title="The whole call transcript" />
      <HeaderButton
        icon={<MessageCircleQuestion size={15} />}
        label={
          <>
            Follow-ups{openFollowups ? <span className="tabular"> · {openFollowups}</span> : null}
          </>
        }
        onClick={() => setDialog('followups')}
        title={`${openFollowups} open follow-up question${openFollowups === 1 ? '' : 's'} for the client`}
      />
      {!done && <HeaderButton icon={<Plus size={15} />} label="Add fact" onClick={() => setDialog('add')} title="Add a fact the call missed" />}
      {!done && <HeaderButton icon={<Download size={15} />} label="Save progress" onClick={() => downloadResult(false)} title="Download the review so far as a draft review result" />}
      {done ? (
        <Button size="sm" variant="primary" icon={<CheckCircle2 size={14} />} onClick={() => setDialog('submit')}>
          Submitted
        </Button>
      ) : (
        <Button size="sm" variant="primary" icon={<Send size={14} />} disabled={!submittable} onClick={() => setDialog('submit')} title={submitTitle}>
          Submit{flags.length ? <span className="tabular"> · {flags.length} open</span> : null}
        </Button>
      )}
    </div>
  );

  const banner = (
    <div className="no-print mx-auto max-w-[1760px] space-y-2 px-3 pt-3 sm:px-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px]">
        <span className="flex min-w-0 items-center gap-1.5">
          <AudioLines size={16} className="shrink-0 text-brand-2" aria-hidden />
          <span className="font-semibold text-ink">Call review</span>
          <span className="truncate text-ink-2">
            {household} · {dateLong(session.bundle.transcript.anchor_date || session.bundle.sidecar.anchor_date)}
          </span>
        </span>
        <span className="text-muted tabular">
          {p.decided}/{p.total} decided · Needs you {p.needsDecided}/{p.needs}
        </span>
        {flags.length > 0 ? (
          <span className="font-medium text-critical-ink">
            {flags.length} blocking flag{flags.length === 1 ? '' : 's'}
          </span>
        ) : (
          !done && <span className="font-medium text-good-ink">No blocking flags</span>
        )}
        <span className="flex items-center gap-1.5 text-muted">
          {audio.available ? (audio.local ? 'Recording opened from this device' : 'Recording attached') : 'No recording attached'}
          <input
            ref={audioInput}
            type="file"
            accept="audio/*"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) audio.openFile(file);
            }}
          />
          <button type="button" tabIndex={-1} onClick={() => audioInput.current?.click()} className="font-semibold text-brand-2 hover:underline">
            {audio.available ? 'Use another file…' : 'Open the recording…'}
          </button>
        </span>
        {!done && (
          <Button size="sm" className="lg:hidden" icon={<ListChecks size={14} />} onClick={() => select(nextUndecided(session, order, current, null))}>
            Next to review
          </Button>
        )}
      </div>
      {audio.error && <Callout tone="warning">{audio.error}</Callout>}
      {quality?.failed && (
        <Callout tone="critical" title="The transcript failed its quality checks">
          Check every fact against the recording before accepting it.
        </Callout>
      )}
      {quality?.banners.map((b) => (
        <Callout key={b} tone="warning">
          {b}
        </Callout>
      ))}
      {quality?.role_review_needed && <Callout tone="warning">The system was unsure who was speaking: check that the adviser’s and the client’s turns are labelled correctly in the transcript.</Callout>}
      {session.conflicts.length > 0 && !done && (
        <Callout tone="info" title="This client’s file changed on this device after it went to the call">
          The call also changed {session.conflicts.length === 1 ? 'one of those fields' : `${session.conflicts.length} of those fields`}:{' '}
          {session.conflicts.map((c, i) => {
            const f = factAt(session, c.path);
            return f ? (
              <span key={c.path}>
                {i > 0 && ', '}
                <button type="button" className="font-medium text-brand underline underline-offset-2" onClick={() => openFact(f.factId)}>
                  {factTitle(session, f)}
                </button>
              </span>
            ) : null;
          })}
          . Each shows both values: keep one by accepting or editing it.
        </Callout>
      )}
      {done && (
        <Callout tone="good" title={`Submitted ${dateLong(session.submittedAt)}`}>
          The reviewed values are in {household}’s file.{' '}
          {docExists && (
            <Link href={`/client?id=${encodeURIComponent(session.docId)}`} className="font-medium text-brand underline underline-offset-2">
              Open the client file
            </Link>
          )}
        </Callout>
      )}
    </div>
  );

  return (
    <OverlayProvider value={overlay}>
      <SheetFrame
        doc={session.working}
        analysis={analysis}
        update={update}
        practice={practice}
        actions={actions}
        banner={banner}
        back={docExists ? { href: `/client?id=${encodeURIComponent(session.docId)}`, label: 'Client file' } : { href: '/', label: 'All clients' }}
        railClassName="hidden w-[22rem] lg:block"
        navClassName="hidden 2xl:flex"
        keyboardHelp={
          <div>
            <kbd className="font-sans font-semibold">F2</kbd> review the field
          </div>
        }
        onMainKeyDown={onSheetKey}
        onMainBlur={onSheetBlur}
        rail={() => (
          <div data-review-rail className="space-y-2 pb-4">
            <ReviewQueue
              session={session}
              queue={queue}
              current={current}
              showCollapsed={showCollapsed}
              setShowCollapsed={setShowCollapsed}
              onSelect={select}
              onKeyDown={onQueueKey}
              onAcceptGroup={acceptGroup}
              onClearFlag={clearFlag}
              listRef={listRef}
            />
            <ImpactStrip session={session} analysis={analysis} last={lastChange} names={names} onOpenFact={openFact} />
          </div>
        )}
      />
      {fact && popover && (
        <FieldPopover
          key={fact.factId}
          session={session}
          fact={fact}
          asAt={asAt}
          audio={audio}
          panel={popover.panel}
          setPanel={(panel) => setPopover((x) => (x ? { ...x, panel } : x))}
          dispatch={dispatch}
          onClose={() => {
            setPopover(null);
            focusFlow();
          }}
          onDecided={() => afterDecision(fact.factId)}
          onConfirmTerm={() => setTermFact(fact.factId)}
          onKeyDown={onQueueKey}
          rootRef={popRef}
          style={placement}
        />
      )}
      <FollowupDialog session={session} open={dialog === 'followups'} onClose={() => setDialog(null)} dispatch={dispatch} onAnswer={answerFollowup} />
      <TranscriptDialog session={session} open={dialog === 'transcript'} onClose={() => setDialog(null)} audio={audio} fact={fact ?? null} />
      {dialog === 'add' && <AddFactDialog session={session} open onClose={() => setDialog(null)} dispatch={dispatch} audio={audio} error={error} />}
      {dialog === 'submit' && (
        <SubmitDialog
          session={session}
          open
          onClose={() => setDialog(null)}
          onSubmit={(c) => void runSubmit(c)}
          apiAvailable={ready}
          busy={submitting.busy}
          error={submitting.error}
          household={household}
          localChanged={localChanged}
          onDownloadResult={() => downloadResult(true)}
          onDownloadFile={() => void downloadFile()}
        />
      )}
      {termFact && <TermDialog key={termFact} session={session} fact={session.facts[termFact] ?? null} onClose={() => setTermFact(null)} dispatch={dispatch} />}
      {error && dialog !== 'add' && (
        <div role="alert" className="no-print fixed bottom-4 left-4 z-50 flex max-w-md items-start gap-2 rounded-lg border border-critical/30 bg-critical-soft px-3 py-2 text-[13px] text-ink-2 shadow-lg">
          <span>{error}</span>
          <button type="button" onClick={clearError} className="rounded p-0.5 text-muted hover:text-ink" aria-label="Dismiss">
            <X size={14} />
          </button>
        </div>
      )}
    </OverlayProvider>
  );
}
