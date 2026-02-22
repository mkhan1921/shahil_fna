/* eslint-disable @typescript-eslint/no-require-imports */
const puppeteer = require('puppeteer');
const fs = require('fs');

async function testPDFGeneration() {
  console.log('Starting PDF generation test...');
  
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  
  try {
    const page = await browser.newPage();
    
    // Navigate to the application
    await page.goto('http://localhost:3000', { 
      waitUntil: 'networkidle0',
      timeout: 60000 
    });
    
    console.log('Page loaded');
    
    // Wait for the generate PDF button and click it
    // Note: This requires the button to be present and clickable
    // We might need to fill out some form data first if the button is disabled
    
    // Taking a screenshot for debugging
    await page.screenshot({ path: 'test-results/page-load.png' });
    
    console.log('Screenshot taken');
    
  } catch (error) {
    console.error('Test failed:', error);
  } finally {
    await browser.close();
  }
}

testPDFGeneration();
