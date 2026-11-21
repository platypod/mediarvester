# 2026-11-17 → 11-21: pinned YouTube player clients kept breaking

## Impact

- 2026-11-16 → 11-17: `HmGlTkbMhII` and `OSanF9CARYA` failed every attempt with
  `HTTP Error 403: Forbidden` on the first stream request (v1.15.3, `mweb`).
- 2026-11-20 → 11-21 (v1.15.5, `web_embedded`): `eSO64xGEAow` exhausted all
  retries with "blocked it from display on this website or application", and
  `smspqpgDKQo` failed with "Requested format is not available" after
  "web_embedded client https formats have been skipped as they are missing a
  URL" (SABR).

## Root cause

Every YouTube call pinned one innertube client (`player_client`). yt-dlp's
maintainers re-tune the default client *set* as YouTube changes, and the set
spreads risk across several clients; a single pinned client fails as soon as
YouTube changes how that client is treated.

- `mweb` (pinned 2026-08-19 for SABR): started 403ing on some videos.
- `tv_embedded` (v1.15.4): **not a real client**. yt-dlp logged
  `Skipping unsupported client "tv_embedded"` and used its defaults — which is
  the only reason v1.15.4 fixed anything.
- `web_embedded` (v1.15.5): refuses embed-restricted videos by design, and got
  SABR-only formats on others.

yt-dlp 2026.10.26 defaults: `tv_samsung, visionos, web` anonymously;
`web_embedded, tv_samsung, web` with logged-in cookies. In the prod pod on
2026-11-21 the defaults downloaded both failing videos with either owner's
cookies.

## Why it wasn't caught

- Testing was by hand, a few runs per client, so noisy results (the same
  config passing and failing minutes apart) were read as signal.
- Local "with cookies" tests used a cookies file YouTube had already rotated,
  so they actually ran anonymously and exercised a different default set.
- "Listing several clients breaks format selection" was concluded from
  `tv_embedded,mweb` — really just `mweb` failing, since `tv_embedded` is
  invalid. yt-dlp's own defaults are a multi-client list and work fine.
- Retries repeat the exact same options, so a deterministic failure (embed
  block) burns all four attempts identically.

## Fix

- v1.15.6: no `player_client` anywhere; all call sites share
  `services/ytdlp_options.py`. `tests/test_player_client.py` fails if a pin
  comes back.

## Follow-ups

- Keep yt-dlp current automatically instead of only at mediarvester releases.
- A canary + cookie health check, so a YouTube change shows up as a signal.
- Retries that classify the error and change strategy per attempt.
