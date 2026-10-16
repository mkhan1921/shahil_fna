/** The opt-in review API client (spec §11.5): nothing is sent until it is configured and allowed. */
import { describe, expect, it, vi } from 'vitest';
import { allowedOrigins, apiReady, createReviewApi, originOf, ReviewApiError, settingsProblems, type ReviewApiSettings } from '@/lib/review/api';
import { normaliseDocument } from '@/lib/fna/defaults';
import { loadPackage } from './reviewFixture';

const ORIGINS = ['https://intel.practice.co.za', 'http://localhost:8000'];
const ON: ReviewApiSettings = { enabled: true, baseUrl: 'https://intel.practice.co.za/api/', token: 'tok-123', reviewerId: 'adviser-sipho' };

const reply = (status: number, body: unknown) => new Response(body === undefined ? '' : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('settings', () => {
  it('reads the deployment’s allowed origins', () => {
    expect(allowedOrigins('https://a.example.com/, http://localhost:8000  https://b.example.com')).toEqual(['https://a.example.com', 'http://localhost:8000', 'https://b.example.com']);
    expect(allowedOrigins('')).toEqual([]);
    expect(originOf('https://intel.practice.co.za/api')).toBe('https://intel.practice.co.za');
    expect(originOf('not a url')).toBeNull();
  });

  it('is off until enabled, and then needs an allowed https origin, a token and a reviewer id', () => {
    expect(settingsProblems({ ...ON, enabled: false }, ORIGINS)).toEqual(['The review API is off.']);
    expect(settingsProblems(ON, ORIGINS)).toEqual([]);
    expect(apiReady(ON, ORIGINS)).toBe(true);
    expect(settingsProblems({ ...ON, baseUrl: 'http://intel.practice.co.za/api' }, ['http://intel.practice.co.za'])).toEqual(['Use https, so client information is encrypted on the way.']);
    expect(settingsProblems({ ...ON, baseUrl: 'http://localhost:8000' }, ORIGINS)).toEqual([]);
    expect(settingsProblems({ ...ON, baseUrl: 'https://evil.example.com' }, ORIGINS)[0]).toMatch(/only allows/);
    expect(settingsProblems(ON, [])[0]).toMatch(/NEXT_PUBLIC_ADVISOR_INTEL_ORIGINS/);
    expect(settingsProblems({ ...ON, token: ' ', reviewerId: '' }, ORIGINS)).toEqual(['Enter the bearer token the practice was issued.', 'Enter your reviewer id.']);
    expect(apiReady(null)).toBe(false);
  });
});

describe('calls', () => {
  vi.stubEnv('NEXT_PUBLIC_ADVISOR_INTEL_ORIGINS', ORIGINS.join(','));

  it('sends the bearer token, no cookies, no cache and no referrer', async () => {
    const bundle = loadPackage().bundle;
    const fetchImpl = vi.fn(async () => reply(200, bundle));
    const api = createReviewApi(ON, fetchImpl);
    const got = await api.getReview('call 1/x');
    expect(got.call_id).toBe(bundle.call_id);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://intel.practice.co.za/api/calls/call%201%2Fx/review');
    expect(init.method).toBe('GET');
    expect(init.headers).toMatchObject({ Authorization: 'Bearer tok-123', Accept: 'application/json' });
    expect(init.credentials).toBe('omit');
    expect(init.cache).toBe('no-store');
    expect(init.referrerPolicy).toBe('no-referrer');
    expect(init.body).toBeUndefined();
  });

  it('posts ops, the submitted result, follow-up answers and a client file', async () => {
    const fetchImpl = vi.fn(async () => reply(200, { applied: [1, 2] }));
    const api = createReviewApi(ON, fetchImpl);
    expect(await api.postOps('c1', [])).toEqual({ applied: [1, 2] });
    await api.answerFollowup('c1', 'fu-1', { text: 'About 62', source: 'followup' });
    await api.sendClientFna('client-9', normaliseDocument({}));
    const calls = fetchImpl.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([u, i]) => `${i.method} ${u}`)).toEqual([
      'POST https://intel.practice.co.za/api/calls/c1/ops',
      'POST https://intel.practice.co.za/api/calls/c1/followups/fu-1/answer',
      'POST https://intel.practice.co.za/api/clients/client-9/fna',
    ]);
    expect(JSON.parse(String(calls[1][1].body))).toEqual({ text: 'About 62', source: 'followup' });
    expect(JSON.parse(String(calls[2][1].body))).toMatchObject({ format: 'shahil-fna', version: 1, encrypted: false });
    expect(calls[0][1].headers).toMatchObject({ 'Content-Type': 'application/json' });
  });

  it('reads the reviewed file in either shape the API may send', async () => {
    const doc = normaliseDocument({});
    const file = { format: 'shahil-fna', version: 1, document: doc };
    expect((await createReviewApi(ON, vi.fn(async () => reply(200, file))).getFna('c1')).file.document.id).toBe(doc.id);
    expect((await createReviewApi(ON, vi.fn(async () => reply(200, { file, sidecar: null }))).getFna('c1')).sidecar).toBeNull();
    await expect(createReviewApi(ON, vi.fn(async () => reply(200, { nope: 1 }))).getFna('c1')).rejects.toThrow(ReviewApiError);
  });

  it('reports refusals with the API’s reason, and network failures plainly', async () => {
    const refused = createReviewApi(ON, vi.fn(async () => reply(409, { detail: 'op 12 is not after the log' })));
    await expect(refused.postOps('c1', [])).rejects.toMatchObject({ status: 409, message: 'The review API refused the request (409): op 12 is not after the log.' });
    const down = createReviewApi(
      ON,
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );
    await expect(down.getReview('c1')).rejects.toMatchObject({ status: null, message: 'Could not reach the review API: Failed to fetch.' });
  });

  it('sends nothing at all while the settings are not usable', async () => {
    const fetchImpl = vi.fn(async () => reply(200, {}));
    await expect(createReviewApi({ ...ON, enabled: false }, fetchImpl).getReview('c1')).rejects.toThrow('The review API is off.');
    await expect(createReviewApi({ ...ON, baseUrl: 'https://elsewhere.example.com' }, fetchImpl).getReview('c1')).rejects.toThrow(/only allows/);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
