const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('file://' + process.cwd() + '/test_layer.html');
  const padding = await page.evaluate(() => window.getComputedStyle(document.getElementById('test')).padding);
  console.log('Padding:', padding);
  await browser.close();
})();
