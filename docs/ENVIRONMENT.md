# 环境变量配置

> 简体中文 | [English](../i18n/docs-en/ENVIRONMENT.md)

告别 RSS 的所有配置均通过环境变量完成。所有变量以 `FAREWELL_RSS_` 开头。

## 配置来源与优先级

```
OS 环境变量  >  数据目录下的 .env 文件
```

- **OS 环境变量优先**：进程启动时已有的环境变量会覆盖 `.env` 中的同名条目。
- **`.env` 自动清理**：启动时会从 `.env` 中删除所有已被 OS 环境变量覆盖的条目（避免过期值残留误导）。
- **`.env` 位置**：`{FAREWELL_RSS_DATA_DIR}/.env`。适合保存密钥等不想写进命令行的项。

## 变量一览

| 变量                      | 默认值         | 说明                                                                                                                                   |
| ------------------------- | -------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `FAREWELL_RSS_DATA_DIR` | `~/.local/share/farewell-rss` | 数据目录（SQLite 数据库、`.env` 等都在此）。`~` 会展开为当前用户的主目录。**只从 OS 环境变量读取**，不从 `.env` 读（因为 `.env` 本身就在这个目录里）。 |
| `FAREWELL_RSS_SECRET`   | 启动时自动生成 | 签名 Auth token 的 HMAC 密钥。**没有会自动生成一个随机值并写入 `.env`**，无需手动配置。改了它所有已登录用户的 token 立即失效。 |
| `FAREWELL_RSS_HOST`     | `0.0.0.0`    | 监听地址。                                                                                                                             |
| `FAREWELL_RSS_PORT`     | `3000`       | 监听端口。Docker 下这是**容器内**端口，对外暴露靠 `docker run -p 宿主端口:容器端口`（或 compose 的 `ports`）映射，二者需对应。              |

### 注册控制

| 变量                            | 默认值 | 说明                                                                                                                         |
| ------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `FAREWELL_RSS_ALLOW_REGISTER` | 允许   | 是否开放自助注册。为假值（见下）时`ClientRegister` 返回 403。**不影响**管理员的 `CreateUser`（管理员加人不受限）。 |
| `FAREWELL_RSS_INVITE_CODE`    | 未配置 | 邀请码。配置后自助注册必须提供匹配的`invite_code`（不匹配返回 403）。未配置则免填。仅在允许注册时生效。                    |

> **首个用户例外**：注册控制只在**已有用户**后生效。数据库为空时第一个注册的用户（会自动成为管理员）无视 `ALLOW_REGISTER` 和 `INVITE_CODE`——否则禁用注册就永远建不出用户。

**`FAREWELL_RSS_ALLOW_REGISTER` 的真值判断**：

- **未配置** → 允许注册
- 配置为 `1` / `true` / `yes` / `on`（大小写不敏感、忽略首尾空白）→ 允许
- 配置为其他任何值（如 `false` / `0` / `no`）→ **禁止**

### 订阅源抓取

| 变量                                         | 默认值             | 说明                                                                     |
| -------------------------------------------- | ------------------ | ------------------------------------------------------------------------ |
| `FAREWELL_RSS_FEED_REFRESH_INTERVAL`       | `900`（15 分钟） | 调度器刷新所有订阅源的间隔（秒）。                                       |
| `FAREWELL_RSS_FEED_DEFAULT_TTL`            | `3600`（1 小时） | 订阅源 feed 未声明 TTL 时的默认 TTL（秒）。TTL 内的订阅源跳过抓取。      |
| `FAREWELL_RSS_FEED_MIN_TTL`                | `900`（15 分钟） | TTL 下限（秒）。feed 声明的 TTL 低于此值时按此值计，防止过于频繁的抓取。 |
| `FAREWELL_RSS_FEED_UPDATE_MAX_CONCURRENCY` | `10`             | 同时抓取的订阅源最大并发数。                                             |

## 示例

### 只读部署（关闭自助注册 + 邀请码）

```powershell
$env:FAREWELL_RSS_ALLOW_REGISTER = "false"          # 完全关闭自助注册
$env:FAREWELL_RSS_PORT = "8080"
farewell-rss
```

```powershell
$env:FAREWELL_RSS_ALLOW_REGISTER = "true"
$env:FAREWELL_RSS_INVITE_CODE = "my-secret-code"    # 凭邀请码注册
farewell-rss
```

### Docker / 生产

```bash
docker run -e FAREWELL_RSS_DATA_DIR=/data \
           -e FAREWELL_RSS_PORT=3000 \
           -e FAREWELL_RSS_ALLOW_REGISTER=false \
           -v farewell-rss-data:/data \
           farewell-rss
```

## 备注

- **密钥（`FAREWELL_RSS_SECRET`）一般不用管**：首次启动自动生成并持久化到 `.env`，重启后保持一致。只有想强制所有用户重新登录时才需要改它。
- **管理员创建用户不受注册控制影响**：即使 `FAREWELL_RSS_ALLOW_REGISTER=false`、配置了邀请码，管理员仍可通过 `POST /accounts/CreateUser` 直接加人。详见 [API 文档](API.md)。
