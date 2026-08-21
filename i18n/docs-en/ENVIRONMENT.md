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
| `FAREWELL_RSS_DATA_DIR` | `data`                  | Data directory (SQLite database, `.env`, etc. all live here). **Read only from OS environment variables**, not from `.env` (because `.env` itself is in this directory). |
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
| `FAREWELL_RSS_FEED_UPDATE_MAX_CONCURRENCY` | `10`                  | Maximum number of feeds fetched concurrently.                                                                   |

## Examples

### Read-only deployment (self-service registration disabled + invite code)

```powershell
$env:FAREWELL_RSS_ALLOW_REGISTER = "false"          # Completely disable self-service registration
$env:FAREWELL_RSS_PORT = "8080"
farewell-rss
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
