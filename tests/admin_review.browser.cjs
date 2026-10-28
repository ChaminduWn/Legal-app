// Run against scripts.serve_admin_fixture, never against the real legal library.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn, spawnSync } = require('node:child_process');
const { chromium } = require('playwright');
(async () => {
  const output = path.resolve('tmp/admin-ui-results'); fs.mkdirSync(output, { recursive: true });
  const channel = process.env.NEETHI_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined);
  const browser = await chromium.launch({ ...(channel ? { channel } : {}), headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1050 } });
  const page = await context.newPage(); const errors = []; page.on('pageerror', e => errors.push(e.message));
  let fixture = null, fixtureLog = '';
  let base = process.env.NEETHI_UI_URL;
  if (!base) {
    const reservation = net.createServer();
    await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve));
    const port = reservation.address().port;
    await new Promise(resolve => reservation.close(resolve));
    base = 'http://127.0.0.1:' + port;
    const python = process.env.NEETHI_PYTHON || path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
    fixture = spawn(python, ['-m', 'scripts.serve_admin_fixture', '--port', String(port)], { cwd: path.resolve('.'), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    fixture.stdout.on('data', data => { fixtureLog += data.toString(); });
    fixture.stderr.on('data', data => { fixtureLog += data.toString(); });
  }
  const api = url => page.evaluate(async url => {
    const response = await fetch(url);
    if (!response.ok) throw new Error('Test API failed: ' + response.status + ' ' + url);
    return response.json();
  }, url);
  try {
    let healthy = false;
    for (let attempt = 0; attempt < 120; attempt++) {
      try { healthy = (await context.request.get(base + '/health', { timeout: 1000 })).ok(); } catch {}
      if (healthy) break;
      if (fixture?.exitCode != null) throw new Error('Fixture exited: ' + fixtureLog);
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    assert.ok(healthy, 'Fixture did not become ready: ' + fixtureLog);
    await page.goto(base + '/admin/login');
    await page.locator('input[name="username"]').fill('reviewer');
    await page.locator('input[name="password"]').fill('reviewer-test-password');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('**/admin/documents');
    await page.waitForFunction(() => document.querySelector('#document-count').textContent === '56 documents');
    assert.equal(await page.locator('#document-list .document-item').count(), 40);
    await page.locator('#next-page').click(); assert.ok(await page.locator('#document-list .document-item').count() < 40);
    await page.locator('#previous-page').click();
    await page.locator('#attention-filter').selectOption('extraction');
    assert.equal(await page.locator('#document-list .document-item').count(), 2);
    await page.locator('#clear-filters').click();
    await page.locator('#topic-filter').selectOption('criminal'); assert.equal(await page.locator('#document-list .document-item').count(), 1);
    await page.locator('#clear-filters').click();
    async function choose(name) {
      if (!await page.locator('#queue-details').getAttribute('open').then(x => x !== null)) await page.locator('#queue-details > summary').click();
      await page.locator('#document-filter').fill(name);
      await page.locator('#document-list .document-item').first().click();
      await page.waitForFunction(() => !state.loading && !!state.activeSection);
    }
    await choose('a.pdf');
    assert.equal(await page.locator('#editing-section').textContent(), 'Page 1');
    await page.locator('#section-text').fill('Unsaved local review text that must not disappear when navigation is cancelled.');
    page.once('dialog', d => d.dismiss());
    await page.locator('#document-filter').fill('b.pdf'); await page.locator('#document-list .document-item').first().click();
    assert.equal(await page.locator('#document-title').textContent(), 'Review example');
    assert.ok((await page.locator('#section-text').inputValue()).startsWith('Unsaved local'));
    await page.locator('#section-form button[value="save"]').click();
    await page.waitForFunction(() => !state.busy && !sectionDirty());
    assert.equal(await page.locator('#document-list .document-item').isEnabled(), true);
    await page.locator('#document-list .document-item').first().click();
    await page.waitForFunction(() => !state.loading && state.activeDocument.source_file === 'b.pdf');
    await page.locator('#section-form button[value="next"]').click();
    await page.waitForFunction(() => !state.busy && state.activeSection.source_id === 'b.pdf-2');
    const bSections = await api('/api/admin/documents/b.pdf/sections');
    assert.equal(bSections.find(s => s.source_id === 'b.pdf-1').status, 'unreviewed');
    const failUrl = '**/api/admin/sections/b.pdf-2';
    await page.route(failUrl, route => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Fixture save failed. Retry safely.' }) }));
    await page.locator('#section-text').fill('This edited passage must remain visible when the server fails to save it.');
    await page.locator('#section-form button[value="save"]').click();
    await page.waitForFunction(() => !state.busy && document.querySelector('#toast').textContent.includes('Fixture save failed'));
    assert.ok((await page.locator('#section-text').inputValue()).startsWith('This edited passage'));
    assert.equal(await page.locator('#save-state').textContent(), 'Unsaved changes');
    await page.unroute(failUrl);
    page.once('dialog', d => d.accept()); await choose('a.pdf');
    await page.locator('#verify-next').click();
    await page.waitForFunction(() => !state.busy && state.activeSection.source_id === 'a.pdf-2');
    const aSections = await api('/api/admin/documents/a.pdf/sections');
    assert.equal(aSections.find(s => s.source_id === 'a.pdf-1').status, 'verified');
    assert.notEqual(aSections.find(s => s.source_id === 'a.pdf-2').status, 'verified');
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, 'desktop-review.png'), fullPage: true });
    await page.locator('#verify-next').click();
    await page.waitForFunction(() => !state.busy && !state.activeSection);
    assert.equal(await page.locator('#review-complete').isVisible(), true);
    assert.equal(await page.locator('#approve-button').isDisabled(), true);
    assert.ok((await page.locator('#publication-blockers').textContent()).includes('edition'));
    await page.locator('#metadata-button').click();
    assert.ok((await page.locator('#metadata-policy').textContent()).includes('To publish'));
    await page.locator('#official-source-url').fill('https://example.gov/example.pdf');
    await page.locator('#publication-date').fill('2024-01-01'); await page.locator('#effective-date').fill('2024-02-01');
    await page.locator('#edition-details > summary').click(); await page.locator('#has-edition').check();
    await page.locator('#instrument-choice').selectOption('__new__'); await page.locator('#instrument-title').fill('Example Law');
    await page.locator('#edition-year').fill('2024'); await page.locator('#valid-from').fill('2024-02-01');
    await page.locator('#metadata-form button[type="submit"]').click();
    await page.waitForFunction(() => !state.busy && !document.querySelector('#metadata-dialog').open);
    const docsBefore = await api('/api/admin/documents');
    const docA = docsBefore.find(d => d.source_file === 'a.pdf');
    assert.equal(docA.instrument_title, 'Example Law'); assert.equal(docA.status, 'in_review');
    assert.equal(await page.locator('#approve-button').isDisabled(), true);
    await page.locator('#extraction-complete').check(); await page.locator('#authority-checked').check();
    assert.equal(await page.locator('#approve-button').isEnabled(), true);
    await page.locator('#approve-button').click();
    await page.waitForFunction(() => !state.busy && state.activeDocument.status === 'verified');
    assert.equal(await page.locator('#count-published').textContent(), '2');
    await choose('b.pdf'); await page.locator('#metadata-button').click();
    assert.equal(await page.locator('#instrument-choice option:checked').textContent(), 'Another law');
    await page.locator('#document-notes').fill('Unsaved metadata note.'); page.once('dialog', d => d.dismiss());
    await page.locator('#metadata-dialog [data-close]').first().click(); assert.equal(await page.locator('#metadata-dialog').isVisible(), true);
    page.once('dialog', d => d.accept()); await page.locator('#metadata-dialog [data-close]').first().click();
    await page.setViewportSize({ width: 390, height: 844 });
    await choose('b.pdf');
    assert.equal(await page.locator('.pdf-panel').isVisible(), true);
    assert.ok((await page.locator('#open-original').getAttribute('href')).includes('/pdf#page='));
    assert.equal(await page.locator('#open-original').getAttribute('target'), '_blank');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, 'mobile-review.png'), fullPage: true });
    await page.locator('#metadata-button').click();
    assert.ok(await page.locator('#metadata-dialog').evaluate(e => e.scrollTop < 5));
    await page.screenshot({ path: path.join(output, 'mobile-source-details.png'), fullPage: false });
    assert.ok(await page.evaluate(() => document.querySelector('#metadata-dialog').getBoundingClientRect().right <= innerWidth));
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ passed: true, checks: ['pagination', 'status and topic filters', 'unsaved navigation guard', 'save-and-next without implicit verification', 'failed save preserves edits', 'single-passage verification', 'production checklist', 'friendly law selection', 'explicit publication', 'metadata discard guard', 'mobile PDF access', 'no horizontal overflow', 'no JavaScript errors'] }, null, 2));
    process.stdout.write('Admin browser checks passed. Screenshots: ' + output + '\n');
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(output, 'failure.txt'), error.stack + '\nURL: ' + page.url() + '\n' + fixtureLog);
    throw error;
  } finally {
    await browser.close();
    if (fixture) {
      if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(fixture.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      else fixture.kill('SIGTERM');
    }
  }
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
