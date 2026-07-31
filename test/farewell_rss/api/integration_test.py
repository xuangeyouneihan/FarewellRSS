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
            "Password": "fried-rice",
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
    data = {"Email": "dup", "Password": "pw", "friendly_name": "D"}
    await client.post("/api/accounts/ClientRegister", data=data)
    r = await client.post("/api/accounts/ClientRegister", data=data)
    assert r.status_code == 409


async def test_login_wrong_password(client: AsyncClient):
    await client.post(
        "/api/accounts/ClientRegister",
        data={
            "Email": "alice",
            "Password": "correct",
            "friendly_name": "A",
        },
    )
    r = await client.post(
        "/api/accounts/ClientLogin",
        data={
            "Email": "alice",
            "Password": "wrong",
        },
    )
    assert r.status_code == 401


async def test_no_auth(client: AsyncClient):
    r = await client.get("/api/reader/api/0/subscription/list")
    assert r.status_code in (401, 422)  # 422 if FastAPI validates before auth
