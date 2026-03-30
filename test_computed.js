const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('http://localhost:5550');
  
  await page.waitForTimeout(2000);
  
  const statsButton = await page.$('text=STATS');
  const computedStyle = await page.evaluate(el => {
    const style = window.getComputedStyle(el);
    return {
      paddingTop: style.paddingTop,
      paddingBottom: style.paddingBottom,
      paddingLeft: style.paddingLeft,
      paddingRight: style.paddingRight,
      height: style.height,
      boxSizing: style.boxSizing
    };
  }, statsButton);
  
  console.log('STATS button computed style:', computedStyle);
  
  await browser.close();
})();
