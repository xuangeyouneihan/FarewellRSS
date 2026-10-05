import http.server
import ssl
import threading
import time
import urllib.error
from datetime import UTC, datetime
from unittest.mock import patch

import feedparser
import pytest

from farewell_rss._version import __version__
from farewell_rss.feed_fetcher import feed_fetcher as feed_fetcher_module
from farewell_rss.feed_fetcher.feed_fetcher import (
    FetchedAuthor,
    FetchedEnclosure,
    FetchedFeed,
    FetchedTag,
    FetchError,
    _parse_author,
    _parse_datetime,
    _parse_enclosure,
    _parse_tag,
    _strip_html,
    _to_html,
    fetch,
)

# ─── RSS/Atom XML 测试数据 ───────────────────────────────────────────────

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>测试博客</title>
    <link>https://example.com</link>
    <description>一个 RSS 测试源</description>
    <pubDate>Wed, 01 Jan 2020 00:00:00 GMT</pubDate>
    <item>
      <title>第一篇文章</title>
      <link>https://example.com/post/1</link>
      <guid isPermaLink="false">post-1</guid>
      <description>&lt;p&gt;这是文章摘要&lt;/p&gt;</description>
      <author>alice@example.com (Alice)</author>
      <pubDate>Wed, 01 Jan 2020 12:00:00 GMT</pubDate>
      <enclosure url="https://example.com/audio.mp3" length="12345" type="audio/mpeg"/>
    </item>
  </channel>
</rss>"""

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title type="text">Atom 博客</title>
  <link href="https://example.com/atom"/>
  <subtitle>一个 Atom 源</subtitle>
  <updated>2020-01-01T00:00:00Z</updated>
  <author>
    <name>Bob</name>
  </author>
  <entry>
    <title type="html">&lt;em&gt;加粗标题&lt;/em&gt;</title>
    <link href="https://example.com/atom/1"/>
    <id>atom-entry-1</id>
    <summary type="text">纯文本摘要</summary>
    <updated>2020-01-01T12:00:00Z</updated>
  </entry>
</feed>"""

EMPTY_FEED_XML = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>空博客</title>
    <link>https://example.com/empty</link>
    <description>没有文章</description>
  </channel>
</rss>"""


def _parse_xml(xml: str) -> feedparser.FeedParserDict:
    """用 feedparser 解析 XML 字符串。"""
    return feedparser.parse(xml)


# ─── 纯函数测试 ──────────────────────────────────────────────────────────


class TestParseDatetime:
    def test_valid_struct_time(self):
        st = time.struct_time((2020, 1, 1, 12, 0, 0, 2, 1, 0))
        result = _parse_datetime(st)
        assert result == datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_none(self):
        assert _parse_datetime(None) is None


class TestParseAuthor:
    def test_full(self):
        result = _parse_author({
            "name": "Alice",
            "href": "https://a.com",
            "email": "a@a.com",
        })
        assert result == FetchedAuthor(
            name="Alice", href="https://a.com", email="a@a.com"
        )

    def test_none(self):
        assert _parse_author(None) is None


class TestParseTag:
    def test_full(self):
        result = _parse_tag({"term": "tech", "scheme": "cat", "label": "科技"})
        assert result == FetchedTag(term="tech", scheme="cat", label="科技")

    def test_none(self):
        assert _parse_tag(None) is None


class TestParseEnclosure:
    def test_full(self):
        result = _parse_enclosure({
            "href": "https://x.com/f.mp3",
            "length": "100",
            "type": "audio/mpeg",
        })
        assert result == FetchedEnclosure(
            href="https://x.com/f.mp3", length=100, type="audio/mpeg"
        )

    def test_none(self):
        assert _parse_enclosure(None) is None


class TestStripHtml:
    def test_html(self):
        # HTMLParser 保留标签间空白，"Hello <em>World</em>" → "Hello  World" 或类似
        result = _strip_html("<p>Hello <em>World</em></p>")
        assert "Hello" in result
        assert "World" in result

    def test_none(self):
        assert _strip_html(None) is None


class TestToHtml:
    def test_plain(self):
        result = _to_html("Hello\n\nWorld")
        assert "<p>Hello</p>" in result
        assert "<p>World</p>" in result

    def test_single_line(self):
        result = _to_html("Hello World")
        assert result == "<p>Hello World</p>"

    def test_none(self):
        assert _to_html(None) is None

    def test_empty(self):
        assert _to_html("") == ""


# ─── fetch() 集成测试（mock 网络，真实 feedparser 解析）──────────────────


def _parsed_html_body() -> feedparser.FeedParserDict:
    """复现「拿到的是 HTML 而不是 feed」：feedparser 会置 bozo=NonXMLContentType"""
    return feedparser.parse(
        b"<html><body>challenge</body></html>",
        response_headers={"content-type": "text/html"},
    )


async def _run_fetch_with_parsed(
    parsed: feedparser.FeedParserDict,
) -> FetchedFeed | None:
    """用预先造好的 feedparser 结果替换网络请求，调用 fetch()。"""

    async def fake_to_thread(func, *args, **kwargs):
        return parsed

    with patch("asyncio.to_thread", fake_to_thread):
        return await fetch("https://example.com/feed.xml")


async def _run_fetch_with_xml(xml: str) -> FetchedFeed | None:
    """用 XML 字符串替换网络请求，调用 fetch()。"""
    parsed = feedparser.parse(xml)
    # feedparser 直接解析字符串时不设这些属性，需要手动补
    parsed["status"] = 200
    parsed["href"] = "https://example.com/feed.xml"
    if "etag" not in parsed:
        parsed["etag"] = None
    if "modified" not in parsed:
        parsed["modified"] = None
    return await _run_fetch_with_parsed(parsed)


class TestFetchRss:
    async def test_basic(self):
        result = await _run_fetch_with_xml(RSS_XML)
        assert result is not None
        assert result.title == "测试博客"
        assert result.link == "https://example.com"
        assert result.subtitle == "一个 RSS 测试源"
        assert len(result.entries) == 1

    async def test_entry(self):
        result = await _run_fetch_with_xml(RSS_XML)
        entry = result.entries[0]
        assert entry.guid == "post-1"
        assert entry.title == "第一篇文章"
        assert entry.link == "https://example.com/post/1"
        assert "文章摘要" in (entry.summary or "")

    async def test_enclosure(self):
        result = await _run_fetch_with_xml(RSS_XML)
        enclosures = result.entries[0].enclosures
        assert len(enclosures) == 1
        assert enclosures[0].href == "https://example.com/audio.mp3"
        assert enclosures[0].length == 12345
        assert enclosures[0].type == "audio/mpeg"


class TestFetchAtom:
    async def test_basic(self):
        result = await _run_fetch_with_xml(ATOM_XML)
        assert result is not None
        assert result.title == "Atom 博客"
        assert result.subtitle == "一个 Atom 源"

    async def test_html_title_stripped(self):
        """Atom title type=html 时应去掉标签"""
        result = await _run_fetch_with_xml(ATOM_XML)
        entry = result.entries[0]
        assert entry.title == "加粗标题"

    async def test_plain_summary_converted(self):
        """纯文本 summary 应生成 HTML 版本"""
        result = await _run_fetch_with_xml(ATOM_XML)
        entry = result.entries[0]
        assert entry.summary_plain == "纯文本摘要"
        assert "<p>纯文本摘要</p>" in (entry.summary or "")


class TestUserAgent:
    def test_agent_shape(self):
        """UA 形制：`FarewellRSS/<版本> (<系统>; <仓库地址>)`

        版本号的来源与兜底在 `test/farewell_rss/version_test.py` 里测。
        """
        assert feed_fetcher_module._AGENT.startswith(f"FarewellRSS/{__version__} (")
        assert feed_fetcher_module._REPO in feed_fetcher_module._AGENT


class TestFetchEdgeCases:
    async def test_empty_feed(self):
        """无条目的 RSS 返回 None"""
        result = await _run_fetch_with_xml(EMPTY_FEED_XML)
        assert result is None

    async def test_not_modified(self):
        """304 未修改返回 None（增量更新靠它，不算失败）"""
        parsed = feedparser.parse(RSS_XML)
        parsed["status"] = 304

        assert await _run_fetch_with_parsed(parsed) is None

    async def test_transport_error_without_status(self):
        """传输层失败（证书/DNS/连接/超时）时 feedparser 连 status 都不设。

        修之前这里会因 `raw_feed.status` 抛 AttributeError —— 网络失败被伪装成代码 bug。
        真实案例：https://www.sitstars.com/feed/ 的证书过期。
        """
        parsed = feedparser.parse(EMPTY_FEED_XML)
        assert "status" not in parsed
        parsed["bozo"] = True
        parsed["bozo_exception"] = urllib.error.URLError(
            ssl.SSLCertVerificationError("certificate has expired")
        )

        with pytest.raises(FetchError):
            await _run_fetch_with_parsed(parsed)

    async def test_http_error(self):
        """403/404/5xx 抛 FetchError：错误页不能被当成「源没有条目」"""
        parsed = _parsed_html_body()
        parsed["status"] = 403

        with pytest.raises(FetchError):
            await _run_fetch_with_parsed(parsed)

    async def test_unparsable_body_without_entries(self):
        """2xx + 解析异常 + 无条目：行为不变（仍返回 None），只是日志能分辨了"""
        parsed = _parsed_html_body()
        parsed["status"] = 200
        assert parsed.bozo

        assert await _run_fetch_with_parsed(parsed) is None

    async def test_network_error(self):
        """网络异常应抛 FetchError"""

        async def fake_to_thread(func, *args, **kwargs):
            raise OSError("连接超时")

        with patch("asyncio.to_thread", fake_to_thread):  # noqa: SIM117
            with pytest.raises(FetchError):
                await fetch("https://example.com/dead.xml")

    async def test_sends_own_user_agent(self):
        """抓取要带自己的 UA（`FarewellRSS/<版本> (<系统>; <仓库地址>)`）

        feedparser 默认的 `feedparser/x.y (+…)` 会被一些 WAF 拦：实测
        `free.apprcn.com/feed/` 回 403，换成自己的 UA 就是 200 + 3 条。这里用本地
        HTTP 服务端真实地看一眼收到的是什么。
        """
        seen: dict[str, str | None] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen["ua"] = self.headers.get("User-Agent")
                data = RSS_XML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # 别往测试输出里喷日志
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = await fetch(f"http://127.0.0.1:{server.server_address[1]}/feed")
        finally:
            server.shutdown()

        assert result is not None
        assert seen["ua"] == feed_fetcher_module._AGENT
        assert seen["ua"] is not None
        assert seen["ua"].startswith("FarewellRSS/")
        assert feed_fetcher_module._REPO in seen["ua"]
        assert "feedparser" not in seen["ua"]
