# Revised Implementation Prompts — MindCI Monetisation

## Canonical Architecture Decisions

These decisions apply to **every prompt** in this document. Do not deviate from them.

| Concern | Decision |
|---|---|
| KV namespaces | Three separate bindings: `FREE_TRIAL`, `TOKEN_BALANCE`, `UNLIMITED` |
| Payment provider | Gumroad (direct product links + webhooks) |
| Payment callback | `background.js` polls `/check-purchase` every 4 s, timeout after 10 min |
| KV write authority | Worker only — the extension **never** writes to KV directly |
| Token deduction | Server-side in the proxy on every request (no `/deduct-tokens` endpoint) |
| KV failure policy | Fail open — allow the request, log the error |
| Webhook auth | HMAC-SHA256 verified against `GUMROAD_WEBHOOK_SECRET` env var |
| Trial reset rule | Reset `used → 0` only if `lastActivity > 90 days ago` AND `used < 20` |
| Balance meter denominator | Fixed at 100,000 (one pack) |
| `X-Request-Type` header | Informational / logging only — worker never trusts it for logic |

---

## Prompt 1 — Worker: per-user rate limiting & token tracking

### Context

`cloudflare-worker/worker.js` currently enforces a single global `DAILY_LIMIT` (50 requests/day across all users). Replace this with per-user entitlement checks backed by three KV namespaces.

### KV namespace schemas (canonical — all prompts use these)

```
FREE_TRIAL      key: free:<extensionId>
                value: { used: number, total: 20, firstSeen: number (ms), lastActivity: number (ms) }
                no TTL — record is permanent

TOKEN_BALANCE   key: tokens:<extensionId>
                value: { balance: number, lastUpdated: number (ms) }
                no TTL

UNLIMITED       key: unlimited:<extensionId>
                value: { active: true, grantedAt: number (ms) }
                no TTL
```

### wrangler.toml additions

Add three KV namespace bindings. Use these exact binding names everywhere:

```toml
[[kv_namespaces]]
binding = "FREE_TRIAL"
id = "<your-kv-id>"

[[kv_namespaces]]
binding = "TOKEN_BALANCE"
id = "<your-kv-id>"

[[kv_namespaces]]
binding = "UNLIMITED"
id = "<your-kv-id>"
```

### Task

Modify `worker.js` to replace the global limit with the following per-request logic. Implement this as a `checkEntitlement(extensionId, env)` async function that returns `{ allowed: boolean, type: 'unlimited'|'token'|'trial'|'rejected', meta: object }`.

**Step 1 — validate header**
- Read `X-Extension-Id` from the request headers.
- If missing or empty → return `400 Bad Request` with JSON `{ error: "missing_extension_id" }`.
- Do not trust `X-Request-Type` for any logic — it is a client hint for logging only.

**Step 2 — entitlement priority order**

Check in this exact order:

1. **Unlimited check** — `UNLIMITED.get("unlimited:<extensionId>", "json")`.
   - If value exists and `active === true` → allow, `type: 'unlimited'`.

2. **Token balance check** — `TOKEN_BALANCE.get("tokens:<extensionId>", "json")`.
   - If value exists and `balance > 0`:
     - Deduct `TOKENS_PER_REQUEST` (read from `env.TOKENS_PER_REQUEST`, default `2500`) from `balance`.
     - Write updated value back: `TOKEN_BALANCE.put("tokens:<extensionId>", JSON.stringify({ balance: newBalance, lastUpdated: Date.now() }))`.
     - Allow, `type: 'token'`, attach `X-Tokens-Remaining: <newBalance>` to the response.

3. **Free trial check** — `FREE_TRIAL.get("free:<extensionId>", "json")`.
   - If key does not exist → initialise `{ used: 0, total: 20, firstSeen: Date.now(), lastActivity: Date.now() }` and write it. Proceed as if `used === 0`.
   - **Reset rule**: if `used < 20` AND `(Date.now() - lastActivity) > 90 * 24 * 60 * 60 * 1000` → reset `used` to `0` before continuing.
   - If `used < 20` → increment `used`, update `lastActivity: Date.now()`, write back. Allow, `type: 'trial'`, attach `X-Free-Remaining: <20 - newUsed>` to the response.
   - If `used >= 20` → reject, `type: 'rejected'`, return `429` with header `X-Limit-Type: trial_exhausted` and JSON `{ error: "trial_exhausted" }`.

4. **Token balance exhausted** — if TOKEN_BALANCE existed but `balance <= 0` → return `429` with `X-Limit-Type: insufficient_tokens` and JSON `{ error: "insufficient_tokens" }`.

**Step 3 — KV failure handling**

Wrap every KV read in try/catch. On error: log `console.error`, allow the request, and attach `X-Entitlement-Source: fallback` to the response for observability.

**Step 4 — proxy the request**

Only after `checkEntitlement` returns `allowed: true` should the worker forward the request to the Anthropic API. Copy all response headers through, and append the entitlement headers (`X-Tokens-Remaining`, `X-Free-Remaining`, `X-Limit-Type`) as appropriate.

### Output expected

- Complete updated `worker.js` with `checkEntitlement` extracted as a named function with inline comments on the reset rule and fail-open logic.
- Updated `wrangler.toml` with the three KV namespace stubs.
- A `// ARCHITECTURE NOTE` comment block at the top of `worker.js` listing the three namespace names and their key schemas.

---

## Prompt 2 — Worker: purchase verification endpoint

### Context

When a user buys a Gumroad product, Gumroad fires a webhook to `/verify-purchase`. This endpoint must validate the request, then update the correct KV namespace. It shares `wrangler.toml` bindings with Prompt 1's worker — either add a new route to the existing worker or deploy as a separate worker with the same three KV bindings.

### Gumroad product IDs (use exactly these strings)

| Product | Gumroad permalink | Effect |
|---|---|---|
| Unlimited access ($9) | `marginalia_unlimited` | Write to `UNLIMITED` |
| 100k token pack ($5) | `marginalia_100k_tokens` | Add 100,000 to `TOKEN_BALANCE` |

### Webhook payload shape (Gumroad standard)

```json
{
  "seller_id": "...",
  "product_permalink": "marginalia_unlimited",
  "license_key": "XXXX-XXXX-XXXX-XXXX",
  "sale_id": "abc123",
  "custom_fields": {
    "extension_id": "<extensionId sent by the extension at checkout>"
  }
}
```

The extension must append `?extensionId=<chrome.runtime.id>` to the Gumroad product URL so Gumroad captures it as a custom field.

### Task

Add a `POST /verify-purchase` route to `worker.js`:

**Step 1 — verify webhook authenticity**

Gumroad signs webhooks with HMAC-SHA256. Verify using `env.GUMROAD_WEBHOOK_SECRET`:

```js
const sig = request.headers.get("X-Gumroad-Signature");
const body = await request.text();
const expected = await hmacSha256(env.GUMROAD_WEBHOOK_SECRET, body); // implement with SubtleCrypto
if (!timingSafeEqual(sig, expected)) return new Response("Forbidden", { status: 403 });
```

Implement `hmacSha256` using the Web Crypto API (`crypto.subtle`) — do not use any external library.

**Step 2 — extract extensionId**

Parse `custom_fields.extension_id` from the webhook body. If missing → return `400` with `{ error: "missing_extension_id" }`.

**Step 3 — update KV based on product**

- `marginalia_unlimited`:
  - Write `UNLIMITED.put("unlimited:<extensionId>", JSON.stringify({ active: true, grantedAt: Date.now() }))`.

- `marginalia_100k_tokens`:
  - Read current `TOKEN_BALANCE.get("tokens:<extensionId>", "json")`. If null, treat as `{ balance: 0 }`.
  - Add 100,000 to `balance`.
  - Write back: `TOKEN_BALANCE.put("tokens:<extensionId>", JSON.stringify({ balance: newBalance, lastUpdated: Date.now() }))`.

- Unknown `product_permalink` → return `400 { error: "unknown_product" }`.

**Step 4 — respond**

Return `200 { success: true, extensionId, product: product_permalink }`.

### Also add: `GET /check-purchase`

This endpoint is polled by `background.js` after a Gumroad checkout:

- Query param: `extensionId`.
- Returns `200` with:

```json
{
  "unlimited": true | false,
  "tokenBalance": <number | null>,
  "freeRemaining": <number>
}
```

- Reads from all three KV namespaces. Fail open on KV error (return nulls, not 500).

### Environment variables to add to `wrangler.toml`

```toml
[vars]
TOKENS_PER_REQUEST = "2500"

# Set as a secret via `wrangler secret put GUMROAD_WEBHOOK_SECRET`
```

### Output expected

- `worker.js` with `/verify-purchase` and `/check-purchase` routes added.
- `wrangler.toml` with env var stubs and a comment indicating `GUMROAD_WEBHOOK_SECRET` must be set as a Worker secret.
- Inline comments explaining the HMAC verification and timing-safe comparison.

---

## Prompt 3 — Extension: update `lib/api.js` for per-user limits

### Context

`lib/api.js` handles two paths: direct Anthropic API calls (user's own key) and proxy calls (our Cloudflare Worker). The proxy path must now include `X-Extension-Id` and parse the entitlement headers from Prompt 1's worker.

The direct key path must remain completely unchanged — no headers added, no storage reads.

### Storage key schema (canonical — use these exact keys across all prompts)

```
chrome.storage.local keys:
  purchase.unlimited       boolean
  purchase.tokenBalance    number | null
  purchase.freeRemaining   number
  trialExhausted           boolean
```

### Task

Modify the proxy branch of `_fetchClaude` (leave the direct key branch untouched):

**Step 1 — attach extension ID header**

Before the fetch:

```js
headers['X-Extension-Id'] = chrome.runtime.id;
headers['X-Request-Type'] = await _getRequestType(); // purely for worker-side logging
```

Implement `_getRequestType()`: reads `purchase.unlimited`, `purchase.tokenBalance`, `purchase.freeRemaining` from `chrome.storage.local` and returns `'unlimited'` | `'token'` | `'trial'`.

**Step 2 — parse response headers**

After a successful (2xx) response, before returning the parsed body:

```js
const freeRemaining = response.headers.get('X-Free-Remaining');
const tokensRemaining = response.headers.get('X-Tokens-Remaining');

if (freeRemaining !== null) {
  await chrome.storage.local.set({ 'purchase.freeRemaining': parseInt(freeRemaining, 10) });
}
if (tokensRemaining !== null) {
  await chrome.storage.local.set({ 'purchase.tokenBalance': parseInt(tokensRemaining, 10) });
}
```

**Step 3 — handle 429 responses**

When the proxy returns `429`:

- Read `X-Limit-Type` from the response headers.
- If `trial_exhausted`:
  - Set `chrome.storage.local` keys: `{ trialExhausted: true, 'purchase.freeRemaining': 0 }`.
  - Throw a typed error: `throw Object.assign(new Error('Free trial used up – upgrade to keep using the proxy, or enter your own API key.'), { code: 'TRIAL_EXHAUSTED' })`.
- If `insufficient_tokens`:
  - Throw: `throw Object.assign(new Error('Out of tokens – buy a token pack to continue, or enter your own API key.'), { code: 'INSUFFICIENT_TOKENS' })`.
- For any other 429 (should not occur): throw generic rate limit error.

**Step 4 — surface errors in `callClaude`**

In the top-level `callClaude` function, catch errors with `.code === 'TRIAL_EXHAUSTED'` or `.code === 'INSUFFICIENT_TOKENS'` and re-throw them — do not swallow them or convert them to generic errors. `sidebar.js` will handle the UI.

### Output expected

- Complete updated `lib/api.js`.
- The direct key path must be visually separated with a `// --- DIRECT KEY PATH (no changes) ---` comment block to make the boundary obvious.

---

## Prompt 4 — Extension: purchase UI in `sidebar.html` / `sidebar.js`

### Context

The sidebar has a settings panel (⚙) accessible via `#settingsPanel`. We need a "License & Credits" section inside it. Payment is handled through Gumroad — direct product links opened in a new tab, with `background.js` polling for confirmation (Prompt 5).

### Gumroad product URLs

```
Unlimited:     https://gumroad.com/l/marginalia_unlimited?extensionId=EXTENSION_ID_PLACEHOLDER
100k tokens:   https://gumroad.com/l/marginalia_100k_tokens?extensionId=EXTENSION_ID_PLACEHOLDER
```

Replace `EXTENSION_ID_PLACEHOLDER` at runtime with `chrome.runtime.id`.

### Task — HTML (`sidebar.html`)

Inside `#settingsPanel`, add the following structure (insert before the API key field):

```html
<div id="licenseSection">
  <h3 class="settings-label">License & Credits</h3>

  <div id="licenseStatus">
    <!-- Populated by JS — see states below -->
  </div>

  <div id="tokenMeter" style="display:none">
    <div class="meter-track">
      <div id="tokenMeterFill" class="meter-fill"></div>
    </div>
    <span id="tokenMeterLabel"></span>
  </div>

  <div id="lowCreditBanner" class="warning-banner" style="display:none">
    Low on credits — <a id="buyMoreLink" href="#">buy more</a> or use your own API key.
  </div>

  <div id="purchaseButtons">
    <button id="btnBuyUnlimited" class="btn-purchase">Upgrade to Unlimited ($9)</button>
    <button id="btnBuyTokens" class="btn-purchase">Buy 100k tokens ($5)</button>
  </div>
</div>
```

Add CSS for `.meter-track`, `.meter-fill`, `.warning-banner`, and `.btn-purchase`. Keep styles consistent with existing sidebar variables.

### Task — JavaScript (`sidebar.js`)

Implement these four functions. Do not inline them — export or attach to a `LicenseUI` object so they are testable:

**`initLicenseUI()`**
- Called once on sidebar load.
- Reads `purchase.unlimited`, `purchase.tokenBalance`, `purchase.freeRemaining`, `trialExhausted` from `chrome.storage.local`.
- Calls `refreshLicenseStatus()`.
- Attaches click handlers to `#btnBuyUnlimited` and `#btnBuyTokens` (both call `buyProduct(productId)`).
- Attaches `#buyMoreLink` click handler → `buyProduct('marginalia_100k_tokens')`.

**`refreshLicenseStatus()`**
- Updates `#licenseStatus` to one of three states:

| State | Condition | Text shown |
|---|---|---|
| Unlimited | `purchase.unlimited === true` | "Unlimited access active ✓" |
| Token pack | `purchase.tokenBalance !== null` | "Token balance: 87,400" (formatted with commas) |
| Free trial | default | "Free trial: 14 requests left" (or "Free trial used up" if `trialExhausted`) |

- Updates `#tokenMeter`: visible only when `purchase.tokenBalance !== null`. Set `#tokenMeterFill` width to `(balance / 100000 * 100)%`. Set `#tokenMeterLabel` to `"<formatted> / 100k tokens"`.
- Shows `#lowCreditBanner` if `purchase.tokenBalance !== null && purchase.tokenBalance < 5000`.
- Hides `#btnBuyUnlimited` if `purchase.unlimited === true`.

**`buyProduct(productId)`**
- Reads `chrome.runtime.id`.
- Sends `chrome.runtime.sendMessage({ action: 'START_PURCHASE', productId })`.
- Disables the clicked button and sets its text to "Opening checkout…".
- Listens for `chrome.runtime.onMessage` for `{ action: 'PURCHASE_COMPLETE', productId }` — on receipt, re-enable the button and call `refreshLicenseStatus()`.
- Listens for `{ action: 'PURCHASE_TIMEOUT' }` — re-enable button, show inline error: "Purchase timed out — if you completed payment, restart the extension."

**`storage.onChanged` listener**
- Add a `chrome.storage.onChanged` listener that calls `refreshLicenseStatus()` whenever any of the four purchase keys change. This ensures the UI updates if `background.js` writes to storage in the background.

### Output expected

- HTML additions for `sidebar.html`.
- CSS additions (can be in `<style>` block or existing `.css` file — follow existing project convention).
- JavaScript functions in `sidebar.js`.

---

## Prompt 5 — Background service worker: Gumroad purchase flow

### Context

`background.js` currently handles context menu, side panel opening, and API calls. Add a message handler for `START_PURCHASE` that opens a Gumroad checkout tab and polls the worker's `/check-purchase` endpoint until the purchase is confirmed.

The worker's `/check-purchase` endpoint is defined in Prompt 2. The storage keys are defined in Prompt 3.

### Gumroad product URLs

```
Unlimited:     https://gumroad.com/l/marginalia_unlimited?extensionId=<chrome.runtime.id>
100k tokens:   https://gumroad.com/l/marginalia_100k_tokens?extensionId=<chrome.runtime.id>
```

### Task

**Step 1 — message handler**

Add to `background.js`:

```js
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'START_PURCHASE') {
    startPurchaseFlow(message.productId);
  }
});
```

**Step 2 — `startPurchaseFlow(productId)`**

```
1. Build the Gumroad URL for productId (use the URLs above).
2. Open the URL in a new tab: chrome.tabs.create({ url: gumroadUrl }).
3. Start polling: call pollForPurchase(productId, tabId) — see below.
```

**Step 3 — `pollForPurchase(productId, tabId)`**

```
- Poll interval: 4000 ms
- Timeout: 10 minutes (600,000 ms)
- Endpoint: GET <WORKER_BASE_URL>/check-purchase?extensionId=<chrome.runtime.id>
  (WORKER_BASE_URL should be a constant at the top of background.js)
- On each poll:
    const data = await fetch(pollUrl).then(r => r.json());
    const purchased = productId === 'marginalia_unlimited'
      ? data.unlimited === true
      : data.tokenBalance !== null && data.tokenBalance > 0;
    if (purchased) → call onPurchaseConfirmed(productId, data); stop polling.
- On timeout (10 min elapsed without confirmation):
    Send message { action: 'PURCHASE_TIMEOUT' } to the sidebar.
    Stop polling.
- If the poll tab is closed by the user before confirmation:
    Use chrome.tabs.onRemoved to detect tab closure.
    Stop polling immediately (do not send PURCHASE_TIMEOUT — the user may have closed it accidentally).
```

**Step 4 — `onPurchaseConfirmed(productId, data)`**

```
- Write to chrome.storage.local:
    if productId === 'marginalia_unlimited':
      { 'purchase.unlimited': true }
    if productId === 'marginalia_100k_tokens':
      { 'purchase.tokenBalance': data.tokenBalance }
- Send message { action: 'PURCHASE_COMPLETE', productId } to the sidebar.
```

**Step 5 — permissions**

Add to `manifest.json` if not already present: `"tabs"` permission (required for `chrome.tabs.create` and `chrome.tabs.onRemoved`).

### Output expected

- `startPurchaseFlow`, `pollForPurchase`, and `onPurchaseConfirmed` functions in `background.js`.
- `WORKER_BASE_URL` constant at the top of the file with a comment: `// TODO: replace with production worker URL before release`.
- The required `manifest.json` change listed as a comment block at the top of the additions.

---

## Prompt 6 — Accurate token deduction (server-side)

### Context

Prompt 1 deducts a flat `TOKENS_PER_REQUEST` (default 2500) per proxied request. This prompt replaces the flat deduction with actual token usage from Anthropic's response, entirely on the worker — the extension does not send a separate deduction request.

### How Anthropic returns usage

Every Anthropic response body includes:

```json
{
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 567
  }
}
```

`total_tokens = input_tokens + output_tokens`.

### Task

**In `worker.js`**, modify the proxy-and-respond flow for `type: 'token'` requests:

1. After forwarding the request and receiving the Anthropic response, parse the response body as JSON.
2. Extract `usage.input_tokens + usage.output_tokens` → `actualTokens`.
3. The worker already deducted `TOKENS_PER_REQUEST` optimistically in `checkEntitlement`. Now perform a **correction write**:
   - Read current `TOKEN_BALANCE` for this user.
   - Compute `correctedBalance = currentBalance + TOKENS_PER_REQUEST - actualTokens`.
     (This refunds the over-deduction or charges the extra, depending on actual usage.)
   - If `correctedBalance < 0`, clamp to `0`.
   - Write back.
   - Update the `X-Tokens-Remaining` header in the response to reflect `correctedBalance`.
4. If parsing the Anthropic response fails (malformed JSON), skip the correction write and log the error. Do not fail the request — the user already received their response.
5. For `type: 'trial'` and `type: 'unlimited'` requests, skip this correction entirely.

**In `lib/api.js`** (extension):

- No changes needed for deduction — it is now fully server-side.
- After a successful proxy response, still update `purchase.tokenBalance` from `X-Tokens-Remaining` as specified in Prompt 3 (this header now reflects the corrected balance).

### Edge case: streaming responses

If the proxy forwards a streaming response (`Content-Type: text/event-stream`), Anthropic includes usage in the final `message_delta` event. Implement streaming correction:

- Buffer the stream as it passes through.
- When the final event is detected (contains `"type":"message_delta"` with `usage`), perform the correction write before closing the response.
- If buffering the full stream is not feasible in the Worker's memory budget, fall back to the flat deduction and log `console.warn("Streaming response — using flat token deduction")`.

### Output expected

- Updated `worker.js` `checkEntitlement` and proxy-response sections with correction logic.
- A comment block explaining the optimistic-deduct / correct-on-response pattern and why it is used instead of deducting post-response only.

---

## Prompt 7 — Free trial abuse prevention

### Context

The `free:<extensionId>` KV record in `FREE_TRIAL` is initialised by Prompt 1's worker on first request. This prompt hardens the logic against the most common abuse vectors.

`chrome.runtime.id` is fixed per extension-ID/browser-profile combination for Chrome Web Store installations — a user cannot change it without reinstalling from a different account. The main abuse vector is a developer side-loading a modified extension with a different ID.

### Task

In `worker.js`, update the free trial initialisation and check section of `checkEntitlement`:

**Rule set (implement in this order, with inline comments for each):**

```
1. READ record from FREE_TRIAL KV.
   On KV error: fail open (allow request), skip all further trial logic.

2. IF record does not exist:
   - Initialise: { used: 0, total: 20, firstSeen: Date.now(), lastActivity: Date.now() }
   - Write to KV (no TTL — record is permanent).
   - Proceed to step 4.

3. IF record exists:
   a. RESET CHECK:
      - If used < 20 AND (Date.now() - lastActivity) > 90 days:
        → Reset used to 0, update lastActivity, write back.
        → Log: console.log(`[trial-reset] extensionId=${extensionId} reset after inactivity`)
      - If used >= 20: skip reset check entirely.

   b. ACTIVITY UPDATE:
      - Always update lastActivity to Date.now() on every request, regardless of used count.
        (Doing this even for exhausted users ensures the 90-day window only opens for
         genuinely inactive users, not users who hit the limit and kept trying.)

4. IF used < 20:
   - Increment used.
   - Write back { used, total: 20, firstSeen, lastActivity: Date.now() }.
   - Allow, attach X-Free-Remaining: (20 - used).

5. IF used >= 20:
   - Write back with updated lastActivity only (keep used unchanged).
   - Reject 429, X-Limit-Type: trial_exhausted.
```

**Anti-cycling note (add as a code comment):**

> `chrome.runtime.id` is stable for Web Store installs. Side-loaded extensions with forged IDs are out of scope for this MVP — they represent a negligible fraction of users and would require a signed install token to prevent fully. Revisit if abuse metrics indicate a problem post-launch.

**Do not implement** a salt or HMAC for extension IDs at this stage — it adds complexity without meaningfully blocking the realistic abuse vector.

### Output expected

- Updated `checkEntitlement` free trial section with the full rule set and inline comments.
- The anti-cycling comment block as described.

---

## Prompt 8 — Token balance UI & low-credit warnings

### Context

`sidebar.js` already calls `initLicenseUI()` and `refreshLicenseStatus()` from Prompt 4. This prompt adds the low-credit warning and zero-balance blocking logic, and adds the pre-call intercept in `callClaude`.

### Task — `sidebar.js`

**`updateTokenUI(balance)`**
- Called whenever `purchase.tokenBalance` changes (via storage listener or after a response).
- Updates `#tokenMeterFill` width: `Math.max(0, (balance / 100000) * 100).toFixed(1) + '%'`.
- Updates `#tokenMeterLabel`: format balance with `toLocaleString()` (e.g., "87,400 / 100k tokens").
- Calls `showLowCreditWarning(balance)`.

**`showLowCreditWarning(balance)`**
- If `balance < 5000` and `balance > 0`:
  - Show `#lowCreditBanner` (set `display: block`).
  - Do not show a modal — the banner is sufficient.
- If `balance <= 0`:
  - Hide `#lowCreditBanner`.
  - The zero-balance modal is triggered separately by `blockOnZeroCredits()`.
- If `balance >= 5000`:
  - Hide `#lowCreditBanner`.

**`blockOnZeroCredits()`**
- Call this at the **start** of the send-message handler in `sidebar.js`, before invoking `callClaude`.
- Read `purchase.tokenBalance` from `chrome.storage.local`.
- If `purchase.unlimited` is true, skip all checks.
- If `purchase.tokenBalance !== null && purchase.tokenBalance <= 0`:
  - Do not call `callClaude`.
  - Show an inline error in the chat area (same mechanism as existing API error display):
    ```
    You have no tokens remaining. Buy a token pack to continue, or enter your own API key.
    ```
  - Include a "Buy tokens" link that calls `buyProduct('marginalia_100k_tokens')`.
  - Return early.
- If `trialExhausted === true` and `purchase.tokenBalance === null` and `purchase.unlimited !== true`:
  - Show inline error:
    ```
    Your free trial has ended. Upgrade to Unlimited ($9) or buy a token pack ($5) to continue.
    ```
  - Return early.

**Error handling from `callClaude`**

Catch the typed errors from Prompt 3 in the send-message handler:

```js
} catch (err) {
  if (err.code === 'TRIAL_EXHAUSTED') {
    showInlineError('Your free trial has ended. Upgrade to continue.');
  } else if (err.code === 'INSUFFICIENT_TOKENS') {
    showInlineError('You have no tokens remaining. Buy a token pack to continue.');
  } else {
    showInlineError(err.message); // existing behaviour
  }
}
```

### Task — CSS

Add to the existing stylesheet:

```css
.meter-track {
  width: 100%;
  height: 6px;
  background: var(--color-surface-secondary, #e5e7eb);
  border-radius: 3px;
  overflow: hidden;
  margin: 6px 0 4px;
}
.meter-fill {
  height: 100%;
  background: var(--color-accent, #4f46e5);
  border-radius: 3px;
  transition: width 0.4s ease;
}
.meter-fill.low {
  background: var(--color-warning, #f59e0b);
}
.warning-banner {
  font-size: 12px;
  color: var(--color-warning-text, #92400e);
  background: var(--color-warning-bg, #fef3c7);
  border-radius: 4px;
  padding: 6px 10px;
  margin: 8px 0;
}
```

Add `.low` class to `#tokenMeterFill` when `balance < 5000` (amber fill).

### Output expected

- `updateTokenUI`, `showLowCreditWarning`, and `blockOnZeroCredits` functions in `sidebar.js`.
- CSS additions as above.
- Updated send-message handler showing where `blockOnZeroCredits()` is called and where the `catch` block handles typed errors.

---

## End-to-end test sequence

After implementing all prompts, verify the following flows manually:

1. **Fresh install → free trial**: first request should initialise `free:<id>` KV, return `X-Free-Remaining: 19`.
2. **Trial exhaustion**: after 20 requests, next request returns 429 `trial_exhausted`. Sidebar shows "trial ended" state.
3. **Buy Unlimited**: click "Upgrade to Unlimited ($9)". Gumroad tab opens. Complete (or simulate) purchase. Poll resolves. `purchase.unlimited` set to true. Sidebar shows "Unlimited active ✓". Send button works without limits.
4. **Buy token pack**: click "Buy 100k tokens ($5)". Complete purchase. `purchase.tokenBalance` set to 100000. Token meter shows full bar.
5. **Token deduction**: make several requests. Meter and numeric balance decrease after each. Verify correction (actual tokens) not flat deduction.
6. **Low credit warning**: reduce balance below 5000 (via KV editor or many requests). Banner appears. Meter turns amber.
7. **Zero balance block**: reduce to 0. Attempt to send. Inline error shown, API call not made.
8. **90-day inactivity reset** (simulate by setting `lastActivity` to a past date in KV directly): make a request. `used` resets to 0. New trial starts.
