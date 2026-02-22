/* eslint-disable @typescript-eslint/no-require-imports */
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  
  // Set content directly or navigate to local server
  // Here we'll try to generate a PDF from the local running app
  
  try {
    await page.goto('http://localhost:3000', { waitUntil: 'networkidle0' });
    
    // Fill in some data if needed, or just print current view
    // Ideally we navigate to a specific print view or click the generate button
    
    // For now, let's just snapshot the homepage
    await page.pdf({
      path: 'test-report.pdf',
      format: 'A4',
      printBackground: true
    });

    console.log('PDF generated successfully: test-report.pdf');
  } catch (error) {
    console.error('Error generating PDF:', error);
  } finally {
    await browser.close();
  }
})();
