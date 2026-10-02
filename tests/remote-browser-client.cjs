const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('/opt/agent-tools/node_modules/playwright');
(async () => {
  assert.equal(process.getuid(), 1000);
  assert.equal(fs.existsSync(chromium.executablePath()), false, 'browser must not be preinstalled');
  const { wsEndpoint } = await (await fetch('http://browser-peer:39232')).json();
  const browser = await chromium.connect(wsEndpoint, { timeout: 30000 });
  try {
    const page = await browser.newPage();
    await page.setContent('<title>Runtime remote browser</title><button>Ready</button>');
    assert.equal(await page.title(), 'Runtime remote browser');
    await page.getByRole('button', { name: 'Ready' }).click();
    assert.equal((await page.screenshot()).subarray(1, 4).toString(), 'PNG');
    console.log('PASS browser-free runtime controls real remote Chromium:', browser.version());
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
