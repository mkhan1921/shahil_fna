/**
 * Name-based UUIDs (RFC 9562 version 5), synchronous so op builders stay pure functions.
 *
 * The pipeline gives a list row it creates during a call the id
 * uuid5(NAMESPACE_URL, `advisor-intel/{call_id}/{entity_id}`) (binding.row_id_for); the overlay
 * computes the same id when the reviewer adds or re-attributes a fact to a new row, so the row
 * the adviser sees is the row the pipeline's reviewed document will have.
 */

export const NAMESPACE_URL = '6ba7b811-9dad-11d1-80b4-00c04fd430c8';

const rotl = (x: number, n: number) => (x << n) | (x >>> (32 - n));

/** SHA-1 of a byte string (FIPS 180-4). */
export const sha1 = (bytes: Uint8Array): Uint8Array => {
  const bitLength = bytes.length * 8;
  const padded = new Uint8Array(((bytes.length + 9 + 63) >> 6) << 6);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(bitLength / 2 ** 32));
  view.setUint32(padded.length - 4, bitLength >>> 0);
  let h0 = 0x67452301;
  let h1 = 0xefcdab89;
  let h2 = 0x98badcfe;
  let h3 = 0x10325476;
  let h4 = 0xc3d2e1f0;
  const w = new Uint32Array(80);
  for (let off = 0; off < padded.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4);
    for (let i = 16; i < 80; i++) w[i] = rotl(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;
    for (let i = 0; i < 80; i++) {
      const [f, k] = i < 20 ? [(b & c) | (~b & d), 0x5a827999] : i < 40 ? [b ^ c ^ d, 0x6ed9eba1] : i < 60 ? [(b & c) | (b & d) | (c & d), 0x8f1bbcdc] : [b ^ c ^ d, 0xca62c1d6];
      const t = (rotl(a, 5) + f + e + k + w[i]) >>> 0;
      e = d;
      d = c;
      c = rotl(b, 30) >>> 0;
      b = a;
      a = t;
    }
    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
  }
  const out = new Uint8Array(20);
  const ov = new DataView(out.buffer);
  [h0, h1, h2, h3, h4].forEach((h, i) => ov.setUint32(i * 4, h));
  return out;
};

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');

const uuidBytes = (uuid: string): Uint8Array => {
  const h = uuid.replace(/-/g, '');
  if (!/^[0-9a-f]{32}$/i.test(h)) throw new RangeError(`not a UUID: ${uuid}`);
  return Uint8Array.from({ length: 16 }, (_, i) => parseInt(h.slice(i * 2, i * 2 + 2), 16));
};

/** uuid5(namespace, name), as Python's uuid.uuid5. */
export const uuid5 = (namespace: string, name: string): string => {
  const ns = uuidBytes(namespace);
  const nm = new TextEncoder().encode(name);
  const buf = new Uint8Array(ns.length + nm.length);
  buf.set(ns);
  buf.set(nm, ns.length);
  const b = sha1(buf).slice(0, 16);
  b[6] = (b[6] & 0x0f) | 0x50;
  b[8] = (b[8] & 0x3f) | 0x80;
  const s = hex(b);
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${s.slice(20)}`;
};

/** The FnaDocument row id the pipeline gives an entity that has no row yet. */
export const rowIdForNewEntity = (callId: string, entityId: string): string => uuid5(NAMESPACE_URL, `advisor-intel/${callId}/${entityId}`);
