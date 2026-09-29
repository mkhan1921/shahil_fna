/**
 * Local-first persistence in the browser's IndexedDB.
 *
 * Client records never leave the adviser's device unless the adviser exports
 * them. This keeps the app POPIA-friendly by design: no server stores special
 * personal information (health, ID numbers) on anyone's behalf.
 */
import { defaultPractice, normaliseDocument } from '../fna/defaults';
import type { FnaDocument, PracticeProfile } from '../fna/types';

const DB_NAME = 'shahil-fna';
const DB_VERSION = 1;
const DOCS = 'documents';
const SETTINGS = 'settings';

let dbPromise: Promise<IDBDatabase> | null = null;

const openDb = (): Promise<IDBDatabase> => {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(DOCS)) db.createObjectStore(DOCS, { keyPath: 'id' });
      if (!db.objectStoreNames.contains(SETTINGS)) db.createObjectStore(SETTINGS);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => {
      dbPromise = null;
      reject(req.error);
    };
  });
  return dbPromise;
};

const run = async <T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
  const db = await openDb();
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction(store, mode);
    const req = fn(tx.objectStore(store));
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
};

export const listDocuments = async (): Promise<FnaDocument[]> => {
  const all = await run<FnaDocument[]>(DOCS, 'readonly', (s) => s.getAll() as IDBRequest<FnaDocument[]>);
  return all.map(normaliseDocument).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
};

export const getDocument = async (id: string): Promise<FnaDocument | null> => {
  const doc = await run<FnaDocument | undefined>(DOCS, 'readonly', (s) => s.get(id) as IDBRequest<FnaDocument | undefined>);
  return doc ? normaliseDocument(doc) : null;
};

export const saveDocument = async (doc: FnaDocument): Promise<void> => {
  await run(DOCS, 'readwrite', (s) => s.put(doc));
  notify();
};

export const deleteDocument = async (id: string): Promise<void> => {
  await run(DOCS, 'readwrite', (s) => s.delete(id));
  notify();
};

export const getPractice = async (): Promise<PracticeProfile> => {
  const stored = await run<PracticeProfile | undefined>(SETTINGS, 'readonly', (s) => s.get('practice') as IDBRequest<PracticeProfile | undefined>);
  const base = defaultPractice();
  return stored ? { ...base, ...stored, defaultAssumptions: { ...base.defaultAssumptions, ...stored.defaultAssumptions } } : base;
};

export const savePractice = async (practice: PracticeProfile): Promise<void> => {
  await run(SETTINGS, 'readwrite', (s) => s.put(practice, 'practice'));
  notify();
};

/* Cross-tab change notifications so lists stay fresh. */
type Listener = () => void;
const listeners = new Set<Listener>();
let channel: BroadcastChannel | null = null;

const getChannel = () => {
  if (typeof BroadcastChannel === 'undefined') return null;
  if (!channel) {
    channel = new BroadcastChannel('shahil-fna');
    channel.onmessage = () => listeners.forEach((l) => l());
  }
  return channel;
};

const notify = () => {
  listeners.forEach((l) => l());
  getChannel()?.postMessage('changed');
};

export const subscribe = (listener: Listener): (() => void) => {
  listeners.add(listener);
  getChannel();
  return () => listeners.delete(listener);
};
