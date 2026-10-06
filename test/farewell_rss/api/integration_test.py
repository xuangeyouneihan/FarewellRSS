"""API 集成测试 —— "顾客点了份炒饭" 全链路。"""

from datetime import datetime
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from farewell_rss.feed_fetcher.feed_fetcher import (
    FetchedAuthor,
    FetchedEnclosure,
    FetchedEntry,
    FetchedFeed,
    FetchedTag,
)

# ─── 测试数据 ────────────────────────────────────────────────────────────


def _A(n: str) -> FetchedAuthor:
    return FetchedAuthor(name=n, href=None, email=None)


TEST_FEED = FetchedFeed(
    href="https://example.com/test.xml",
    title="测试 RSS 源",
    link="https://example.com",
    subtitle="一个用于集成测试的 RSS 源",
    author=_A("Test Author"),
    ttl=15,
    entries=[
        FetchedEntry(
            guid="entry-1",
            title="《炒饭指南》第一章",
            link="https://example.com/1",
            summary="这是一篇关于炒饭的文章",
            summary_plain="这是一篇关于炒饭的文章",
            author=_A("Chef"),
            tags=[FetchedTag(term="food", label="美食")],
            enclosures=[
                FetchedEnclosure(
                    href="https://example.com/recipe.pdf",
                    length=1024,
                    type="application/pdf",
                ),
            ],
        ),
        FetchedEntry(
            guid="entry-2",
            title="《炒饭指南》第二章",
            link="https://example.com/2",
            summary="<p>炒饭的要诀在于火候</p>",
            summary_plain="炒饭的要诀在于火候",
            content="<p>大火快炒是中餐的精华</p>",
            content_plain="大火快炒是中餐的精华",
            author=_A("Chef"),
        ),
        FetchedEntry(
            guid="entry-3",
            title="《炒饭指南》第三章",
            link="https://example.com/3",
            summary="甜品时间",
        ),
    ],
)


async def _fake_fetch(url, etag=None, modified=None):
    return TEST_FEED


# ─── 基础设施 ────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(monkeypatch):
    import os

    os.environ["FAREWELL_RSS_DATA_DIR"] = ":memory:"
    os.environ["FAREWELL_RSS_FEED_REFRESH_INTERVAL"] = "999999"

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from farewell_rss.db.models import Base
    from farewell_rss.main import app

    test_engine = create_async_engine("sqlite+aiosqlite://")
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS entry_fts USING fts5("
                "title, content_plain, summary_plain, "
                "tokenize='trigram', content='entries', content_rowid='id')"
            )
        )
        for trigger in (
            (
                "CREATE TRIGGER IF NOT EXISTS entry_fts_ai AFTER INSERT ON entries BEGIN "
                "INSERT INTO entry_fts(rowid, title, content_plain, summary_plain) "
                "VALUES (new.id, new.title, new.content_plain, new.summary_plain); END"
            ),
            (
                "CREATE TRIGGER IF NOT EXISTS entry_fts_ad AFTER DELETE ON entries BEGIN "
                "INSERT INTO entry_fts(entry_fts, rowid, title, content_plain, summary_plain) "
                "VALUES ('delete', old.id, old.title, old.content_plain, old.summary_plain); END"
            ),
            (
                "CREATE TRIGGER IF NOT EXISTS entry_fts_au AFTER UPDATE ON entries BEGIN "
                "INSERT INTO entry_fts(entry_fts, rowid, title, content_plain, summary_plain) "
                "VALUES ('delete', old.id, old.title, old.content_plain, old.summary_plain); "
                "INSERT INTO entry_fts(rowid, title, content_plain, summary_plain) "
                "VALUES (new.id, new.title, new.content_plain, new.summary_plain); END"
            ),
        ):
            await conn.execute(text(trigger))

    from unittest.mock import AsyncMock

    from farewell_rss.db.db import get_session

    # 删除账户后的硬删除是**后台作业**，它自建 session（进程级 SessionLocal，
    # 会指向真实数据目录）。这里把它挡掉，只验证 API 层契约（软删除后立即
    # 无法登录）；作业本身的清理逻辑由 jobs_test.py 覆盖。
    monkeypatch.setattr("farewell_rss.jobs.hard_delete_user", AsyncMock())
    # 导入 OPML 之后会 spawn 一轮后台刷新（`jobs.refresh_all_feeds`）。它自建 session、
    # 会真去联网抓源，所以在测试里一律挡掉；要验证「有没有触发」的用例自己再断言一次。
    monkeypatch.setattr("farewell_rss.jobs.refresh_all_feeds", AsyncMock())

    # 把应用的 session 工厂换到测试引擎（否则 get_session 会去连真实数据目录）
    monkeypatch.setattr("farewell_rss.db.db.SessionLocal", TestSession)

    # 依赖覆写保留着，但**实现直接复用生产的 get_session**：事务边界（提交／回滚）
    # 由生产代码执行，测试不会因为覆写漏了提交而静默漂移——repository 改成只 flush
    # 之后，「请求结束提交」这件事只在这一个地方发生，测试必须走同一条路径。
    # 另外下面几处用例要靠它拿一个 session 来种数据／断言。
    async def _override_get_session():
        async for session in get_session():
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    # 跳过 lifespan 的 init_db 和 scheduler
    with (
        patch("farewell_rss.main.init_db", new=AsyncMock()),
        patch("farewell_rss.main.scheduler_run", new=AsyncMock()),
    ):
        transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    # 必须 dispose：否则 aiosqlite 的连接工作线程会在测试的 event loop
    # 关闭之后才去交付结果，报 "RuntimeError: Event loop is closed"
    await test_engine.dispose()


def _auth(text: str) -> str:
    for line in text.split("\n"):
        if line.startswith("Auth="):
            return line[5:].strip()
    raise ValueError(f"无法解析认证响应: {text!r}")


# ─── "顾客点了份炒饭" 主流程 ────────────────────────────────────────────

BASE = "/api/greader.php/reader/api/0"


async def test_full_flow(client: AsyncClient):
    """注册 → 订阅 → 列表 → 读流 → 标已读 → 确认已空"""

    # 1. 注册
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={
            "Email": "chef",
            "Passwd": "fried-rice",
            "friendly_name": "大厨",
        },
    )
    assert r.status_code == 200, r.text  # 200 or 201 depending on FastAPI version
    token = _auth(r.text)
    h = {"Authorization": f"GoogleLogin auth={token}"}

    # Google Reader 标准状态流会出现在 tag/list 中
    r = await client.get(f"{BASE}/tag/list", headers=h)
    assert r.status_code == 200, r.text
    tag_ids = {tag["id"] for tag in r.json()["tags"]}
    assert {
        "user/-/state/com.google/reading-list",
        "user/-/state/com.google/starred",
        "user/-/state/com.google/read",
        "user/-/state/com.google/unread",
    } <= tag_ids

    # 2. 订阅
    with patch("farewell_rss.services.feed.fetch", side_effect=_fake_fetch):
        r = await client.post(
            f"{BASE}/subscription/quickadd",
            data={
                "quickadd": "https://example.com/test.xml",
            },
            headers=h,
        )
    assert r.status_code == 200, r.text

    # 3. 列表
    r = await client.get(f"{BASE}/subscription/list", headers=h)
    assert r.status_code == 200
    subs = r.json()["subscriptions"]
    assert len(subs) == 1
    assert subs[0]["title"] == "测试 RSS 源"

    # 4. 未读流
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/reading-list",
        headers=h,
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 3
    titles = {it["title"] for it in items}
    assert "《炒饭指南》第一章" in titles
    assert "《炒饭指南》第二章" in titles
    assert "《炒饭指南》第三章" in titles

    # 5. 全部标已读
    r = await client.post(
        f"{BASE}/mark-all-as-read",
        data={
            "s": "user/-/state/com.google/reading-list",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text

    # 6. 标已读后，所有条目应有 user/-/state/com.google/read
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/reading-list",
        headers=h,
    )
    items = r.json()["items"]
    assert len(items) == 3
    for item in items:
        assert "user/-/state/com.google/read" in item["categories"]

    # 7. FreshRSS 兼容：直接访问已读流
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/read",
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 3

    # 7b. Google Reader 标准未读流
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/unread",
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []

    # 8. FreshRSS 兼容：已读流可作为 it/xt 筛选值
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/reading-list",
        params={"it": "user/-/state/com.google/read"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 3
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/reading-list",
        params={"xt": "user/-/state/com.google/read"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []

    # 9. edit-tag 可移除和恢复已读状态
    item_id = items[0]["id"]
    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": item_id, "r": "user/-/state/com.google/read"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/read",
        headers=h,
    )
    assert len(r.json()["items"]) == 2
    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": item_id, "a": "user/-/state/com.google/read"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # 10. mark-all-as-read 可接受已读流（幂等）
    r = await client.post(
        f"{BASE}/mark-all-as-read",
        data={"s": "user/-/state/com.google/read"},
        headers=h,
    )
    assert r.status_code == 200, r.text


# ─── 错误路径 ────────────────────────────────────────────────────────────


async def test_register_duplicate(client: AsyncClient):
    data = {"Email": "dup", "Passwd": "pw", "friendly_name": "D"}
    await client.post("/api/accounts/ClientRegister", data=data)
    r = await client.post("/api/accounts/ClientRegister", data=data)
    assert r.status_code == 409


async def test_login_wrong_password(client: AsyncClient):
    await client.post(
        "/api/accounts/ClientRegister",
        data={
            "Email": "alice",
            "Passwd": "correct",
            "friendly_name": "A",
        },
    )
    r = await client.post(
        "/api/accounts/ClientLogin",
        data={
            "Email": "alice",
            "Passwd": "wrong",
        },
    )
    assert r.status_code == 401


async def test_no_auth(client: AsyncClient):
    r = await client.get("/api/reader/api/0/subscription/list")
    assert r.status_code in (401, 422)  # 422 if FastAPI validates before auth


async def test_import_standard_opml(client: AsyncClient):
    """标准 OPML 的 outline 位于 body 下，导入后保留文件夹与自定义标题"""
    headers = await _register(client, "opml-user")
    opml = b"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>Subscriptions</title></head>
  <body>
    <outline text="Technology" title="Technology">
      <outline type="rss" text="Imported feed" title="Imported feed"
               xmlUrl="https://example.com/imported.xml" />
    </outline>
  </body>
</opml>"""
    with patch("farewell_rss.services.feed.fetch", side_effect=_fake_fetch):
        r = await client.post(
            f"{BASE}/subscription/import",
            content=opml,
            headers={**headers, "Content-Type": "text/xml"},
        )
    assert r.status_code == 200, r.text

    r = await client.get(f"{BASE}/subscription/list", headers=headers)
    subscriptions = r.json()["subscriptions"]
    assert len(subscriptions) == 1
    assert subscriptions[0]["title"] == "Imported feed"
    assert subscriptions[0]["categories"] == [
        {"id": "user/-/label/Technology", "label": "Technology"}
    ]


async def test_import_opml_does_not_fetch_feeds(client: AsyncClient):
    """导入 OPML 只建订阅、**不抓内容**（对齐 FreshRSS），抓取交给导入后的那一轮刷新

    以前 `subscribe()` 会同步抓一次，于是「某个源抓不到」就变成「少一条订阅」
    （403/503/网络抖动都算），而且几十个源要串行等网络。现在导入期间压根不联网：
    抓不到的源照样入库（占位记录，内容由之后的那轮刷新补）。
    """
    headers = await _register(client, "opml-nofetch-user")
    opml = b"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <body>
    <outline type="rss" text="Good one" xmlUrl="https://example.com/good-1.xml" />
    <outline type="rss" text="Unreachable" xmlUrl="https://example.com/unreachable.xml" />
    <outline type="rss" text="Good two" xmlUrl="https://example.com/good-2.xml" />
  </body>
</opml>"""

    async def _explode(url, etag=None, modified=None):
        raise AssertionError("导入期间不该抓取任何源")

    with patch("farewell_rss.services.feed.fetch", side_effect=_explode):
        r = await client.post(
            f"{BASE}/subscription/import",
            content=opml,
            headers={**headers, "Content-Type": "text/xml"},
        )
    assert r.status_code == 200, r.text

    r = await client.get(f"{BASE}/subscription/list", headers=headers)
    titles = sorted(s["title"] for s in r.json()["subscriptions"])
    assert titles == ["Good one", "Good two", "Unreachable"], titles


async def test_import_opml_survives_db_write_failure(client: AsyncClient, monkeypatch):
    """OPML 里某条**写库**失败：同样只跳过它自己，其余照常导入

    抓取失败有每条的 `except` 兜住就够了；写库失败不够 —— 异常发生在 flush 里，
    SQLAlchemy 会把 session 标成「必须先 rollback」，之后每个 outline 都抛
    `PendingRollbackError`、最后 commit 也抛（实测过：HTTP 500、库里 0 个源）。
    这条钉住 savepoint 那层隔离。

    制造失败的办法：把那条 outline 的 `fetched` 换成 naive datetime —— 模型的 UTCDateTime
    只在**写库时**才拒绍它。导入路径已经不抓内容了，所以走不了「坏条目」那条老路。
    """
    from farewell_rss.db.repositories.feed import FeedRepository

    headers = await _register(client, "opml-db-fail-user")
    opml = b"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <body>
    <outline type="rss" text="Good one" xmlUrl="https://example.com/good-1.xml" />
    <outline type="rss" text="Bad write" xmlUrl="https://example.com/bad-write.xml" />
    <outline type="rss" text="Good two" xmlUrl="https://example.com/good-2.xml" />
  </body>
</opml>"""

    real_stub = FeedRepository.get_or_create_stub

    async def _flaky_stub(self, href, fetched):
        if "bad-write" in href:
            fetched = datetime(2026, 1, 1)  # naive：写库时才抛
        return await real_stub(self, href, fetched)

    monkeypatch.setattr(FeedRepository, "get_or_create_stub", _flaky_stub)

    r = await client.post(
        f"{BASE}/subscription/import",
        content=opml,
        headers={**headers, "Content-Type": "text/xml"},
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"{BASE}/subscription/list", headers=headers)
    titles = sorted(s["title"] for s in r.json()["subscriptions"])
    assert titles == ["Good one", "Good two"], titles


async def test_export_opml_filename_uses_username(client: AsyncClient):
    """导出文件名用用户名，不用容易乱码的英文标题"""
    headers = await _register(client, "opml-export-user")

    r = await client.get(f"{BASE}/subscription/export", headers=headers)

    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == (
        'attachment; filename="opml-export-user.opml"'
    )
    assert r.headers["content-type"].startswith("application/xml")

    headers = await _register(client, "export user")
    r = await client.get(f"{BASE}/subscription/export", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''export%20user.opml"
    )


async def test_edit_profile(client: AsyncClient):
    """EditProfile：修改昵称、空串清空、未认证拒绝"""
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={"Email": "profile-user", "Passwd": "pw", "friendly_name": "旧昵称"},
    )
    assert r.status_code == 200, r.text
    h = {"Authorization": f"GoogleLogin auth={_auth(r.text)}"}

    # 修改昵称
    r = await client.post(
        "/api/accounts/EditProfile", data={"friendly_name": "新昵称"}, headers=h
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"{BASE}/user-info", headers=h)
    assert r.json()["userName"] == "新昵称"

    # 空字符串 → 置空（而不是保留原值）
    r = await client.post(
        "/api/accounts/EditProfile", data={"friendly_name": ""}, headers=h
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"{BASE}/user-info", headers=h)
    assert r.json()["userName"] is None

    # 缺省参数 → 同样置空
    r = await client.post(
        "/api/accounts/EditProfile", data={"friendly_name": "再改一次"}, headers=h
    )
    assert r.status_code == 200, r.text
    r = await client.post("/api/accounts/EditProfile", headers=h)
    assert r.status_code == 200, r.text
    r = await client.get(f"{BASE}/user-info", headers=h)
    assert r.json()["userName"] is None

    # 未认证 → 拒绝（FastAPI 先校验 header 时为 422，进入认证逻辑时为 401）
    r = await client.post("/api/accounts/EditProfile", data={"friendly_name": "x"})
    assert r.status_code in (401, 422), r.text


async def test_register_control(client: AsyncClient, monkeypatch):
    """注册开关与邀请码（已有用户后）：ALLOW_REGISTER 假→403；配置邀请码→必须匹配"""
    # 先注册一个种子用户（第一个用户无视注册控制），之后才会被拦
    await _register(client, "seed")

    # 1. 禁止注册
    monkeypatch.setenv("FAREWELL_RSS_ALLOW_REGISTER", "false")
    monkeypatch.delenv("FAREWELL_RSS_INVITE_CODE", raising=False)
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={"Email": "u1", "Passwd": "pw"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "RegisterDisabledError"

    # 2. 允许注册 + 配置邀请码：缺/错 → 403，对 → 成功
    monkeypatch.setenv("FAREWELL_RSS_ALLOW_REGISTER", "true")
    monkeypatch.setenv("FAREWELL_RSS_INVITE_CODE", "secret-code")
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={"Email": "u2", "Passwd": "pw"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "InvalidInviteCodeError"
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={"Email": "u2", "Passwd": "pw", "invite_code": "wrong"},
    )
    assert r.status_code == 403, r.text
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={"Email": "u2", "Passwd": "pw", "invite_code": "secret-code"},
    )
    assert r.status_code == 200, r.text

    # 3. 允许注册 + 未配置邀请码：直接成功
    monkeypatch.setenv("FAREWELL_RSS_ALLOW_REGISTER", "1")
    monkeypatch.delenv("FAREWELL_RSS_INVITE_CODE", raising=False)
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={"Email": "u3", "Passwd": "pw"},
    )
    assert r.status_code == 200, r.text

    # 4. 未配置 ALLOW_REGISTER（默认允许）
    monkeypatch.delenv("FAREWELL_RSS_ALLOW_REGISTER", raising=False)
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={"Email": "u4", "Passwd": "pw"},
    )
    assert r.status_code == 200, r.text


async def test_first_user_bypasses_register_control(client: AsyncClient, monkeypatch):
    """第一个用户（自动成管理员）无视注册开关和邀请码，否则建不出管理员"""
    # 禁用注册 + 配置邀请码，第一个用户仍应注册成功
    monkeypatch.setenv("FAREWELL_RSS_ALLOW_REGISTER", "false")
    monkeypatch.setenv("FAREWELL_RSS_INVITE_CODE", "some-code")
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={"Email": "first-admin", "Passwd": "pw"},
    )
    assert r.status_code == 200, r.text
    # 且自动成为管理员
    h = {"Authorization": f"GoogleLogin auth={_auth(r.text)}"}
    r = await client.get(f"{BASE}/user-info", headers=h)
    assert r.json()["isAdmin"] is True

    # 第二个用户开始被注册控制拦截
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={"Email": "second-user", "Passwd": "pw"},
    )
    assert r.status_code == 403, r.text


async def test_create_user(client: AsyncClient, monkeypatch):
    """CreateUser：管理员直接创建（无视注册开关/邀请码）、权限、重复、空密码"""
    from sqlalchemy import text

    from farewell_rss.db.db import get_session

    # 先注册操作者（注册开关关闭后 _register 会失败）
    await _register(client, "creator-admin")

    # 关闭公开注册 + 配置邀请码，CreateUser 应完全无视
    monkeypatch.setenv("FAREWELL_RSS_ALLOW_REGISTER", "false")
    monkeypatch.setenv("FAREWELL_RSS_INVITE_CODE", "some-code")

    async for session in client._transport.app.dependency_overrides[get_session]():
        await session.execute(
            text("UPDATE users SET is_admin = 1 WHERE username = 'creator-admin'")
        )
        await session.commit()
        break

    def create(
        username: str,
        password: str = "pw",
        op: str = "creator-admin",
        op_pw: str = "pw",
        **extra,
    ):
        return client.post(
            "/api/accounts/CreateUser",
            data={
                "username": username,
                "password": password,
                "operator_username": op,
                "operator_password": op_pw,
                **extra,
            },
        )

    # 管理员创建普通用户（无视注册禁用与邀请码）
    r = await create("new-user", friendly_name="新用户")
    assert r.status_code == 201, r.text

    # 新用户能登录
    r = await client.post(
        "/api/accounts/ClientLogin", data={"Email": "new-user", "Passwd": "pw"}
    )
    assert r.status_code == 200, r.text

    # 创建管理员用户
    r = await create("new-admin", is_admin="true")
    assert r.status_code == 201, r.text
    r = await client.post(
        "/api/accounts/ClientLogin", data={"Email": "new-admin", "Passwd": "pw"}
    )
    h = {"Authorization": f"GoogleLogin auth={_auth(r.text)}"}
    r = await client.get(f"{BASE}/user-info", headers=h)
    assert r.json()["isAdmin"] is True

    # 非管理员 → 403
    r = await create("x1", op="new-user", op_pw="pw")
    assert r.status_code == 403, r.text

    # 重复用户名 → 409
    r = await create("new-user")
    assert r.status_code == 409, r.text

    # 空密码 → 400
    r = await create("x2", password="  ")
    assert r.status_code == 400, r.text

    # 操作者密码错误 → 400（不是 401）
    r = await create("x3", op_pw="wrong")
    assert r.status_code == 400, r.text


async def test_list_users(client: AsyncClient):
    """ListUsers：管理员可列出全部用户，非管理员 403"""
    from sqlalchemy import text

    from farewell_rss.db.db import get_session

    h_admin = await _register(client, "list-admin")
    h_plain = await _register(client, "list-plain")
    async for session in client._transport.app.dependency_overrides[get_session]():
        await session.execute(
            text("UPDATE users SET is_admin = 1 WHERE username = 'list-admin'")
        )
        await session.commit()
        break

    # 非管理员 → 403
    r = await client.get("/api/accounts/ListUsers", headers=h_plain)
    assert r.status_code == 403, r.text

    # 管理员 → 返回包含两个用户，字段齐全
    r = await client.get("/api/accounts/ListUsers", headers=h_admin)
    assert r.status_code == 200, r.text
    users = {u["username"]: u for u in r.json()["users"]}
    assert users["list-admin"]["isAdmin"] is True
    assert users["list-plain"]["isAdmin"] is False
    assert users["list-plain"]["friendlyName"] == "U"


async def test_set_admin(client: AsyncClient):
    """SetAdmin：管理员设置/取消、非管理员拒绝、最后管理员保护、不存在用户 404"""
    from sqlalchemy import text

    from farewell_rss.db.db import get_session

    # 注册普通用户和目标用户，再用 SQL 把操作者提为管理员
    ha = await _register(client, "admin-op")  # noqa: F841
    hb = await _register(client, "target-user")
    async for session in client._transport.app.dependency_overrides[get_session]():
        await session.execute(
            text("UPDATE users SET is_admin = 1 WHERE username = 'admin-op'")
        )
        await session.commit()
        break

    # 非管理员拒绝（target-user 尝试改自己）
    r = await client.post(
        "/api/accounts/SetAdmin",
        data={
            "username": "target-user",
            "is_admin": "true",
            "operator_username": "target-user",
            "operator_password": "pw",
        },
    )
    assert r.status_code == 403, r.text

    # 管理员设为管理员
    r = await client.post(
        "/api/accounts/SetAdmin",
        data={
            "username": "target-user",
            "is_admin": "true",
            "operator_username": "admin-op",
            "operator_password": "pw",
        },
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"{BASE}/user-info", headers=hb)
    assert r.json()["isAdmin"] is True

    # 不存在用户 → 404
    r = await client.post(
        "/api/accounts/SetAdmin",
        data={
            "username": "ghost",
            "is_admin": "true",
            "operator_username": "admin-op",
            "operator_password": "pw",
        },
    )
    assert r.status_code == 404, r.text

    # 操作者密码错误 → 400（不是 401，避免触发前端清 token）
    r = await client.post(
        "/api/accounts/SetAdmin",
        data={
            "username": "target-user",
            "is_admin": "false",
            "operator_username": "admin-op",
            "operator_password": "wrong",
        },
    )
    assert r.status_code == 400, r.text

    # 取消 target-user 的管理员（此时还有 admin-op，允许）
    r = await client.post(
        "/api/accounts/SetAdmin",
        data={
            "username": "target-user",
            "is_admin": "false",
            "operator_username": "admin-op",
            "operator_password": "pw",
        },
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"{BASE}/user-info", headers=hb)
    assert r.json()["isAdmin"] is False

    # 取消最后一个管理员（admin-op 自己）→ 422
    r = await client.post(
        "/api/accounts/SetAdmin",
        data={
            "username": "admin-op",
            "is_admin": "false",
            "operator_username": "admin-op",
            "operator_password": "pw",
        },
    )
    assert r.status_code == 422, r.text


# ─── type 参数与 starred-uncategorized 流 ───────────────────────────────


async def _register(client: AsyncClient, username: str = "user") -> dict:
    r = await client.post(
        "/api/accounts/ClientRegister",
        data={"Email": username, "Passwd": "pw", "friendly_name": "U"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"GoogleLogin auth={_auth(r.text)}"}


async def _subscribe(client: AsyncClient, headers: dict) -> None:
    with patch("farewell_rss.services.feed.fetch", side_effect=_fake_fetch):
        r = await client.post(
            f"{BASE}/subscription/quickadd",
            data={"quickadd": "https://example.com/test.xml"},
            headers=headers,
        )
    assert r.status_code == 200, r.text


async def _title_to_id(client: AsyncClient, headers: dict) -> dict[str, str]:
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/reading-list",
        headers=headers,
    )
    return {it["title"]: it["id"] for it in r.json()["items"]}


async def test_label_stream_type_param(client: AsyncClient):
    """同名 folder/tag 时，type 参数能区分"""
    h = await _register(client)
    await _subscribe(client, h)

    # 创建同名 folder 和 tag
    await client.post(
        f"{BASE}/enable-tag",
        data={"s": "user/-/label/科技", "type": "folder"},
        headers=h,
    )
    await client.post(
        f"{BASE}/enable-tag",
        data={"s": "user/-/label/科技", "type": "tag"},
        headers=h,
    )

    # 订阅归 folder
    r = await client.get(f"{BASE}/subscription/list", headers=h)
    feed_id = r.json()["subscriptions"][0]["id"]  # feed/{id}
    r = await client.post(
        f"{BASE}/subscription/edit",
        data={"ac": "edit", "s": feed_id, "a": "user/-/label/科技"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # 收藏第一篇到 tag
    ids = await _title_to_id(client, h)
    entry1 = ids["《炒饭指南》第一章"]
    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": entry1, "a": "user/-/label/科技"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # 不传 type → FOLDER 优先 → folder 下 3 个条目
    r = await client.get(f"{BASE}/stream/contents/user/-/label/科技", headers=h)
    assert len(r.json()["items"]) == 3

    # type=folder → 3 个
    r = await client.get(
        f"{BASE}/stream/contents/user/-/label/科技",
        params={"type": "folder"},
        headers=h,
    )
    assert len(r.json()["items"]) == 3

    # type=tag → 只有收藏的那 1 个
    r = await client.get(
        f"{BASE}/stream/contents/user/-/label/科技",
        params={"type": "tag"},
        headers=h,
    )
    items = r.json()["items"]
    assert len(items) == 1
    assert "第一章" in items[0]["title"]


async def test_starred_uncategorized(client: AsyncClient):
    """未分类收藏流只返回 tag_id=None 的收藏"""
    h = await _register(client)
    await _subscribe(client, h)

    ids = await _title_to_id(client, h)
    entry1 = ids["《炒饭指南》第一章"]
    entry2 = ids["《炒饭指南》第二章"]

    # 纯收藏 entry-1（无 tag）
    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": entry1, "a": "user/-/state/com.google/starred"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    # 收藏 entry-2 到 tag
    await client.post(
        f"{BASE}/enable-tag",
        data={"s": "user/-/label/收藏夹", "type": "tag"},
        headers=h,
    )
    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": entry2, "a": "user/-/label/收藏夹"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/farewell-rss/starred-uncategorized",
        headers=h,
    )
    items = r.json()["items"]
    assert len(items) == 1
    assert "第一章" in items[0]["title"]


async def test_read_history_stream(client: AsyncClient):
    """历史流只含「真正读过」的条目；批量标已读（timestamp=None）不算读过"""
    h = await _register(client)
    await _subscribe(client, h)
    ids = await _title_to_id(client, h)
    entry1 = ids["《炒饭指南》第一章"]

    # 批量标已读：写入 timestamp=None 的已读状态，不算真正读过
    r = await client.post(
        f"{BASE}/mark-all-as-read",
        data={"s": "user/-/state/com.google/reading-list"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # 已读流有 3 条（含批量标的），历史流为空
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/read", headers=h
    )
    assert len(r.json()["items"]) == 3
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/farewell-rss/history", headers=h
    )
    assert r.json()["items"] == []

    # 单独打开一条：edit-tag add read 会带时间戳 → 进入历史
    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": entry1, "a": "user/-/state/com.google/read"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/farewell-rss/history", headers=h
    )
    items = r.json()["items"]
    assert len(items) == 1
    assert "第一章" in items[0]["title"]
    assert "user/-/state/com.google/read" in items[0]["categories"]

    # 再标记为未读：历史里这条也消失了
    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": entry1, "r": "user/-/state/com.google/read"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/farewell-rss/history", headers=h
    )
    assert r.json()["items"] == []


async def test_mark_all_as_read_type(client: AsyncClient):
    """mark-all-as-read 的 type 参数只标对应类型"""
    h = await _register(client)
    await _subscribe(client, h)

    # 同名 folder + tag
    await client.post(
        f"{BASE}/enable-tag",
        data={"s": "user/-/label/科技", "type": "folder"},
        headers=h,
    )
    await client.post(
        f"{BASE}/enable-tag",
        data={"s": "user/-/label/科技", "type": "tag"},
        headers=h,
    )
    r = await client.get(f"{BASE}/subscription/list", headers=h)
    feed_id = r.json()["subscriptions"][0]["id"]
    await client.post(
        f"{BASE}/subscription/edit",
        data={"ac": "edit", "s": feed_id, "a": "user/-/label/科技"},
        headers=h,
    )

    ids = await _title_to_id(client, h)
    entry1 = ids["《炒饭指南》第一章"]
    await client.post(
        f"{BASE}/edit-tag",
        data={"i": entry1, "a": "user/-/label/科技"},
        headers=h,
    )

    # 只标 tag 下条目已读
    r = await client.post(
        f"{BASE}/mark-all-as-read",
        data={"s": "user/-/label/科技", "type": "tag"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # tag 流下的条目应已读
    r = await client.get(
        f"{BASE}/stream/contents/user/-/label/科技",
        params={"type": "tag"},
        headers=h,
    )
    items = r.json()["items"]
    assert "user/-/state/com.google/read" in items[0]["categories"]


# ─── Google Reader 协议与完整资源生命周期 ────────────────────────────────


async def test_auth_get_token_user_info_and_xml_negotiation(client: AsyncClient):
    """认证 GET/POST、T token、用户信息和 XML 拒绝均符合协议约定"""
    r = await client.get(
        "/api/accounts/ClientRegister",
        params={"Email": "protocol-user", "Passwd": "pw", "friendly_name": "协议用户"},
    )
    assert r.status_code in (200, 201), r.text
    auth = _auth(r.text)
    headers = {"Authorization": f"GoogleLogin auth={auth}"}

    r = await client.get(
        "/api/accounts/ClientLogin",
        params={"Email": "protocol-user", "Passwd": "pw"},
    )
    assert r.status_code == 200, r.text
    assert _auth(r.text).split("/", 1)[1]

    r = await client.get(f"{BASE}/token", headers=headers)
    assert r.status_code == 200, r.text
    assert r.text == auth.split("/", 1)[1]

    r = await client.get(f"{BASE}/user-info", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "userId": "protocol-user",
        "userName": "协议用户",
        "userProfileId": r.json()["userProfileId"],
        "userEmail": "protocol-user",
        "isAdmin": True,
    }

    r = await client.get(
        f"{BASE}/subscription/list", params={"output": "xml"}, headers=headers
    )
    assert r.status_code == 501, r.text


async def test_subscription_list_is_read_only(client: AsyncClient):
    """subscription/list 是读接口：源不在了只跳过，不顺手删订阅

    清理交给 scheduler.prune_orphan_subscriptions —— 维护任务的活，不该让（会被轮询 /
    预取 / 重放的）GET 承担。
    """
    from sqlalchemy import text

    from farewell_rss.db.db import get_session

    headers = await _register(client, "subscription-readonly")
    with patch("farewell_rss.services.feed.fetch", side_effect=_fake_fetch):
        r = await client.post(
            f"{BASE}/subscription/edit",
            data={"ac": "subscribe", "s": "feed/https://example.com/readonly.xml"},
            headers=headers,
        )
    assert r.status_code == 200, r.text

    # 直接删源，绕开「只删没有订阅者的源」那道保护，造出孤儿订阅
    async for session in client._transport.app.dependency_overrides[get_session]():
        await session.execute(
            text("DELETE FROM feeds WHERE id IN (SELECT feed_id FROM subscriptions)")
        )
        await session.commit()
        break

    r = await client.get(f"{BASE}/subscription/list", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["subscriptions"] == []  # 列表里不出现

    # 但订阅行还在：读取路径没有写库（清理是 scheduler 的事）
    async for session in client._transport.app.dependency_overrides[get_session]():
        assert await session.scalar(text("SELECT count(*) FROM subscriptions")) == 1
        break


async def test_subscription_edit_lifecycle_and_validation(client: AsyncClient):
    """subscription/edit 的 subscribe/edit/unsubscribe 分支和错误参数"""
    headers = await _register(client, "subscription-user")

    with patch("farewell_rss.services.feed.fetch", side_effect=_fake_fetch):
        r = await client.post(
            f"{BASE}/subscription/edit",
            data={
                "ac": "subscribe",
                "s": "feed/https://example.com/second.xml",
                "t": "自定义标题",
            },
            headers=headers,
        )
    assert r.status_code == 200, r.text

    r = await client.get(f"{BASE}/subscription/list", headers=headers)
    assert r.status_code == 200, r.text
    subscriptions = r.json()["subscriptions"]
    assert len(subscriptions) == 1
    assert subscriptions[0]["title"] == "自定义标题"
    feed_id = subscriptions[0]["id"]

    r = await client.post(
        f"{BASE}/subscription/edit",
        data={"ac": "edit", "s": feed_id, "t": "重命名"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"{BASE}/subscription/list", headers=headers)
    assert r.json()["subscriptions"][0]["title"] == "重命名"

    r = await client.post(
        f"{BASE}/subscription/edit",
        data={"ac": "unsubscribe", "s": feed_id},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"{BASE}/subscription/list", headers=headers)
    assert r.json()["subscriptions"] == []

    r = await client.post(
        f"{BASE}/subscription/edit",
        data={"ac": "unsubscribe", "s": "feed/not-a-number"},
        headers=headers,
    )
    assert r.status_code == 400, r.text

    r = await client.post(
        f"{BASE}/subscription/quickadd",
        data={"quickadd": "not-a-url"},
        headers=headers,
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["numResults"] == 0


async def test_stream_filters_pagination_search_and_item_endpoints(client: AsyncClient):
    """标准状态流、Feed/搜索流、筛选、时间范围、分页及 items 端点"""
    headers = await _register(client, "stream-user")
    await _subscribe(client, headers)
    ids = await _title_to_id(client, headers)
    first_id = ids["《炒饭指南》第一章"]
    second_id = ids["《炒饭指南》第二章"]

    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": first_id, "a": "user/-/state/com.google/read"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": second_id, "a": "user/-/state/com.google/starred"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    for stream_id, expected_titles in (
        ("user/-/state/com.google/read", {"《炒饭指南》第一章"}),
        (
            "user/-/state/com.google/unread",
            {"《炒饭指南》第二章", "《炒饭指南》第三章"},
        ),
        ("user/-/state/com.google/starred", {"《炒饭指南》第二章"}),
    ):
        r = await client.get(f"{BASE}/stream/contents/{stream_id}", headers=headers)
        assert r.status_code == 200, r.text
        assert {item["title"] for item in r.json()["items"]} == expected_titles
        # 顶层 `updated` 是整秒（浮点会让 Gson 客户端解析抛异常，见 items/contents 那条）
        assert isinstance(r.json()["updated"], int), r.json()["updated"]

    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/reading-list",
        params={"it": "user/-/state/com.google/unread"},
        headers=headers,
    )
    assert [item["title"] for item in r.json()["items"]] == [
        "《炒饭指南》第三章",
        "《炒饭指南》第二章",
    ]
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/reading-list",
        params={"xt": "user/-/state/com.google/read"},
        headers=headers,
    )
    assert all("第一章" not in item["title"] for item in r.json()["items"])

    feed_id = (await client.get(f"{BASE}/subscription/list", headers=headers)).json()[
        "subscriptions"
    ][0]["id"]
    r = await client.get(f"{BASE}/stream/contents/{feed_id}", headers=headers)
    assert len(r.json()["items"]) == 3

    r = await client.get(
        f"{BASE}/stream/contents/user/-/search/炒饭指南",
        params={"n": 1},
        headers=headers,
    )
    assert len(r.json()["items"]) == 1
    assert "continuation" in r.json()
    continuation = r.json()["continuation"]
    r = await client.get(
        f"{BASE}/stream/contents/user/-/search/炒饭指南",
        params={"n": 5, "c": continuation},
        headers=headers,
    )
    assert len(r.json()["items"]) == 2

    r = await client.get(
        f"{BASE}/stream/items/ids",
        params={"s": "user/-/state/com.google/reading-list", "n": 2},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["itemRefs"]) == 2
    item_ids = [item["id"] for item in r.json()["itemRefs"]]
    r = await client.post(
        f"{BASE}/stream/items/contents",
        data={"i": item_ids},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 2
    assert all(
        item["origin"]["streamId"].startswith("feed/") for item in r.json()["items"]
    )
    # `updated` 必须是**整秒**：Python 的 time() 是浮点，带小数点的数字会被 Gson 按 Long
    # 解析时直接抛异常（ReadYou 就是这样：我们回 200、字段全对，它每轮 sync 却全失败）。
    # Python 的 json 会把 1791284278.015 解析成 float、把 1791284278 解析成 int，所以这条
    # 断言正好卡住这个 bug。
    assert isinstance(r.json()["updated"], int), r.json()["updated"]

    # Reeder 回传条目 id 时发的是**不带 `tag:google.com,2005:reader/item/` 前缀的 16 位
    # hex**：以前这条会回 400，然后它就陷入重试，一篇文章都同步不出来（FreshRSS 因为用
    # `hex2dec(basename($e_id))` 而天然容忍）。
    r = await client.post(
        f"{BASE}/stream/items/contents",
        data={"i": [f"{int(item_id):016x}" for item_id in item_ids]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 2

    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/reading-list",
        params={"n": 1, "r": "o"},
        headers=headers,
    )
    assert len(r.json()["items"]) == 1
    assert "continuation" in r.json()


async def test_items_contents_only_returns_visible_entries(client: AsyncClient):
    """`stream/items/contents` 按 entry id 收，所以要校验可见性

    条目 id 是自增整数（`tag:google.com,2005:reader/item/{id:016x}`），不校验的话按 id
    枚举就能读到别人私有源里的正文。
    """
    alice = await _register(client, "items-alice")
    await _subscribe(client, alice)
    alice_items = await _title_to_id(client, alice)
    assert len(alice_items) == 3

    # Bob 什么都没订阅：别人的 id 一条都不给
    bob = await _register(client, "items-bob")
    r = await client.post(
        f"{BASE}/stream/items/contents",
        data={"i": list(alice_items.values())},
        headers=bob,
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []

    # Bob 订阅同一个（公共）源之后就能看到 —— 源是共享的，这是既有语义
    await _subscribe(client, bob)
    r = await client.post(
        f"{BASE}/stream/items/contents",
        data={"i": list(alice_items.values())},
        headers=bob,
    )
    assert len(r.json()["items"]) == 3

    # 收藏一条 → 退订 → 那条仍取得到（收藏是 Bob 自己的数据），其余取不到
    starred = alice_items["《炒饭指南》第一章"]
    other = alice_items["《炒饭指南》第二章"]
    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": starred, "a": "user/-/state/com.google/starred"},
        headers=bob,
    )
    assert r.status_code == 200, r.text
    subscriptions = (await client.get(f"{BASE}/subscription/list", headers=bob)).json()[
        "subscriptions"
    ]
    r = await client.post(
        f"{BASE}/subscription/edit",
        data={"ac": "unsubscribe", "s": subscriptions[0]["id"]},
        headers=bob,
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        f"{BASE}/stream/items/contents", data={"i": [starred]}, headers=bob
    )
    assert [item["id"] for item in r.json()["items"]] == [starred]
    r = await client.post(
        f"{BASE}/stream/items/contents", data={"i": [other]}, headers=bob
    )
    assert r.json()["items"] == []


async def test_search_syntax_error_is_a_client_error(client: AsyncClient):
    """查询串本身有问题 → 400（用户少打一个引号不该算服务器故障）"""
    headers = await _register(client, "search-syntax-user")
    await _subscribe(client, headers)

    # 引号不闭合（FTS5 报 unterminated string）、空表达式（报 fts5: syntax error）
    # 和只有空白（会变成 LIKE '%%' 把整库倒出来）
    for bad in ("%22炒饭指南", "炒饭%20AND", "%20"):
        r = await client.get(
            f"{BASE}/stream/contents/user/-/search/{bad}", headers=headers
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "InvalidSearchQueryError"

    # 对照组：同一个端点，正常查询照样 200
    r = await client.get(
        f"{BASE}/stream/contents/user/-/search/炒饭指南", headers=headers
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 3

    # 短查询（2 字，trigram 索引里没有这种 gram）走 LIKE 兜底，照样搜得到
    r = await client.get(f"{BASE}/stream/contents/user/-/search/炒饭", headers=headers)
    assert r.status_code == 200, r.text
    assert {item["title"] for item in r.json()["items"]} == {
        "《炒饭指南》第一章",
        "《炒饭指南》第二章",
        "《炒饭指南》第三章",
    }


async def test_search_is_scoped_to_my_subscriptions(client: AsyncClient):
    """搜索只搜自己订阅的源：别人的源里的文章搜不到

    `entries` 是所有用户共用的（源也是共享的），搜索不带订阅范围的话，A 搜一个词就能
    读到 B 订阅的源里的文章 —— 这条就是那个越权读的回归测试。
    """
    mine = FetchedFeed(
        href="https://example.com/mine.xml",
        title="我的源",
        entries=[FetchedEntry(guid="mine-1", title="我的文章：香菜炒饭")],
    )
    theirs = FetchedFeed(
        href="https://example.com/theirs.xml",
        title="别人的源",
        entries=[FetchedEntry(guid="theirs-1", title="别人的文章：秘制炒饭")],
    )

    async def _fetch(url, etag=None, modified=None):
        return mine if url.endswith("mine.xml") else theirs

    headers_a = await _register(client, "search-scope-a")
    headers_b = await _register(client, "search-scope-b")
    with patch("farewell_rss.services.feed.fetch", side_effect=_fetch):
        for headers, url in ((headers_a, mine.href), (headers_b, theirs.href)):
            r = await client.post(
                f"{BASE}/subscription/quickadd",
                data={"quickadd": url},
                headers=headers,
            )
            assert r.status_code == 200, r.text

    async def _search(headers: dict, query: str) -> list[str]:
        r = await client.get(
            f"{BASE}/stream/contents/user/-/search/{query}", headers=headers
        )
        assert r.status_code == 200, r.text
        return [it["title"] for it in r.json()["items"]]

    assert await _search(headers_a, "香菜炒饭") == ["我的文章：香菜炒饭"]
    # 别人的：一条都不该有
    assert await _search(headers_a, "秘制炒饭") == []
    # 对照：B 搜自己的还是搜得到（证明不是把搜索结果整个干掉了）
    assert await _search(headers_b, "秘制炒饭") == ["别人的文章：秘制炒饭"]
    # 短词兜底那条路（2 字）也要带范围：两个源的文章都含「炒饭」
    assert await _search(headers_a, "炒饭") == ["我的文章：香菜炒饭"]
    assert await _search(headers_b, "炒饭") == ["别人的文章：秘制炒饭"]


async def test_label_lifecycle_and_type_specific_streams(client: AsyncClient):
    """folder/tag 创建、重命名、删除及同名流类型选择"""
    headers = await _register(client, "label-user")
    await _subscribe(client, headers)
    folder_id = "user/-/label/项目"
    tag_id = "user/-/label/项目"
    for label_id, type_ in ((folder_id, "folder"), (tag_id, "tag")):
        r = await client.post(
            f"{BASE}/enable-tag", data={"s": label_id, "type": type_}, headers=headers
        )
        assert r.status_code == 200, r.text

    ids = await _title_to_id(client, headers)
    r = await client.post(
        f"{BASE}/edit-tag",
        data={"i": ids["《炒饭指南》第一章"], "a": tag_id},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    feed_id = (await client.get(f"{BASE}/subscription/list", headers=headers)).json()[
        "subscriptions"
    ][0]["id"]
    await client.post(
        f"{BASE}/subscription/edit",
        data={"ac": "edit", "s": feed_id, "a": folder_id},
        headers=headers,
    )

    r = await client.get(
        f"{BASE}/stream/contents/{folder_id}", params={"type": "tag"}, headers=headers
    )
    assert len(r.json()["items"]) == 1
    r = await client.get(
        f"{BASE}/stream/contents/{folder_id}",
        params={"type": "folder"},
        headers=headers,
    )
    assert len(r.json()["items"]) == 3

    r = await client.post(
        f"{BASE}/rename-tag",
        data={"s": folder_id, "dest": "user/-/label/工作", "type": "folder"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = await client.get(
        f"{BASE}/stream/contents/user/-/label/工作",
        params={"type": "folder"},
        headers=headers,
    )
    assert len(r.json()["items"]) == 3

    r = await client.post(
        f"{BASE}/disable-tag",
        data={"s": "user/-/label/工作", "type": "folder"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = await client.get(
        f"{BASE}/stream/contents/user/-/label/工作",
        params={"type": "folder"},
        headers=headers,
    )
    assert r.status_code == 404

    r = await client.post(
        f"{BASE}/rename-tag",
        data={"s": tag_id, "dest": "user/-/label/标签"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"{BASE}/disable-tag", data={"s": "user/-/label/标签"}, headers=headers
    )
    assert r.status_code == 200, r.text


async def test_unread_count_and_mark_all_as_read_scopes(client: AsyncClient):
    """未读计数覆盖 feed/folder/tag，批量已读覆盖截止 ID 和 starred 流"""
    headers = await _register(client, "count-user")
    await _subscribe(client, headers)
    ids = await _title_to_id(client, headers)
    first_id = ids["《炒饭指南》第一章"]
    await client.post(
        f"{BASE}/enable-tag",
        data={"s": "user/-/label/收藏", "type": "tag"},
        headers=headers,
    )
    await client.post(
        f"{BASE}/edit-tag",
        data={
            "i": first_id,
            "a": ["user/-/state/com.google/starred", "user/-/label/收藏"],
        },
        headers=headers,
    )
    r = await client.get(f"{BASE}/unread-count", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["max"] == 3
    counts = {item["id"]: item["count"] for item in r.json()["unreadcounts"]}
    assert counts["user/-/state/com.google/reading-list"] == 3
    assert counts["user/-/label/收藏"] == 1

    r = await client.post(
        f"{BASE}/mark-all-as-read",
        data={"s": "user/-/state/com.google/starred", "ts": first_id},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = await client.get(
        f"{BASE}/stream/contents/user/-/state/com.google/unread", headers=headers
    )
    assert len(r.json()["items"]) <= 2

    r = await client.post(
        f"{BASE}/mark-all-as-read",
        data={"s": "feed/not-a-number"},
        headers=headers,
    )
    assert r.status_code == 400, r.text


async def test_change_password_and_delete_account_permissions(client: AsyncClient):
    """本人改密/删号、非管理员越权和管理员删除用户"""
    admin = await _register(client, "account-admin")
    # 这两个只为了把账号建出来：后面的请求体里用的是用户名字符串，用不到返回的
    # Authorization 头，所以不接返回值
    await _register(client, "account-owner")
    await _register(client, "account-other")

    r = await client.post(
        "/api/accounts/ChangePassword",
        data={
            "username": "account-owner",
            "new_password": "new-pw",
            "operator_username": "account-owner",
            "operator_password": "pw",
        },
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/accounts/ClientLogin", data={"Email": "account-owner", "Passwd": "new-pw"}
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        "/api/accounts/ChangePassword",
        data={
            "username": "account-other",
            "new_password": "x",
            "operator_username": "account-owner",
            "operator_password": "new-pw",
        },
    )
    assert r.status_code == 403, r.text

    r = await client.post(
        "/api/accounts/DeleteAccount",
        data={
            "username": "account-other",
            "operator_username": "account-admin",
            "operator_password": "pw",
        },
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/accounts/ClientLogin", data={"Email": "account-other", "Passwd": "pw"}
    )
    assert r.status_code == 401, r.text

    r = await client.post(
        "/api/accounts/DeleteAccount",
        data={
            "username": "account-owner",
            "operator_username": "account-owner",
            "operator_password": "new-pw",
        },
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/accounts/ClientLogin", data={"Email": "account-owner", "Passwd": "new-pw"}
    )
    assert r.status_code == 401, r.text

    # 删除后的账户不能再次登录
    r = await client.post(
        "/api/accounts/DeleteAccount",
        data={
            "username": "account-owner",
            "operator_username": "account-owner",
            "operator_password": "new-pw",
        },
    )
    assert r.status_code == 400, r.text

    assert admin["Authorization"].startswith("GoogleLogin auth=")
