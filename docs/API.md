# 告别 RSS Google Reader API 文档

> 简体中文 | [English](../i18n/docs-en/API.md)

## 概述

告别 RSS 实现了 Google Reader API，基于 FreshRSS 的 `greader.php` 作为参考实现。API 的 Google Reader 兼容端点基础路径为 `/reader/api/0/`，认证及账户管理端点路径为 `/accounts/`。通过以下前缀均可访问：

- `/api` — 标准路径
- `/api/greader` — FreshRSS 兼容
- `/api/greader.php` — FreshRSS 兼容

以下各端点标注了完整路径。

> **关于输出格式**：原版 Google Reader API 多数端点支持 `?output=json` 和 `?output=xml` 两种输出格式。但告别 RSS 仅支持 JSON 输出，与 FreshRSS 一致，传入 `output=xml` 将返回 `501 Not Implemented`。

---

## 认证

### ClientLogin

`GET/POST /accounts/ClientLogin`

| 参数            | 说明           |
| --------------- | -------------- |
| `Email`       | 用户名         |
| `Passwd`      | 密码           |
| `accountType` | 固定`GOOGLE` |
| `service`     | 固定`reader` |

返回：`SID=...\nLSID=null\nAuth=...`（`text/plain`）

认证方式为 HMAC-SHA256 Auth Token，通过 `Authorization: GoogleLogin auth=` 头传递。T token 为 Auth token 的 `/` 分隔后半部分。

> **与 FreshRSS 的差异**：FreshRSS 使用 SHA1 + salt，告别 RSS 使用 HMAC-SHA256 + bcrypt。

### ClientRegister

`GET/POST /accounts/ClientRegister`

| 参数              | 说明           |
| ----------------- | -------------- |
| `Email`         | 用户名（必填） |
| `Passwd`        | 密码（必填）   |
| `friendly_name` | 昵称（可选）   |
| `invite_code`   | 邀请码（配置了 `FAREWELL_RSS_INVITE_CODE` 时必填） |

注册受环境变量控制：`FAREWELL_RSS_ALLOW_REGISTER` 为假时拒绝（403 `RegisterDisabledError`）；允许时若配置了 `FAREWELL_RSS_INVITE_CODE`，`invite_code` 不匹配则拒绝（403 `InvalidInviteCodeError`）。详见[环境变量文档](ENVIRONMENT.md)。

> **扩展端点**，Google Reader API 和 FreshRSS 均无此端点。

### DeleteAccount (POST)

`POST /accounts/DeleteAccount`

| 参数                | 说明           |
| ------------------- | -------------- |
| `username`          | 要删除的用户名 |
| `operator_username` | 操作者用户名   |
| `operator_password` | 操作者密码     |

删除账户。本人删除自己时，`operator_password` 为自己的密码；管理员删除其他用户时，`operator_password` 为管理员自己的密码。最后一个管理员不可删除（返回 422）。操作者凭证错误返回 400，非管理员删除他人返回 403，目标用户不存在返回 404。

> **扩展端点**。

### ChangePassword (POST)

`POST /accounts/ChangePassword`

| 参数                | 说明               |
| ------------------- | ------------------ |
| `username`          | 要修改密码的用户名 |
| `new_password`      | 新密码             |
| `operator_username` | 操作者用户名       |
| `operator_password` | 操作者密码         |

修改密码。本人修改自己时，`operator_password` 为旧密码；管理员修改其他用户时，`operator_password` 为管理员自己的密码。操作者凭证错误返回 400，非管理员修改他人返回 403，目标用户不存在返回 404。

> **扩展端点**。

### EditProfile (POST)

`POST /accounts/EditProfile`

需认证（`Authorization` 头），修改当前登录用户的个人资料。

| 参数              | 说明                                   |
| ----------------- | -------------------------------------- |
| `friendly_name` | 昵称。缺省或空白 → 置空（不是保留原值） |

> **扩展端点**。当前仅支持修改昵称。注意：空值语义是「置空」，要保留原昵称请不要调用本端点。

### SetAdmin (POST)

`POST /accounts/SetAdmin`

| 参数                | 说明                     |
| ------------------- | ------------------------ |
| `username`          | 目标用户名               |
| `is_admin`          | `true`/`false`         |
| `operator_username` | 操作者用户名（须管理员） |
| `operator_password` | 操作者密码               |

设置/取消管理员。仅管理员可操作（非管理员 403）。不允许取消最后一个管理员（422）。操作者凭证错误返回 400，目标用户不存在返回 404。

> **扩展端点**。

### CreateUser (POST)

`POST /accounts/CreateUser`

| 参数                | 说明                     |
| ------------------- | ------------------------ |
| `username`          | 新用户名                 |
| `password`          | 初始密码（不能为空）     |
| `friendly_name`     | 昵称（可选）             |
| `is_admin`          | 是否管理员（可选，默认 false） |
| `operator_username` | 操作者用户名（须管理员） |
| `operator_password` | 操作者密码               |

管理员直接创建用户，**不受** `FAREWELL_RSS_ALLOW_REGISTER` / `FAREWELL_RSS_INVITE_CODE` 限制。成功返回 201。空密码 400，非管理员 403，用户名已存在 409，操作者凭证错误 400。

> **扩展端点**。

### ListUsers (GET)

`GET /accounts/ListUsers`

需认证，仅管理员（非管理员 403）。返回：

```json
{"users": [{"username": "...", "friendlyName": "...", "isAdmin": true}]}
```

不含已删除（软删除）的用户。

> **扩展端点**。

---

## 流（Stream）

### stream/contents

`GET /reader/api/0/stream/contents/{path}`

| 参数   | 说明                               |
| ------ | ---------------------------------- |
| `n`  | 返回条目数（默认 20）              |
| `r`  | 排序：`n`/`d` 降序，`o` 升序（按条目有效时间戳 + id） |
| `ot` | 起始时间戳（秒）                   |
| `nt` | 结束时间戳（秒）                   |
| `c`  | 分页 continuation（32 位 hex：16 位时间戳 + 16 位 id） |
| `xt` | 排除标签                           |
| `it` | 仅含标签                           |
| `type` | `folder`/`tag`，指定 `user/-/label/{name}` 的类型，不传 = FOLDER 优先 |

> **参数作用域**：`type` 仅对路径参数 `{path}`（流 ID）起效；`it`/`xt` 目前只支持 `read`、`unread`、`starred` 三个状态标签，尚不支持 `user/-/label/{name}`。

支持的流路径：

| path                                     | 说明                       |
| ---------------------------------------- | -------------------------- |
| `user/-/state/com.google/reading-list` | 全部                       |
| `user/-/state/com.google/starred`      | 已收藏                     |
| `user/-/state/com.google/read`         | 已读                       |
| `user/-/state/com.google/unread`       | 未读                       |
| `user/-/state/farewell-rss/starred-uncategorized` | 未分类收藏（扩展）     |
| `feed/{id}`                            | 单个订阅源                 |
| `user/-/label/{name}`                  | 文件夹/标签（FOLDER 优先） |
| `user/-/search/{query}`                | FTS5 全文搜索（扩展）      |

> **搜索流说明**：`user/-/search/{query}` 使用 SQLite FTS5 全文搜索，支持布尔表达式（`python OR go`）、短语（`"hello world"`）、列限定（`title:python`）。搜索结果按相关性（BM25）排序，`r` 参数被忽略。分页通过 `n`（limit）和 `c`（continuation = offset 的 hex）控制，与普通流兼容。

> **排序与分页说明**：条目按「有效时间戳」（`published > updated > fetched` 取其一，秒级）+ 自增 id 排序；`o` 为时间升序（最旧在前），`n`/`d` 为降序（最新在前）。continuation 为 32 位 hex，前 16 位是排序时间戳、后 16 位是条目 id，作为复合分页锚点——同一秒有多条时按 id 精确切分，不重不漏。搜索流除外（见下）。
>
> **与 Google Reader 和 FreshRSS 的差异**：continuation 使用 hex 格式（Google Reader 标准），FreshRSS 使用十进制；告别 RSS 采用 hex，并额外携带「时间戳 + id」复合锚点。告别 RSS 新增搜索流以支持全文搜索。

### stream/items/ids

`GET /reader/api/0/stream/items/ids`

参数同上，返回 `{"itemRefs": [{"id": "..."}]}`。

### stream/items/contents

`POST /reader/api/0/stream/items/contents`

| 参数  | 说明                                 |
| ----- | ------------------------------------ |
| `i` | 条目 ID（可重复，支持 hex 和十进制） |

条目 ID 解析兼容 `tag:google.com,2005:reader/item/{hex}` 和纯十进制两种格式。

### Categories 输出

每个条目返回 `categories` 数组：

- `user/-/state/com.google/reading-list` — 始终存在
- `user/-/state/com.google/read` — 已读
- `user/-/state/com.google/starred` — 已收藏
- `user/-/label/{name}` — 文件夹标签（来自订阅的 folder_id）
- `user/-/label/{name}` — 条目标签（来自 StarState 的 tag_id，与文件夹同名时不作去重）

> **与 FreshRSS 的差异**：告别 RSS 额外输出了条目的 TAG 标签，同名 folder/tag 会同时出现两条，前端可通过 `origin.streamId` 与 `subscription/list` 区分来源。

---

## 订阅（Subscription）

### subscription/list

`GET /reader/api/0/subscription/list?output=json`

返回 `{"subscriptions": [...]}`，每个订阅包含 `id`、`title`、`categories`、`url`、`htmlUrl`、`iconUrl`。

> **与 FreshRSS 的差异**：FreshRSS 额外输出 `frss:priority`，告别 RSS 不包含。

### subscription/edit

`POST /reader/api/0/subscription/edit`

| 参数   | 说明                                       |
| ------ | ------------------------------------------ |
| `ac` | `subscribe` / `unsubscribe` / `edit` |
| `s`  | 流 ID（可重复）                            |
| `t`  | 标题（可重复）                             |
| `a`  | 添加到标签                                 |
| `r`  | 从标签移除                                 |

- `subscribe`：`s` 中 feed 部分为 URL
- `unsubscribe` / `edit`：`s` 中 feed 部分为数字 ID
- `a` 的标签不存在时自动创建为 FOLDER 类型

### subscription/quickadd

`POST /reader/api/0/subscription/quickadd`

| 参数         | 说明     |
| ------------ | -------- |
| `quickadd` | feed URL |

### subscription/export

`GET /reader/api/0/subscription/export`

返回 OPML XML，按文件夹分组。

### subscription/import

`POST /reader/api/0/subscription/import`

接收 OPML XML body，解析并导入订阅源和文件夹。文件夹不存在时自动创建。

---

## 标签（Tag / Label）

告别 RSS 的标签系统区分两种类型：

| 类型       | 用途                       | 关联                       |
| ---------- | -------------------------- | -------------------------- |
| `folder` | 文件夹，归类订阅源         | `Subscription.folder_id` |
| `tag`    | 收藏夹分类，归类已收藏条目 | `StarState.tag_id`       |

> **与 Google Reader 的差异**：
>
> - Google Reader 不区分 folder 和 tag，文件夹下订阅源的所有条目自动继承同名标签。实际上用户给订阅源打标签归类和单独给某个条目打标签记住并归类完全是两个目的，强行将二者混为一谈就是纯傻逼设计
> - 告别 RSS 的 TAG 依附于 StarState——有 tag 必收藏，一个条目只能有一个 tag。这是收藏夹分类的设计，而非自由标签系统。一个条目多个 tag 写起来多少有点麻烦，而且没太大必要
> - 同名 folder 和 tag 可以共存，FOLDER 优先匹配；部分端点新增 `type` 参数以精确区分

### tag/list

`GET /reader/api/0/tag/list?output=json`

返回 `{"tags": [...]}`，包含系统标签 `reading-list`、`starred` 以及用户的 folder 和 tag。

> **与 FreshRSS 的差异**：FreshRSS 为 TAG 输出 `unread_count`，告别 RSS 暂不包含。目前没有已知客户端使用此字段。

### edit-tag

`POST /reader/api/0/edit-tag`

| 参数  | 说明               |
| ----- | ------------------ |
| `i` | 条目 ID（可重复）  |
| `a` | 添加标签（可重复） |
| `r` | 移除标签（可重复） |
| `T` | token              |

支持的标签值：`user/-/state/com.google/read`、`starred`、`user/-/label/{name}`。

`a=label/X` 中 TAG 不存在时自动创建。`r` 操作对于不存在的标签静默忽略。

### enable-tag

`POST /reader/api/0/enable-tag`

| 参数     | 说明                                             |
| -------- | ------------------------------------------------ |
| `s`    | 标签 ID（可重复）                                |
| `type` | `folder` 或 `tag`（可重复，默认 `folder`） |

> **扩展端点**，允许明确指定类型创建标签。

### rename-tag

`POST /reader/api/0/rename-tag`

| 参数     | 说明                             |
| -------- | -------------------------------- |
| `s`    | 原名（可重复）                   |
| `dest` | 新名（可重复）                   |
| `type` | 类型（可重复，默认 FOLDER 优先） |

> **扩展**：支持批量重命名（FreshRSS 仅支持单次），支持同名互换（使用临时 UUID 中转）。`type` 为扩展参数，用于精确匹配同名 folder 和 tag。

### disable-tag

`POST /reader/api/0/disable-tag`

| 参数     | 说明                             |
| -------- | -------------------------------- |
| `s`    | 标签 ID（可重复）                |
| `type` | 类型（可重复，默认 FOLDER 优先） |

删除标签时自动清空关联的 `folder_id`（Subscription）或 `tag_id`（StarState）。`type` 为扩展参数，用于精确匹配同名 folder 和 tag。

---

## 杂项（Misc）

### unread-count

`GET /reader/api/0/unread-count?output=json`

返回四部分：

- `user/-/state/com.google/reading-list` — 总计
- `feed/{id}` — 每个订阅源
- `user/-/label/{name}` — 每个文件夹
- `user/-/label/{name}` — 每个标签

> **与 FreshRSS 的差异**：folder 和 tag 同名时，告别 RSS 将 tag 的条目固定在数组后面，以便前端区分相同名称的来源（folder 为订阅源未读数，tag 为收藏条目未读数），而非让 tag 覆盖 folder。

### mark-all-as-read

`POST /reader/api/0/mark-all-as-read`

| 参数    | 说明                                       |
| ------- | ------------------------------------------ |
| `s`   | 流 ID                                      |
| `ts`  | 条目 ID（之前的全部标已读）                |
| `type` | 指定`s` 中 label 类型：`folder`/`tag`，不传 = FOLDER 优先（扩展参数） |
| `T`   | token                                      |

支持的 `s` 值：`feed/{id}`、`user/-/label/{name}`、`reading-list`、`starred`。

已读状态通过 `ReadState` 插入实现，timestamp 为 `None`（批量操作不进入阅读历史）。已有的 ReadState 不会被覆盖。

### token

`GET /reader/api/0/token`

返回 T token（Auth token 的 `/` 分隔后半部分）。

### user-info

`GET /reader/api/0/user-info`

返回 `{"userId": ..., "userName": ..., "userProfileId": ..., "userEmail": ..., "isAdmin": ...}`。

---

## 与 Google Reader / FreshRSS 的主要差异汇总

| 特性                      | Google Reader | FreshRSS            | 告别 RSS             |
| ------------------------- | ------------- | ------------------- | -------------------- |
| 认证方式                  | Google OAuth  | SHA1 + salt         | HMAC-SHA256 + bcrypt |
| Continuation 格式         | hex           | 十进制              | hex（时间戳 + id 复合） |
| 标签系统                  | 扁平 tags     | Category + Tag 分离 | Folder + Tag 分离    |
| Folder/Tag 同名           | N/A           | FOLDER 遮蔽 TAG     | FOLDER 优先，可指定  |
| 批量 rename-tag           | 不支持        | 不支持              | 支持（含互换）       |
| enable-tag                | 无            | 无                  | 支持                 |
| ClientRegister            | 无            | 无                  | 支持（可配注册开关/邀请码） |
| DeleteAccount             | 无            | 无                  | 支持                 |
| EditProfile / SetAdmin / CreateUser / ListUsers | 无 | 无         | 支持                 |
| OPML 导出                 | N/A           | 支持                | 支持                 |
| OPML 导入                 | N/A           | 支持                | 支持                 |
| mark-all-as-read 已读保护 | -             | 覆盖已读            | 跳过已读             |
