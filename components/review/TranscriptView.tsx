'use client';

/**
 * The call transcript, turn by turn: who spoke, when, and what the recogniser heard — words it was
 * unsure of are underlined, quotes behind the fact in view are highlighted, and each turn plays
 * from 2 s before it. Selecting words inside one turn hands them back as a transcript span, which is
 * how a reviewer quotes a fact they add and how an annotator marks the spans a fact rests on.
 */
import { Pause, Play } from 'lucide-react';
import { useEffect, useMemo, useRef, type MouseEvent } from 'react';
import type { FnaDocument } from '@/lib/fna/types';
import { pyPercent } from '@/lib/review/dist';
import { clock, evidenceFromSpan, speakerName, wordOffsets } from '@/lib/review/transcript';
import type { Evidence, Segment, Transcript } from '@/lib/review/types';
import { cx } from '../ui';
import type { CallAudio } from './useCallAudio';

/** Words heard with less confidence than this are underlined. */
const UNSURE = 0.6;

const FLAG_LABEL: Record<string, string> = { self_correction: 'corrects itself', interrupted: 'interrupted', crosstalk: 'crosstalk', low_asr: 'hard to hear' };

interface Chunk {
  start: number;
  text: string;
  conf: number | null;
  alts: string[];
}

/** The segment's text cut at word boundaries, so a selection maps back to character offsets. */
const chunksOf = (s: Segment): Chunk[] => {
  const out: Chunk[] = [];
  let pos = 0;
  for (const { word, start, end } of wordOffsets(s)) {
    if (start > pos) out.push({ start: pos, text: s.text.slice(pos, start), conf: null, alts: [] });
    out.push({ start, text: s.text.slice(start, end), conf: word.conf, alts: word.alts.map(([t]) => t).filter((t) => t !== word.text) });
    pos = end;
  }
  if (pos < s.text.length) out.push({ start: pos, text: s.text.slice(pos), conf: null, alts: [] });
  return out;
};

/** Character offset in its segment of a DOM selection point. */
const offsetOf = (node: Node | null, offset: number): { seg: string; at: number } | null => {
  const el = node instanceof Element ? node : (node?.parentElement ?? null);
  const segEl = el?.closest<HTMLElement>('[data-seg]');
  if (!el || !segEl) return null;
  const seg = segEl.dataset.seg ?? '';
  const chunk = el.closest<HTMLElement>('[data-off]');
  if (chunk) {
    const base = Number(chunk.dataset.off);
    if (node instanceof Text) return { seg, at: base + offset };
    return { seg, at: offset === 0 ? base : base + (chunk.textContent?.length ?? 0) };
  }
  // Between chunks: the offset counts the paragraph's children.
  if (el.tagName === 'P') {
    const child = el.children[offset] as HTMLElement | undefined;
    return { seg, at: child?.dataset.off !== undefined ? Number(child.dataset.off) : Number.MAX_SAFE_INTEGER };
  }
  // In the turn's header, which comes before its words.
  return { seg, at: 0 };
};

export default function TranscriptView({
  transcript,
  doc,
  audio,
  highlight = [],
  onSelect,
  focus,
  className,
}: {
  transcript: Transcript;
  doc?: FnaDocument | null;
  audio: CallAudio;
  /** Quotes to highlight: the evidence of the fact in view. */
  highlight?: Pick<Evidence, 'segment_id' | 'char_start' | 'char_end'>[];
  /** Selecting words inside one turn yields a span (absent: the transcript is read-only). */
  onSelect?: (e: Evidence) => void;
  /** Scroll this turn into view. */
  focus?: string | null;
  className?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const chunks = useMemo(() => new Map(transcript.segments.map((s) => [s.segment_id, chunksOf(s)])), [transcript]);

  useEffect(() => {
    if (!focus) return;
    root.current?.querySelector(`[data-seg="${CSS.escape(focus)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [focus]);

  const onMouseUp = (e: MouseEvent) => {
    if (!onSelect || e.button !== 0) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !root.current?.contains(sel.anchorNode)) return;
    const a = offsetOf(sel.anchorNode, sel.anchorOffset);
    const b = offsetOf(sel.focusNode, sel.focusOffset);
    if (!a || !b || a.seg !== b.seg) return;
    const seg = transcript.segments.find((s) => s.segment_id === a.seg);
    if (!seg) return;
    const [start, end] = [Math.min(a.at, b.at), Math.min(seg.text.length, Math.max(a.at, b.at))];
    try {
      onSelect(evidenceFromSpan(transcript, a.seg, start, end));
      sel.removeAllRanges();
    } catch {
      // Only spaces were selected.
    }
  };

  if (!transcript.segments.length)
    return <p className={cx('px-3 py-4 text-[12px] text-muted', className)}>This review came without a transcript: the quotes on each field show what was said.</p>;

  return (
    <div ref={root} className={cx('space-y-1.5', className)} onMouseUp={onMouseUp}>
      {transcript.segments.map((s) => {
        const marks = highlight.filter((h) => h.segment_id === s.segment_id);
        const key = `${s.segment_id}:-1`;
        const playing = audio.playing === key;
        return (
          <div key={s.segment_id} data-seg={s.segment_id} className={cx('rounded-md px-2 py-1', marks.length ? 'bg-warning-soft/70' : 'hover:bg-wash', focus === s.segment_id && 'ring-1 ring-brand-2/40')}>
            <div className="flex items-center gap-1.5 text-[10.5px] text-muted">
              <button
                type="button"
                disabled={!audio.available}
                onClick={() => audio.play({ segment_id: s.segment_id, char_start: -1, t_start: s.t_start, t_end: s.t_end })}
                className="rounded p-0.5 text-brand-2 hover:bg-brand-soft disabled:text-faint disabled:hover:bg-transparent"
                aria-label={playing ? 'Stop' : `Play from ${clock(Math.max(0, s.t_start - 2))}`}
                title={audio.available ? `Play from ${clock(Math.max(0, s.t_start - 2))}` : 'No recording attached'}
              >
                {playing ? <Pause size={11} /> : <Play size={11} />}
              </button>
              <span className={cx('font-semibold', s.speaker_role === 'advisor' ? 'text-ink-2' : 'text-brand')}>{speakerName(s.speaker_role, doc)}</span>
              <span className="tabular">{clock(s.t_start)}</span>
              {s.role_conf < 0.8 && <span className="text-warning-ink">speaker unsure ({pyPercent(s.role_conf)})</span>}
              {s.flags.map((f) => (
                <span key={f} className="rounded bg-canvas px-1">
                  {FLAG_LABEL[f] ?? f}
                </span>
              ))}
            </div>
            <p className={cx('text-[12.5px] leading-[18px] text-ink', onSelect && 'cursor-text select-text')}>
              {(chunks.get(s.segment_id) ?? []).map((c) => {
                const end = c.start + c.text.length;
                const quoted = marks.some((m) => m.char_start < end && m.char_end > c.start);
                const unsure = c.conf !== null && c.conf < UNSURE;
                return (
                  <span
                    key={c.start}
                    data-off={c.start}
                    title={unsure ? `Heard with ${pyPercent(c.conf ?? 0)} confidence${c.alts.length ? `; could be ${c.alts.map((t) => `“${t}”`).join(' or ')}` : ''}` : undefined}
                    className={cx(quoted && 'bg-warning/35', unsure && 'underline decoration-warning decoration-dotted decoration-2 underline-offset-2')}
                  >
                    {c.text}
                  </span>
                );
              })}
            </p>
          </div>
        );
      })}
    </div>
  );
}
