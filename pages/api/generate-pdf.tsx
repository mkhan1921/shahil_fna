
import type { NextApiRequest, NextApiResponse } from 'next';
import puppeteer from 'puppeteer';
import type { Browser } from 'puppeteer';
import { renderToString } from 'react-dom/server';
import PDFTemplate from '@/components/PDFTemplate';
import fs from 'fs';
import path from 'path';
import { FormData } from '@/types';

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  if (req.method !== 'POST') {
    return res.status(405).json({ message: 'Method not allowed' });
  }

  const data: FormData = req.body;
  if (!data || typeof data !== 'object') {
    return res.status(400).json({ message: 'Invalid payload' });
  }

  const clientName = typeof data.clientName === 'string' ? data.clientName.trim() : '';
  const shouldPersist = clientName.length > 0;

  try {
    if (shouldPersist) {
      const dataDir = path.join(process.cwd(), 'data');
      if (!fs.existsSync(dataDir)) {
        fs.mkdirSync(dataDir, { recursive: true });
      }
      
      const record = {
          ...data,
          savedAt: new Date().toISOString(),
          isAutosave: false
      };

      const recordsPath = path.join(dataDir, 'records.json');
      let records = [];
      if (fs.existsSync(recordsPath)) {
        const fileContent = fs.readFileSync(recordsPath, 'utf-8');
        try {
          records = JSON.parse(fileContent);
        } catch (error) {
          console.error('Error parsing records:', error);
          records = [];
        }
      }
      records.push(record);
      fs.writeFileSync(recordsPath, JSON.stringify(records, null, 2));

      const safeClientName = clientName.replace(/[^a-z0-9]/gi, '_').toLowerCase();
      const clientFilePath = path.join(dataDir, `${safeClientName}.json`);
      
      let clientRecords = [];
      if (fs.existsSync(clientFilePath)) {
        const fileContent = fs.readFileSync(clientFilePath, 'utf-8');
        try {
          clientRecords = JSON.parse(fileContent);
          if (!Array.isArray(clientRecords)) {
            clientRecords = [clientRecords];
          }
        } catch (error) {
          console.error('Error parsing client records:', error);
          clientRecords = [];
        }
      }
      
      if (clientRecords.length > 0 && clientRecords[clientRecords.length - 1].isAutosave) {
        clientRecords[clientRecords.length - 1] = record;
      } else {
        clientRecords.push(record);
      }

      fs.writeFileSync(clientFilePath, JSON.stringify(clientRecords, null, 2));
    }

    // Render React component to HTML
    const htmlContent = renderToString(<PDFTemplate data={data} />);
    
    // Wrap in basic HTML structure for better rendering with IBM Plex Sans
    const html = `
      <!DOCTYPE html>
      <html>
        <head>
          <meta charset="UTF-8" />
          <style>
            @import url('https://fonts.googleapis.com/css2?family=Merriweather:wght@300;400;700;900&display=swap');
            body { 
              font-family: 'Merriweather', 'Times New Roman', serif; 
              -webkit-print-color-adjust: exact;
              margin: 0;
              padding: 0;
            }
          </style>
        </head>
        <body>
          ${htmlContent}
        </body>
      </html>
    `;

    let browser: Browser | null = null;
    try {
      browser = await puppeteer.launch({
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-accelerated-2d-canvas',
          '--no-first-run',
          '--no-zygote',
          '--disable-gpu'
        ]
      });

      const page = await browser.newPage();
      
      // Increase timeout for large PDFs
      page.setDefaultTimeout(90000);

      await page.setContent(html, {
        waitUntil: 'domcontentloaded',
        timeout: 90000
      });

      const pdf = await page.pdf({
        format: 'A4',
        printBackground: true,
        margin: {
          top: '20px',
          right: '20px',
          bottom: '20px',
          left: '20px'
        },
        preferCSSPageSize: true
      });

      // Generate filename with primary member name and timestamp
      const primaryMember = data.primaryMember;
      const memberName = `${primaryMember.firstName || 'Client'}_${primaryMember.surname || 'Intake'}`.replace(/[^a-zA-Z0-9]/g, '_');
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5); // YYYY-MM-DDTHH-MM-SS
      const filename = `${memberName}_FNA_Report_${timestamp}.pdf`;

      res.setHeader('Content-Type', 'application/pdf');
      res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
      res.status(200).send(Buffer.from(pdf));
    } catch (error) {
      console.error('PDF Generation Error:', error);
      if (!res.headersSent) {
        res.status(500).json({ message: 'Error generating PDF', error: String(error) });
      }
    } finally {
      if (browser) {
        await browser.close();
      }
    }

  } catch (error) {
    console.error('PDF Generation Error:', error);
    res.status(500).json({ error: 'Failed to generate PDF' });
  }
}
