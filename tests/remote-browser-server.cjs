const http = require('node:http');
const { chromium } = require('/opt/agent-tools/node_modules/playwright');
(async () => {
  const browser = await chromium.launchServer({ host: '0.0.0.0', port: 39231, headless: true, chromiumSandbox: false });
  const endpoint = new URL(browser.wsEndpoint());
  endpoint.hostname = 'browser-peer';
  const health = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ wsEndpoint: endpoint.href }));
  }).listen(39232, '0.0.0.0');
  process.on('SIGTERM', async () => { health.close(); await browser.close(); process.exit(0); });
})().catch(error => { console.error(error); process.exit(1); });
