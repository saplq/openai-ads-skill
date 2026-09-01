# Pixel + Conversions API

Official sources: [Measurement Pixel](https://developers.openai.com/ads/measurement-pixel), [Conversions API](https://developers.openai.com/ads/conversions-api), [Conversion setup](https://developers.openai.com/ads/api-reference/conversion-setup), [Supported events](https://developers.openai.com/ads/supported-events).

## Target-repository workflow

1. Inspect framework, package manager, browser/server boundaries, analytics, consent manager, existing event IDs, env conventions, CSP, tests, and deployment secret manager.
2. Choose one documented event and data shape. Use `custom` only when no standard event fits.
3. Add the Pixel once in a browser-only root point. Do not render it server-side or duplicate it across layouts.
4. Set measurement consent before `oaiq("init", ...)`; connect changes to the existing consent manager. Do not default to consent where the site requires opt-in.
5. Send CAPI only from server code. Put only the server-side env variable name in source, never its value.
6. Generate one event ID in application logic and reuse it as Pixel `event_id` and CAPI event `id`, with the same Pixel ID and event name.
7. Validate with `capi send --validate-only`, then confirm recent Pixel events. Production sending and secret-manager transfer are explicit manual steps.

`--validate-only` still sends the batch to OpenAI's remote validation endpoint; it does not persist events. For a fully offline check, run the local unit validator through the test suite or import `openai_ads_lib.conversions.validate_batch` in a trusted local script.

Pixel SDK URL: `https://bzrcdn.openai.com/sdk/oaiq.min.js`. CAPI endpoint: `POST https://bzr.openai.com/v1/events?pid=PIXEL_ID`.

For a browser application, preserve the framework's script and nonce conventions while implementing this equivalent bootstrap once:

```js
if (!window.oaiq) {
  const queue = (...args) => queue.q.push(args);
  queue.q = [];
  window.oaiq = queue;
  const sdk = document.createElement("script");
  sdk.async = true;
  sdk.src = "https://bzrcdn.openai.com/sdk/oaiq.min.js";
  document.head.appendChild(sdk);
}
oaiq("consent", false);
oaiq("init", { pixelId: "PIXEL_ID" });
```

Replace the fixed `false` with the existing consent manager's state and update via `oaiq("consent", value)`. In a server-only repository such as an API-only FastAPI service, add only the server-side CAPI helper; locate and patch the separate browser client for Pixel/CSP instead of putting browser code into the API.

Browser measurement uses `oaiq("measure", eventName, eventData, { event_id: eventId })`; the matching server event uses the same value in `events[].id`.

Merge CSP sources instead of replacing policy: `script-src https://bzrcdn.openai.com`; `connect-src https://bzr.openai.com https://bzrcdn.openai.com`; `img-src https://bzr.openai.com`. Do not add `unsafe-inline` solely for the Pixel.

## CAPI validation

- 1–1,000 events; one invalid event rejects the entire batch.
- `timestamp_ms`: no older than seven days and no more than ten minutes ahead.
- Web events require a valid `source_url`; strip query and fragment before sending unless the application proves they contain no identifiers.
- Reuse opaque `oppref` unchanged when available. Put the consented `__obref` cookie into `events[].user.obref` unchanged.
- Hash supported user identifiers as documented; never send raw email, phone, external ID, first name, or last name.
- Stop collecting or forwarding measurement identifiers after consent is withdrawn.

The CLI stores a generated CAPI key in `capi-secrets.json` mode `0600`, prints only its fingerprint/path, and cannot revoke it. Move it manually to the production secret manager, verify the deployment, then remove the local copy if appropriate.
