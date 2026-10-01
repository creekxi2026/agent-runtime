// Real bundled browser launches with network disabled and an empty HOME mount.
const fs = require('node:fs');
const path = require('node:path');
const nodeBin = fs.realpathSync('/opt/agent-upstream/.local/node-active');
const pw = require(path.resolve(nodeBin, '../lib/node_modules/@playwright/test'));
(async () => {
  for (const name of ['chromium', 'firefox', 'webkit']) {
    const browser = await pw[name].launch({ headless: true, timeout: 30000 });
    try {
      const page = await browser.newPage();
      if (await page.evaluate(() => document.location.href) !== 'about:blank') throw new Error('Unexpected page');
      console.log('PASS relocated browser:', name, browser.version());
    } finally { await browser.close(); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
