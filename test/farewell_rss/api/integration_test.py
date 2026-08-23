"""API 集成测试 —— "顾客点了份炒饭" 全链路。"""

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

_A = lambda n: FetchedAuthor(name=n, href=None, email=None)

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
async def client():
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

    async def _override_get_session():
        async with TestSession() as s:
            yield s

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


async def test_export_opml_filename_uses_username(client: AsyncClient):
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
