# Maintained BioLayne userscript

The existing installed script was not in the repository, and Chrome was not connected for inspection. `biolayne-sync.user.js` is a maintained replacement source, **not an automatically installed update**. Its browser adapter has not been live-tested in the user's Tampermonkey environment.

## Manual update

Back up the current working userscript privately in Tampermonkey; it may contain the existing sync key. Do not commit, paste, or log that backup. Install the maintained source, then use its Tampermonkey menu **Configure existing Next_Set connection** to enter the existing private HTTPS `*.ts.net` origin and existing sync key. The key stays in Tampermonkey's isolated storage; no real values are supplied in source. Disable the older script only when ready to test this replacement, so both do not forward/sync simultaneously. No installation, credential transfer, disabling, page reload, or live production sync was performed by the agent.

Only the existing Tailscale `*.ts.net` HTTPS destination is accepted. If the actual private hostname uses a custom domain, update the explicit validation and `@connect` allowlist after reviewing that destination; do not substitute a wildcard or HTTP destination. No coach key belongs in the userscript.

## Bootstrap and passive fallback

Two seconds after DOM readiness, the script makes one same-origin GET to `/wp-json/biolayne-app/v1/get-token` using the browser's existing cookies. It never reads, copies, or stores cookies, username, or password. If available, `wpApiSettings.nonce` or an observed token-request `X-WP-Nonce` is used in memory only. No nonce is persisted or logged. A logged-out, nonce-rejected, malformed, or timed-out request fails quietly, without redirecting, reloading, or retrying in a loop.

The bootstrap has a 60-second per-page cooldown and a timestamp-only sessionStorage cooldown across reloads in the same tab. Token forwarding also has a 60-second cooldown. Tokens remain in memory only while forwarding; no persistent token cache is introduced. Fetch and XHR interception continue to observe successful native token calls as fallback.

Accepted token response shapes are `{token, expires}`, `{access_token, expires}`, or those objects inside `data`. Expiry must be Unix seconds; if absent, a JWT's `exp` may supply expiry metadata (this is not signature verification or authorization). Other shapes fail closed. **Confirm the installed working script's response mapping and the site's nonce exposure during the first manual check.** If the current site exposes its nonce elsewhere or uses a different request method, adapt the browser adapter from observed working behavior; do not scrape cookies or bypass WordPress nonce checks.

## Automatic workout sync

Successful page fetch/XHR responses from the known Azure `/api/workout-exercises?workoutId=...` route schedule `/sync/workout/{id}`. Other origins/routes, duplicate ID parameters, invalid IDs, and unrelated requests are ignored. This hook covers observed workout refresh activity; it does not assume every set-save endpoint triggers that read. Additional save routes should only be added after inspecting their actual behavior.

Debounce is 20 seconds after the latest relevant response, with a 60-second minimum between starts per workout. One sync runs at a time per tab; further activity queues at most one follow-up per workout. Failed syncs are not automatically retried without new activity. Different tabs can still race, so the server's 409 lock remains authoritative. No distributed or cross-tab lock was added.

Only fixed sanitized status strings are logged, using console.error for failures. Raw response bodies, headers, exceptions, keys, and tokens are never logged. Next_Set requests omit cookies (`anonymous: true`), use HTTPS and `redirect: 'error'`, and have a timeout. Use a current Tampermonkey version supporting this redirect option; confirm permission prompts and page-world fetch/XHR hooks in the browser.

The script uses its own 65-second timer and aborts timed-out requests because [Tampermonkey documents](https://www.tampermonkey.net/documentation.php?locale=en&q=GM_xmlhttpRequest) that Chrome fetch mode ignores `details.timeout`. The redirect option requires build 6180 or later. Bootstrap requests have a separate 10-second abort timer.

## Validation scope

`node --test tests/test_userscript.cjs` tests token parsing, proactive authenticated fetch through mocked IO, logged-out/cooldown behavior, strict workout detection, debounce, queued overlap handling, sanitized logs, and passive fetch/XHR wrappers. It makes no network calls. Browser-only checks remaining: Tampermonkey isolated/page-world interoperability, actual nonce and response shape, extension cross-origin permission/redirect behavior, and live restart recovery. Installing and enabling this script opts into live automatic sync; do not perform that live check until approved.
