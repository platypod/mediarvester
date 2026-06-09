"""
Generate a .shortcut file for iOS that adds the shared URL to mediarvester.

Flow when the user taps Share → mediarvester on iOS:
  1. The Shortcut receives the shared URL (or text containing a URL).
  2. It opens  https://<host>/share?url=<shared-url>  in Safari.
  3. Safari has the Authelia session cookie, so the request is authenticated.
  4. The existing /share SPA page queues the download and redirects to /queue.

This is intentionally simpler than a background API POST.  A fully background
variant (no browser window) would require a per-user API token and an Authelia
bypass rule — a worthwhile follow-up but not needed for the MVP.
"""

import plistlib
from os import environ

from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter(prefix="/api/shortcuts")


def _token_string(prefix: str) -> dict:
    """
    Build a WFTextTokenString where the share-sheet input is appended to *prefix*.

    The Object Replacement Character (U+FFFC) is Shortcuts' placeholder for an
    inline variable token.  The dict key "{offset, 1}" encodes its byte position
    inside the string.
    """
    offset = len(prefix)
    return {
        "Value": {
            "attachmentsByRange": {
                f"{{{offset}, 1}}": {"Type": "ExtensionInput"},
            },
            "string": prefix + "￼",
        },
        "WFSerializationType": "WFTextTokenString",
    }


def _build_shortcut(base_url: str) -> bytes:
    """
    Return the binary plist bytes for the mediarvester iOS Shortcut.

    The shortcut:
      • Appears in the iOS Share sheet (WFSharingExtensionWorkflow).
      • Accepts URLs and plain text (covers Instagram which shares "text + URL").
      • Opens  <base_url>/share?url=<input>  in Safari so the app can queue it.
    """
    share_url_prefix = base_url.rstrip("/") + "/share?url="

    shortcut: dict = {
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionDescription": "iOS 16.4",
        "WFWorkflowTypes": ["WFSharingExtensionWorkflow"],
        "WFWorkflowInputContentItemClasses": [
            "WFURLContentItem",
            "WFStringContentItem",
        ],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowIcon": {
            # Blue background, download-arrow glyph
            "WFWorkflowIconStartColor": 431817727,
            "WFWorkflowIconGlyphNumber": 59511,
        },
        "WFWorkflowActions": [
            {
                # Open the /share page in Safari; auth is handled by the existing
                # Authelia session cookie in the browser.
                "WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
                "WFWorkflowActionParameters": {
                    "WFInput": _token_string(share_url_prefix),
                },
            },
        ],
    }
    return plistlib.dumps(shortcut, fmt=plistlib.FMT_BINARY)


@router.get("/mediarvester.shortcut", include_in_schema=False)
async def download_shortcut(request: Request):
    """
    Serve a dynamically generated iOS Shortcut pre-configured for this server.

    The PUBLIC_URL env var overrides the Host header (useful behind a reverse
    proxy where the internal hostname differs from the public one).
    """
    base_url = environ.get("PUBLIC_URL") or str(request.base_url)
    data = _build_shortcut(base_url)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": 'attachment; filename="mediarvester.shortcut"',
            # Prevent the service worker from caching this — it must always
            # reflect the current server URL.
            "Cache-Control": "no-store",
        },
    )
