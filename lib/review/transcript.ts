/**
 * Transcript helpers for the overlay and annotate mode: turning a selected span into evidence,
 * where to start the recording for a quote, and the amount readings behind a quote (§3.1, §5.3).
 */
import type { FnaDocument } from '../fna/types';
import { formatIn, pyG, pyGrouped } from './dist';
import type { Evidence, EvidenceRole, NumericSpan, Reading, Segment, SpeakerRole, Transcript, Word } from './types';

/** Audio starts this long before a quote, so the listener hears it in context (§11.1). */
export const AUDIO_LEAD_SECONDS = 2;
/** …and plays on this long after it. */
export const AUDIO_TAIL_SECONDS = 1.5;

/** Where to start the recording for a quote: t_start − 2 s, never before the call. */
export const seekTime = (e: Pick<Evidence, 't_start'>): number => Math.max(0, e.t_start - AUDIO_LEAD_SECONDS);

/** When to stop playing a quote. */
export const stopTime = (e: Pick<Evidence, 't_end'>): number => e.t_end + AUDIO_TAIL_SECONDS;

export const segmentById = (t: Transcript, id: string): Segment | undefined => t.segments.find((s) => s.segment_id === id);

/** Character offsets of each word within its segment's 1-best text. */
export const wordOffsets = (s: Segment): { word: Word; start: number; end: number }[] => {
  let pos = 0;
  return s.words.map((word) => {
    const found = s.text.indexOf(word.text, pos);
    const start = found >= 0 ? found : pos;
    const end = start + word.text.length;
    pos = end;
    return { word, start, end };
  });
};

export const wordsInSpan = (s: Segment, charStart: number, charEnd: number): Word[] =>
  wordOffsets(s)
    .filter((w) => w.end > charStart && w.start < charEnd)
    .map((w) => w.word);

/** Widen a selection to whole words, so a quote never starts or ends mid-word. */
export const snapToWords = (s: Segment, charStart: number, charEnd: number): [number, number] => {
  const offs = wordOffsets(s).filter((w) => w.end > charStart && w.start < charEnd);
  if (!offs.length) return [charStart, charEnd];
  return [Math.min(charStart, offs[0].start), Math.max(charEnd, offs[offs.length - 1].end)];
};

/** Evidence for a span of one segment: its quote, timestamps, speaker and mean ASR confidence. */
export const evidenceFromSpan = (t: Transcript, segmentId: string, charStart: number, charEnd: number, role: EvidenceRole = 'supports'): Evidence => {
  const s = segmentById(t, segmentId);
  if (!s) throw new RangeError(`no segment ${segmentId}`);
  const [start, end] = snapToWords(s, Math.max(0, charStart), Math.min(s.text.length, charEnd));
  const words = wordsInSpan(s, start, end);
  const quote = s.text.slice(start, end);
  if (!quote.trim()) throw new RangeError('select some words of the segment');
  return {
    segment_id: s.segment_id,
    char_start: start,
    char_end: end,
    quote,
    speaker_role: s.speaker_role,
    t_start: words.length ? words[0].t_start : s.t_start,
    t_end: words.length ? words[words.length - 1].t_end : s.t_end,
    asr_mean_conf: words.length ? Math.round((words.reduce((a, w) => a + w.conf, 0) / words.length) * 1000) / 1000 : 0,
    role,
    aligner_score: null,
  };
};

/** The grammar's readings of amounts inside a quote (§5.3): what a scale-ambiguous figure could mean. */
export const readingsFor = (t: Transcript, e: Evidence): NumericSpan[] => {
  const s = segmentById(t, e.segment_id);
  if (!s) return [];
  const offs = wordOffsets(s);
  return t.numeric_spans.filter((n) => {
    if (n.segment_id !== e.segment_id || !offs[n.word_start] || !offs[n.word_end - 1]) return false;
    return offs[n.word_end - 1].end > e.char_start && offs[n.word_start].start < e.char_end;
  });
};

/** A grammar reading as the overlay writes amounts: "R1,850,000", "11.5%", "Allan Gray". */
export const readingText = (r: Pick<Reading, 'value' | 'currency' | 'is_rate'>): string => {
  if (typeof r.value !== 'number') return String(r.value);
  if (r.is_rate) return `${pyG(r.value)}%`;
  return r.currency ? formatIn({ unit: 'ZAR' }, r.value) : pyGrouped(r.value);
};

/** Who is speaking, by name where the file knows it. */
export const speakerName = (role: SpeakerRole | string, doc?: FnaDocument | null): string => {
  if (role === 'advisor') return 'Adviser';
  if (role === 'client_primary') return doc?.client.firstName || 'Client';
  if (role === 'client_secondary') return doc?.spouse.firstName || 'Second client';
  return role === 'other' ? 'Other speaker' : 'Unknown speaker';
};

/** mm:ss from call start. */
export const clock = (seconds: number): string => {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
