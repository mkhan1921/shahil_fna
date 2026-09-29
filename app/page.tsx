'use client';

import {
  ArrowRight,
  Copy,
  Download,
  FileText,
  FolderOpen,
  Plus,
  Search,
  Sparkles,
  Trash2,
  Upload,
  UserRound,
} from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMemo, useRef, useState } from 'react';
import AppShell from '@/components/AppShell';
import ExportDialog from '@/components/ExportDialog';
import Modal from '@/components/Modal';
import { Badge, Button, Callout, Card, EmptyState, TextField, cx } from '@/components/ui';
import { analyse, personName } from '@/lib/fna/analysis';
import { newDocument, normaliseDocument, uid } from '@/lib/fna/defaults';
import { sampleDocument } from '@/lib/fna/sample';
import type { FnaDocument } from '@/lib/fna/types';
import { relativeTime } from '@/lib/format';
import { deleteDocument, saveDocument } from '@/lib/store/db';
import { useDocumentList, usePractice } from '@/lib/store/hooks';
import { PasswordRequired, importDocument } from '@/lib/store/transfer';

const STATUS: Record<FnaDocument['status'], { label: string; tone: 'neutral' | 'brand' | 'good' | 'warning' }> = {
  draft: { label: 'Draft', tone: 'neutral' },
  'in-progress': { label: 'In progress', tone: 'brand' },
  'advice-given': { label: 'Advice given', tone: 'good' },
  archived: { label: 'Archived', tone: 'neutral' },
};

const householdName = (d: FnaDocument) => {
  const c = personName(d.client, '');
  const s = d.household.includeSpouse ? personName(d.spouse, '') : '';
  if (!c && !s) return 'Unnamed client';
  if (c && s && d.client.surname && d.client.surname === d.spouse.surname) return `${d.client.firstName} & ${d.spouse.firstName} ${d.client.surname}`;
  return [c, s].filter(Boolean).join(' & ');
};

export default function ClientsPage() {
  const router = useRouter();
  const { docs } = useDocumentList();
  const { practice } = usePractice();
  const [query, setQuery] = useState('');
  const [exporting, setExporting] = useState<FnaDocument | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<FnaDocument | null>(null);
  const [importText, setImportText] = useState<string | null>(null);
  const [importPassword, setImportPassword] = useState('');
  const [importError, setImportError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const rows = useMemo(() => {
    if (!docs) return null;
    const q = query.trim().toLowerCase();
    return docs
      .filter((d) => !q || householdName(d).toLowerCase().includes(q) || d.client.idNumber.includes(q))
      .map((d) => {
        const a = analyse(d);
        return {
          doc: d,
          name: householdName(d),
          completeness: a.completenessScore,
          critical: a.findings.filter((f) => f.severity === 'critical').length,
        };
      });
  }, [docs, query]);

  const create = async (doc: FnaDocument) => {
    await saveDocument(doc);
    router.push(`/client?id=${doc.id}`);
  };

  const onNew = () => create(newDocument(practice?.defaultAssumptions));
  const onSample = () => create(sampleDocument());

  const onDuplicate = async (d: FnaDocument) => {
    const copy = normaliseDocument({ ...structuredClone(d), id: uid(), createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), status: 'draft' });
    copy.advice = { ...copy.advice, clientSignedAt: '', adviserSignedAt: '' };
    await saveDocument(copy);
  };

  const tryImport = async (text: string, password?: string) => {
    try {
      const doc = await importDocument(text, password);
      const existing = docs?.find((d) => d.id === doc.id);
      if (existing) doc.id = uid();
      doc.updatedAt = new Date().toISOString();
      await saveDocument(doc);
      setImportText(null);
      setImportPassword('');
      setImportError(null);
      router.push(`/client?id=${doc.id}`);
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Import failed';
      if (e instanceof PasswordRequired) {
        setImportText(text);
        setImportError(null);
      } else if (password) {
        setImportError(message);
      } else {
        setBanner(message);
      }
    }
  };

  const [banner, setBanner] = useState<string | null>(null);

  const practiceIncomplete = practice && (!practice.fspNumber || !practice.adviserName || !practice.fspName);

  return (
    <AppShell>
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-ink">Clients</h1>
            <p className="mt-1 text-sm text-muted">Financial needs analyses and records of advice. Everything is saved automatically on this device.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".fna,.json,application/json"
              className="hidden"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                e.target.value = '';
                if (f) await tryImport(await f.text());
              }}
            />
            <Button icon={<Upload size={15} />} onClick={() => fileRef.current?.click()}>
              Import
            </Button>
            <Button icon={<Sparkles size={15} />} onClick={onSample}>
              Sample client
            </Button>
            <Button variant="primary" icon={<Plus size={16} />} onClick={onNew}>
              New FNA
            </Button>
          </div>
        </div>

        {banner && (
          <Callout tone="critical" className="mb-4" title="Import failed">
            {banner}{' '}
            <button className="underline" onClick={() => setBanner(null)}>
              Dismiss
            </button>
          </Callout>
        )}

        {practiceIncomplete && (
          <Callout tone="warning" className="mb-6" title="Complete your practice profile">
            Your FSP name, licence number and representative details appear on every report as required by the FAIS General Code of Conduct.{' '}
            <Link href="/settings" className="font-medium text-brand underline underline-offset-2">
              Set up practice details
            </Link>
          </Callout>
        )}

        <Card>
          <div className="flex items-center gap-3 border-b border-line px-4 py-3">
            <Search size={16} className="text-faint" aria-hidden />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name or ID number"
              className="w-full bg-transparent text-sm outline-none placeholder:text-faint"
              aria-label="Search clients"
            />
            {rows && <span className="shrink-0 text-xs text-muted">{rows.length} client{rows.length === 1 ? '' : 's'}</span>}
          </div>

          {rows === null ? (
            <div className="px-5 py-16 text-center text-sm text-muted">Loading…</div>
          ) : rows.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<FolderOpen size={36} strokeWidth={1.5} />}
                title={query ? 'No clients match your search' : 'No clients yet'}
                description={
                  query
                    ? 'Try a different name or ID number.'
                    : 'Start a new financial needs analysis, or open the sample family to see a completed FNA and report.'
                }
                action={
                  !query && (
                    <div className="flex gap-2">
                      <Button icon={<Sparkles size={15} />} onClick={onSample}>
                        Open sample client
                      </Button>
                      <Button variant="primary" icon={<Plus size={16} />} onClick={onNew}>
                        New FNA
                      </Button>
                    </div>
                  )
                }
              />
            </div>
          ) : (
            <ul className="divide-y divide-line">
              {rows.map(({ doc, name, completeness, critical }) => (
                <li key={doc.id} className="group flex flex-wrap items-center gap-4 px-4 py-3.5 hover:bg-wash/60 sm:flex-nowrap">
                  <Link href={`/client?id=${doc.id}`} className="flex min-w-0 flex-1 items-center gap-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand">
                      <UserRound size={18} aria-hidden />
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-ink group-hover:text-brand">{name}</span>
                      <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                        <Badge tone={STATUS[doc.status].tone}>{STATUS[doc.status].label}</Badge>
                        <span>Updated {relativeTime(doc.updatedAt)}</span>
                        {critical > 0 && <Badge tone="critical">{critical} critical finding{critical === 1 ? '' : 's'}</Badge>}
                      </span>
                    </span>
                  </Link>
                  <div className="hidden w-40 shrink-0 sm:block" title={`${Math.round(completeness * 100)}% complete`}>
                    <div className="mb-1 flex justify-between text-[11px] text-muted">
                      <span>Completeness</span>
                      <span className="tabular">{Math.round(completeness * 100)}%</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-brand-soft">
                      <div className={cx('h-full rounded-full', completeness >= 0.9 ? 'bg-good' : 'bg-brand-2')} style={{ width: `${Math.max(4, completeness * 100)}%` }} />
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Link
                      href={`/report?id=${doc.id}`}
                      className="focus-ring inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[13px] font-medium text-ink-2 hover:bg-wash hover:text-ink"
                      title="Open report"
                    >
                      <FileText size={15} /> <span className="hidden lg:inline">Report</span>
                    </Link>
                    <Button variant="ghost" size="sm" onClick={() => setExporting(doc)} title="Export" aria-label="Export">
                      <Download size={15} />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => onDuplicate(doc)} title="Duplicate for a new review" aria-label="Duplicate">
                      <Copy size={15} />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(doc)} title="Delete" aria-label="Delete" className="hover:text-critical-ink">
                      <Trash2 size={15} />
                    </Button>
                    <Link href={`/client?id=${doc.id}`} className="focus-ring ml-1 inline-flex h-8 items-center gap-1 rounded-lg bg-brand px-3 text-[13px] font-medium text-white hover:bg-brand-2">
                      Open <ArrowRight size={14} />
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <p className="mt-6 text-xs leading-5 text-muted">
          Client files are stored in this browser only and are not sent to any server. Export files regularly to keep a backup and to meet the
          five-year record-keeping requirement of the FAIS Act and FICA. Clearing your browser data will remove stored clients.
        </p>
      </main>

      <ExportDialog doc={exporting} open={!!exporting} onClose={() => setExporting(null)} />

      <Modal
        open={!!confirmDelete}
        onClose={() => setConfirmDelete(null)}
        title="Delete client?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              icon={<Trash2 size={15} />}
              onClick={async () => {
                if (confirmDelete) await deleteDocument(confirmDelete.id);
                setConfirmDelete(null);
              }}
            >
              Delete permanently
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          <strong>{confirmDelete && householdName(confirmDelete)}</strong> will be removed from this device. This cannot be undone — export the file
          first if you need to keep a record.
        </p>
      </Modal>

      <Modal
        open={importText !== null}
        onClose={() => {
          setImportText(null);
          setImportError(null);
        }}
        title="Encrypted file"
        footer={
          <Button variant="primary" onClick={() => importText && tryImport(importText, importPassword)} disabled={!importPassword}>
            Decrypt & import
          </Button>
        }
      >
        <div className="space-y-3">
          <p className="text-sm text-ink-2">This client file is password protected.</p>
          <TextField label="Password" type="password" value={importPassword} onChange={setImportPassword} error={importError ?? undefined} />
        </div>
      </Modal>
    </AppShell>
  );
}
