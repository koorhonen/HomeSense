// Development test: point NODE_PATH to the Playwright installation.
// The voice test uses a browser fake microphone and simulated AI responses.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.BROWSER_CHANNEL || undefined, args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream']});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1200}, permissions: ['microphone']});
    await context.addInitScript(() => {
      window.testSpoken = [];
      Object.defineProperty(window, 'speechSynthesis', {value: {
        getVoices: () => [], cancel: () => {},
        speak: utterance => { window.testSpoken.push(utterance.text); utterance.onend?.(); }
      }});
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto('http://127.0.0.1:8000');
    await page.getByText('Simulation mode', {exact: true}).waitFor();
    assert.equal(await page.locator('#speech-label').textContent(), 'Gemma 4 (Google API)');
    assert.equal(await page.locator('[role="switch"]').count(), 0);
    assert.ok((await page.locator('#privacy-note').textContent()).includes('including in simulation mode'));
    const realStatus = await (await page.request.get('http://127.0.0.1:8000/api/status')).json();
    if (!realStatus.speech.ready) assert.equal(await page.locator('#record').isDisabled(), true);
    // Test voice readiness without real credentials or cloud requests.
    await page.route('**/api/status', async route => {
      const response = await route.fetch();
      const status = await response.json(); status.speech.ready = true;
      await route.fulfill({json: status});
    });
    await page.route('**/api/text', async route => {
      const {text} = route.request().postDataJSON();
      const rejected = text.startsWith('Älä') || text.includes('<');
      const clarify = text === 'Laita valo päälle';
      await route.fulfill({json: {transcript: text, status: clarify ? 'clarification' : rejected ? 'rejected' : 'simulated',
        entity_id: 'light.keittio', state: 'on', interpretation_seconds: 0.1,
        message: clarify ? 'Which room do you mean? Repeat the complete command.' : rejected ? 'Command not executed.' : 'Simulated state updated.',
        spoken_feedback: clarify ? 'Which room do you mean?' : rejected ? 'Command not executed.' : 'Simulation: Kitchen light on.'}});
    });
    await page.reload();
    await page.getByText('Simulation mode', {exact: true}).waitFor();
    await page.locator('#command').fill('Sytytä keittiön valo');
    await page.locator('#send').click();
    await page.getByText('Simulation complete', {exact: true}).first().waitFor();
    assert.equal(await page.locator('.device.on').count(), 1);
    assert.equal(await page.locator('#transcript').textContent(), 'Sytytä keittiön valo');
    assert.ok(await page.evaluate(() => window.testSpoken.some(text => text.includes('Simulation'))));
    const output = path.resolve(__dirname, '..', 'docs');
    fs.mkdirSync(output, {recursive: true});
    await page.screenshot({path: path.join(output, 'preview.png'), fullPage: true});
    await page.locator('#command').fill('Älä sytytä keittiön valo');
    await page.locator('#send').click();
    await page.locator('#feedback-title').filter({hasText: 'Command not executed'}).waitFor();
    assert.equal(await page.locator('.device.on').count(), 1);
    await page.locator('#command').fill('<img src=x onerror=alert(1)>');
    await page.locator('#send').click();
    await page.waitForFunction(() => document.querySelector('#transcript').textContent.includes('<img'));
    assert.equal(await page.locator('#transcript img').count(), 0);
    let recordedBytes = 0;
    await page.route('**/api/voice', async route => {
      const data = route.request().postDataBuffer();
      assert.equal(data.toString('ascii', 0, 4), 'RIFF');
      assert.equal(data.readUInt32LE(24), 16000);
      assert.equal(data.readUInt16LE(22), 1);
      assert.equal(data.readUInt16LE(34), 16);
      recordedBytes = data.length;
      await route.fulfill({json: {transcript: 'Sammuta keittiön valo', entity_id: 'light.keittio', state: 'off', status: 'simulated', message: 'Test simulation', interpretation_seconds: 0.1, spoken_feedback: 'Simulation: Kitchen light off.'}});
    });
    await page.locator('#record').click();
    await page.locator('#record[aria-pressed="true"]').waitFor();
    await page.waitForTimeout(700);
    await page.locator('#record').click();
    await page.waitForFunction(() => document.querySelector('#transcript').textContent === 'Sammuta keittiön valo');
    assert.ok(recordedBytes > 6400 && recordedBytes <= 260096);
    assert.equal(await page.locator('.device.on').count(), 0);
    assert.equal(await page.locator('#record').getAttribute('aria-pressed'), 'false');
    await page.locator('#command').fill('Laita valo päälle');
    await page.locator('#send').click();
    await page.getByText('Clarify the command', {exact: true}).first().waitFor();
    assert.ok(await page.evaluate(() => window.testSpoken.includes('Which room do you mean?')));
    await page.locator('#spoken-feedback').uncheck();
    const before = await page.evaluate(() => window.testSpoken.length);
    await page.locator('#command').fill('Sytytä keittiön valo');
    await page.locator('#send').click();
    await page.getByText('Simulation complete', {exact: true}).first().waitFor();
    assert.equal(await page.evaluate(() => window.testSpoken.length), before);
    await page.locator('#clear-history').click();
    await page.locator('#command').fill('Sammuta keittiön valo');
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({path: path.join(output, 'preview-mobile.png'), fullPage: true});
    assert.deepEqual(errors, []);
    console.log('UI OK: text, device state, negation, XSS, fake microphone/WAV, mobile layout, and cloud labels. AI and speech responses are simulated.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
