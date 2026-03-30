const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('http://localhost:5550');
  
  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'current_main.png' });
  
  await page.click('text=SCRAPERS');
  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'current_settings.png' });
  
  await browser.close();
})();
