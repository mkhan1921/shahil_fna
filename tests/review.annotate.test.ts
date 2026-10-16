/** Annotate mode (spec §15.2): gold facts from the transcript alone, exported as the evals model expects. */
import { produce } from 'immer';
import { describe, expect, it } from 'vitest';
import {
  addAnnotatorRow,
  annotationProblems,
  annotationSource,
  createAnnotation,
  editAnnotationDocument,
  forecastInstance,
  goldFile,
  goldProblems,
  goldToEditor,
  goldValue,
  removeGoldFact,
  setGoldFact,
  type GoldSpan,
} from '@/lib/review/annotate';
import { slotById } from '@/lib/review/catalog';
import { describeErrors, validate } from '@/lib/review/jsonSchema';
import { getPath } from '@/lib/review/paths';
import { evidenceFromSpan } from '@/lib/review/transcript';
import { rowIdForNewEntity } from '@/lib/review/uuid';
import { loadPackage, schema } from './reviewFixture';

const bundle = loadPackage().bundle;
const NOW = '2026-10-17T08:00:00Z';
const KAGISO_ROW = 'b4db1d21-aadf-4118-8b47-437035cf7952';
const span = (seg: string, words: string): GoldSpan => {
  const s = bundle.transcript.segments.find((x) => x.segment_id === seg)!;
  const at = s.text.indexOf(words);
  const e = evidenceFromSpan(bundle.transcript, seg, at, at + words.length);
  return { segment_id: e.segment_id, char_start: e.char_start, char_end: e.char_end, quote: e.quote, speaker_role: e.speaker_role, t_start: e.t_start, t_end: e.t_end, role: e.role };
};
const start = () => createAnnotation(annotationSource(bundle), { annotatorId: 'annotator-07', now: NOW });
const slot = (id: string) => slotById(id)!;

describe('what an annotator sees', () => {
  it('never the system’s output: only the transcript, the recording, the prior file and its rows', () => {
    const src = annotationSource(bundle);
    expect(Object.keys(src).sort()).toEqual(['anchorDate', 'audioUrl', 'callId', 'entities', 'prior', 'schemaVersion', 'transcript']);
    expect(src.prior).toEqual(bundle.prior_document);
    // The call's discoveries (a new policy, the Krugerrands, the study plan) are system output.
    expect(Object.keys(src.entities)).not.toContain('policy.group_disability_new');
    expect(Object.keys(src.entities)).not.toContain('property.krugerrands_new');
    expect(src.entities['person.kagiso_mokoena_2']?.fna_id).toBe(KAGISO_ROW);
    const a = start();
    expect(a.working).toEqual(a.prior);
    expect(a.annotation.facts).toEqual([]);
  });
});

describe('annotating', () => {
  it('a range keeps the bounds as said and the annotator’s quantiles; the sheet shows the p50', () => {
    const s = slot('retirement.{p}.retirementAge');
    const value = goldValue(s, { mode: 'range', low: 60, likely: 62, high: 65 }, { p10: 60, p50: 62, p90: 65 });
    expect(value).toEqual({ value: { kind: 'range', low: 60, high: 65, p10: 60, p50: 62, p90: 65 }, unit: 'age', period: 'none' });
    const a = setGoldFact(start(), { slotId: s.slot_id, instance: 'client', status: 'stated', value, attribution: 'client', spans: [span('s0010', 'somewhere between sixty and sixty-five')] });
    expect(a.working.retirement.client.retirementAge).toBe(62);
    expect(a.annotation.facts[0]).toMatchObject({ fact_id: 'retirement.{p}.retirementAge#client', fna_path: 'retirement.client.retirementAge' });
    expect(goldToEditor(s, value)).toEqual({ value: { mode: 'range', low: 60, likely: 62, high: 65 }, quantiles: { p10: 60, p50: 62, p90: 65 } });
    expect(() => goldValue(s, { mode: 'range', low: '', likely: 62, high: '' }, { p10: 64, p50: 62, p90: 65 })).toThrow(/rise/);
    expect(() => goldValue(s, { mode: 'range', low: 60, likely: 62, high: 65 })).toThrow(/p10, p50 and p90/);
  });

  it('options keep their weights; a not-discussed slot has no value', () => {
    const s = slot('dependants[].tertiaryCostAnnual');
    const value = goldValue(s, { mode: 'options', options: [{ value: 150000, weight: 60 }, { value: 450000, weight: 40 }] });
    expect(value.value).toEqual({ kind: 'options', options: [{ value: 150000, weight: 0.6 }, { value: 450000, weight: 0.4 }] });
    let a = setGoldFact(start(), { slotId: s.slot_id, instance: 'person.kagiso_mokoena_2', status: 'stated', value, attribution: 'client', spans: [span('s0017', 'about a hundred and fifty a year')] });
    expect(getPath(a.working, `dependants[${KAGISO_ROW}].tertiaryCostAnnual`)).toBe(150000);
    a = setGoldFact(a, { slotId: '{p}.smoker', instance: 'client', status: 'not_discussed', value: goldValue(slot('{p}.smoker'), { mode: 'point', value: true }), attribution: 'client', spans: [] });
    const smoker = a.annotation.facts.find((f) => f.fact_id === '{p}.smoker#client')!;
    expect(smoker.value).toBeNull();
    expect(goldProblems(smoker)).toEqual([]);
    a = removeGoldFact(a, 'dependants[].tertiaryCostAnnual#person.kagiso_mokoena_2');
    expect(getPath(a.working, `dependants[${KAGISO_ROW}].tertiaryCostAnnual`)).toBe(getPath(a.prior!, `dependants[${KAGISO_ROW}].tertiaryCostAnnual`));
  });

  it('typing in the sheet annotates the field as stated, and asks for the words it rests on', () => {
    const a = editAnnotationDocument(
      start(),
      produce(start().working, (d) => {
        d.income.client.grossMonthly = 118000;
      }),
    );
    const f = a.annotation.facts.find((x) => x.fna_path === 'income.client.grossMonthly')!;
    expect(f).toMatchObject({ status: 'stated', value: { value: { kind: 'point', value: 118000 }, unit: 'ZAR', period: 'monthly' } });
    expect(goldProblems(f)).toEqual(['a stated fact needs at least one transcript span']);
    expect(annotationProblems(a)).toHaveLength(1);
  });

  it('new objects get rows the pipeline’s way, and forecast-only objects are named', () => {
    const [a, row] = addAnnotatorRow(start(), 'policies', 'Group disability through work');
    expect(row.entityId).toBe('policy.annotator_policies_1');
    expect(row.rowId).toBe(rowIdForNewEntity(bundle.call_id, 'policy.annotator_policies_1'));
    expect(a.working.policies.some((p) => p.id === row.rowId)).toBe(true);
    expect(forecastInstance(slot('event[].occurs'), 'Kagiso studies overseas!')).toBe('event.annotator_kagiso_studies_overseas');
    expect(() => forecastInstance(slot('event[].occurs'), '  ')).toThrow();
  });
});

describe('the gold file', () => {
  /** A short annotation of the demo call, kept for the pipeline's contract test (GoldAnnotation). */
  const annotated = () => {
    let a = start();
    const add = (slotId: string, instance: string | null, v: Parameters<typeof goldValue>[1] | null, spans: GoldSpan[], o: { status?: 'stated' | 'implied' | 'explicitly_unknown' | 'contradicted'; q?: Parameters<typeof goldValue>[2]; label?: string; who?: 'client' | 'advisor_suggested' } = {}) => {
      a = setGoldFact(a, {
        slotId,
        instance,
        status: o.status ?? 'stated',
        value: v ? goldValue(slot(slotId), v, o.q) : null,
        attribution: o.who ?? 'client',
        spans,
        entityLabel: o.label,
      });
    };
    add('income.{p}.grossMonthly', 'client', { mode: 'point', value: 118000 }, [span('s0004', 'one hundred and eighteen thousand gross a month')]);
    add('retirement.{p}.retirementAge', 'client', { mode: 'range', low: 60, likely: 62, high: 65 }, [span('s0010', 'somewhere between sixty and sixty-five')], { q: { p10: 60, p50: 62, p90: 65 } });
    add('retirement.{p}.targetPercent', 'client', { mode: 'point', value: 70 }, [span('s0011', 'about seventy percent of salary')], { who: 'advisor_suggested' });
    add('dependants[].tertiaryCostAnnual', 'person.kagiso_mokoena_2', { mode: 'options', options: [{ value: 150000, weight: 60 }, { value: 450000, weight: 40 }] }, [span('s0017', 'about a hundred and fifty a year')]);
    add('event[].occurs', forecastInstance(slot('event[].occurs'), 'Kagiso studies overseas'), { mode: 'options', options: [{ value: true, weight: 40 }, { value: false, weight: 60 }] }, [span('s0016', 'She might study overseas one day.')], { label: 'Kagiso studies overseas' });
    const [withRow, row] = addAnnotatorRow(a, 'policies', 'Group disability through work');
    a = withRow;
    add('policies[].cover', row.entityId, null, [span('s0023', "I don't know the amount yet.")], { status: 'explicitly_unknown' });
    return a;
  };

  it('validates against the JSON Schema exported from the evals GoldAnnotation model', async () => {
    const a = annotated();
    expect(annotationProblems(a)).toEqual([]);
    const file = goldFile(a, '2026-10-17T09:15:00Z');
    expect(describeErrors(validate(schema('gold_annotation'), file))).toBe('');
    expect(file.facts.map((f) => f.fact_id)).toEqual([...file.facts.map((f) => f.fact_id)].sort());
    await expect(`${JSON.stringify(file, null, 1)}\n`).toMatchFileSnapshot('fixtures/gold_annotation.json');
  });

  it('checks the model’s rules a JSON Schema cannot say, before export', () => {
    const file = goldFile(annotated(), null);
    const broken = structuredClone(file.facts);
    broken[0].fact_id = 'something#else';
    broken[1].spans = [];
    broken[2].value = null;
    expect(goldProblems(broken[0])).toEqual([`fact id something#else does not match slot ${broken[0].slot_id}`]);
    expect(goldProblems(broken[1])).toEqual([`a ${broken[1].status.replace('_', ' ')} fact needs at least one transcript span`]);
    expect(goldProblems(broken[2])).toContain(`a ${broken[2].status.replace('_', ' ')} fact needs a value`);
  });
});
