/**
 * Export / import of client files, optionally encrypted with a password
 * (PBKDF2-SHA256 → AES-256-GCM via WebCrypto) so files can be moved between
 * devices or archived for the 5-year FAIS record-keeping period safely.
 */
import { normaliseDocument } from '../fna/defaults';
import type { FnaDocument } from '../fna/types';

const FORMAT = 'shahil-fna';
const ITERATIONS = 310_000;

interface PlainFile {
  format: typeof FORMAT;
  version: 1;
  encrypted: false;
  exportedAt: string;
  document: FnaDocument;
}

interface EncryptedFile {
  format: typeof FORMAT;
  version: 1;
  encrypted: true;
  exportedAt: string;
  kdf: { name: 'PBKDF2'; hash: 'SHA-256'; iterations: number; salt: string };
  cipher: { name: 'AES-GCM'; iv: string };
  data: string;
}

const toB64 = (buf: ArrayBuffer | Uint8Array): string => {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
};

const fromB64 = (b64: string): Uint8Array<ArrayBuffer> => {
  const s = atob(b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
};

const deriveKey = async (password: string, salt: Uint8Array<ArrayBuffer>, iterations: number) => {
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', hash: 'SHA-256', salt, iterations },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  );
};

export const exportDocument = async (doc: FnaDocument, password?: string): Promise<Blob> => {
  const exportedAt = new Date().toISOString();
  if (!password) {
    const file: PlainFile = { format: FORMAT, version: 1, encrypted: false, exportedAt, document: doc };
    return new Blob([JSON.stringify(file, null, 2)], { type: 'application/json' });
  }
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(password, salt, ITERATIONS);
  const cipher = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(JSON.stringify(doc)));
  const file: EncryptedFile = {
    format: FORMAT,
    version: 1,
    encrypted: true,
    exportedAt,
    kdf: { name: 'PBKDF2', hash: 'SHA-256', iterations: ITERATIONS, salt: toB64(salt) },
    cipher: { name: 'AES-GCM', iv: toB64(iv) },
    data: toB64(cipher),
  };
  return new Blob([JSON.stringify(file)], { type: 'application/json' });
};

export class PasswordRequired extends Error {}

export const importDocument = async (text: string, password?: string): Promise<FnaDocument> => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error('This file is not a valid FNA export.');
  }
  const file = parsed as { format?: string; encrypted?: boolean };
  if (file.format !== FORMAT) throw new Error('This file is not a valid FNA export.');
  if (!file.encrypted) return normaliseDocument((file as PlainFile).document);
  if (!password) throw new PasswordRequired('This file is encrypted. Enter its password.');
  const enc = file as EncryptedFile;
  try {
    const key = await deriveKey(password, fromB64(enc.kdf.salt), enc.kdf.iterations);
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: fromB64(enc.cipher.iv) }, key, fromB64(enc.data));
    return normaliseDocument(JSON.parse(new TextDecoder().decode(plain)));
  } catch {
    throw new Error('Incorrect password, or the file is damaged.');
  }
};

export const downloadBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

export const safeFilename = (s: string) => s.replace(/[^a-z0-9]+/gi, '_').replace(/^_+|_+$/g, '') || 'client';
