# 2026-08-20/21: YouTube 403s on adaptive formats, then an OOM crash loop

Triggered by a request to download three YouTube playlists (MrDeriv's,
attempted and failed earlier by user `reivi`). Three separate problems
surfaced in sequence; this records what each one was, how it was diagnosed,
what was tried and rejected, and what actually fixed it.

## Timeline

| Time (CEST) | Event |
|---|---|
| ~22:10 | v1.6.0 shipped (playlist progress tracking, yt-dlp log routing — separate, unrelated work) and deployed to prod |
| 22:21 | Reivi's three playlists re-queued against v1.6.0 → all three failed, every video `HTTP Error 403: Forbidden` |
| 22:22–22:32 | Root-caused to a new YouTube A/B test; shipped v1.6.1 (force progressive-only format as a stopgap) |
| 22:41–23:07 | Re-ran all three playlists against v1.6.1 → all 92 videos downloaded, capped at ~360p |
| 08:54–10:20 (next day) | Pod OOMKilled repeatedly (6 restarts observed); investigated once LAN access was re-established via SSH bastion |
| 10:24 | Found `bgutil-ytdlp-pot-provider` 1.3.2 (released 03:19 that morning) fixes the exact 403 upstream; verified locally against the real 403'ing video with full adaptive/1080p60 format, no truncation |
| — | Shipped the sidecar bump, reverted the format restriction, bumped memory limit + added `MALLOC_ARENA_MAX=2`, added a degraded-service banner to the GUI |

## Issue 1: every video 403ing (adaptive/DASH formats only)

**Symptom:** all three playlists failed completely under v1.6.0; per-download
logs (routed through our logger as of v1.6.0 — previously this would have
gone straight to raw stderr and been invisible) showed
`HTTP Error 403: Forbidden` for the actual video-data request, not
extraction.

**Diagnosis:**
- Reproduced directly inside the prod pod (`kubectl exec` + a one-off
  yt-dlp script against the real failing video ID) rather than guessing —
  the mock/local setup wouldn't have reproduced whatever's different about
  the pod's exact yt-dlp/plugin versions.
- Verbose yt-dlp output showed: `Detected experiment to bind GVS PO Token
  to video ID for mweb client`. That's YouTube rolling out a new
  requirement, not a config regression on our side.
- Narrowed it to *which* formats fail: itag 18 (progressive, muxed,
  ~360p ceiling) succeeded every time; every adaptive/DASH itag (video-only
  or audio-only — 299, bestvideo\*, bestaudio, etc.) 403'd, even with a
  fresh per-video PO token minted by the bgutil sidecar.

**Rejected alternatives:**
- *Try other yt-dlp player clients* (`tv`, `web_safari`, `ios`,
  `android_vr`, `web`) — tested all five directly against the same video;
  every one either errored outright or reported no available formats. mweb
  remained the only client that worked at all, confirming the earlier
  2026-08-19 choice was still right and this wasn't a "pick a different
  client" problem.
- *Manually pass a static `po_token` via extractor-args* — rejected because
  it would have needed a token minted and refreshed by hand per video/session;
  not viable for an unattended download queue.

**Stopgap shipped (v1.6.1):** force `format: "best/bestvideo*+bestaudio"` —
prefer the working progressive stream, only fall through to the (currently
broken) adaptive path if no progressive format exists at all. This is what
let the three playlists complete, but at a real quality cost (~360p instead
of up to 1080p60) — called out to the user immediately as unacceptable and
temporary.

**Actual fix:** `bgutil-ytdlp-pot-provider` 1.3.2, released 2026-08-21
03:19 — a few hours after we hit this — mints WebPO tokens from the
homepage challenge + `ytcfg` instead of the method 1.3.1 used, explicitly
"mitigating 403s for clients in certain A/B tests"
([release](https://github.com/Brainicism/bgutil-ytdlp-pot-provider/releases/tag/1.3.2),
fixes [#242](https://github.com/Brainicism/bgutil-ytdlp-pot-provider/issues/242)).
Verified by running the 1.3.2 Docker image locally (no cluster access
needed) against the exact video that 403'd before, with the format
restriction removed: it selected the full adaptive 1080p60 stream
(itag 299+251) and completed the ~4.1 GB download with no mid-transfer
failure. Sidecar bumped to `1.3.2-node` in
[`stack/values/default/media/mediarvester.yaml`](../../../stack/values/default/media/mediarvester.yaml)
and the format restriction reverted in
[`src/services/downloader.py`](../../src/services/downloader.py) — full
quality is back to being the default.

If this recurs with a different signature, that's what the new
`/api/settings/service-status` endpoint and the queue-page banner are for
(see below) — surfacing it instead of silently downgrading everyone's
quality again.

## Issue 2: mediarvester OOMKilled every ~1–3 hours

**Symptom:** discovered via `kubectl describe pod`, which showed 6 restarts,
most recently `OOMKilled` (exit 137) after ~1h24m of uptime. Pulled the
actual memory curve from Mimir (`k8s_pod_memory_working_set`,
`namespace="prd-platypod", pod=~"mediarvester.*"`, 10-min steps over the
prior ~11h): a repeating sawtooth — climbs from a ~200 MB post-restart
baseline up to 600–900 MB+ over a few hours, occasionally pins flat at
exactly 1050 MB (the 1 GiB limit) for several consecutive samples right
before a kill, then resets.

**Diagnosis:**
- Live process snapshot (`/proc/[0-9]*` — the image doesn't ship `ps`)
  right after a fresh restart showed exactly one process (`uvicorn`) and no
  lingering Node.js child processes, ruling out a zombie-subprocess leak.
- The climb continues during periods with no active downloads (poller-only
  activity), so it isn't purely proportional to concurrent yt-dlp jobs.
- Working theory: glibc malloc arena fragmentation, not a Python-level
  object leak. The workload is a textbook trigger for it — a long-running
  multi-threaded process (`ThreadPoolExecutor`, `DOWNLOAD_CONCURRENCY=2`)
  repeatedly allocating and freeing large short-lived buffers (full
  playlist/video JSON metadata, subprocess stdin/stdout for the Node.js
  JS-challenge solver) — glibc gives each thread its own arena and doesn't
  reliably return freed memory to the OS, so RSS climbs even with zero
  actual leaks.

**Not fully proven** — confirming this precisely would need in-process
heap profiling (e.g. `tracemalloc`) sustained over several hours to catch
the climb live, which wasn't done here. Treat the arena-fragmentation
diagnosis as the leading hypothesis, not a certainty.

**Fix shipped:**
- `MALLOC_ARENA_MAX=2` added to the mediarvester container env
  ([`mediarvester--deployment.yaml`](../../../stack/src/media/templates/mediarvester/mediarvester--deployment.yaml))
  — the standard mitigation for this exact pattern (same fix commonly
  applied to gunicorn/celery workers).
- Memory limit raised 1Gi → 2Gi, request 640Mi → 768Mi, as headroom while
  the above is monitored — not treated as the fix itself, since that would
  just buy a longer runway before the same climb hits a higher ceiling.

**Follow-up if it recurs post-fix:** add a debug endpoint or scheduled job
dumping `tracemalloc` top allocations, since a live snapshot mid-climb would
settle whether this is really glibc arenas or a genuine Python leak.

## New: degraded-service banner (GUI)

Added `GET /api/settings/service-status`
([`api/settings.py`](../../src/api/settings.py)): counts recent
(last 2h) `Download` rows with `status="error"` whose error text matches a
known transient-failure pattern (403/429, "PO Token", "n challenge",
"format is not available", etc.). Three or more within the window flips
`degraded: true`, and the queue page shows a banner with when it was first
noticed.

Deliberately does **not** predict a recovery time ("service expected to be
renewed by HH:MM") — there's no reliable way to know that; it depends on an
upstream fix landing or YouTube ending an A/B test, neither of which is on
a knowable schedule. Promising a specific time would just be fabricated.
"First noticed around HH:MM" is the one honest, useful data point, plus a
count and a suggestion to retry later or resubmit the failed download.

## Follow-up, same day: real YouTube rate limit + two product gaps it exposed

After the quality fix, the three MrDeriv playlists were deleted (92 files,
360p, worth ~47 GB) and resubmitted to get them at full quality. This
tripped a genuine YouTube-side rate limit — *"Your account has been
rate-limited by YouTube for up to an hour"* — almost certainly from the
day's cumulative testing (multiple full playlist runs, in-pod exec tests,
local reproduction runs, all against the same account/IP in a short window).
Across the three re-runs: 23/93 videos saved before the limit started
biting, the remaining 70 skipped.

This wasn't a bug, but it exposed two real product gaps, fixed here instead
of manually chasing the missing 70 videos:

1. **Download order was whatever the platform returned**, not necessarily
   chronological. The Kingdom Come playlist came back newest-first (episode
   14 before episode 1) — so even on a clean run, watching from episode 1
   meant waiting for the entire playlist first. Fixed in
   [`services/downloader.py`](../../src/services/downloader.py)
   (`_ordered_playlist_items`): a cheap flat-metadata pre-pass resolves each
   entry's episode number (reusing the same heuristic already trusted for
   on-disk renaming, `episode_naming.resolve_episode`) and builds an
   explicit yt-dlp `playlist_items` spec in that order. Verified against
   yt-dlp's own `PlaylistEntries.get_requested_items` that it downloads in
   exactly the order given, not re-sorted — so this reuses the existing
   single-download-call flow rather than requiring a bigger restructure.

2. **A failed item just... stayed failed.** `status: "done"` is terminal —
   nothing revisits a skipped entry later, and re-running the same
   playlist URL would re-touch every already-successful entry too (a real,
   separate rough edge — no dedupe check exists before creating a new
   `MediaItem` row for an entry that already has one, which would double up
   the library). Fixed with automatic requeuing: every entry that fails to
   produce a file (or a whole single-video download that fails outright)
   gets its own fresh `Download` row scheduled after a delay (2min / 10min /
   30min for the 1st/2nd/3rd attempt), capped at 3 auto-retries so a
   genuinely broken video doesn't retry forever. Each retry is a normal,
   visible queue entry (`retry_count > 0`, shown as a badge) rather than
   mutating history in place. Verified offline (no network) against a
   throwaway SQLite DB: confirmed the first retry creates a correctly
   incremented row, and that a chain of repeated failures stops at exactly
   3 retries with no further rows.

**Deliberately not done:** manually re-fetching the 70 still-missing
videos from today's incident. The rate limit was still active and
resubmitting immediately would just fail again; the fix that matters is
that *future* failures — including a retry of these same videos, whenever
someone resubmits — now requeue themselves automatically instead of
silently staying gone.
