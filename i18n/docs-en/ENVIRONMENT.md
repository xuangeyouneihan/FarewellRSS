> [简体中文](../../docs/ENVIRONMENT.md) | English

# Environment Variables

All FarewellRSS configuration is done through environment variables. All variables start with `FAREWELL_RSS_`.

## Configuration Sources and Precedence

```
OS environment variables  >  .env file in the data directory
```

- **OS environment variables take precedence**: Environment variables that already exist when the process starts override entries with the same name in `.env`.
- **Automatic `.env` cleanup**: On startup, all entries in `.env` that have been overridden by OS environment variables are removed (to avoid stale values lingering and causing confusion).
- **`.env` location**: `{FAREWELL_RSS_DATA_DIR}/.env`. Suitable for storing secrets and other values you don't want on the command line.

## Variable Reference

| Variable                  | Default                   | Description                                                                                                                                                  |
| ------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `FAREWELL_RSS_DATA_DIR` | `~/.local/share/farewell-rss` | Data directory (SQLite database, `.env`, etc. all live here). `~` expands to the current user's home directory. **Read only from OS environment variables**, not from `.env` (because `.env` itself is in this directory). |
| `FAREWELL_RSS_SECRET`   | Auto-generated on startup | HMAC key used to sign Auth tokens. **If absent, a random value is automatically generated and written to `.env`** — no manual configuration needed. Changing it immediately invalidates the tokens of all logged-in users. |
| `FAREWELL_RSS_HOST`     | `0.0.0.0`               | Listen address.                                                                                                                                              |
| `FAREWELL_RSS_PORT`     | `3000`                  | Listen port. Under Docker this is the **container-internal** port; external access is mapped via `docker run -p host:container` (or compose `ports`), and the two must match. |

### Registration Control

| Variable                        | Default      | Description                                                                                                                                        |
| ------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FAREWELL_RSS_ALLOW_REGISTER` | Allowed      | Whether self-service registration is open. When falsy (see below), `ClientRegister` returns 403. **Does not affect** the admin's `CreateUser` (admins can add users without restriction). |
| `FAREWELL_RSS_INVITE_CODE`    | Not set      | Invite code. When set, self-service registration must provide a matching `invite_code` (mismatch returns 403). When not set, no invite code is required. Only takes effect when registration is allowed. |

> **First-user exception**: Registration control only takes effect **after a user already exists**. When the database is empty, the first user to register (who automatically becomes the admin) ignores `ALLOW_REGISTER` and `INVITE_CODE` — otherwise disabling registration would make it impossible to ever create a user.

**Truthiness of `FAREWELL_RSS_ALLOW_REGISTER`**:

- **Not set** → registration allowed
- Set to `1` / `true` / `yes` / `on` (case-insensitive, leading/trailing whitespace ignored) → allowed
- Set to any other value (e.g. `false` / `0` / `no`) → **denied**

### Feed Fetching

| Variable                                     | Default                 | Description                                                                                                     |
| -------------------------------------------- | ----------------------- | --------------------------------------------------------------------------------------------------------------- |
| `FAREWELL_RSS_FEED_REFRESH_INTERVAL`       | `900` (15 minutes)    | Interval (seconds) at which the scheduler refreshes all feeds.                                                  |
| `FAREWELL_RSS_FEED_DEFAULT_TTL`            | `3600` (1 hour)       | Default TTL (seconds) used when a feed does not declare a TTL. Feeds within their TTL are skipped during fetch. |
| `FAREWELL_RSS_FEED_MIN_TTL`                | `900` (15 minutes)    | TTL floor (seconds). When a feed declares a TTL below this value, this value is used instead, to prevent overly frequent fetching. |
| `FAREWELL_RSS_FEED_FULL_REFRESH_INTERVAL`  | `86400` (1 day)       | At most this often, a feed is fetched **without conditional headers** (a full fetch), to recover from a "304 but the content actually changed". |
| `FAREWELL_RSS_FEED_UPDATE_MAX_CONCURRENCY` | `10`                  | Maximum number of feeds fetched concurrently.                                                                   |
| `FAREWELL_RSS_FEED_FETCH_TIMEOUT`          | `30`                  | Time limit (seconds) for a single fetch: a hard cap on the whole fetch (connect + redirects + reading the body), on top of a separate 10-second limit for connecting and for each read. |

- A 304 does **not** guarantee the content is unchanged: a weak ETag only promises semantic equivalence (not identical bytes); `Last-Modified` has one-second resolution (change the content within the same second and a server will still answer 304); a CDN / reverse proxy may compare against a stale object. Hit any of those and the feed silently stops updating forever (all you get is a "not modified" line in the log). Periodically dropping the conditional headers is the only mitigation that covers all of these — at the cost of one extra full download per feed per day.
- The fetch timeout is **not optional** — don't set it to `0`: that makes the `asyncio.timeout(0)` deadline "right now", so every fetch fails immediately. It exists for a concrete reason: this code used to hand the URL to feedparser, which goes through urllib without a timeout, and sockets default to *never* timing out — so one feed that connects but never answers holds a thread forever. The whole refresh round then never returns, and the scheduler's `try/except` catches exceptions but not hangs: **the instance stops refreshing for good** (observed in production: one round never finished, then zero ticks for 8 hours while the web UI stayed up). For reference: SimplePie defaults to 10 seconds, FreshRSS to 20.

### Password Hashing

| Variable                                 | Default                | Description                                                                                                                                                                    |
| ---------------------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `FAREWELL_RSS_PASSWORD_MAX_CONCURRENCY` | `min(4, CPU cores)`   | Size of the thread pool used for bcrypt password hashing / verification. Password operations are expensive (~0.2s each) and blocking, so they run in a dedicated pool to avoid stalling the event loop. bcrypt releases the GIL, so the pool achieves true parallelism (measured: 4 concurrent hashes ≈ 4x speedup). |

- This pool is **dedicated** to password operations and is not shared with other work such as feed fetching (otherwise fetching would fill the pool and login requests would have to queue behind it).
- Lower → less concurrency for password operations, which adds a layer of rate limiting against brute force; higher → less queueing under concurrent logins, with throughput scaling up to about the CPU core count (more threads than cores only contend for CPU, with no extra gain).
- Must be an integer of at least 1, otherwise startup fails (the variable is read at process startup, so a restart is required after changing it).

## Examples

### Read-only deployment (self-service registration disabled + invite code)

```powershell
$env:FAREWELL_RSS_ALLOW_REGISTER = "false"          # Completely disable self-service registration
$env:FAREWELL_RSS_PORT = "8080"
farewell-rss                                       # short alias fwrss / python -m farewell_rss also work
```

```powershell
$env:FAREWELL_RSS_ALLOW_REGISTER = "true"
$env:FAREWELL_RSS_INVITE_CODE = "my-secret-code"    # Register with an invite code
farewell-rss
```

### Docker / Production

```bash
docker run -e FAREWELL_RSS_DATA_DIR=/data \
           -e FAREWELL_RSS_PORT=3000 \
           -e FAREWELL_RSS_ALLOW_REGISTER=false \
           -v farewell-rss-data:/data \
           farewell-rss
```

## Notes

- **The secret (`FAREWELL_RSS_SECRET`) generally needs no attention**: It is auto-generated on first startup and persisted to `.env`, staying consistent across restarts. You only need to change it if you want to force all users to log in again.
- **Admin-created users are unaffected by registration control**: Even with `FAREWELL_RSS_ALLOW_REGISTER=false` and an invite code configured, admins can still add users directly via `POST /accounts/CreateUser`. See the [API documentation](API.md) for details.

## Credentials and data-directory permissions

**Credentials embedded in a URL (`https://user:password@host/feed`) are stored in `feeds.href` as plain text.** That is deliberate:

- The URL is both *the identity of the feed* (subscriptions are deduplicated by an **exact** href match: without the full URL you cannot match that row) and *the capability needed to fetch it*.
- The GReader API offers URL as the only channel for credentials (`s=feed/<URL>` on `subscription/edit`, `quickadd`, OPML's `xmlUrl`) — and that is the only way clients send them.

**What is guaranteed:**

- Credentials **never reach the logs**: every log line replaces the userinfo with `***` (`https://***@host/feed`).
- Credentials are **never echoed to other users**: `subscription/list` only returns the caller's own subscriptions, and subscribing requires an exact URL match — guessing the address cannot hit that row.
- Nobody can piggyback on them: the same href with different credentials is **two** feed rows, fetched and isolated separately.

**What is not guaranteed:**

- To fetch feeds for you in the background, the server must be able to recover the plain text — so **the owner of a self-hosted instance (the admin) can, in principle, read the credentials in the database**: by reading the DB file, by reading process memory, or by resetting a user's password and then calling `subscription/list` as that user to obtain the full URL. This is not an implementation defect; it is a consequence of "the server fetches for you".
- If you do not want the server to see the credentials at all, keep them out of it: use a token URL provided by the site, or run a proxy such as rss-bridge on your own side.

**Transport (a different question from storage above):**

- **This service → the feed's server**: with an `https://` feed the `Authorization` header travels inside TLS; with an `http://` feed it goes over the wire **in plain text** (inherent to Basic auth: `user:pass` must be placed in a request header, and there is no "handshake first, then encrypt the credentials" mechanism — any device on the network can read it). A WARNING is logged the first time such a feed is fetched, but it is **not** rejected — LAN feeds like `http://192.168.x.x/feed` with Basic auth are a common setup.
- **Client → this service**: credentials travel in the POST body (not in the URL path or query string), so they do **not** end up in a reverse proxy's access log (which logs the request line by default). The whole tunnel **must be HTTPS** though: this service itself only listens on plain HTTP and relies on the proxy for TLS. With Cloudflare, make sure the SSL mode is `Full` / `Full (strict)` and **never `Flexible`** (Flexible leaves the Cloudflare-to-origin hop in plain text, exposing the credentials).

**So permissions are tightened at startup (via `chmod` on POSIX and `icacls` on Windows):**

| Object | Mode | Why |
| --- | --- | --- |
| Data directory | `0700` (Windows: a private ACL inherited by children) | Nothing inside is for anyone else |
| `farewell_rss.db` (plus `-wal` / `-shm`) | `0600` | Contains plain-text credentials and every article |
| `.env` | `0600` | Contains the HMAC key for auth tokens |

Two notes for Windows:

- The default location (under `%USERPROFILE%`) already inherits only `SYSTEM / Administrators / the current user`, so other *standard* local accounts cannot read it. But if you point `FAREWELL_RSS_DATA_DIR` at a shared place (a drive root, a folder granted to `Everyone`, a NAS), the inherited ACL may include `Users` / `Everyone` — so at startup the ACL of the directory and of the database/`.env` is reduced to "current account + `SYSTEM` + `Administrators`" (built-in accounts are written as SIDs, so localized names cannot mismatch). This drops permissions inherited from the parent, including ones an administrator added deliberately (a backup account, say) — if that is your setup, look for the `已把 … 收成…` line in the log.
- ACLs only exist on NTFS; on FAT/exFAT `icacls` fails, which only produces a warning and never blocks startup.

> **Do not share or back up `farewell_rss.db` on its own** (for example by pasting it while debugging) — it holds plain-text credentials and everything you have read. Share an empty or edited copy instead.
