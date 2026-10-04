# 2026-10-04: YouTube videos silently downloaded at 360p (third occurrence)

Downloads "succeeded" but landed at 640x360 even though the video offers up
to 1080p60. Nothing failed, nothing was retried, nothing was flagged.

## Impact

- Prod `media_item` rows at 360p since the 2026-09-18 fix: **2**
  (id 1086 `psO0o00ZsG4`, 2026-09-25 09:33 UTC; id 1087 `Va3JtRKmeJ0`,
  2026-09-27 15:34 UTC). Everything else since 2026-08-31 is 720p-1440p.
- Earlier occurrences of the same failure: 2026-08-21/22 (~95 videos, the
  v1.6.1 forced-progressive stopgap) and 2026-09-18 (8 videos, plugin 2.0.0
  vs sidecar 1.3.2 mismatch).
- Not checked: the local cluster's database.

## Root cause

yt-dlp's default selector is `bv*+ba/b`. The trailing `/b` means "if no
video-only + audio-only pair is available, take the best single muxed file".
On YouTube that file is itag 18, capped at 360p.

With the `mweb` client, every adaptive format needs a GVS PO Token. If the
token can't be obtained at extraction time, yt-dlp logs one warning and
**drops all adaptive formats from the list**:

```
WARNING: [youtube] <id>: mweb client https formats require a GVS PO Token
which was not provided. They will be skipped as they may yield HTTP Error 403.
```

Only itag 18 is left, `/b` selects it, the download completes, and
`_build_media_item` records it as a success. A transient PO-token failure
therefore becomes a permanent 360p file. This is the same mechanism behind
all three occurrences; only the reason the token was missing differed.

### Reproduction (read-only, inside the prod pod, 2026-10-04)

Same video as id 1086, `yt-dlp -F` / `-s`, mweb client:

| Provider | Formats listed | `bv*+ba/b` picks | `bv*+ba` picks |
|---|---|---|---|
| sidecar reachable (normal) | 144p ... 1080p60 adaptive + itag 18 | 1080p60 | 1080p60 |
| provider URL pointed at a dead port | **itag 18 only** | **18 (360p), silently** | `Requested format is not available` |

So the video had full quality available; the 360p was purely a selection
fallback.

### What we could not establish

Why the token fetch failed on 09-25 and 09-27. The pod's container logs had
rotated past those dates (oldest line 2026-09-29) and Loki does not ingest
mediarvester's pod logs. Sidecar (1.3.2) and plugin (1.3.2) versions match
now, and the sidecar re-mints its integrity token every 12h ("POT minter
expired, getting a new one"), a plausible window for a failed request, but
that is unconfirmed.

## Why it wasn't caught

1. The `/b` fallback turns a recoverable error into a success.
2. The `/api/settings/service-status` banner only counts failed downloads.
3. The 2026-08-20 incident note promised "a future site-side issue surfaces as
   a visible failure ... rather than silently degrading". It didn't, because
   removing the format override restored the default, and the default has
   the fallback in it.
4. No pod logs outside the container's rotating buffer.

## Fix

`services/downloader.py`: for YouTube URLs the format is now `bv*+ba`, with
no muxed fallback (`_format_for`). If adaptive formats are missing, the item
fails with `Requested format is not available (adaptive formats skipped: ...
PO Token ...)` and goes through the existing auto-retry schedule (2 min, 30
min, 3 h, 1 d), by which time the sidecar has recovered. It also shows up in
the Queue error column and feeds the degraded-service banner. Other
platforms (X, Instagram, ...) keep `bv*+ba/b`, since muxed-only is normal
there. Tests: `tests/test_format_selection.py`.

Trade-off: a YouTube video that genuinely has no adaptive formats would now
fail rather than download at low quality. YouTube publishes adaptive formats
for everything we've seen, so this is accepted; after the retries are
exhausted the item stays in error for a human to decide.

## Follow-ups

- [ ] Re-download ids 1086 and 1087 once the fix is deployed.
- [ ] Ship mediarvester (and sidecar) pod logs to Loki so the next one is
      diagnosable after a rotation.
- [ ] Consider a gauge / alert on `media_item.height <= 360` for YouTube.
- [ ] Check the local cluster for 360p rows.
