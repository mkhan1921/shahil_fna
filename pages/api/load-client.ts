
import type { NextApiRequest, NextApiResponse } from 'next';
import fs from 'fs';
import path from 'path';

export default function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  if (req.method !== 'GET') {
    return res.status(405).json({ message: 'Method not allowed' });
  }

  const { filename } = req.query;

  if (!filename || typeof filename !== 'string') {
    return res.status(400).json({ message: 'Filename is required' });
  }

  const safeFilename = path.basename(filename);
  if (
    safeFilename !== filename ||
    !/^[a-z0-9_-]+\.json$/i.test(safeFilename) ||
    safeFilename.toLowerCase() === 'records.json'
  ) {
    return res.status(400).json({ message: 'Invalid filename' });
  }

  try {
    const dataDir = path.join(process.cwd(), 'data');
    const filePath = path.join(dataDir, safeFilename);

    if (!fs.existsSync(filePath)) {
      return res.status(404).json({ message: 'Client file not found' });
    }

    const fileContent = fs.readFileSync(filePath, 'utf-8');
    const data = JSON.parse(fileContent);

    // If it's an array of records (history), return the last one (most recent)
    // or return the object itself if it's a single record
    const latestData = Array.isArray(data) ? data[data.length - 1] : data;

    res.status(200).json(latestData);
  } catch (error) {
    console.error('Error loading client:', error);
    res.status(500).json({ error: 'Failed to load client data' });
  }
}
