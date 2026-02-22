import { test, expect } from '@playwright/test';

test('Generate Comprehensive Test PDF', async ({ page }) => {
  test.setTimeout(300000); // 5 minutes for PDF generation
  
  console.log('=== Starting Comprehensive PDF Generation ===');
  
  // Navigate to the form
  await page.goto('/');
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(2000);
  
  console.log('Page loaded, filling form...');

  // ===== PRIMARY MEMBER =====
  console.log('Filling Primary Member...');
  await page.locator('#primary-member select').first().selectOption('Mr.');
  await page.waitForTimeout(300);
  
  const textInputs = await page.locator('#primary-member input[type="text"]').all();
  if (textInputs.length > 0) await textInputs[0].fill('John');
  if (textInputs.length > 1) await textInputs[1].fill('TestUser');
  
  const idInput = page.locator('#primary-member input[placeholder="0000000000000"]').first();
  if (await idInput.count() > 0) {
    await idInput.fill('9001015009087');
    await idInput.blur();
    await page.waitForTimeout(1000);
  }
  
  // ===== RELATIONSHIP =====
  console.log('Filling Relationship...');
  const relSection = page.locator('#relationship');
  await relSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  
  const marriedRadio = page.locator('#relationship input[type="radio"]').nth(1);
  if (await marriedRadio.count() > 0) {
    await marriedRadio.evaluate((el) => {
      (el as HTMLInputElement).checked = true;
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }
  const dateInput = page.locator('#relationship input[type="date"]');
  if (await dateInput.count() > 0) {
    await dateInput.first().fill('2015-06-15');
  }
  
  // ===== EDUCATION =====
  console.log('Filling Education...');
  const eduSection = page.locator('#education');
  await eduSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await page.locator('#education select').first().selectOption("Bachelor's Degree");
  const institutionInput = page.locator('#education input[placeholder*="Institution"]').first();
  if (await institutionInput.count() > 0) {
    await institutionInput.fill('University of Witwatersrand');
  }
  
  // ===== FINANCIAL PLANNING =====
  console.log('Filling Financial Planning...');
  const fpSection = page.locator('#financial-planning');
  await fpSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const fpInputs = await page.locator('#financial-planning input[type="text"]').all();
  if (fpInputs.length > 0) await fpInputs[0].fill('Allan Gray, Old Mutual');
  if (fpInputs.length > 1) await fpInputs[1].fill('None');
  const fpTextarea = page.locator('#financial-planning textarea');
  if (await fpTextarea.count() > 0) {
    await fpTextarea.first().fill('Review all policies');
  }
  
  // ===== CONTACT DETAILS =====
  console.log('Filling Contact Details...');
  const contactSection = page.locator('#contact');
  await contactSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  
  // Address
  const contactInputs = await page.locator('#contact input[type="text"]').all();
  if (contactInputs.length > 0) await contactInputs[0].fill('123 Test Street');
  if (contactInputs.length > 1) await contactInputs[1].fill('Johannesburg');
  if (contactInputs.length > 2) await contactInputs[2].fill('Gauteng');
  if (contactInputs.length > 3) await contactInputs[3].fill('2000');
  
  // Phone
  const telInputs = await page.locator('#contact input[type="tel"]').all();
  if (telInputs.length > 0) await telInputs[0].fill('0821234567');
  if (telInputs.length > 1) await telInputs[1].fill('0111234567');
  
  // Email
  const emailInput = page.locator('#contact input[type="email"]');
  if (await emailInput.count() > 0) {
    await emailInput.first().fill('john.test@example.com');
  }
  
  // Insurance amounts
  const numberInputs = await page.locator('#contact input[type="number"]').all();
  for (let i = 0; i < Math.min(10, numberInputs.length); i++) {
    await numberInputs[i].fill((i + 1) * 1000000 + '');
  }
  
  // Insurance recommendations
  const contactSelects = await page.locator('#contact select').all();
  for (let i = 0; i < Math.min(5, contactSelects.length); i++) {
    const options = await contactSelects[i].locator('option').all();
    if (options.length > 1) {
      await contactSelects[i].selectOption(options[1]);
    }
  }
  
  // ===== EMPLOYMENT =====
  console.log('Filling Employment...');
  const empSection = page.locator('#employment');
  await empSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  
  await page.locator('#employment select').first().selectOption('Full-time');
  const empInputs = await page.locator('#employment input[type="text"]').all();
  if (empInputs.length > 0) await empInputs[0].fill('Finance');
  if (empInputs.length > 1) await empInputs[1].fill('Financial Analyst');
  if (empInputs.length > 2) await empInputs[2].fill('ABC Corporation');
  
  const empNumberInputs = await page.locator('#employment input[type="number"]').all();
  if (empNumberInputs.length > 0) await empNumberInputs[0].fill('50000');
  if (empNumberInputs.length > 1) await empNumberInputs[1].fill('5');
  if (empNumberInputs.length > 2) await empNumberInputs[2].fill('10');
  
  // Tax checkbox
  const taxCheckbox = page.locator('#employment input[type="checkbox"]').first();
  if (await taxCheckbox.count() > 0) {
    await taxCheckbox.click();
  }
  
  // ===== EXPENSES =====
  console.log('Filling Expenses...');
  const expSection = page.locator('#expenses');
  await expSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  
  const expNumberInputs = await page.locator('#expenses input[type="number"]').all();
  for (let i = 0; i < Math.min(30, expNumberInputs.length); i++) {
    await expNumberInputs[i].fill((i + 1) * 500 + '');
  }
  
  // ===== ASSETS =====
  console.log('Adding Asset...');
  const assetsSection = page.locator('#assets');
  await assetsSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  
  const addAssetBtn = page.locator('#assets button:has-text("+ Add")');
  if (await addAssetBtn.count() > 0) {
    await addAssetBtn.first().click();
    await page.waitForTimeout(500);
    
    const assetInputs = await page.locator('#assets input[type="text"]').all();
    if (assetInputs.length > 0) await assetInputs[0].fill('Family Home');
    
    const assetNumberInputs = await page.locator('#assets input[type="number"]').all();
    if (assetNumberInputs.length > 0) await assetNumberInputs[0].fill('4500000');
  }
  
  // ===== LIABILITIES =====
  console.log('Adding Liability...');
  const liabSection = page.locator('#liabilities');
  await liabSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  
  const addLiabBtn = page.locator('#liabilities button:has-text("+ Add")');
  if (await addLiabBtn.count() > 0) {
    await addLiabBtn.first().click();
    await page.waitForTimeout(500);
    
    const liabInputs = await page.locator('#liabilities input[type="text"]').all();
    if (liabInputs.length > 0) await liabInputs[0].fill('Standard Bank');
    
    const liabNumberInputs = await page.locator('#liabilities input[type="number"]').all();
    if (liabNumberInputs.length > 0) await liabNumberInputs[0].fill('2500000');
    if (liabNumberInputs.length > 1) await liabNumberInputs[1].fill('25000');
  }
  
  // ===== RETIREMENT =====
  console.log('Filling Retirement...');
  const retSection = page.locator('#retirement');
  await retSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  
  const retNumberInputs = await page.locator('#retirement input[type="number"]').all();
  for (let i = 0; i < Math.min(5, retNumberInputs.length); i++) {
    await retNumberInputs[i].fill((i + 1) * 10000 + '');
  }
  
  // ===== GOALS =====
  console.log('Adding Goal...');
  const goalsSection = page.locator('#goals');
  await goalsSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  
  const addGoalBtn = page.locator('#goals button:has-text("+ Add")');
  if (await addGoalBtn.count() > 0) {
    await addGoalBtn.first().click();
    await page.waitForTimeout(500);
    
    const goalInputs = await page.locator('#goals input[type="text"]').all();
    if (goalInputs.length > 0) await goalInputs[0].fill("Child's University");
    
    const goalNumberInputs = await page.locator('#goals input[type="number"]').all();
    if (goalNumberInputs.length > 0) await goalNumberInputs[0].fill('500000');
  }
  
  // Emergency Fund
  console.log('Filling Emergency Fund...');
  const emergencyInputs = await page.locator('#goals input[type="number"]').all();
  const count = emergencyInputs.length;
  if (count > 2) {
    await emergencyInputs[count - 3].fill('6');
    await emergencyInputs[count - 2].fill('150000');
    await emergencyInputs[count - 1].fill('10000');
  }
  
  // Wait for autosave
  console.log('Waiting for autosave...');
  await page.waitForTimeout(3000);
  
  // ===== GENERATE PDF =====
  console.log('Generating PDF...');
  const generateBtn = page.locator('button:has-text("Generate PDF")');
  
  if (await generateBtn.count() > 0) {
    // Give more time for PDF generation
    const downloadPromise = page.waitForEvent('download', { timeout: 300000 });
    await generateBtn.first().click();
    
    try {
      const download = await downloadPromise;
      console.log('✓ PDF download started:', download.suggestedFilename());
      
      // Save PDF
      const savePath = 'C:\\Users\\Khan\\CascadeProjects\\shahil_fna\\COMPREHENSIVE_TEST_REPORT.pdf';
      await download.saveAs(savePath);
      console.log('✓ PDF saved to:', savePath);

      // Get file size (using dynamic import for fs)
      try {
        const fs = await import('fs');
        const stats = fs.statSync(savePath);
        const fileSizeMB = (stats.size / 1024 / 1024).toFixed(2);
        console.log('✓ PDF file size:', fileSizeMB, 'MB');
      } catch (e) {
        console.log('Could not get file size (expected in test environment)');
      }
      
      console.log('\n=== PDF Generation Complete ===');
      console.log('File: COMPREHENSIVE_TEST_REPORT.pdf');
      console.log('Location:', savePath);
      console.log('Size:', fileSizeMB, 'MB');
      console.log('===============================\n');
      
      expect(download.suggestedFilename()).toContain('.pdf');
    } catch (error) {
      console.error('✗ PDF generation failed:', error.message);
      throw error;
    }
  } else {
    console.error('✗ Generate PDF button not found');
    throw new Error('Generate PDF button not found');
  }
});
