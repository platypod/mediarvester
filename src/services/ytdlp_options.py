"""yt-dlp options shared by every call that can touch YouTube.

**No player_client is pinned, on purpose.** yt-dlp's maintainers re-tune the
default client set as YouTube changes (2026.08.19 added visionos and dropped
android_vr; 2026.10.26 uses tv_samsung/visionos/web anonymously and
web_embedded/tv_samsung/web with logged-in cookies), and the set already
spreads the risk across several clients. Every pin this project shipped broke
within weeks while the defaults kept working:

- mweb (pinned 2026-08-19): 403 on the first data request for some videos
  from 2026-11-17, and "formats missing a URL" (SABR) for others.
- "tv_embedded" (v1.15.4): not a client at all -- yt-dlp skipped it with a
  warning and fell back to its defaults, which is why v1.15.4 worked.
- web_embedded (v1.15.5): refused embed-restricted videos ("blocked it from
  display on this website or application", eSO64xGEAow) and hit SABR-only
  formats on others (smspqpgDKQo). The defaults downloaded both, with and
  without cookies, in the prod pod on 2026-11-21.

Keeping yt-dlp itself current is what keeps the defaults right.
"""

# Use Node.js for YouTube's n-challenge (requires yt-dlp-ejs + Node 22+).
# yt-dlp defaults to deno only, so node must be enabled explicitly.
JS_RUNTIMES = {"node": {}}


def youtube_opts(player_client: list[str] | None = None) -> dict:
    """Base options for a YoutubeDL that may extract from YouTube.

    `player_client` is only for an explicit retry override; leave it None to
    let yt-dlp choose. Returns a fresh dict each call so callers can mutate it.
    """
    opts: dict = {"js_runtimes": dict(JS_RUNTIMES)}
    if player_client:
        opts["extractor_args"] = {"youtube": {"player_client": list(player_client)}}
    return opts
