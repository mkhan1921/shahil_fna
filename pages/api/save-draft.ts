
import type { NextApiRequest, NextApiResponse } from 'next';
import fs from 'fs';
import path from 'path';
import { FormData } from '@/types';

export default function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  if (req.method !== 'POST') {
    return res.status(405).json({ message: 'Method not allowed' });
  }

  try {
    const data: FormData = req.body;
    if (!data || typeof data !== 'object') {
      return res.status(400).json({ message: 'Invalid payload' });
    }
    
    // Don't save if no client name is provided
    if (!data.clientName || data.clientName.trim() === '') {
      return res.status(200).json({ message: 'Skipped save: No client name' });
    }

    const dataDir = path.join(process.cwd(), 'data');
    if (!fs.existsSync(dataDir)) {
      fs.mkdirSync(dataDir, { recursive: true });
    }

    // Sanitize filename
    const safeClientName = data.clientName.replace(/[^a-z0-9]/gi, '_').toLowerCase();
    const clientFilePath = path.join(dataDir, `${safeClientName}.json`);
    
    let clientRecords = [];
    if (fs.existsSync(clientFilePath)) {
      const fileContent = fs.readFileSync(clientFilePath, 'utf-8');
      try {
        clientRecords = JSON.parse(fileContent);
        if (!Array.isArray(clientRecords)) {
          clientRecords = [clientRecords];
        }
      } catch (e) {
        console.error('Error parsing records:', e);
        clientRecords = [];
      }
    }

    const record = {
      ...data,
      savedAt: new Date().toISOString(),
      isAutosave: true
    };

    // Smart overwrite: If the last record was an autosave, overwrite it.
    // Otherwise, append a new record.
    if (clientRecords.length > 0 && clientRecords[clientRecords.length - 1].isAutosave) {
      clientRecords[clientRecords.length - 1] = record;
    } else {
      clientRecords.push(record);
    }
    
    fs.writeFileSync(clientFilePath, JSON.stringify(clientRecords, null, 2));

    // Also update main records file? 
    // The user requirement says "Each client should have there own labelled json file".
    // Saving every 10s to the main "records.json" might be too much noise and concurrency.
    // Let's stick to the client-specific file for autosaves as that's what's used for reloading.

    res.status(200).json({ message: 'Draft saved successfully' });
  } catch (error) {
    console.error('Autosave Error:', error);
    res.status(500).json({ error: 'Failed to save draft' });
  }
}
