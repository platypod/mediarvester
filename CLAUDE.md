# mediarvester

Self-hosted media downloader with a web GUI. Supports any platform yt-dlp handles
(YouTube, Instagram, TikTok, X/Twitter, …). Users can paste a URL for a one-off
download or follow a creator/channel/playlist to automatically harvest new content.

---

## Stack

| Layer | Choice | Reason |
|-------|--------|--------|
| Download engine | yt-dlp | Handles 1000+ platforms; cookie auth; progress hooks |
| Backend | FastAPI | Async, clean REST |
| Database | SQLite (dev) / PostgreSQL (prod) via SQLAlchemy 2.0 async | Portable; same ORM for both |
| Background jobs | APScheduler | Embedded scheduler; no broker needed |
| Frontend | Vue 3 + TypeScript + Vite | Explicit SFCs, typed, large training corpus |
| State (frontend) | Pinia | Simple, typed, Vue-native |
| Container | Docker (multi-arch) | Already in place; ARM64 for home server |

The YouTube Data API (`google-api-python-client`) is **removed**. yt-dlp resolves
channel/playlist URLs natively; a separate Google API key is not needed.

### Database portability

SQLAlchemy async is used throughout (`asyncpg` driver for Postgres, `aiosqlite` for SQLite).
The target production database is the shared PostgreSQL instance in the `platypod/stack` media
layer. The connection string is set via `DATABASE_URL`; swapping from SQLite to Postgres requires
no code changes.

---

## Authentication & multi-user

mediarvester has **no built-in auth**. It is designed to run behind Traefik + Authelia, which
handles OIDC and forwards the authenticated user's identity as an HTTP header on every request.

- The header name is configurable via `AUTH_HEADER` (default: `Remote-User`).
- If the header is absent (unauthenticated / direct access), the request is attributed to
  `DEFAULT_USER` (default: `anonymous`). This keeps the app fully functional without Authelia
  for local/dev use.
- The backend trusts the forwarded header completely — no signature verification needed because
  Authelia guarantees no unauthenticated request reaches the app.

All `Download`, `Source`, and `MediaItem` rows carry an `owner` field (the forwarded user ID).
Every query filters by `owner`, so users only see their own data.

---

## Cookies

Each user can upload a Netscape-format cookies file via the Settings page. Cookies are stored
at `{COOKIES_ROOT}/{user}.txt` and used automatically by yt-dlp for that user's downloads.

- `COOKIES_ROOT` is an env var (default: `/app/cookies`) — mount it as a volume in Kubernetes
  so cookies survive pod crashes.
- A global fallback `YT_DLP_COOKIES_PATH` is still supported for single-user / legacy setups;
  per-user cookies take priority when present.
- The Settings page shows when cookies were last uploaded and provides a drag-and-drop zone
  to refresh them.

---

## Core domain concepts

| Concept | Description |
|---------|-------------|
| **Download** | A one-off job tied to a single URL. Lifecycle: `queued → downloading → done / error`. |
| **Source** | A followed creator, channel, or playlist URL. Polled on a schedule; spawns Downloads for new items. |
| **MediaItem** | A file that has been successfully downloaded, with metadata (title, platform, source URL, local path). |

---

## Repository layout

```
src/                          # Python backend
  main.py                     # FastAPI app factory; mounts router; starts scheduler
  db.py                       # SQLAlchemy models, engine, async session dependency
  api/
    deps.py                   # Shared FastAPI dependencies (get_current_user)
    downloads.py              # POST /api/downloads, GET /api/downloads, DELETE /api/downloads/{id}
    sources.py                # POST /api/sources, GET /api/sources, DELETE /api/sources/{id}, PATCH /api/sources/{id}
    media.py                  # GET /api/media, DELETE /api/media/{id}
    settings.py               # GET /api/settings/me, GET+POST /api/settings/cookies
  services/
    downloader.py             # yt-dlp wrapper; ThreadPoolExecutor; progress hooks → DB
    poller.py                 # APScheduler jobs; one per enabled Source

frontend/                     # Vue 3 + TypeScript + Vite
  index.html
  vite.config.ts
  src/
    main.ts
    App.vue                   # root: router-view + sidebar nav (shows current user)
    stores/
      downloads.ts            # Pinia: download list + SSE progress updates
      sources.ts              # Pinia: followed sources
      media.ts                # Pinia: media library
      settings.ts             # Pinia: current user + cookies status
    pages/
      QueuePage.vue           # /queue  — live download queue with progress bars
      LibraryPage.vue         # /       — responsive card grid of MediaItems
      SourcesPage.vue         # /sources — manage followed creators/playlists
      SettingsPage.vue        # /settings — cookies upload, current user info
    components/
      DownloadCard.vue
      MediaCard.vue
      SourceCard.vue
      AddDownloadDialog.vue
      AddSourceDialog.vue
      CookiesDropZone.vue     # drag-and-drop / click-to-upload cookies file
```

---

## Data models (SQLAlchemy 2.0)

```python
class Download(Base):
    __tablename__ = "download"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str]
    title: Mapped[str | None]
    platform: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="queued")  # queued|downloading|done|error
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None]
    owner: Mapped[str] = mapped_column(default="anonymous", server_default="anonymous")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    finished_at: Mapped[datetime | None]

class Source(Base):
    __tablename__ = "source"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str]
    label: Mapped[str | None]
    platform: Mapped[str | None]
    enabled: Mapped[bool] = mapped_column(default=True)
    poll_interval_minutes: Mapped[int] = mapped_column(default=60)
    owner: Mapped[str] = mapped_column(default="anonymous", server_default="anonymous")
    last_polled_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

class MediaItem(Base):
    __tablename__ = "media_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    platform: Mapped[str | None]
    source_url: Mapped[str]
    local_path: Mapped[str]
    thumbnail_path: Mapped[str | None]
    duration_seconds: Mapped[int | None]
    owner: Mapped[str] = mapped_column(default="anonymous", server_default="anonymous")
    download_id: Mapped[int] = mapped_column(ForeignKey("download.id"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
```

---

## Key implementation notes

### User identity (`api/deps.py`)

```python
AUTH_HEADER  = environ.get("AUTH_HEADER",  "Remote-User")
DEFAULT_USER = environ.get("DEFAULT_USER", "anonymous")

def get_current_user(request: Request) -> str:
    return request.headers.get(AUTH_HEADER, DEFAULT_USER)
```

### yt-dlp wrapper (`services/downloader.py`)

- Run each download in a `ThreadPoolExecutor` (max workers = `DOWNLOAD_CONCURRENCY`, default 2).
- `enqueue(download_id, url, owner)` — owner is used to resolve the cookies file.
- Cookies resolution order: `{COOKIES_ROOT}/{owner}.txt` → `YT_DLP_COOKIES_PATH` → no cookies.
- Output template: `{MEDIA_ROOT}/{platform}/{uploader}/{title}.%(ext)s`.
- On finish, read yt-dlp's `.info.json` sidecar and upsert a `MediaItem` row (with `owner`).

### Cookies upload (`api/settings.py`)

- `POST /api/settings/cookies` — accepts a multipart file upload, writes to
  `{COOKIES_ROOT}/{owner}.txt`.
- `GET /api/settings/cookies` — returns `{ has_cookies, uploaded_at }` for the current user.
- `GET /api/settings/me` — returns `{ user }` so the frontend can display who is logged in.

### Poller (`services/poller.py`)

- On startup, register one APScheduler job per enabled Source.
- Each job calls yt-dlp `extract_info(download=False)` to list available items, then
  skips URLs already present in `Download` or `MediaItem`, and enqueues the rest.
- Downloads spawned by the poller inherit the Source's `owner`.
- When a Source's `poll_interval_minutes` is patched via the API, the job is rescheduled.

### Frontend ↔ backend during development

Vite proxies `/api/*` and `/media-files/*` to `http://localhost:8080`.
In production, FastAPI serves the built `frontend/dist/` as static files under `/`.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/mediarvester.db` | SQLAlchemy async connection string |
| `MEDIA_ROOT` | `/app/downloads` | Where media files are written |
| `COOKIES_ROOT` | `/app/cookies` | Per-user cookies files (`{user}.txt`) — mount as volume |
| `AUTH_HEADER` | `Remote-User` | HTTP header forwarded by Authelia with the user identity |
| `DEFAULT_USER` | `anonymous` | User identity when the auth header is absent |
| `DOWNLOAD_CONCURRENCY` | `2` | Max parallel yt-dlp processes |
| `YT_DLP_COOKIES_PATH` | _(empty)_ | Global fallback cookies file (legacy / single-user) |
| `YT_DLP_USERNAME` | _(empty)_ | Optional account username |
| `YT_DLP_PASSWORD` | _(empty)_ | Optional account password |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `UVICORN_HOST` | `0.0.0.0` | |
| `UVICORN_PORT` | `8080` | |

---

## What is intentionally out of scope

- Built-in auth / login UI (handled entirely by Traefik + Authelia).
- Transcoding / re-encoding (yt-dlp post-processors handle remuxing if needed).
- A separate message broker (APScheduler embedded is sufficient).
