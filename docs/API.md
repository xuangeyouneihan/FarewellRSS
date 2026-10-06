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
| `ot` | 起始时间戳（秒），闭区间下界（含）   |
| `nt` | 结束时间戳（秒），闭区间上界（含，涵盖整个第 `nt` 秒） |
| `c`  | 分页 continuation（32 位 hex：16 位时间戳 + 16 位 id） |
| `xt` | 排除标签                           |
| `it` | 仅含标签                           |
| `type` | `folder`/`tag`，指定 `user/-/label/{name}` 的类型，不传 = FOLDER 优先 |

> **参数作用域**：`type` 仅对路径参数 `{path}`（流 ID）起效；`it`/`xt` 目前只支持 `read`、`unread`、`starred` 三个状态标签，尚不支持 `user/-/label/{name}`。

支持的流路径：

| path                                     | 说明                       | 范围                     |
| ---------------------------------------- | -------------------------- | ------------------------ |
| `user/-/state/com.google/reading-list` | 全部                       | 仅当前订阅               |
| `user/-/state/com.google/starred`      | 已收藏                     | 全部（含已退订的源）     |
| `user/-/state/com.google/read`         | 已读                       | 全部（含已退订的源）     |
| `user/-/state/com.google/unread`       | 未读                       | 仅当前订阅               |
| `user/-/state/farewell-rss/starred-uncategorized` | 未分类收藏（扩展）     | 全部（含已退订的源） |
| `user/-/state/farewell-rss/history`    | 阅读历史（扩展）           | 全部（含已退订的源）     |
| `feed/{id}`                            | 单个订阅源                 | 仅当前订阅               |
| `user/-/label/{name}`                  | 文件夹/标签（FOLDER 优先） | folder：仅当前订阅；tag：全部（含已退订的源） |
| `user/-/search/{query}`                | FTS5 全文搜索（扩展）      | 仅当前订阅               |

> **「范围」的含义，以及两对不再互补的流**：退订（`subscription/edit` 的 `ac=unsubscribe`）只删除订阅关系，并丢弃「标为已读但从没打开过」的状态（`ReadState.timestamp IS NULL`）；**真正读过的历史（`ReadState` 带 timestamp）和收藏（`StarState`）都会保留**，对应条目在孤儿源清理时也会被留下。于是上表分两类：
>
> - **按当前订阅算**：`reading-list`、`unread`、`feed/{id}`、`user/-/search/{query}`（搜索），以及 `user/-/label/{name}` 的 folder 形态。
> - **按用户全部数据算（含已退订的源）**：`read`、`starred`、`starred-uncategorized`、`history`，以及 `user/-/label/{name}` 的 tag 形态。
>
> 由此有两对流**不再互补**，客户端不要拿它们做对账：
>
> | 看起来该互补 | 实际情况 |
> | --- | --- |
> | `read` + `unread` | 退订源的已读条目只在 `read` 里，既不在 `unread` 也不在 `reading-list`（所以 `read + unread ≠ 全部条目`） |
> | `starred` + （`reading-list` 上 `xt=starred` 的「未收藏」） | 退订源的收藏条目只在 `starred` 里，不出现在阅读列表的「未收藏」里 |
>
> 换句话说：**已读/收藏是「读过什么 / 收藏过什么」的账本，不随退订消失；而阅读列表和未读是「现在订阅里有什么」的视图**。

> **已读 vs 阅读历史**：`user/-/state/com.google/read` 返回所有**有已读状态**的条目，包含批量操作（`mark-all-as-read`）标记的那些；`user/-/state/farewell-rss/history` 只返回**真正读过**的条目（已读状态带时间戳），也就是逐篇打开过的那种。两者排序与分页逻辑一致。

> **搜索流说明**：`user/-/search/{query}` 使用 SQLite FTS5 全文搜索，支持布尔表达式（`python OR go`）、短语（`"hello world"`）、列限定（`title:python`）。搜索结果按相关性（BM25）排序，`r` 参数被忽略。分页通过 `n`（limit）和 `c`（continuation = offset 的 hex）控制，与普通流兼容。**搜索只搜当前用户订阅的源**（语义同 `reading-list`）：别人的订阅源里的文章搜不到，退订后也不再出现在搜索结果里。
>
> **短词查询与非法查询**：FTS5 的 trigram 分词器只索引 3 字 gram，短于 3 个字符的**词**在索引里根本不存在 —— 按词算，不是按整串长度：`异步 编程` 整串 5 个字符、两个词都短，实测照样 0 条。所以「朴素查询且每个词都短于 3 个字符」（`编程`、`协程`、`炒饭 火候`）会自动退回 `LIKE '%x%'` 全表扫描：多词按「每个词都要出现（AND）、不要求相邻」匹配，只看 `title` / `content_plain` / `summary_plain` 三列，按有效时间降序返回，`r` 也被忽略。实测代价（3 万条、正文约 800 字）：有命中约 **2ms**（按时间倒序扫索引，凑够一页就停），无命中约 **240ms**（必须扫完整个库）。带 FTS 语法的查询一律按 FTS5 解释、不走兜底（短语 `"炒饭 火候"`、布尔 `协程 OR 编程`、前缀 `编程*`、列限定 `title:编程`），能不能命中就看索引里有没有对应的 gram（trigram 是**按整串**取 gram 的，空格也算一个字符，所以 `"炒饭 火候"` 这样的整串短语仍然能命中，而布尔/前缀形态里含短词就是 0 条）；也不要用 LIKE 去猜这类查询的意思。另外注意：**不走兜底的多词查询按 FTS5 的短语语义解释（要求相邻）**，想要「都要有」就显式写 `AND`。查询串本身不合法时返回 **400**，而不是 500：引号不闭合（`"编程`）、运算符/括号错（`编程 AND`、`()`）、以及空查询（`/search/` 后面什么都没有或只有空白 —— 否则 `LIKE '%%'` 会把整个库倒出来）。错误体形如 `{"detail": {"code": "InvalidSearchQueryError", "detail": "搜索查询语法错误（…）"}}`。

> **排序与分页说明**：条目按「有效时间戳」（`published > updated > fetched` 取其一，秒级）+ 自增 id 排序；`o` 为时间升序（最旧在前），`n`/`d` 为降序（最新在前）。continuation 为 32 位 hex，前 16 位是排序时间戳、后 16 位是条目 id，作为复合分页锚点——同一秒有多条时按 id 精确切分，不重不漏。搜索流除外（见下）。
>
> **时间范围说明**：`ot`/`nt` 的比较也在秒级、且都是闭区间（`有效时间戳 >= ot` 且 `<= nt`）；注意 `nt` 涵盖**整个第 `nt` 秒**。微秒不参与排序和分页，只出现在 item 的 `crawlTimeMsec` / `timestampUsec` 两个展示字段里。
>
> **`updated` 是整秒**：`stream/contents` 和 `stream/items/contents` 顶层的 `updated` 是 Unix **整数**秒（FreshRSS 发的是 `time()`）。**别写成浮点**：Gson 系客户端（ReadYou 等）把它按 `Long` 解析，带小数点会直接抛 `Expected a long but was …`，整个同步因此失败 —— 而服务端这边看起来只是「200，一切正常」（实测：客户端每轮 sync 都会重登 + 重发同一批 id，最后一篇文章都存不进去）。
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

条目 ID 解析兼容三种写法（对齐 FreshRSS）：`tag:google.com,2005:reader/item/{hex}` 长格式、**不带前缀的 16 位 hex**（`000000000000001c`，Reeder 就用这种）、以及纯十进制（Google Reader 的 short form，ReadYou 用这种）。全数字且不以 `0` 开头的才算十进制 —— 与 FreshRSS 的 `hex2dec(basename($e_id))` 同一条规则。

**可见性**：只返回当前用户可见的条目 —— 即该条目所属的源在当前用户的订阅列表里，或该条目有当前用户自己的已读/收藏记录（退订后留下的收藏条目仍能取回）。其余条目会被静默忽略（不报错，只是不出现在 `items` 里）。这样按 id 枚举无法读到别人私有源里的正文。

### Categories 输出

每个条目返回 `categories` 数组：

- `user/-/state/com.google/reading-list` — 始终存在
- `user/-/state/com.google/read` — 已读
- `user/-/state/com.google/starred` — 已收藏
- `user/-/label/{name}` — 文件夹标签（来自订阅的 folder_id）
- `user/-/label/{name}` — 条目标签（来自 StarState 的 tag_id，与文件夹同名时不作去重）

> **与 FreshRSS 的差异**：告别 RSS 额外输出了条目的 TAG 标签，同名 folder/tag 会同时出现两条，前端可通过 `origin.streamId` 与 `subscription/list` 区分来源。

### 附件（enclosure）输出

带附件的条目**同时**走两条路输出：`enclosure` 数组（给机器读）+ 拼在正文末尾的播放器/图片（给人看）。

#### 1. `enclosure` 数组

`stream/contents` 和 `stream/items/contents` 都有：

```json
{
  "enclosure": [
    { "href": "https://media.example.com/ep12.mp3", "type": "audio/mpeg", "length": 12345678 }
  ]
}
```

- `href` — 附件地址，**只发绝对的 `http`/`https`**（`javascript:`、`data:`、相对地址一律不发，免得客户端把它塞进播放器）。
- `type` — 源站声明的 MIME。缺失时发空字符串，**不按扩展名替源站猜**（这是 MIME 字段，写假值会被按 `type.split("/")` 用的客户端吃出问题）。
- `length` — 字节数，仅有值（非 0）时才有这个键。
- **`href` 排在 `type` 前面**这一点是契约的一部分：有客户端实现是「见到 `type` 才分派 image/video/audio，用之前记下的 `href`」，`type` 排在前面它会拿到 `null`。
- **没有附件时整个键不出现**（不是空数组），和 FreshRSS 一致。

> **关于这个字段的来历**：Google Reader API 没有为它留下正式文档 —— 那份事实上的非官方规范（`mihaip/google-reader-api`）里 item 对象那一页（`StreamContents`）从来没有写出来，只剩断链引用；FeedHQ 的文档也不提它。这里的形状和键序是按真实客户端的读法定的：FreshRSS 的 `Entry::toGReader()` 在所有模式下都发 `{href, type, length?}`，News+ 在 Google Reader / Bazqux / Inoreader 三个后端用同一段代码解析并按 `type` 前缀分派。所以**也不要往里加自有字段**（比如时长、单集封面）：GReader 只给了这三个键，FreshRSS 加自己的东西时用的是 `frss:` 前缀。

#### 2. `summary.content` 末尾的附件块

**为什么要拼第二遍**：真实客户端里 ReadYou、Reeder、NetNewsWire **没有一个读 `enclosure` 字段** —— 它们只渲染 `summary.content` 里的 HTML（Reeder 和 NetNewsWire 走 WebView，ReadYou 走 Compose 的 HTML 子集）。只发数组的话，播客在这些客户端里就是个没有播放器的简介页。FreshRSS 也是两条路都给。

拼在**末尾**而不是开头：正文本身就是文章内容，插到最前面会把标题、首图挤下去；播客 show notes 的顺序是「简介 → 时间线 → 播放」，播放器跟在后面正好。

| 附件类型 | 追加的 HTML |
| -------- | ----------- |
| `audio/*` | `<figure class="enclosure"><p class="enclosure-content"><audio preload="none" controls="controls" src="…"></audio> <a href="…" target="_blank" rel="noopener noreferrer">💾</a></p></figure>` |
| `video/*` | 同上，`<audio>` 换成 `<video>` |
| `image/*` | `<figure class="enclosure"><p class="enclosure-content"><img src="…" alt="" /></p></figure>` |
| 其它 | 只有那个 💾 保存链接 |

- 类型判定：先看 MIME 前缀（`audio/` `video/` `image/`），MIME 缺失或给不出前缀时**按扩展名兜底**（`mp3`/`m4a`/`aac`/`opus`/`flac`/`wav`… 见 `src/farewell_rss/api/_enclosures.py` 的 `_EXTENSION_KINDS`）。这里按扩展名猜是安全的：猜错了最坏只是控件不对，不涉及往 MIME 字段里写假值。
- 不可播放的 `href`（相对地址、`javascript:`、`data:`）一律不拼，和数组那边同一条规则。
- `href`/`src` 都经 `html.escape(href, quote=True)` 转义。
- **正文里已经出现过同一个 `href` 的条目不再重复拼**（有的源自己就把音频写进正文了）。
- `preload="none"` 是故意的：正文里可能有好几个播放器，不预载就代表**不按播放不发请求**。
- 没有可拼的附件时正文**原样返回**，一个字节都不动。

> 前端因此不再解析 `enclosure` 字段：播放控件由服务端给，客户端只要放行 `figure` / `audio` / `video` / `img` 就能显示。告别 RSS 自己的前端还会接管正文里 `#t=00:05:18` 这类章节链接 —— 点了不跳页，而是 seek 正文里的播放器。


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

> **`unsubscribe` 会删掉什么、留下什么**：只删除订阅关系，并丢弃该源下「标为已读但从没打开过」的状态（`ReadState.timestamp IS NULL`）；**真正读过的历史（带 timestamp 的 `ReadState`）和收藏（`StarState`）都保留**，对应条目在孤儿源清理时也会被留下。所以退订之后，该源的已读/收藏条目**仍然出现在 `read`、`starred`、`history` 等流里**，但不再出现在 `reading-list`、`unread` 里 —— 详见「流」一节的「范围」说明。

### subscription/quickadd

`POST /reader/api/0/subscription/quickadd`

| 参数         | 说明     |
| ------------ | -------- |
| `quickadd` | feed URL |

成功：`{"numResults": 1, "query": "<源 URL>", "streamId": "feed/<id>", "streamName": "<标题>"}`。

**抓取失败时的语义**（订阅是交互式的，所以会当场抓一次拿标题/图标）：

- **网络层失败（超时/DNS/TLS）与 5xx/429** —— 上游「现在不行」，**订阅照建**（内容交给后台刷新，占位记录的 `fetched` 早于任何 TTL，下一轮必然被挑中）。此时 `streamName` 可能是空串，列表里该源的名字会退回显示 URL，直到首次抓到标题。这样做的理由：慢源 / 不稳的链路（实测同一个 1 MB 的源，经代理有时 4.7 秒抓到、有时 14 秒超时）不该让用户反复手点重试。
- **4xx（403 被 WAF 拒 / 404 源已失效 / 410 已删）与「抓到了但没有条目」** —— 那是地址不对或源已废，重试也不会变好：返回 **502**，错误体是 Google Reader 那句 `numResults: 0` 形状：

  ```json
  { "detail": { "numResults": 0, "error": { "code": "FeedFetchFailed", "detail": "无法订阅 <URL>：对方返回 HTTP 404" } } }
  ```

  此时**不会**创建订阅。（`subscription/edit` 的 `ac=subscribe` 走同一套逻辑；它是批量订阅，一条永久失败会让整个请求失败。）

### subscription/export

`GET /reader/api/0/subscription/export`

返回 OPML XML，按文件夹分组。

### subscription/import

`POST /reader/api/0/subscription/import`

接收 OPML XML body，解析并导入订阅源和文件夹。文件夹不存在时自动创建。

**导入只建记录、不抓取内容**（对齐 FreshRSS）：导入不依赖网络，也不会因为某个源 403/503 就少一条订阅。
内容由紧跟的一次后台刷新补齐 —— 新订阅的源会被标为「还未抓取过」，所以必然被那一轮挑中。
`title` 取 OPML 里的 `title`/`text`；源自己的标题与图标要等抓到之后才有。

响应当前仍是纯文本 `OK`（无失败清单），跳过的条数只记在服务端日志里。

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

已读状态通过 `ReadState` 插入实现，timestamp 为 `None`（批量操作不进入阅读历史）。已有的 ReadState 不会被覆盖；退订该源时这些状态会被一并丢弃（真实阅读历史保留），见「流」一节的「范围」说明。

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
