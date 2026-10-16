/** File exchange with the pipeline (spec §11.5): importing what it hands over, exporting what goes back. */
import { describe, expect, it } from 'vitest';
import { normaliseDocument } from '@/lib/fna/defaults';
import { bundleFromFiles, checkPackage, ImportError, readReviewPackage, readSidecar, reviewedFileName, reviewResultBlob, reviewResultName } from '@/lib/review/exchange';
import { describeErrors, validate } from '@/lib/review/jsonSchema';
import { applyAction, createSession, submit, toResult } from '@/lib/review/session';
import { planStart, reviewerIdOf } from '@/lib/review/start';
import { defaultPractice } from '@/lib/fna/defaults';
import { exportDocument, importDocument, readTransferFile } from '@/lib/store/transfer';
import { F, loadPackage, schema, STAMP } from './reviewFixture';

const pkg = loadPackage();
const bundle = pkg.bundle;
const text = JSON.stringify(pkg);

describe('importing a review package', () => {
  it('reads a package that matches the contract', async () => {
    const b = await readReviewPackage(text);
    expect(b.call_id).toBe(bundle.call_id);
    expect(Object.keys(b.sidecar.fields)).toHaveLength(Object.keys(bundle.sidecar.fields).length);
  });

  it('refuses what is not a package, with a reason', async () => {
    await expect(readReviewPackage('not json')).rejects.toThrow(ImportError);
    await expect(readReviewPackage(JSON.stringify({ format: 'shahil-fna', version: 1, encrypted: false, document: {} }))).rejects.toThrow(/shahil-fna file, not a review package/);
    const broken = structuredClone(pkg);
    (broken.bundle as unknown as Record<string, unknown>).call_id = 42;
    await expect(readReviewPackage(JSON.stringify(broken))).rejects.toThrow(/does not match the review contract/);
  });

  it('refuses a package whose parts do not belong together', () => {
    const schemas = { pkg: schema('review_package'), sidecar: schema('sidecar') };
    const wrong = structuredClone(pkg);
    wrong.bundle.blocking_flags[0].fact_id = 'no.such#fact';
    wrong.bundle.sidecar.call_id = 'another-call';
    const field = Object.values(wrong.bundle.sidecar.fields).find((f) => f.evidence.length)!;
    field.evidence[0].segment_id = 's9999';
    const problems = checkPackage(wrong, schemas);
    expect(problems).toEqual(
      expect.arrayContaining([
        'the sidecar belongs to another call or run',
        `flag ${wrong.bundle.blocking_flags[0].flag_id} names a fact the sidecar does not have`,
        expect.stringMatching(/quotes segment s9999, which the transcript does not have/),
      ]),
    );
    expect(checkPackage(pkg, schemas)).toEqual([]);
  });

  it('builds a review from a plain client file and its sidecar when there is no package', async () => {
    const sidecarText = JSON.stringify(bundle.sidecar);
    expect(readTransferFile(sidecarText)?.format).toBe('advisor-intel-sidecar');
    const sidecar = await readSidecar(sidecarText);
    const median = await importDocument(await (await exportDocument(normaliseDocument(structuredClone(bundle.median_document)))).text());
    const b = bundleFromFiles(median, sidecar, normaliseDocument(structuredClone(bundle.prior_document!)));
    expect(b.transcript.segments).toEqual([]);
    expect(b.entities['policy.group_disability_new']?.fna_list).toBe('policies');
    expect(b.entities['person.kagiso_mokoena_2']?.fna_id).toBe('b4db1d21-aadf-4118-8b47-437035cf7952');
    const s = createSession(b, { docId: median.id, reviewerId: STAMP.reviewerId, now: STAMP.at });
    expect(s.facts[F.income].valueText).toBe(createSession(bundle, { docId: median.id, reviewerId: STAMP.reviewerId, now: STAMP.at }).facts[F.income].valueText);
    await expect(readSidecar(text)).rejects.toThrow(/not a provenance sidecar/);
  });

  it('tells the file formats apart by their tag', () => {
    expect(readTransferFile(text)?.format).toBe('advisor-intel-review');
    expect(readTransferFile(JSON.stringify({ format: 'advisor-intel-review-result' }))?.format).toBe('advisor-intel-review-result');
    expect(readTransferFile(JSON.stringify({ format: 'something-else' }))).toBeNull();
    expect(readTransferFile('[1, 2]')).toBeNull();
  });
});

describe('starting a review on this device', () => {
  it('belongs to the prior file’s client, storing the prior when the client is not here yet', () => {
    const local = normaliseDocument(structuredClone(bundle.prior_document!));
    expect(planStart(bundle, null)).toMatchObject({ docId: bundle.prior_document!.id, local: null });
    expect(planStart(bundle, null).saveClient?.id).toBe(bundle.prior_document!.id);
    expect(planStart(bundle, local)).toEqual({ docId: bundle.prior_document!.id, saveClient: null, local });
    expect(planStart({ ...bundle, prior_document: null }, null)).toEqual({ docId: bundle.median_document.id, saveClient: null, local: null });
  });

  it('stamps ops with the API reviewer id, else the practice’s adviser', () => {
    const practice = { ...defaultPractice(), adviserName: 'Sipho Dlamini' };
    expect(reviewerIdOf({ enabled: false, baseUrl: '', token: '', reviewerId: ' rev-7 ' }, practice)).toBe('rev-7');
    expect(reviewerIdOf(null, practice)).toBe('adviser-sipho-dlamini');
    expect(reviewerIdOf(null, null)).toBe('adviser');
  });
});

describe('exporting the review', () => {
  const s = applyAction(createSession(bundle, { docId: bundle.prior_document!.id, reviewerId: STAMP.reviewerId, now: STAMP.at }), { type: 'accept', factId: F.groceries }, STAMP);

  it('writes a draft review result that matches the contract, named for the client and call', async () => {
    const result = toResult(s, { submitted: false, at: STAMP.at });
    const json = JSON.parse(await reviewResultBlob(result).text());
    expect(describeErrors(validate(schema('review_result'), json))).toBe('');
    expect(json.submitted).toBe(false);
    expect(reviewResultName(s.working, result, STAMP.at)).toBe('Thabo_Mokoena_review_call_2026_10_15_mokoena_2026-10-16_draft.json');
  });

  it('round-trips the reviewed client file through the app’s own export and import', async () => {
    expect(() => submit(s, STAMP.at)).toThrow(/blocking flag/);
    const doc = { ...s.working, id: s.docId };
    const back = await importDocument(await (await exportDocument(doc)).text());
    expect(back).toEqual(normaliseDocument(structuredClone(doc)));
    expect(reviewedFileName(doc, STAMP.at, false)).toBe('Thabo_Mokoena_FNA_2026-10-16_reviewed.fna.json');
  });
});
