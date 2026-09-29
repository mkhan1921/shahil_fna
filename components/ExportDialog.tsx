'use client';

import { Download, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { personName } from '@/lib/fna/analysis';
import type { FnaDocument } from '@/lib/fna/types';
import { downloadBlob, exportDocument, safeFilename } from '@/lib/store/transfer';
import Modal from './Modal';
import { Button, Callout, TextField } from './ui';

export default function ExportDialog({ doc, open, onClose }: { doc: FnaDocument | null; open: boolean; onClose: () => void }) {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const mismatch = password.length > 0 && confirm.length > 0 && password !== confirm;
  const weak = password.length > 0 && password.length < 8;

  const run = async () => {
    if (!doc) return;
    setBusy(true);
    try {
      const blob = await exportDocument(doc, password || undefined);
      const name = safeFilename(personName(doc.client, 'client'));
      downloadBlob(blob, `${name}_FNA_${new Date().toISOString().slice(0, 10)}${password ? '.fna' : '.fna.json'}`);
      setPassword('');
      setConfirm('');
      onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Export client file"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" icon={<Download size={15} />} disabled={busy || mismatch || weak} onClick={run}>
            {password ? 'Export encrypted' : 'Export unencrypted'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-ink-2">
          Download this client’s FNA to back it up, archive it for the FAIS five-year retention period, or open it on another device.
        </p>
        <Callout tone="info" title="Encrypt with a password (recommended)">
          The file contains special personal information. With a password it is encrypted with AES-256 and cannot be read without it.
        </Callout>
        <TextField label="Password" type="password" value={password} onChange={setPassword} error={weak ? 'Use at least 8 characters' : undefined} optional />
        {password && <TextField label="Confirm password" type="password" value={confirm} onChange={setConfirm} error={mismatch ? 'Passwords do not match' : undefined} />}
        {password && (
          <p className="flex items-center gap-1.5 text-xs text-muted">
            <ShieldCheck size={14} className="text-good-ink" /> Store the password safely — it cannot be recovered.
          </p>
        )}
      </div>
    </Modal>
  );
}
