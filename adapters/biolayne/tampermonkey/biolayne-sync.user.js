// ==UserScript==
// @name         BioLayne Next_Set Sync (maintained)
// @namespace    next-set-biolayne-sync
// @version      2.0.0
// @description  Authenticated token bootstrap and debounced private workout sync
// @match        https://biolayne.com/*
// @match        https://www.biolayne.com/*
// @run-at       document-start
// @noframes
// @grant        unsafeWindow
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @connect      *.ts.net
// ==/UserScript==

(function () {
    'use strict';
    const API_ORIGIN = 'https://app-wobuilder-prod-001.azurewebsites.net';
    const TOKEN_PATH = '/wp-json/biolayne-app/v1/get-token';
    const DEBOUNCE_MS = 20000;
    const COOLDOWN_MS = 60000;

    function urlOf(input, base) {
        try { return new URL(typeof input === 'string' ? input : input.url, base); }
        catch { return null; }
    }
    function workoutId(input, base) {
        const url = urlOf(input, base);
        if (!url || url.origin !== API_ORIGIN || url.pathname !== '/api/workout-exercises') return null;
        const ids = url.searchParams.getAll('workoutId');
        if (ids.length !== 1 || !/^[1-9]\d*$/.test(ids[0])) return null;
        try { return BigInt(ids[0]) <= 9223372036854775807n ? ids[0] : null; }
        catch { return null; }
    }
    function tokenURL(input, base) {
        const url = urlOf(input, base);
        return Boolean(url && url.origin === new URL(base).origin && url.pathname === TOKEN_PATH);
    }
    function parseToken(body, now = Date.now(), decode = globalThis.atob) {
        const data = body && typeof body.data === 'object' ? body.data : body;
        if (!data || typeof data !== 'object') return null;
        const token = data.token ?? data.access_token;
        if (typeof token !== 'string' || !token || /\s/.test(token)) return null;
        let expires = data.expires;
        if (expires === undefined) {
            try {
                let segment = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
                segment += '='.repeat((4 - segment.length % 4) % 4);
                expires = JSON.parse(decode(segment)).exp;
            } catch { return null; }
        }
        if (typeof expires === 'string' && /^\d+$/.test(expires)) expires = Number(expires);
        if (!Number.isSafeInteger(expires) || expires <= Math.floor(now / 1000) + 30) return null;
        return { token, expires };
    }

    function createController(io) {
        const now = io.now || Date.now;
        const later = io.setTimeout || setTimeout;
        const cancel = io.clearTimeout || clearTimeout;
        let bootstrapFlight = null, forwardFlight = null, syncBusy = false;
        let lastBootstrap = -Infinity, lastForward = -Infinity, forwardedUntil = 0;
        const jobs = new Map();
        function log(message) { io.log(message); } // Only fixed strings reach this method.
        async function forward(body) {
            const payload = parseToken(body, now());
            if (!payload) return false;
            if (forwardFlight) {
                return forwardFlight;
            }
            if (now() - lastForward < COOLDOWN_MS) return forwardedUntil > now() + 30000;
            lastForward = now();
            forwardFlight = (async () => {
                try {
                    await io.send('/token', payload);
                    forwardedUntil = payload.expires * 1000;
                    return true;
                } catch { log('Next_Set token forwarding failed.'); return false; }
            })();
            try { return await forwardFlight; }
            finally { forwardFlight = null; }
        }
        async function bootstrap() {
            if (bootstrapFlight) return bootstrapFlight;
            if (now() - lastBootstrap < COOLDOWN_MS || !io.claimBootstrap(now())) return false;
            lastBootstrap = now();
            bootstrapFlight = (async () => {
                try {
                    const response = await io.fetchToken();
                    if (!response.ok) return false; // Logged-out/nonce failures stay quiet.
                    return await forward(await response.json());
                } catch { return false; }
            })();
            try { return await bootstrapFlight; }
            finally { bootstrapFlight = null; }
        }
        function schedule(id, job) {
            if (job.timer !== null) cancel(job.timer);
            const delay = Math.max(0, job.activity + DEBOUNCE_MS - now(), job.lastStart + COOLDOWN_MS - now());
            job.timer = later(() => { job.timer = null; void run(id, job); }, delay);
        }
        async function run(id, job) {
            if (syncBusy) { job.waiting = true; return; }
            syncBusy = true;
            job.waiting = false;
            job.dirty = false;
            job.lastStart = now();
            try {
                if (forwardedUntil <= now() + 30000) await bootstrap();
                if (forwardFlight) await forwardFlight;
                if (forwardedUntil <= now() + 30000) {
                    log('Next_Set sync skipped: token unavailable.');
                    return;
                }
                await io.send('/sync/workout/' + id);
                log('Next_Set workout sync completed.');
            } catch { log('Next_Set workout sync failed; check connection or token status.'); }
            finally {
                syncBusy = false;
                for (const [nextId, next] of jobs) {
                    if ((next.dirty || next.waiting) && next.timer === null) schedule(nextId, next);
                }
            }
        }
        function observe(input) {
            const id = workoutId(input, io.base);
            if (!id) return;
            let job = jobs.get(id);
            if (!job) {
                if (jobs.size >= 20) return; // Bound per-page memory and request queue.
                job = { timer: null, lastStart: -Infinity, activity: now(), dirty: false, waiting: false };
                jobs.set(id, job);
            }
            job.activity = now();
            job.dirty = true;
            schedule(id, job);
        }
        return { bootstrap, forward, observe };
    }

    function installPassive(page, controller, rememberNonce) {
        const nativeFetch = page.fetch;
        page.fetch = async function (input, options) {
            if (tokenURL(input, page.location.href)) rememberNonce(options?.headers || input?.headers);
            const response = await nativeFetch.apply(this, arguments);
            try {
                if (response.ok) {
                    controller.observe(input);
                    if (tokenURL(input, page.location.href)) {
                        void response.clone().json().then(body => controller.forward(body)).catch(() => {});
                    }
                }
            } catch { /* Observers must never break page requests. */ }
            return response;
        };
        const proto = page.XMLHttpRequest?.prototype;
        if (proto) {
            const open = proto.open, send = proto.send, setHeader = proto.setRequestHeader;
            const urls = new WeakMap();
            proto.open = function (method, url) { urls.set(this, url); return open.apply(this, arguments); };
            proto.setRequestHeader = function (name, value) {
                if (tokenURL(urls.get(this), page.location.href) && name.toLowerCase() === 'x-wp-nonce') {
                    rememberNonce({ 'X-WP-Nonce': value });
                }
                return setHeader.apply(this, arguments);
            };
            proto.send = function () {
                this.addEventListener('load', () => {
                    try {
                        if (this.status < 200 || this.status >= 300) return;
                        const url = urls.get(this);
                        controller.observe(url);
                        if (tokenURL(url, page.location.href)) {
                            const body = this.responseType === 'json' ? this.response : JSON.parse(this.responseText);
                            void controller.forward(body);
                        }
                    } catch { /* No response contents are logged. */ }
                }, { once: true });
                return send.apply(this, arguments);
            };
        }
        return nativeFetch;
    }

    function createTransport(origin, key, request, later = setTimeout, cancel = clearTimeout) {
        return (path, payload) => new Promise((resolve, reject) => {
            let settled = false, handle;
            function finish(ok) {
                if (settled) return;
                settled = true;
                cancel(watchdog);
                if (ok) resolve(); else reject(new Error('Next_Set request failed'));
            }
            // Tampermonkey's fetch mode ignores details.timeout in Chrome.
            const watchdog = later(() => {
                finish(false);
                try { handle?.abort(); } catch { /* Do not log exceptions. */ }
            }, 65000);
            try {
                handle = request({ method: 'POST', url: origin + path, anonymous: true,
                    redirect: 'error', timeout: 65000,
                    headers: { 'X-Next-Set-Key': key, 'Content-Type': 'application/json' },
                    data: payload === undefined ? undefined : JSON.stringify(payload),
                    onload: response => {
                        try {
                            finish(response.status >= 200 && response.status < 300 &&
                                (!response.finalUrl || new URL(response.finalUrl).origin === origin));
                        } catch { finish(false); }
                    },
                    onerror: () => finish(false), ontimeout: () => finish(false), onabort: () => finish(false),
                });
            } catch { finish(false); }
        });
    }
    const helpers = { workoutId, parseToken, createController, installPassive, createTransport, DEBOUNCE_MS, COOLDOWN_MS };
    if (typeof module === 'object' && module.exports) { module.exports = helpers; return; }

    // The key stays in userscript-manager storage/closure, never in page globals.
    GM_registerMenuCommand('Configure existing Next_Set connection', () => {
        const base = prompt('Existing private Next_Set HTTPS origin (https://...ts.net):');
        if (!base) return;
        let url;
        try { url = new URL(base); } catch { return; }
        if (url.protocol !== 'https:' || !url.hostname.endsWith('.ts.net') || url.username || url.password ||
            url.pathname !== '/' || url.search || url.hash) return;
        const key = prompt('Existing NEXT_SET_SYNC_KEY (stored only by Tampermonkey; never logged):');
        if (!key) return;
        GM_setValue('nextSetOrigin', url.origin);
        GM_setValue('nextSetSyncKey', key);
        alert('Connection saved. Reload BioLayne to enable the maintained script.');
    });
    const origin = GM_getValue('nextSetOrigin', '');
    const syncKey = GM_getValue('nextSetSyncKey', '');
    let target;
    try { target = new URL(origin); } catch { return; }
    if (!syncKey || target.protocol !== 'https:' || !target.hostname.endsWith('.ts.net') || target.origin !== origin) return;
    const page = unsafeWindow;
    let nonce = null;
    const nativeFetch = page.fetch;
    function rememberNonce(headers) {
        try { const value = new Headers(headers).get('X-WP-Nonce'); if (value) nonce = value; } catch { /* ignore */ }
    }
    const controller = createController({
        base: page.location.href,
        log: message => message.includes('failed') ? console.error(message) : console.info(message),
        claimBootstrap: now => {
            try {
                const key = 'nextSet-last-bootstrap-at';
                const previous = Number(sessionStorage.getItem(key));
                if (previous > 0 && now - previous < COOLDOWN_MS) return false;
                sessionStorage.setItem(key, String(now)); // Timestamp only. No token/cookies/nonce.
            } catch { /* Per-page cooldown still applies if storage is blocked. */ }
            return true;
        },
        fetchToken: async () => {
            const availableNonce = nonce || page.wpApiSettings?.nonce;
            const headers = availableNonce ? { 'X-WP-Nonce': availableNonce } : {};
            const abort = new AbortController();
            const timeout = setTimeout(() => abort.abort(), 10000);
            try {
                return await nativeFetch.call(page, TOKEN_PATH, { method: 'GET', credentials: 'same-origin',
                    cache: 'no-store', redirect: 'error', headers, signal: abort.signal });
            } finally { clearTimeout(timeout); }
        },
        send: createTransport(origin, syncKey, GM_xmlhttpRequest),
    });
    installPassive(page, controller, rememberNonce);
    const start = () => setTimeout(() => void controller.bootstrap(), 2000);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
})();
