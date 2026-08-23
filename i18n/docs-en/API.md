> [简体中文](../../docs/API.md) | English

# FarewellRSS Google Reader API Documentation

## Overview

FarewellRSS implements the Google Reader API, using FreshRSS's `greader.php` as the reference implementation. The base path for Google Reader-compatible endpoints is `/reader/api/0/`, and the path for authentication and account management endpoints is `/accounts/`. All endpoints are accessible through the following prefixes:

- `/api` — standard path
- `/api/greader` — FreshRSS compatible
- `/api/greader.php` — FreshRSS compatible

Full paths are annotated for each endpoint below.

> **About output formats**: Most endpoints of the original Google Reader API support both `?output=json` and `?output=xml` output formats. However, FarewellRSS only supports JSON output, consistent with FreshRSS; passing `output=xml` returns `501 Not Implemented`.

---

## Authentication

### ClientLogin

`GET/POST /accounts/ClientLogin`

| Parameter       | Description         |
| --------------- | ------------------- |
| `Email`       | Username            |
| `Passwd`      | Password            |
| `accountType` | Fixed `GOOGLE`    |
| `service`     | Fixed `reader`    |

Returns: `SID=...\nLSID=null\nAuth=...` (`text/plain`)

Authentication uses an HMAC-SHA256 Auth Token, passed via the `Authorization: GoogleLogin auth=` header. The T token is the second half of the Auth token after splitting on `/`.

> **Difference from FreshRSS**: FreshRSS uses SHA1 + salt; FarewellRSS uses HMAC-SHA256 + bcrypt.

### ClientRegister

`GET/POST /accounts/ClientRegister`

| Parameter         | Description     |
| ----------------- | --------------- |
| `Email`         | Username (required) |
| `Passwd`      | Password (required) |
| `friendly_name` | Nickname (optional) |
| `invite_code`   | Invite code (required when `FAREWELL_RSS_INVITE_CODE` is configured) |

Registration is controlled by environment variables: when `FAREWELL_RSS_ALLOW_REGISTER` is false, registration is rejected (403 `RegisterDisabledError`); when allowed, if `FAREWELL_RSS_INVITE_CODE` is configured and `invite_code` does not match, registration is rejected (403 `InvalidInviteCodeError`). See the [environment variable documentation](ENVIRONMENT.md) for details.

> **Extension endpoint**; neither the Google Reader API nor FreshRSS has this endpoint.

### DeleteAccount (POST)

`POST /accounts/DeleteAccount`

| Parameter           | Description              |
| ------------------- | ------------------------ |
| `username`          | Username to delete       |
| `operator_username` | Operator's username      |
| `operator_password` | Operator's password      |

Deletes an account. When a user deletes their own account, `operator_password` is their own password; when an admin deletes another user, `operator_password` is the admin's own password. The last admin cannot be deleted (returns 422). Returns 400 for invalid operator credentials, 403 when a non-admin tries to delete someone else, and 404 when the target user does not exist.

> **Extension endpoint**.

### ChangePassword (POST)

`POST /accounts/ChangePassword`

| Parameter           | Description                    |
| ------------------- | ------------------------------ |
| `username`          | Username whose password to change |
| `new_password`      | New password                   |
| `operator_username` | Operator's username            |
| `operator_password` | Operator's password            |

Changes a password. When a user changes their own password, `operator_password` is the old password; when an admin changes another user's password, `operator_password` is the admin's own password. Returns 400 for invalid operator credentials, 403 when a non-admin tries to change someone else's password, and 404 when the target user does not exist.

> **Extension endpoint**.

### EditProfile (POST)

`POST /accounts/EditProfile`

Requires authentication (`Authorization` header). Modifies the profile of the currently logged-in user.

| Parameter         | Description                              |
| ----------------- | ---------------------------------------- |
| `friendly_name` | Nickname. Omitted or blank → cleared (not preserved) |

> **Extension endpoint**. Currently only the nickname can be modified. Note: the empty-value semantics are "clear it"; to keep the existing nickname, do not call this endpoint.

### SetAdmin (POST)

`POST /accounts/SetAdmin`

| Parameter           | Description                       |
| ------------------- | --------------------------------- |
| `username`          | Target username                   |
| `is_admin`          | `true`/`false`                  |
| `operator_username` | Operator's username (must be admin) |
| `operator_password` | Operator's password               |

Grants/revokes admin status. Only admins can perform this action (403 for non-admins). Revoking the last admin is not allowed (422). Returns 400 for invalid operator credentials and 404 when the target user does not exist.

> **Extension endpoint**.

### CreateUser (POST)

`POST /accounts/CreateUser`

| Parameter           | Description                       |
| ------------------- | --------------------------------- |
| `username`          | New username                      |
| `password`          | Initial password (cannot be empty) |
| `friendly_name`     | Nickname (optional)               |
| `is_admin`          | Whether admin (optional, default false) |
| `operator_username` | Operator's username (must be admin) |
| `operator_password` | Operator's password               |

Admins create users directly; **not subject to** the `FAREWELL_RSS_ALLOW_REGISTER` / `FAREWELL_RSS_INVITE_CODE` restrictions. Returns 201 on success. Returns 400 for an empty password, 403 for non-admins, 409 when the username already exists, and 400 for invalid operator credentials.

> **Extension endpoint**.

### ListUsers (GET)

`GET /accounts/ListUsers`

Requires authentication, admins only (403 for non-admins). Returns:

```json
{"users": [{"username": "...", "friendlyName": "...", "isAdmin": true}]}
```

Soft-deleted users are not included.

> **Extension endpoint**.

---

## Stream

### stream/contents

`GET /reader/api/0/stream/contents/{path}`

| Parameter | Description                          |
| --------- | ------------------------------------ |
| `n`     | Number of entries to return (default 20) |
| `r`     | Sort order: `n`/`d` descending, `o` ascending (by entry effective timestamp + id) |
| `ot`    | Start timestamp (seconds)            |
| `nt`    | End timestamp (seconds)              |
| `c`     | Pagination continuation (32-digit hex: 16 digits timestamp + 16 digits id) |
| `xt`    | Exclude tag                          |
| `it`    | Include only tag                     |
| `type`  | `folder`/`tag`, specifies the type of `user/-/label/{name}`; if omitted, FOLDER takes precedence |

> **Parameter scope**: `type` only applies to the path parameter `{path}` (the stream ID); `it`/`xt` currently only support the three state tags `read`, `unread`, and `starred`, and do not yet support `user/-/label/{name}`.

Supported stream paths:

| path                                     | Description                |
| ---------------------------------------- | -------------------------- |
| `user/-/state/com.google/reading-list` | All entries                |
| `user/-/state/com.google/starred`      | Starred                    |
| `user/-/state/farewell-rss/starred-uncategorized` | Uncategorized starred (extension) |
| `feed/{id}`                            | A single feed              |
| `user/-/label/{name}`                  | Folder/tag (FOLDER takes precedence) |
| `user/-/search/{query}`                | FTS5 full-text search (extension) |

> **About the search stream**: `user/-/search/{query}` uses SQLite FTS5 full-text search, supporting boolean expressions (`python OR go`), phrases (`"hello world"`), and column qualifiers (`title:python`). Search results are sorted by relevance (BM25); the `r` parameter is ignored. Pagination is controlled by `n` (limit) and `c` (continuation = offset in hex), compatible with regular streams.

> **About sorting and pagination**: Entries are sorted by "effective timestamp" (whichever of `published > updated > fetched` applies, in seconds) + auto-increment id; `o` is ascending by time (oldest first), `n`/`d` is descending (newest first). The continuation is a 32-digit hex value whose first 16 digits are the sort timestamp and last 16 digits are the entry id, serving as a compound pagination anchor — when multiple entries share the same second, they are split precisely by id, with no duplicates or omissions. Except for the search stream (see above).
>
> **Differences from Google Reader and FreshRSS**: The continuation uses hex format (the Google Reader standard), while FreshRSS uses decimal; FarewellRSS adopts hex and additionally carries a "timestamp + id" compound anchor. FarewellRSS adds the search stream to support full-text search.

### stream/items/ids

`GET /reader/api/0/stream/items/ids`

Same parameters as above; returns `{"itemRefs": [{"id": "..."}]}`.

### stream/items/contents

`POST /reader/api/0/stream/items/contents`

| Parameter | Description                          |
| --------- | ------------------------------------ |
| `i`     | Item ID (repeatable; supports hex and decimal) |

Item ID parsing is compatible with both the `tag:google.com,2005:reader/item/{hex}` format and plain decimal.

### Categories Output

Each entry returns a `categories` array:

- `user/-/state/com.google/reading-list` — always present
- `user/-/state/com.google/read` — read
- `user/-/state/com.google/starred` — starred
- `user/-/label/{name}` — folder tag (from the subscription's folder_id)
- `user/-/label/{name}` — item tag (from the StarState's tag_id; not deduplicated when it shares a name with a folder)

> **Difference from FreshRSS**: FarewellRSS additionally outputs the entry's TAG labels; a folder and tag with the same name will both appear, and the frontend can distinguish their sources via `origin.streamId` and `subscription/list`.

---

## Subscription

### subscription/list

`GET /reader/api/0/subscription/list?output=json`

Returns `{"subscriptions": [...]}`. Each subscription includes `id`, `title`, `categories`, `url`, `htmlUrl`, and `iconUrl`.

> **Difference from FreshRSS**: FreshRSS additionally outputs `frss:priority`; FarewellRSS does not include it.

### subscription/edit

`POST /reader/api/0/subscription/edit`

| Parameter | Description                              |
| --------- | ---------------------------------------- |
| `ac`    | `subscribe` / `unsubscribe` / `edit` |
| `s`     | Stream ID (repeatable)                   |
| `t`     | Title (repeatable)                       |
| `a`     | Add to tag                               |
| `r`     | Remove from tag                          |

- `subscribe`: the feed part of `s` is a URL
- `unsubscribe` / `edit`: the feed part of `s` is a numeric ID
- When the tag in `a` does not exist, it is automatically created as type FOLDER

### subscription/quickadd

`POST /reader/api/0/subscription/quickadd`

| Parameter    | Description |
| ------------ | ----------- |
| `quickadd` | feed URL    |

### subscription/export

`GET /reader/api/0/subscription/export`

Returns OPML XML, grouped by folder.

### subscription/import

`POST /reader/api/0/subscription/import`

Accepts an OPML XML body, parses it, and imports feeds and folders. Folders are created automatically if they do not exist.

---

## Tag / Label

FarewellRSS's tag system distinguishes between two types:

| Type       | Purpose                              | Association                |
| ---------- | ------------------------------------ | -------------------------- |
| `folder` | Folder, for categorizing feeds       | `Subscription.folder_id` |
| `tag`    | Starred-item category, for organizing starred entries | `StarState.tag_id` |

> **Differences from Google Reader**:
>
> - Google Reader does not distinguish folders from tags; all entries of the feeds under a folder automatically inherit the tag of the same name. In reality, tagging a feed for categorization and tagging an individual entry to remember and classify it serve entirely different purposes; forcibly conflating the two is a fundamentally flawed design.
> - FarewellRSS's TAG is attached to StarState — having a tag implies being starred, and an entry can have only one tag. This is a starred-item categorization design, not a free-form tagging system. Supporting multiple tags per entry would be somewhat cumbersome to implement and is not really necessary.
> - A folder and a tag with the same name can coexist, with FOLDER taking precedence in matching; some endpoints add a `type` parameter to distinguish them precisely.

### tag/list

`GET /reader/api/0/tag/list?output=json`

Returns `{"tags": [...]}`, including the system tags `reading-list`, `starred`, and the user's folders and tags.

> **Difference from FreshRSS**: FreshRSS outputs `unread_count` for TAGs; FarewellRSS currently does not include it. No known client uses this field.

### edit-tag

`POST /reader/api/0/edit-tag`

| Parameter | Description            |
| --------- | ---------------------- |
| `i`     | Item ID (repeatable)   |
| `a`     | Add tag (repeatable)   |
| `r`     | Remove tag (repeatable) |
| `T`     | Token                  |

Supported tag values: `user/-/state/com.google/read`, `starred`, `user/-/label/{name}`.

When the TAG in `a=label/X` does not exist, it is created automatically. `r` operations on non-existent tags are silently ignored.

### enable-tag

`POST /reader/api/0/enable-tag`

| Parameter | Description                                  |
| --------- | -------------------------------------------- |
| `s`     | Tag ID (repeatable)                          |
| `type`  | `folder` or `tag` (repeatable, default `folder`) |

> **Extension endpoint**; allows explicitly specifying the type when creating a tag.

### rename-tag

`POST /reader/api/0/rename-tag`

| Parameter | Description                          |
| --------- | ------------------------------------ |
| `s`     | Old name (repeatable)                |
| `dest`  | New name (repeatable)                |
| `type`  | Type (repeatable, default FOLDER takes precedence) |

> **Extensions**: supports batch renaming (FreshRSS only supports one at a time) and supports swapping same-named tags (using a temporary UUID as an intermediary). `type` is an extension parameter used to precisely match folders and tags with the same name.

### disable-tag

`POST /reader/api/0/disable-tag`

| Parameter | Description                          |
| --------- | ------------------------------------ |
| `s`     | Tag ID (repeatable)                  |
| `type`  | Type (repeatable, default FOLDER takes precedence) |

Deleting a tag automatically clears the associated `folder_id` (Subscription) or `tag_id` (StarState). `type` is an extension parameter used to precisely match folders and tags with the same name.

---

## Misc

### unread-count

`GET /reader/api/0/unread-count?output=json`

Returns four sections:

- `user/-/state/com.google/reading-list` — total
- `feed/{id}` — per feed
- `user/-/label/{name}` — per folder
- `user/-/label/{name}` — per tag

> **Difference from FreshRSS**: when a folder and a tag share the same name, FarewellRSS pins the tag's entry after the folder's in the array, so the frontend can distinguish sources with the same name (folder = unread count of subscribed feeds, tag = unread count of starred entries), instead of letting the tag overwrite the folder.

### mark-all-as-read

`POST /reader/api/0/mark-all-as-read`

| Parameter | Description                              |
| --------- | ---------------------------------------- |
| `s`     | Stream ID                                |
| `ts`    | Item ID (everything before it is marked as read) |
| `type`  | Specifies the label type in `s`: `folder`/`tag`; if omitted, FOLDER takes precedence (extension parameter) |
| `T`     | Token                                    |

Supported `s` values: `feed/{id}`, `user/-/label/{name}`, `reading-list`, `starred`.

The read state is implemented by inserting a `ReadState` record whose timestamp is `None` (batch operations do not enter the reading history). Existing ReadStates are not overwritten.

### token

`GET /reader/api/0/token`

Returns the T token (the second half of the Auth token after splitting on `/`).

### user-info

`GET /reader/api/0/user-info`

Returns `{"userId": ..., "userName": ..., "userProfileId": ..., "userEmail": ..., "isAdmin": ...}`.

---

## Summary of Major Differences from Google Reader / FreshRSS

| Feature                   | Google Reader | FreshRSS            | FarewellRSS          |
| ------------------------- | ------------- | ------------------- | -------------------- |
| Authentication            | Google OAuth  | SHA1 + salt         | HMAC-SHA256 + bcrypt |
| Continuation format       | hex           | decimal             | hex (timestamp + id compound) |
| Tag system                | Flat tags     | Category + Tag separated | Folder + Tag separated |
| Folder/Tag name collision | N/A           | FOLDER shadows TAG  | FOLDER takes precedence; can be specified |
| Batch rename-tag          | Not supported | Not supported       | Supported (including swap) |
| enable-tag                | None          | None                | Supported            |
| ClientRegister            | None          | None                | Supported (configurable registration toggle/invite code) |
| DeleteAccount             | None          | None                | Supported            |
| EditProfile / SetAdmin / CreateUser / ListUsers | None | None   | Supported            |
| OPML export               | N/A           | Supported           | Supported            |
| OPML import               | N/A           | Supported           | Supported            |
| mark-all-as-read read protection | -    | Overwrites read state | Skips already-read entries |
