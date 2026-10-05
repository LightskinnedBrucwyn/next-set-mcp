const test = require('node:test');
const assert = require('node:assert/strict');
const { createController, workoutId, parseToken, installPassive, createTransport } = require('../adapters/biolayne/tampermonkey/biolayne-sync.user.js');
const URL_WORKOUT = 'https://app-wobuilder-prod-001.azurewebsites.net/api/workout-exercises?workoutId=100';
const flush = async () => { for (let n = 0; n < 20; n++) await Promise.resolve(); };
function fixture(overrides = {}) {
    let now = 1800000000000, counter = 0;
    const timers = new Map(), calls = [], logs = [];
    const payload = { token: 'fake-secret-token', expires: 1800003600 };
    const io = { base: 'https://biolayne.com/workout/', now: () => now,
        setTimeout: (fn, delay) => { timers.set(++counter, { at: now + delay, fn }); return counter; },
        clearTimeout: id => timers.delete(id), claimBootstrap: () => true,
        fetchToken: async () => { calls.push('fetch-token'); return { ok: true, json: async () => payload }; },
        send: async (path, body) => calls.push([path, body]), log: message => logs.push(message), ...overrides };
    const controller = createController(io);
    async function advance(ms) {
        now += ms;
        for (let n = 0; n < 20; n++) {
            const due = [...timers].filter(([, timer]) => timer.at <= now);
            if (!due.length) break;
            for (const [id, timer] of due) { timers.delete(id); timer.fn(); }
            await flush();
        }
        await flush();
    }
    return { controller, io, calls, logs, payload, advance };
}

test('workout detection accepts only validated upstream route and positive IDs', () => {
    assert.equal(workoutId(URL_WORKOUT, 'https://biolayne.com'), '100');
    for (const url of [URL_WORKOUT.replace('100', '0'), URL_WORKOUT+'&workoutId=2',
        URL_WORKOUT.replace('100', '9223372036854775808'), URL_WORKOUT.replace('app-wobuilder-prod-001.azurewebsites.net', 'evil.example'),
        'https://biolayne.com/unrelated']) assert.equal(workoutId(url, 'https://biolayne.com'), null);
});
test('token parsing rejects missing expiry, expired tokens and bad structure', () => {
    assert.equal(parseToken({ token: 'secret' }, 1800000000000), null);
    assert.equal(parseToken({ token: 'secret', expires: 1 }, 1800000000000), null);
    assert.deepEqual(parseToken({ data: { token: 'secret', expires: 1800003600 } }, 1800000000000), { token: 'secret', expires: 1800003600 });
});
test('proactive authenticated fetch forwards token exactly once within cooldown', async () => {
    const f = fixture();
    await Promise.all([f.controller.bootstrap(), f.controller.bootstrap()]);
    await f.controller.bootstrap();
    assert.equal(f.calls.filter(c => c === 'fetch-token').length, 1);
    assert.equal(f.calls.filter(c => Array.isArray(c) && c[0] === '/token').length, 1);
});
test('unauthenticated bootstrap fails quietly without token forwarding or loops', async () => {
    let count = 0;
    const f = fixture({ fetchToken: async () => { count++; return { ok: false }; } });
    for (let i = 0; i < 10; i++) await f.controller.bootstrap();
    assert.equal(count, 1);
    assert.equal(f.calls.length, 0);
    assert.equal(f.logs.length, 0);
});
test('reload cooldown prevents token fetch', async () => {
    const f = fixture({ claimBootstrap: () => false });
    await f.controller.bootstrap();
    assert.equal(f.calls.length, 0);
});
test('activity debounce uses most recent observed request', async () => {
    const f = fixture();
    await f.controller.bootstrap();
    f.controller.observe(URL_WORKOUT);
    await f.advance(15000);
    f.controller.observe(URL_WORKOUT);
    await f.advance(15000);
    assert.equal(f.calls.filter(c => c[0]?.startsWith('/sync/')).length, 0);
    await f.advance(5000);
    assert.equal(f.calls.filter(c => c[0]?.startsWith('/sync/')).length, 1);
});
test('overlapping activity queues one follow-up and respects cooldown', async () => {
    let complete, active = 0, maximum = 0, syncs = 0;
    const f = fixture({ send: async path => {
        if (path === '/token') return;
        syncs++; active++; maximum = Math.max(maximum, active);
        if (syncs === 1) await new Promise(resolve => { complete = resolve; });
        active--;
    } });
    await f.controller.bootstrap();
    f.controller.observe(URL_WORKOUT);
    await f.advance(20000);
    for (let i=0; i<10; i++) f.controller.observe(URL_WORKOUT);
    await f.advance(20000);
    assert.equal(syncs, 1);
    complete(); await flush();
    await f.advance(40000);
    assert.equal(syncs, 2);
    assert.equal(maximum, 1);
    await f.advance(120000);
    assert.equal(syncs, 2);
});
test('failures log fixed sanitized messages and never retry continuously', async () => {
    const f = fixture({ send: async () => { throw new Error('fake-secret-token fake-sync-key'); } });
    await f.controller.bootstrap();
    f.controller.observe(URL_WORKOUT);
    await f.advance(20000);
    await f.advance(120000);
    assert.ok(f.logs.length > 0);
    assert.ok(!JSON.stringify(f.logs).includes('fake-secret'));
    assert.ok(!JSON.stringify(f.logs).includes('fake-sync-key'));
});
test('unrelated requests never schedule a sync', async () => {
    const f = fixture();
    f.controller.observe('https://biolayne.com/assets/image.jpg');
    await f.advance(120000);
    assert.equal(f.calls.length, 0);
});
test('passive fetch token interception and workout observation preserve original response', async () => {
    const seen = [], tokens = [], nonces = [];
    const body = { token: 'fake-secret-token', expires: 1800003600 };
    const response = { ok: true, clone: () => ({ json: async () => body }) };
    const page = { location: { href: 'https://biolayne.com/workout/' }, fetch: async () => response };
    installPassive(page, { observe: url => seen.push(url), forward: async value => tokens.push(value) }, h => nonces.push(h));
    assert.equal(await page.fetch('/wp-json/biolayne-app/v1/get-token', { headers: { 'X-WP-Nonce': 'fake-nonce' } }), response);
    await flush();
    assert.deepEqual(tokens, [body]);
    assert.equal(nonces.length, 1);
    await page.fetch(URL_WORKOUT);
    assert.ok(seen.includes(URL_WORKOUT));
});
test('passive XHR interception handles JSON and remembers nonce without exposing it', async () => {
    const tokens = [], seen = [], nonces = [];
    class XHR {
        open() {} setRequestHeader() {} send() {}
        addEventListener(name, callback) { this.callback = callback; }
    }
    const page = { location: { href: 'https://biolayne.com/' }, fetch: async () => {}, XMLHttpRequest: XHR };
    installPassive(page, { observe: url => seen.push(url), forward: async body => tokens.push(body) }, h => nonces.push(h));
    const xhr = new XHR();
    xhr.open('GET', '/wp-json/biolayne-app/v1/get-token');
    xhr.setRequestHeader('X-WP-Nonce', 'fake-nonce');
    xhr.send(); xhr.status = 200; xhr.responseType = 'json'; xhr.response = { token: 'fake-token', expires: 1800003600 };
    xhr.callback(); await flush();
    assert.equal(tokens.length, 1); assert.equal(nonces.length, 1);
    assert.equal(seen.length, 1);
});

test('transport omits cookies, rejects redirects and uses a separate timeout watchdog', async () => {
    let options, alarm, aborted = false;
    const transport = createTransport('https://example.ts.net', 'fake-sync-key', opts => {
        options = opts; return { abort: () => { aborted = true; } };
    }, callback => { alarm = callback; return 1; }, () => {});
    const pending = transport('/token', { token: 'fake-token', expires: 1800003600 });
    const rejected = assert.rejects(pending, /^Error: Next_Set request failed$/);
    assert.equal(options.anonymous, true);
    assert.equal(options.redirect, 'error');
    assert.equal(options.headers['X-Next-Set-Key'], 'fake-sync-key');
    alarm(); await rejected;
    assert.equal(aborted, true);
});

test('transport success and invalid redirect responses settle without leaking body', async () => {
    const make = finalUrl => createTransport('https://example.ts.net', 'fake-key', opts => {
        opts.onload({ status: 200, finalUrl, responseText: 'fake-secret' });
        return { abort() {} };
    });
    await make('https://example.ts.net/token')('/token');
    await assert.rejects(make('https://evil.example/token')('/token'), /^Error: Next_Set request failed$/);
});
