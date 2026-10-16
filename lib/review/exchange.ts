/**
 * File exchange with the pipeline (spec §11.5, §17 default for M3): the adviser imports a review
 * package — or a plain shahil-fna file with its provenance sidecar — and returns a review result
 * file beside the reviewed client file. Packages are checked against the JSON Schema exported from
 * the pipeline's models before anything is shown, so a malformed file fails with a reason instead
 * of half-loading.
 */
import { personName } from '../fna/analysis';
import { normaliseDocument } from '../fna/defaults';
import type { FnaDocument } from '../fna/types';
import { exportJsonFile, readTransferFile, safeFilename } from '../store/transfer';
import { splitFactId } from './catalog';
import { checkDist } from './dist';
import { describeErrors, validate, type JsonSchema } from './jsonSchema';
import { isDocumentPath, isListContainer, parsePath } from './paths';
import type { Entity, EntityType, ReviewBundle, ReviewPackage, ReviewResult, Sidecar, Transcript, VersionPins } from './types';

export class ImportError extends Error {}

interface Schemas {
  pkg: JsonSchema;
  sidecar: JsonSchema;
}

/** The schemas load only when a file is imported. */
const loadSchemas = async (): Promise<Schemas> => {
  const [pkg, sidecar] = await Promise.all([import('./schema/review_package.json'), import('./schema/sidecar.json')]);
  return { pkg: pkg.default as unknown as JsonSchema, sidecar: sidecar.default as unknown as JsonSchema };
};

/** What the schemas cannot say: the parts of a package must belong together. */
const coherence = (b: ReviewBundle): string[] => {
  const out: string[] = [];
  const side = b.sidecar;
  if (side.call_id !== b.call_id || side.run_id !== b.run_id) out.push('the sidecar belongs to another call or run');
  const ids = new Set(Object.values(side.fields).map((f) => f.fact_id));
  for (const flag of b.blocking_flags) if (flag.fact_id && !ids.has(flag.fact_id)) out.push(`flag ${flag.flag_id} names a fact the sidecar does not have`);
  for (const q of b.followups) for (const id of q.fact_ids) if (!ids.has(id)) out.push(`follow-up ${q.followup_id} names a fact the sidecar does not have`);
  const segments = new Set(b.transcript.segments.map((s) => s.segment_id));
  for (const [path, f] of Object.entries(side.fields)) {
    if (f.exported && !isDocumentPath(path)) out.push(`${path} is not a place in the client file`);
    for (const q of [f.quantity, ...f.hypotheses.map((h) => h.quantity)]) if (q) out.push(...checkDist(q.dist, path));
    if (segments.size) for (const e of f.evidence) if (!segments.has(e.segment_id)) out.push(`${path} quotes segment ${e.segment_id}, which the transcript does not have`);
  }
  return out;
};

/** Problems that make a review package unusable (empty when it can be reviewed). */
export const checkPackage = (json: unknown, schemas: Schemas): string[] => {
  const errors = validate(schemas.pkg, json);
  if (errors.length) return [describeErrors(errors)];
  const bundle = (json as ReviewPackage).bundle;
  const side = validate(schemas.sidecar, bundle.sidecar);
  if (side.length) return [`sidecar: ${describeErrors(side)}`];
  return coherence(bundle);
};

const shorten = (problems: string[]) => problems.slice(0, 3).join('; ') + (problems.length > 3 ? `; and ${problems.length - 3} more` : '');

/** Read a review package file (format "advisor-intel-review"). */
export const readReviewPackage = async (text: string): Promise<ReviewBundle> => {
  const file = readTransferFile(text);
  if (!file) throw new ImportError('This file is not a review package or a client file.');
  if (file.format !== 'advisor-intel-review') throw new ImportError(`This is a ${file.format} file, not a review package.`);
  const problems = checkPackage(file.json, await loadSchemas());
  if (problems.length) throw new ImportError(`This review package does not match the review contract: ${shorten(problems)}.`);
  return (file.json as unknown as ReviewPackage).bundle;
};

/** Read a provenance sidecar file (format "advisor-intel-sidecar"). */
export const readSidecar = async (text: string): Promise<Sidecar> => {
  const file = readTransferFile(text);
  if (!file || file.format !== 'advisor-intel-sidecar') throw new ImportError('This file is not a provenance sidecar.');
  const errors = validate((await loadSchemas()).sidecar, file.json);
  if (errors.length) throw new ImportError(`This sidecar does not match the review contract: ${describeErrors(errors, 3)}.`);
  return file.json as unknown as Sidecar;
};

const DEFAULT_VERSIONS: VersionPins = {
  schema_version: 'profile_v1',
  lexicon_version: '',
  grammar_version: '',
  calibrator_version: '',
  asr_provider: '',
  asr_model_version: '',
  diarization_model: '',
  frontier_model: '',
  reasoning_model: '',
  verifier_model: '',
  small_model: '',
  embedding_model: '',
  prompt_versions: {},
  forecast_config_version: '',
  cma_version: '',
  fna_tables_version: '',
};

const ENTITY_TYPE: Record<string, EntityType> = {
  dependants: 'person',
  retirementFunds: 'account',
  assets: 'property',
  liabilities: 'liability',
  policies: 'policy',
  goals: 'goal',
  'expenses.custom': 'account',
};

/** The registry the sidecar implies: each list row's entity, from the paths and fact ids. */
const entitiesFromSidecar = (sidecar: Sidecar, doc: FnaDocument): Record<string, Entity> => {
  const entity = (entity_id: string, type: EntityType, canonical_name: string, fna_list: string | null = null, fna_id: string | null = null, attributes: Record<string, unknown> = {}): Entity => ({
    entity_id,
    type,
    canonical_name,
    aliases: [],
    attributes,
    crm_match: [],
    first_mention: null,
    fna_list,
    fna_id,
  });
  const out: Record<string, Entity> = {
    'person.client_primary': entity('person.client_primary', 'person', personName(doc.client, 'Client'), null, null, { person_key: 'client' }),
    'person.advisor': entity('person.advisor', 'person', 'Adviser', null, null, { role: 'advisor' }),
  };
  if (doc.household.includeSpouse) out['person.spouse'] = entity('person.spouse', 'person', personName(doc.spouse, 'Spouse'), null, null, { person_key: 'spouse' });
  for (const [path, f] of Object.entries(sidecar.fields)) {
    const instance = splitFactId(f.fact_id).instance;
    const pp = parsePath(path);
    if (!instance || out[instance] || pp.key === null || !isListContainer(pp.container)) continue;
    out[instance] = entity(instance, ENTITY_TYPE[pp.container], instance, pp.container, pp.key);
  }
  return out;
};

/**
 * A review from a plain shahil-fna file (the pipeline's median document) and its sidecar, when the
 * pipeline hands over the two files rather than a package. There is no transcript or forecast;
 * the quotes in the sidecar still show what was said.
 */
export const bundleFromFiles = (median: FnaDocument, sidecar: Sidecar, prior: FnaDocument | null): ReviewBundle => {
  const doc = normaliseDocument(median);
  const transcript: Transcript = {
    call_id: sidecar.call_id,
    anchor_date: sidecar.anchor_date,
    language: 'en-ZA',
    segments: [],
    asr_provider: '',
    asr_model_version: '',
    asr_alternatives: false,
    numeric_spans: [],
    quality: null,
    channel_layout: 'mono',
  };
  const bundle: ReviewBundle = {
    call_id: sidecar.call_id,
    run_id: sidecar.run_id,
    profile_version: sidecar.profile_version,
    state: 'AWAITING_REVIEW',
    median_document: doc,
    prior_document: prior ? normaliseDocument(prior) : null,
    sidecar,
    transcript,
    audio_url: null,
    entities: entitiesFromSidecar(sidecar, doc),
    followups: sidecar.followups,
    blocking_flags: sidecar.blocking_flags,
    preview_forecast: null,
    deterministic_analysis: null,
    quality: null,
    spot_check: false,
    versions: { ...DEFAULT_VERSIONS, schema_version: sidecar.schema_version },
    attribution_text: null,
  };
  const problems = coherence(bundle);
  if (problems.length) throw new ImportError(`The client file and the sidecar do not belong together: ${shorten(problems)}.`);
  return bundle;
};

/* ------------------------------------------------------------------ */
/* Export                                                              */
/* ------------------------------------------------------------------ */

const day = (iso: string) => iso.slice(0, 10);

export const reviewResultBlob = (result: ReviewResult): Blob => exportJsonFile(result);

export const reviewResultName = (doc: FnaDocument, result: ReviewResult, at: string) =>
  `${safeFilename(personName(doc.client, 'client'))}_review_${safeFilename(result.call_id)}_${day(at)}${result.submitted ? '' : '_draft'}.json`;

export const reviewedFileName = (doc: FnaDocument, at: string, encrypted: boolean) => `${safeFilename(personName(doc.client, 'client'))}_FNA_${day(at)}_reviewed${encrypted ? '.fna' : '.fna.json'}`;
