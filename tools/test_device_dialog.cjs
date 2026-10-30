/* Isolated browser regression. HA supplies native UI; device traffic is mocked. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const credentials = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const output = path.resolve(process.env.MOSWITCH_UI_OUTPUT || path.join(root, 'dist/0.1.37/verification'));
fs.mkdirSync(output, { recursive: true });

(async () => {
  const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({ base, token }) => localStorage.setItem('hassTokens', JSON.stringify({
      hassUrl: base, access_token: token, token_type: 'Bearer', expires_in: 1800,
      expires: Date.now() + 1800000, refresh_token: 'isolated-ui-test',
    })), { base: credentials.url, token: credentials.token });
    for (const filename of ['device-settings.js', 'device-dialog.js']) {
      await page.route('**/moswitch_static/' + filename + '*', route => route.fulfill({
        contentType: 'text/javascript', body: fs.readFileSync(path.join(root, 'custom_components/moswitch', filename), 'utf8'),
      }));
    }
    await page.goto(credentials.url + '/config/devices/dashboard', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelector('home-assistant')?.hass?.devices);
    const target = await page.evaluate(() => {
      const device = Object.values(document.querySelector('home-assistant').hass.devices).find(d => d.manufacturer === 'MoSwitch' && d.configuration_url);
      if (!device) throw Error('No configured MoSwitch device available');
      const url = new URL(device.configuration_url.replace('homeassistant://', location.origin + '/'));
      const entryId = url.searchParams.get('moswitch_settings') || url.pathname.split('/').pop();
      return { deviceId: device.id, entryId };
    });
    await page.goto(credentials.url + '/config/devices/device/' + target.deviceId, { waitUntil: 'domcontentloaded' });
    await page.locator('ha-device-info-card').waitFor();
    await page.evaluate(({ deviceId, entryId }) => {
      const base = document.querySelector('home-assistant');
      const real = base.hass;
      const data = {
        entry_id: entryId, device_id: deviceId, name: 'MoSwitch · Testraum', area_id: 'testraum',
        areas: [{ value: 'testraum', label: 'Testraum' }, { value: 'wohnen', label: 'Wohnzimmer' }],
        lights: [{ name: 'Testdimmer', entity_id: 'light.testdimmer', direct: true }],
        options: { double_action: 'media', players: [], actions: [], scene_order: [] },
        schema: [{ name: 'double_action', selector: { select: {} } },
          { name: 'players', selector: { entity: { include_entities: [] } } },
          { name: 'actions', selector: { action: {} } },
          { name: 'scene_order', selector: { entity: { include_entities: [] } } }],
        touch: { state: 3, calibrated: true, test_mode: false, baseline_us: 18, press_delta_us: 5,
          release_delta_us: 2, parameters_writable: true, tuning_writable: true },
        fresh: true, quiet: false, health: 'Bereit', message: '',
        parameters: [{ key: 'baseline_us', label: 'Baseline einstellen', min: 1, max: 1000, step: 1, unit: 'µs' }],
      };
      const subscribers = new Set();
      window.dialogCalls = [];
      window.dialogReport = report => { Object.assign(data.touch, report); for (const cb of subscribers) cb(structuredClone(data)); };
      const mock = { ...real, connection: { ...real.connection,
        subscribeMessage: async (cb, msg) => {
          if (msg.entry_id !== entryId) throw Error('Wrong subscription scope');
          subscribers.add(cb); cb(structuredClone(data));
          return () => { subscribers.delete(cb); window.dialogCalls.push({ closed: true }); };
        },
      }, callWS: async msg => {
        if (!msg.type.startsWith('moswitch/')) throw Error('Unexpected backend call');
        window.dialogCalls.push(msg);
        if (msg.entry_id !== entryId) throw Error('Wrong action scope');
        if (msg.action === 'prepare') { data.touch.state = 9; data.touch.test_mode = true; data.quiet = true; }
        if (msg.action === 'baseline') data.touch.state = 2;
        if (msg.action === 'activate') { data.touch.state = 3; data.touch.test_mode = false; data.quiet = false; }
        if (msg.action === 'cancel') data.touch.state = 8;
        if (msg.type.endsWith('/save')) { data.options = msg.options; data.area_id = msg.area_id; }
        for (const cb of subscribers) cb(structuredClone(data));
        return structuredClone(data);
      } };
      // Only our test dialog is supplied with mocked state and commands.
      const provide = base.provideHass.bind(base);
      base.provideHass = element => element.localName === 'moswitch-device-dialog' ? (element.hass = mock) : provide(element);
      const call = real.callWS.bind(real);
      real.callWS = msg => msg.type === 'moswitch/device/info'
        ? Promise.resolve({ entry_id: msg.entry_id, device_id: window.wrongDialogDevice ? 'wrong' : deviceId, name: data.name })
        : call(msg);
    }, target);
    await page.addScriptTag({ type: 'module', url: credentials.url + '/moswitch_static/device-dialog.js?v=0.1.37' });
    await page.waitForFunction(() => !!customElements.get('moswitch-device-dialog'));
    const href = '/config/devices/device/' + target.deviceId + '?moswitch_settings=' + target.entryId;
    const link = page.locator('ha-device-info-card a[href*="moswitch"]').first();
    await link.evaluate((element, value) => element.setAttribute('href', value), href);
    const before = page.url();
    await link.click();
    const dialog = page.locator('moswitch-device-dialog');
    await dialog.getByRole('button', { name: 'Touchfläche einlernen', exact: true }).waitFor();
    assert.equal(page.url(), before);
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(output, 'dialog-desktop.png') });
    await dialog.getByRole('tab', { name: 'Zuordnung & Aktionen' }).click();
    await dialog.locator('#double').selectOption('none');
    await dialog.getByRole('button', { name: 'Zuordnung speichern' }).click();
    await dialog.getByText('Raum und Aktionen gespeichert.', { exact: true }).waitFor();
    await dialog.getByRole('tab', { name: 'Touchfläche', exact: true }).click();
    await dialog.getByRole('button', { name: 'Touchfläche einlernen', exact: true }).click();
    await dialog.getByRole('heading', { name: 'Fläche vollständig loslassen' }).waitFor();
    assert(await dialog.getByRole('tab', { name: 'Zuordnung & Aktionen' }).isDisabled());
    await dialog.getByRole('button', { name: 'Fläche ist frei · Ruhemessung starten' }).click();
    await page.evaluate(() => dialogReport({ state: 7, learning_taps: 3, feedback: 4 }));
    await dialog.getByRole('heading', { name: 'Zehnmal kurz tippen' }).waitFor();
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(output, 'dialog-learning.png') });
    for (const [state, heading] of [[11, 'Jetzt berühren und halten'], [12, 'Weiter halten'], [13, 'Jetzt loslassen'], [3, 'Touchfläche erfolgreich eingelernt']]) {
      await page.evaluate(state => dialogReport({ state, learning_taps: 10, hold_remaining_ms: 1200 }), state);
      await dialog.getByRole('heading', { name: heading, exact: true }).waitFor();
    }
    await dialog.getByRole('button', { name: 'Fertig', exact:true }).click();
    await dialog.locator('moswitch-device-settings').waitFor({ state: 'detached' });
    assert.equal((await page.evaluate(()=>dialogCalls)).filter(call=>call.action==='activate').length,1);
    await link.click();
    await dialog.getByRole('button', { name: 'Touchfläche einlernen', exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(400);
    await dialog.getByRole('button',{name:'Touchfläche einlernen',exact:true}).waitFor();
    await page.screenshot({ path: path.join(output, 'dialog-mobile.png') });
    assert(!(await dialog.evaluate(el => el.shadowRoot.querySelector('.body').scrollWidth > el.shadowRoot.querySelector('.body').clientWidth)));
    await page.keyboard.press('Escape');
    await dialog.locator('moswitch-device-settings').waitFor({ state: 'detached' });
    assert.equal(page.url(), before);
    // A direct device link opens the same dialog and removes only its own query.
    await page.evaluate(href => {history.replaceState(history.state,'',href);window.dispatchEvent(new CustomEvent('location-changed'));}, href);
    await dialog.getByRole('button', {name:'Touchfläche einlernen',exact:true}).waitFor();
    assert.equal(page.url(),before);
    await page.keyboard.press('Escape');
    await dialog.locator('moswitch-device-settings').waitFor({state:'detached'});
    assert.equal((await page.evaluate(()=>dialogCalls)).filter(call=>call.action==='activate').length,1);
    await link.click();
    await dialog.getByRole('button', {name:'Touchfläche einlernen',exact:true}).waitFor();
    await page.evaluate(()=>{
      const root=document.querySelector('home-assistant');
      for(const [key,value] of Object.entries({'--primary-background-color':'#111','--card-background-color':'#1c1c1c','--ha-card-background':'#1c1c1c','--ha-color-surface-default':'#1c1c1c','--primary-text-color':'#eee','--secondary-text-color':'#aaa','--secondary-background-color':'#282828','--divider-color':'#3c3c3c','--ha-dialog-surface-background':'#1c1c1c'}))root.style.setProperty(key,value);
    });
    await page.waitForTimeout(400);
    await page.screenshot({path:path.join(output,'dialog-dark.png')});
    await page.keyboard.press('Escape');
    await dialog.locator('moswitch-device-settings').waitFor({state:'detached'});
    // A descriptor for another device must never open an assistant.
    await page.evaluate(() => { window.wrongDialogDevice = true; });
    await link.click();
    await page.waitForTimeout(300);
    assert.equal(await dialog.locator('moswitch-device-settings').count(), 0);
    assert.deepEqual(errors, []);
    console.log('Device dialog: launch, unchanged URL, scoped actions, assignment, all learning phases, finish, Escape, mobile and wrong-device guard passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exit(1); });
