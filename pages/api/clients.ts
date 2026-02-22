
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

  try {
    const dataDir = path.join(process.cwd(), 'data');
    if (!fs.existsSync(dataDir)) {
      fs.mkdirSync(dataDir, { recursive: true });
      return res.status(200).json([]);
    }

    const files = fs.readdirSync(dataDir)
      .filter(file => file.endsWith('.json') && file !== 'records.json')
      .map(file => {
        const filePath = path.join(dataDir, file);
        const stats = fs.statSync(filePath);
        return {
          filename: file,
          name: file.replace('.json', ''),
          lastModified: stats.mtime
        };
      })
      .sort((a, b) => b.lastModified.getTime() - a.lastModified.getTime());

    res.status(200).json(files);
  } catch (error) {
    console.error('Error listing clients:', error);
    res.status(500).json({ error: 'Failed to list clients' });
  }
}
