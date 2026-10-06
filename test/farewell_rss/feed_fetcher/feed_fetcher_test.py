import base64
import contextlib
import gzip
import http.server
import logging
import threading
import time
from datetime import UTC, datetime
from unittest.mock import patch

import feedparser
import httpx
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
    _split_credentials,
    _strip_html,
    _to_html,
    fetch,
    normalize_href,
    redact_credentials,
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


class TestSplitCredentials:
    def test_no_credentials(self):
        """没有 userinfo 就原样返回（绝大多数源都是这样）"""
        url = "https://example.com/feed.xml"

        assert _split_credentials(url) == (url, None)

    def test_strips_credentials_and_decodes_them(self):
        """凭据要从 URL 里摘掉，而且要**先解码**再进 Basic 头

        `me%2Bfeeds` 的真实用户名是 `me+feeds`。urllib 的 `.username` 不解码——直接把
        编码串送出去，服务端比对的是解码后的值，必然 401（feedparser 也踩在这一点上）。
        """
        clean, authorization = _split_credentials(
            "https://me%2Bfeeds:p%40ss@example.com/feed.xml"
        )

        assert clean == "https://example.com/feed.xml"
        assert authorization == "Basic " + base64.b64encode(b"me+feeds:p@ss").decode()

    def test_clean_url_keeps_path_query_and_fragment(self):
        """clean_url 只摘凭据，其余部分原样保留

        它只用来拼请求地址（身份由调用方给的 href 决定，见 `FeedService.insert_by_href`），
        所以“原样”是故意的：不要把 URL 规范化掺进来。
        """
        clean, _ = _split_credentials(
            "https://user:pw@example.com:8443/a/Feed.xml?q=1#frag"
        )

        assert clean == "https://example.com:8443/a/Feed.xml?q=1#frag"


class TestNormalizeHref:
    def test_without_credentials_is_untouched(self):
        url = "https://example.com/Feed.xml?q=1#frag"

        assert normalize_href(url) == url

    def test_credentials_are_percent_encoded(self):
        """`p@ss` 与 `p%40ss` 折叠成同一个串（href 是去重键，不能两种写法两行）"""
        expected = "http://u:p%40ss@example.com/feed"

        assert normalize_href("http://u:p@ss@example.com/feed") == expected
        assert normalize_href(expected) == expected
        assert normalize_href("http://me%2Bfeeds:pw@example.com/feed") == (
            "http://me%2Bfeeds:pw@example.com/feed"
        )

    def test_keeps_host_case_and_ipv6_brackets(self):
        """除 userinfo 外一个字符都不动（不能用 parts.hostname 重建）"""
        assert normalize_href("http://u:p@Example.COM:8443/Feed") == (
            "http://u:p@Example.COM:8443/Feed"
        )
        assert normalize_href("http://u:p@[::1]:8080/feed") == (
            "http://u:p@[::1]:8080/feed"
        )

    def test_unparsable_url_returns_as_is(self):
        assert normalize_href("http://[::1/feed") == "http://[::1/feed"


class TestRedactCredentials:
    def test_replaces_userinfo(self):
        assert redact_credentials("https://u:p@example.com/feed?q=1") == (
            "https://***@example.com/feed?q=1"
        )

    def test_without_credentials_is_untouched(self):
        url = "https://example.com/feed"

        assert redact_credentials(url) == url

    def test_unparsable_url_is_fully_masked(self):
        """解析不了就整串打码：判断不出有没有凭据时，宁可少一条信息"""
        assert redact_credentials("http://[::1/feed") == "***"


# ─── fetch() 测试（真实 feedparser 解析）──────────────────────────────
#
# 抓取层走 httpx：能真起服务端验的（UA、条件头、跳转、压缩、超时、凭据）就真起一个
# 本地 HTTP 服务端；只关心状态码分支的用例，把网络边界 `_request` 换成造好的响应。

_RESPONSE_URL = "https://example.com/feed.xml"


def _stub_response(
    body: bytes = b"",
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    url: str = _RESPONSE_URL,
) -> httpx.Response:
    """造一个不碰网络的 httpx.Response（`request` 是它算 `.url` 的必需项）"""
    return httpx.Response(
        status,
        content=body,
        headers=headers or {},
        request=httpx.Request("GET", url),
    )


@contextlib.contextmanager
def _patched_request(response: httpx.Response | Exception):
    """把网络边界（`_request`）换成这个响应或异常

    替换的是 `_request` 而不是 httpx 本身：状态码分支、href / etag 的取值、解析全在
    我们自己的代码里，这些才是要测的东西。
    """

    async def fake_request(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    with patch.object(feed_fetcher_module, "_request", fake_request):
        yield


async def _run_fetch_with_xml(xml: str) -> FetchedFeed | None:
    """一份 XML 走完整个 fetch()（真实 feedparser 解析）"""
    with _patched_request(
        _stub_response(xml.encode(), headers={"content-type": "application/rss+xml"})
    ):
        return await fetch(_RESPONSE_URL)


class _QuietServer(http.server.ThreadingHTTPServer):
    """测试用服务端：客户端提前放弃（超时用例）时别喷 traceback"""

    def handle_error(self, request, client_address):
        pass


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextlib.contextmanager
def _http_server(handler: type[http.server.BaseHTTPRequestHandler]):
    """起一个本地 HTTP 服务端，yield 它的 base URL"""
    server = _QuietServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _rss_handler(
    body: bytes = RSS_XML.encode(),
    *,
    status: int = 200,
    content_type: str = "application/rss+xml",
    extra_headers: dict[str, str] | None = None,
    seen: dict[str, str | None] | None = None,
):
    """造一个「只回一份固定内容」的 handler（顺手把收到的相关请求头记进 seen）"""

    class Handler(_QuietHandler):
        def do_GET(self):
            if seen is not None:
                for name in (
                    "User-Agent",
                    "If-None-Match",
                    "If-Modified-Since",
                    "Authorization",
                ):
                    seen[name.lower()] = self.headers.get(name)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            # 304 按定义没有响应体，也就不能带 Content-Length
            if status != 304:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if status != 304:
                self.wfile.write(body)

    return Handler


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
        assert await _run_fetch_with_xml(EMPTY_FEED_XML) is None

    async def test_not_modified(self):
        """304 未修改返回 None（增量更新靠它，不算失败）"""
        with _patched_request(_stub_response(status=304)):
            assert await fetch(_RESPONSE_URL) is None

    async def test_transport_failure_raises_fetch_error(self):
        """传输层失败（拒连 / 证书 / DNS）都变成 FetchError

        真实案例：https://www.sitstars.com/feed/ 的证书过期。旧实现靠「feedparser
        连 status 都不设」来识别它（修之前还会先抛 AttributeError，把网络失败伪装成
        代码 bug）；这里连一个没人监听的端口，走的是 httpx 的 ConnectError。
        """
        with pytest.raises(FetchError):
            await fetch("http://127.0.0.1:1/feed")

    async def test_too_many_redirects_is_a_fetch_error(self):
        """跳转打转（超过 max_redirects）算抓取失败，不能当成「源没有条目」"""

        class Handler(_QuietHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "/loop")
                self.send_header("Content-Length", "0")
                self.end_headers()

        with _http_server(Handler) as base, pytest.raises(FetchError):
            await fetch(f"{base}/loop")

    async def test_http_error(self):
        """403/404/5xx 抛 FetchError：错误页不能被当成「源没有条目」"""
        challenge = _stub_response(
            b"<html><body>challenge</body></html>",
            status=403,
            headers={"content-type": "text/html"},
        )

        with _patched_request(challenge), pytest.raises(FetchError):
            await fetch(_RESPONSE_URL)

    async def test_unparsable_body_without_entries(self):
        """2xx + 解析异常 + 无条目：行为不变（仍返回 None），只是日志能分辨了"""
        challenge = _stub_response(
            b"<html><body>challenge</body></html>",
            headers={"content-type": "text/html"},
        )

        with _patched_request(challenge):
            assert await fetch(_RESPONSE_URL) is None

    async def test_invalid_url_is_a_fetch_error(self):
        """端口不是数字：`_is_http_url` 拦不住（urlparse 不校验端口），到取 port 时才炸

        不翻译成 FetchError 的话 `FeedService.update` 的 `except FetchError` 接不住，
        每轮刷新都会冒一条未捕获异常。
        """
        with pytest.raises(FetchError):
            await fetch("http://example.com:notaport/feed")


class TestFetchOverTheWire:
    """真走一遍 HTTP（本地服务端）"""

    async def test_sends_own_user_agent(self):
        """抓取要带自己的 UA（`FarewellRSS/<版本> (<系统>; <仓库地址>)`）

        feedparser 默认的 `feedparser/x.y (+…)` 会被一些 WAF 拦：实测
        `free.apprcn.com/feed/` 回 403，换成自己的 UA 就是 200 + 3 条。这里用本地
        HTTP 服务端真实地看一眼收到的是什么。
        """
        seen: dict[str, str | None] = {}

        with _http_server(_rss_handler(seen=seen)) as base:
            result = await fetch(f"{base}/feed")

        assert result is not None
        agent = seen["user-agent"] or ""
        assert agent == feed_fetcher_module._AGENT
        assert agent.startswith("FarewellRSS/")
        assert feed_fetcher_module._REPO in agent
        assert "feedparser" not in agent

    async def test_sends_conditional_headers(self):
        """带了 etag / modified 就要发条件头（增量更新和假 304 兜底全靠它）"""
        seen: dict[str, str | None] = {}

        with _http_server(_rss_handler(status=304, seen=seen)) as base:
            result = await fetch(
                f"{base}/feed",
                etag='"v1"',
                modified="Wed, 01 Jan 2020 00:00:00 GMT",
            )

        assert result is None  # 304 → 未修改
        assert seen["if-none-match"] == '"v1"'
        assert seen["if-modified-since"] == "Wed, 01 Jan 2020 00:00:00 GMT"

    async def test_decompresses_gzip(self):
        """服务端 gzip 压缩：feedparser 拿到的必须是解压后的字节（httpx 替我们解）"""
        with _http_server(
            _rss_handler(
                gzip.compress(RSS_XML.encode()),
                extra_headers={"Content-Encoding": "gzip"},
            )
        ) as base:
            result = await fetch(f"{base}/feed")

        assert result is not None
        assert result.title == "测试博客"

    async def test_follows_redirect_and_keeps_the_final_url(self):
        """跳转要跟到底：href（订阅的去重键）与条件头都取最终地址那一份"""

        class Handler(_QuietHandler):
            def do_GET(self):
                if self.path == "/old":
                    self.send_response(302)
                    self.send_header("Location", "/new")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = RSS_XML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml")
                self.send_header("ETag", '"v2"')
                self.send_header("Last-Modified", "Wed, 01 Jan 2020 00:00:00 GMT")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        with _http_server(Handler) as base:
            result = await fetch(f"{base}/old")

        assert result is not None
        assert result.href == f"{base}/new"
        assert result.etag == '"v2"'
        assert result.modified == "Wed, 01 Jan 2020 00:00:00 GMT"

    async def test_basic_auth_in_url(self):
        """URL 里的 `user:pass@` 要拆成 Authorization 头发出去

        httpx 不会自己转（实测 0.28.1），不拆就是安静地把凭据丢掉、白吃一个 401。
        """
        seen: dict[str, str | None] = {}

        with _http_server(_rss_handler(seen=seen)) as base:
            host = base.removeprefix("http://")
            result = await fetch(f"http://alice:s3cr3t@{host}/feed")

        assert result is not None
        token = base64.b64encode(b"alice:s3cr3t").decode()
        assert seen["authorization"] == f"Basic {token}"

    async def test_basic_auth_in_url_is_percent_decoded(self):
        """URL 里的凭据是百分号编码的：要解码后再发

        用 urllib 的 `.username` 拿到的是没解码的原串，直接把 `me%2Bfeeds` 丢进
        Authorization 就是 401。
        """
        seen: dict[str, str | None] = {}

        with _http_server(_rss_handler(seen=seen)) as base:
            host = base.removeprefix("http://")
            result = await fetch(f"http://me%2Bfeeds:p%40ss@{host}/feed")

        assert result is not None
        token = base64.b64encode(b"me+feeds:p@ss").decode()
        assert seen["authorization"] == f"Basic {token}"

    async def test_relative_links_resolve_against_the_feed_url(self):
        """条目里的相对链接要拼成绝对地址

        自己发请求之后，feedparser 拿到的只是字节、不知道这些字节从哪来 —— 位置靠
        `content-location` 头（最终地址）传给它。
        """
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>相对链接</title>
  <link>/site</link>
  <item>
    <title>第一篇</title>
    <guid>post-1</guid>
    <link>/posts/1</link>
    <description>&lt;img src="/img/a.png"&gt;</description>
  </item>
</channel></rss>"""

        with _http_server(_rss_handler(xml.encode())) as base:
            result = await fetch(f"{base}/feed")

        assert result is not None
        assert result.link == f"{base}/site"
        entry = result.entries[0]
        assert entry.link == f"{base}/posts/1"
        assert f"{base}/img/a.png" in (entry.summary or "")


class TestFetchTimeout:
    """超时：不响应的源不能把这一轮刷新永久占住（线上就是这么卡死的）"""

    class _NeverAnswers(_QuietHandler):
        def do_GET(self):
            time.sleep(5)  # 连上了，但一直不回包

    async def test_read_timeout(self, monkeypatch):
        """单次读超时：等响应 / 等下一个数据块不能无限期"""
        monkeypatch.setattr(feed_fetcher_module, "_FETCH_READ_TIMEOUT", 0.3)

        with _http_server(self._NeverAnswers) as base:
            started = time.monotonic()
            with pytest.raises(FetchError):
                await fetch(f"{base}/feed")
            elapsed = time.monotonic() - started

        assert elapsed < 3, f"没按读超时放弃，等了 {elapsed:.1f} 秒"

    async def test_total_timeout(self, monkeypatch):
        """整次抓取的硬上限：read 超时只管「每次读」，管不住总时长"""
        monkeypatch.setattr(feed_fetcher_module, "FETCH_TIMEOUT", 0.3)
        monkeypatch.setattr(feed_fetcher_module, "_FETCH_READ_TIMEOUT", 30.0)
        monkeypatch.setattr(feed_fetcher_module, "_FETCH_CONNECT_TIMEOUT", 30.0)

        with _http_server(self._NeverAnswers) as base:
            started = time.monotonic()
            with pytest.raises(FetchError):
                await fetch(f"{base}/feed")
            elapsed = time.monotonic() - started

        assert elapsed < 3, f"没按总时长放弃，等了 {elapsed:.1f} 秒"


class TestInsecureCredentials:
    """明文 HTTP + 凭据：只提醒，不拦；每条源只警告一次

    Basic 认证就是把 `user:pass` 放进请求头，没有「先握手再加密凭据」这回事 —— 源站
    不是 https 的话，同网络的任何设备都能读到。但内网 `http://192.168.x.x/feed` +
    Basic 是常见的自托管配置，一刀切拒绝会伤到它。
    """

    @staticmethod
    def _is_insecure_warning(record: logging.LogRecord) -> bool:
        return "明文过网" in record.getMessage()

    async def test_warns_once_for_plain_http_with_credentials(
        self, monkeypatch, caplog
    ):
        monkeypatch.setattr(feed_fetcher_module, "_INSECURE_AUTH_WARNED", set())
        caplog.set_level(logging.WARNING, logger="farewell_rss.feed_fetcher")

        with _http_server(_rss_handler()) as base:
            host = base.removeprefix("http://")
            url = f"http://alice:s3cr3t@{host}/feed"
            assert await fetch(url) is not None
            assert await fetch(url) is not None  # 第二轮不该再警告

        warnings = [
            r.getMessage() for r in caplog.records if self._is_insecure_warning(r)
        ]
        assert len(warnings) == 1
        assert "s3cr3t" not in warnings[0], "警告里不能带密码"

    async def test_https_with_credentials_does_not_warn(self, monkeypatch, caplog):
        """https 上凭据在 TLS 里，不该有这条警告"""
        monkeypatch.setattr(feed_fetcher_module, "_INSECURE_AUTH_WARNED", set())
        caplog.set_level(logging.WARNING, logger="farewell_rss.feed_fetcher")

        with _patched_request(_stub_response()):
            await fetch("https://alice:s3cr3t@example.com/feed.xml")

        assert not [r for r in caplog.records if self._is_insecure_warning(r)]

    async def test_plain_http_without_credentials_does_not_warn(
        self, monkeypatch, caplog
    ):
        """没有凭据就无所谓明文（本来也没什么可泄露）"""
        monkeypatch.setattr(feed_fetcher_module, "_INSECURE_AUTH_WARNED", set())
        caplog.set_level(logging.WARNING, logger="farewell_rss.feed_fetcher")

        with _patched_request(_stub_response()):
            await fetch("http://example.com/feed.xml")

        assert not [r for r in caplog.records if self._is_insecure_warning(r)]


# ─── 内嵌播放器（2026-10-06）─────────────────────────────────────────────

# 照抄 RSSHub 哔哩哔哩路由的真实形状：整个播放器 HTML 被转义放在 <description> 里，
# URL 里的 & 是双重转义的。width/height 得有，前端要靠它算宽高比。
RSSHUB_BILIBILI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>某人的哔哩哔哩合集</title>
    <link>https://space.bilibili.com/3969839</link>
    <description>合集</description>
    <item>
      <title>抽象新闻：9月人类迷惑行为大赏（中）</title>
      <link>https://www.bilibili.com/video/BV1raaZ6EEQa</link>
      <guid isPermaLink="false">https://www.bilibili.com/video/BV1raaZ6EEQa</guid>
      <description>&lt;iframe width="640" height="360" src="https://www.bilibili.com/blackboard/html5mobileplayer.html?aid=117358924399048&amp;amp;cid=undefined&amp;amp;bvid=BV1raaZ6EEQa" frameborder="0" allowfullscreen="" referrerpolicy="no-referrer"&gt;&lt;/iframe&gt;&lt;br&gt;&lt;img src="https://i0.hdslb.com/bfs/archive/5762c762e415b0b0bc4d48108176ad3705e1723d.jpg"&gt;&lt;br&gt;</description>
    </item>
  </channel>
</rss>"""

# 同一份源里混进活动内容：脚本、内联事件、srcdoc 包裹的脚本
HOSTILE_FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>恶意源</title>
    <link>https://evil.example</link>
    <description>x</description>
    <item>
      <title>标题</title>
      <link>https://evil.example/1</link>
      <guid isPermaLink="false">evil-1</guid>
      <description>&lt;p onclick="alert(1)"&gt;正文&lt;/p&gt;&lt;script&gt;alert(2)&lt;/script&gt;&lt;iframe srcdoc="&amp;lt;script&amp;gt;alert(3)&amp;lt;/script&amp;gt;"&gt;&lt;/iframe&gt;</description>
    </item>
  </channel>
</rss>"""


class TestEmbeddedPlayers:
    """源站内嵌的播放器要活着走到库里

    feedparser 默认会对条目 HTML 走一遍消毒，而它的元素白名单里没有 iframe —— 于是
    RSSHub 的哔哩哔哩播放器在入库前就被删掉了（实测：同一份 XML，summary 从 363 字节
    变成 103 字节，只剩封面图）。这里盯着"iframe 能进来"和"活动内容仍被挡住"两件事。
    """

    async def test_bilibili_player_survives_parsing(self):
        result = await _run_fetch_with_xml(RSSHUB_BILIBILI_XML)

        assert result is not None
        summary = result.entries[0].summary or ""
        assert "<iframe" in summary, "内嵌播放器被过滤掉了"
        assert "html5mobileplayer.html" in summary
        assert "bvid=BV1raaZ6EEQa" in summary
        # 宽高比是前端算 aspect-ratio 的依据，丢了就只能退回 16:9
        assert 'width="640"' in summary
        assert 'height="360"' in summary

    async def test_script_and_event_handlers_are_still_stripped(self):
        """放行的只有 iframe：脚本 / 内联事件 / srcdoc 仍在入库时就被清掉"""
        result = await _run_fetch_with_xml(HOSTILE_FEED_XML)

        assert result is not None
        summary = result.entries[0].summary or ""
        assert "正文" in summary
        assert "alert(" not in summary
        assert "onclick" not in summary
        assert "srcdoc" not in summary
        assert "<script" not in summary

    def test_feedparser_sanitizer_whitelist_is_still_patchable(self):
        """feedparser 升级后若白名单结构变了，这条会红 —— 别让内嵌播放器静默消失

        `_HTMLSanitizer.acceptable_elements` 是私有类属性，我们的补丁打在它上面。
        """
        from feedparser import sanitizer

        elements = sanitizer._HTMLSanitizer.acceptable_elements
        attributes = sanitizer._HTMLSanitizer.acceptable_attributes
        assert "iframe" in elements
        assert "script" not in elements
        assert "srcdoc" not in attributes
