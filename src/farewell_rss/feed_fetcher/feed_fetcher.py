import asyncio
import base64
import html
import logging
import os
import platform
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser

import feedparser  # type: ignore[import-untyped]
import httpx

from .._version import __version__

_logger = logging.getLogger(__name__)

# 与 pyproject.toml 的 [project.urls] Repository 一致；UA 里带出处，源站能找到人
_REPO = "https://github.com/xuangeyouneihan/FarewellRSS"

# UA 形制对齐 FreshRSS（`FreshRSS/<版本> (<OS>; <官网>)`）：名字 + 版本 + 出处，源站从日志
# 里能认出我们、也找得到人。**不能用 feedparser 的默认 UA**（`feedparser/x.y (+…)`）：
# 一些 WAF 专门拦它，实测 `free.apprcn.com/feed/` 回 403（换自己的 UA 就是 200 + 3 条）。
_AGENT = f"FarewellRSS/{__version__} ({platform.system()}; {_REPO})"

# 一次抓取的时间预算（秒），三层各管一段：
#   FETCH_TIMEOUT          整次抓取的硬上限（建连 + 跳转 + 读 body），用 asyncio.timeout 表达
#   _FETCH_CONNECT_TIMEOUT 建连接（TCP + TLS）
#   _FETCH_READ_TIMEOUT    等下一个数据块（注意是**每次读**，不是总时长）
#
# 为什么非要超时不可：线上实测过一次「调度器此后再无 tick」。改之前的写法是把 URL 交给
# `feedparser.parse()`，它内部走 urllib 且不传 timeout，而 socket 的默认超时是「永不超时」
# —— 一个连上了却再也不回包的源会把那个线程永久占住（默认执行器的线程数有限且全局共享，
# 连密码哈希都得排队）；于是整轮刷新的 `gather` 永不返回，而 `run()` 的 try/except 只挡
# 异常、挡不住挂死：进程还活着，只是再也不刷新了。
#
# 超时交给 httpx（走事件循环）而不是「另开线程 + 事后取消」：httpx 的超时会真的把 socket
# 关掉，`asyncio.timeout` 也就真能取消它；`to_thread` 那种写法里取消只是「不等了」，
# 线程还在 socket 上挂着。参考 SimplePie：它从 2004 年起就默认带 10 秒超时。
FETCH_TIMEOUT = float(os.getenv("FAREWELL_RSS_FEED_FETCH_TIMEOUT", "30"))
_FETCH_CONNECT_TIMEOUT = 10.0
_FETCH_READ_TIMEOUT = 10.0

# 跳转次数上限，与 SimplePie 的默认值一致
_MAX_REDIRECTS = 5

# 与 feedparser 自带的默认值一致（feedparser.http.ACCEPT_HEADER）
_ACCEPT = (
    "application/atom+xml,application/rdf+xml,application/rss+xml,"
    "application/xml;q=0.9,text/xml;q=0.2,*/*;q=0.1"
)


class FetchError(Exception):
    """RSS 源抓取失败（网络错误等）"""


def _split_credentials(url: str) -> tuple[str, str | None]:
    """URL 里的 `user:pass@` 拆成「干净的 URL + Authorization 头」

    httpx 不会把 URL 里的凭据转成 Authorization（实测 0.28.1：build_request 出来的
    authorization 是空的），直接发出去等于把凭据丢掉、然后白吃一个 401。

    凭据先按 RFC 3986 解码再拼进 Basic 头：`me%2Bfeeds` 送出去的是 `me+feeds`。
    """
    parts = urllib.parse.urlsplit(url)
    if parts.username is None:
        return url, None
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    clean = urllib.parse.urlunsplit((
        parts.scheme,
        host,
        parts.path,
        parts.query,
        parts.fragment,
    ))
    # userinfo 是百分号编码的，而 urllib 的 .username / .password **不解码**（`me%2Bfeeds`
    # 会原样送出去 → 服务端必然 401；feedparser 也踩在这一点上），httpx 的 `URL.username`
    # 才会解。这里刻意继续用 urllib：clean_url 要尽量原样保留（href 是订阅的去重键，
    # 不能让 httpx 的 URL 规范化改写它），所以解码只做在凭据这一步。
    username = urllib.parse.unquote(parts.username)
    password = urllib.parse.unquote(parts.password or "")
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return clean, f"Basic {token}"


async def _request(
    url: str, authorization: str | None, etag: str | None, modified: str | None
) -> httpx.Response:
    """GET 一次源；传输层失败（DNS、连接、TLS、超时、跳转过多）一律抛 `FetchError`

    非 2xx **不抛异常**：304 和 4xx/5xx 都是有意义的结果，怎么处理留给 `fetch`。

    **每次抓取建一个 client。** 连接池在这里本来也没有复用机会（不同源基本是不同主机，
    同一轮里每个源也只抓一次），换来的是不用管生命周期：模块级单例会绑事件循环，
    测试里每个用例一个新 loop 就炸了。
    """
    headers = {"User-Agent": _AGENT, "Accept": _ACCEPT}
    if authorization:
        headers["Authorization"] = authorization
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                FETCH_TIMEOUT, connect=_FETCH_CONNECT_TIMEOUT, read=_FETCH_READ_TIMEOUT
            ),
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
        ) as client:
            return await client.get(url, headers=headers)
    except httpx.TimeoutException as error:
        _logger.warning("订阅源 %s 抓取超时：%s", url, error)
        raise FetchError(url) from error
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        _logger.warning("订阅源 %s 抓取失败：%s", url, error)
        raise FetchError(url) from error


@dataclass
class FetchedAuthor:
    """作者类"""

    name: str | None  # 作者名称
    href: str | None  # 作者链接
    email: str | None  # 作者邮箱


@dataclass
class FetchedTag:
    """标签类"""

    term: str  # 标签名称
    scheme: str | None = None  # 标签分类
    label: str | None = None  # 标签显示名称，没有的话回退到 term


@dataclass
class FetchedEnclosure:
    """附件类"""

    href: str  # 附件地址
    length: int | None = None  # 附件大小，单位为字节
    type: str | None = None  # 附件 MIME 类型


@dataclass
class FetchedEntry:
    """RSS 条目类"""

    guid: str  # RSS 条目唯一标识符
    title: str | None = None  # RSS 条目标题
    link: str | None = None  # RSS 条目链接，可以给人看或者用在条目本身没多少内容的时候
    published: datetime | None = (
        None  # RSS 条目发布时间，需要是 UTC 时间，没有时需要用 updated 代替
    )
    updated: datetime | None = None  # RSS 条目更新时间，需要是 UTC 时间
    fetched: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC)
    )  # RSS 条目最后一次抓取的 UTC 时间
    summary: str | None = None  # RSS 条目摘要/描述（HTML）
    summary_plain: str | None = None  # RSS 条目摘要/描述（纯文本）
    content: str | None = None  # RSS 条目正文（HTML）
    content_plain: str | None = None  # RSS 条目正文（纯文本）
    author: FetchedAuthor | None = None  # RSS 条目作者信息
    tags: list[FetchedTag] = field(
        default_factory=list
    )  # RSS 条目自己声明的标签列表，不知道有啥用，但还是存着吧，或许前端会用到
    enclosures: list[FetchedEnclosure] = field(
        default_factory=list
    )  # RSS 条目附件列表，通常是音频、视频、图片等媒体文件
    source: FetchedFeed | None = (
        None  # 聚合源中文章的原始来源，实体可以只包含 href、title 和 link
    )


@dataclass
class FetchedFeed:
    """RSS 源类"""

    href: str  # RSS 源地址，RSS 源的唯一标识（给阅读器看的）
    etag: str | None = None  # ETag，增量更新用
    modified: str | None = None  # Last-Modified，增量更新用
    title: str | None = None  # RSS 源标题
    link: str | None = None  # RSS 源链接（给人看的）
    subtitle: str | None = None  # RSS 源副标题/描述
    published: datetime | None = None  # RSS 源发布时间，需要是 UTC 时间
    updated: datetime | None = None  # RSS 源更新时间，需要是 UTC 时间
    fetched: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC)
    )  # RSS 源最后一次抓取的 UTC 时间
    author: FetchedAuthor | None = None  # RSS 源作者信息
    icon: str | None = (
        None  # RSS 源图标地址，对于 feedparser 来说是从 icon、logo、image 中选取的第一个
    )
    rights: str | None = None  # RSS 源版权信息
    tags: list[FetchedTag] = field(
        default_factory=list
    )  # RSS 源自己声明的标签列表，不知道有啥用，但还是存着吧，或许前端会用到
    ttl: int | None = (
        None  # RSS 源缓存时间，在 feedparser 中是分钟单位，返回时转换为秒单位
    )
    entries: list[FetchedEntry] = field(default_factory=list)  # RSS 源条目列表


def _parse_datetime(Fetched) -> datetime | None:
    """将 feedparser 的 struct_time 转为 UTC datetime，None 安全"""
    if Fetched is None:
        return None
    return datetime(
        Fetched.tm_year,
        Fetched.tm_mon,
        Fetched.tm_mday,
        Fetched.tm_hour,
        Fetched.tm_min,
        Fetched.tm_sec,
        tzinfo=UTC,
    )


def _parse_author(author: dict | None) -> FetchedAuthor | None:
    """将 feedparser 的 author dict 转为 Author，None 安全"""
    if author is None:
        return None
    return FetchedAuthor(
        name=author.get("name"),
        href=author.get("href"),
        email=author.get("email"),
    )


def _parse_tag(tag: dict | None) -> FetchedTag | None:
    """将 feedparser 的 tag dict 转为 Tag"""
    if tag is None:
        return None
    return FetchedTag(
        term=tag.get("term", ""),
        scheme=tag.get("scheme"),
        label=tag.get("label"),
    )


def _parse_enclosure(enclosure: dict | None) -> FetchedEnclosure | None:
    """将 feedparser 的 enclosure dict 转为 Enclosure"""
    if enclosure is None:
        return None
    return FetchedEnclosure(
        href=enclosure.get("href", ""),
        length=int(enclosure.get("length") or 0),
        type=enclosure.get("type"),
    )


class _Stripper(HTMLParser):
    """去掉所有 HTML 标签，只保留文本"""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """<p>Hello</p> → Hello 被这个方法捕获"""
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def _strip_html(text: str | None) -> str | None:
    """<p>Hello <em>World</em></p> → Hello World"""
    if text is None:
        return None
    s = _Stripper()
    s.feed(text)
    return s.get_text()


def _to_html(text: str | None) -> str | None:
    """纯文本 → HTML，保留段落换行"""
    if text is None:
        return None
    if not text:
        return ""
    escaped = html.escape(text)  # & → &amp;  < → &lt;
    paragraphs = escaped.split("\n\n")  # 双换行 = 新段落
    return "".join(
        f"<p>{p.replace('\n', '<br>')}</p>"  # 单换行 = <br>
        for p in paragraphs
    )


async def fetch(
    url: str,
    etag: str | None = None,
    modified: str | None = None,
) -> FetchedFeed | None:
    """获取 RSS 源

    抓取失败（传输层、HTTP 层）一律抛 `FetchError`；返回 `None` 只表示两件事：
    304 未修改，或者 2xx 且这个源目前确实没有条目 —— 调用方据此决定要不要 touch。

    超时不开放成参数（测试要快就 monkeypatch 那几个模块常量）：它是 socket / 事件循环
    层面的预算，由这里用 `asyncio.timeout` 表达，而不是让每个调用方各自传一个数字。

    feedparser 只负责解析：响应体和响应头一起交给它，相对链接靠 `content-location`
    （最终地址）解析。
    """
    try:
        clean_url, authorization = _split_credentials(url)
    except ValueError as error:
        # 比如 `http://host:不是端口/feed`：`_is_http_url` 拦不住（urlparse 不校验端口），
        # 到取 port 时才炸。按抓取失败处理，别让它变成每轮一条未捕获异常。
        _logger.warning("订阅源 %s 地址不合法：%s", url, error)
        raise FetchError(url) from error

    try:
        # 整次抓取的硬上限：httpx 的 read 超时只约束「每次读」，两次读之间可以无限长
        async with asyncio.timeout(FETCH_TIMEOUT):
            response = await _request(clean_url, authorization, etag, modified)
    except TimeoutError as error:
        _logger.warning(
            "订阅源 %s 超过 %.0f 秒仍未抓完，放弃本次抓取", url, FETCH_TIMEOUT
        )
        raise FetchError(url) from error

    status = response.status_code
    headers = dict(response.headers)  # httpcore 已经全小写了
    if status == 304:
        _logger.debug("订阅源 %s 未修改", url)
        return None
    if not 200 <= status < 300:
        # 403（WAF / 防盗链）、404（源已失效）、5xx（上游故障）：错误页也能解析出「零条目」，
        # 必须在这里拦住，否则会被当成「源没有条目」。
        # 3xx 落到这里只有两种情况：带 Location 的真跳转 httpx 已经跟完了（跟不完会抛
        # TooManyRedirects），剩下的是**没有 Location 的 3xx**（坏服务端 / 门户网关），
        # 以及 300/305 这类 httpx 不认的重定向 —— 同样是「没拿到源」，不能放它变成
        # touch()。
        _logger.warning(
            "订阅源 %s 返回 HTTP %d（content-type: %s），本次抓取失败",
            url,
            status,
            headers.get("content-type"),
        )
        raise FetchError(url)

    raw_feed = feedparser.parse(
        response.content,
        response_headers={
            **headers,
            # parse() 收到的是字节，它不知道这些字节是从哪来的：条目里的相对链接
            # （图片、站内地址）要靠这个头才能拼成绝对地址
            "content-location": str(response.url),
        },
    )
    if raw_feed.bozo and raw_feed.entries:
        # 有条目还能解析出来：宽进严出，照常返回，只留一条警告
        _logger.warning(
            "订阅源 %s 解析异常，bozo_exception: %s", url, raw_feed.bozo_exception
        )
    if not raw_feed.entries:
        if raw_feed.bozo:
            # 2xx、解析报错、且一条都没有：拿到的多半不是 feed（WAF 挑战页、HTML 错误页），
            # 与「源本身是空的」是两回事，日志要能分辨。
            _logger.warning(
                "订阅源 %s 没有条目，且解析异常（content-type: %s）：拿到的可能不是 feed",
                url,
                headers.get("content-type"),
            )
        else:
            _logger.info("订阅源 %s 没有条目", url)
        return None

    feed_title = None
    feed_title_detail = raw_feed.feed.get("title_detail")
    if feed_title_detail:
        if feed_title_detail.get("type") in ("text/html", "application/xhtml+xml"):
            feed_title = _strip_html(feed_title_detail.get("value"))
        else:
            feed_title = feed_title_detail.get("value")

    feed_subtitle = None
    feed_subtitle_detail = raw_feed.feed.get("subtitle_detail")
    if feed_subtitle_detail:
        if feed_subtitle_detail.get("type") in ("text/html", "application/xhtml+xml"):
            feed_subtitle = _strip_html(feed_subtitle_detail.get("value"))
        else:
            feed_subtitle = feed_subtitle_detail.get("value")

    feed_tags = []
    for tag in raw_feed.feed.get("tags", []):
        feed_tag = _parse_tag(tag)
        if feed_tag is not None:
            feed_tags.append(feed_tag)

    ttl = raw_feed.feed.get("ttl")

    result = FetchedFeed(
        # 没跳转时用请求时的地址原样存：href 是订阅的去重键（也是 TTL 判断的输入），
        # 不能因为 URL 规范化（大小写、百分号编码之类）而变样
        href=str(response.url) if response.history else clean_url,
        # etag / modified 从响应头拿（feedparser 只在它自己发请求时才会填这两个字段，
        # 交给它字节的话它无从得知，增量更新的条件头就断了）
        etag=headers.get("etag"),
        modified=headers.get("last-modified"),
        title=feed_title,
        link=raw_feed.feed.get("link"),
        subtitle=feed_subtitle,
        published=_parse_datetime(raw_feed.feed.get("published_parsed")),
        updated=_parse_datetime(raw_feed.feed.get("updated_parsed")),
        author=_parse_author(raw_feed.feed.get("author_detail")),
        icon=raw_feed.feed.get("icon")
        or raw_feed.feed.get("logo")
        or (raw_feed.feed.get("image") or {}).get("href"),
        rights=raw_feed.feed.get("rights"),
        tags=feed_tags,
        ttl=int(ttl) * 60 if ttl is not None else None,
    )

    for entry in raw_feed.entries:
        entry_title = None
        entry_title_detail = entry.get("title_detail")
        if entry_title_detail:
            if entry_title_detail.get("type") in ("text/html", "application/xhtml+xml"):
                entry_title = _strip_html(entry_title_detail.get("value"))
            else:
                entry_title = entry_title_detail.get("value")

        entry_summary = None
        entry_summary_plain = None
        entry_summary_detail = entry.get("summary_detail")
        if entry_summary_detail:
            if entry_summary_detail.get("type") in (
                "text/html",
                "application/xhtml+xml",
            ):
                entry_summary = entry_summary_detail.get("value")
                entry_summary_plain = _strip_html(entry_summary)
            else:
                entry_summary_plain = entry_summary_detail.get("value")
                entry_summary = _to_html(entry_summary_plain)

        entry_content = None
        entry_content_plain = None
        for content in entry.get("content", []):
            if (
                content.get("type") in ("text/html", "application/xhtml+xml")
                and not entry_content
            ):
                # 第一个 HTML 内容会进 Entry.content 给人看，其他的会被忽略
                entry_content = content.get("value")
            elif not entry_content and not entry_content_plain:
                # 第一个非 HTML 内容会进 Entry.content_plain 给搜索用，其他的会被忽略，前面的判断防止 HTML 内容进来
                entry_content_plain = content.get("value")
        if entry_content and entry_content_plain:
            pass
        elif entry_content:
            # 只有 HTML 内容时，Entry.content_plain 需要去掉 HTML 标签
            entry_content_plain = _strip_html(entry_content)
        elif entry_content_plain:
            # 只有非 HTML 内容时，Entry.content 需要转为 HTML
            entry_content = _to_html(entry_content_plain)

        entry_tags = []
        for tag in entry.get("tags", []):
            entry_tag = _parse_tag(tag)
            if entry_tag is not None:
                entry_tags.append(entry_tag)

        enclosures = []
        for raw_enclosure in entry.get("enclosures", []):
            enclosure = _parse_enclosure(raw_enclosure)
            if enclosure is not None:
                enclosures.append(enclosure)

        result.entries.append(
            FetchedEntry(
                guid=entry.get("id", entry.get("link", "")),
                title=entry_title,
                link=entry.get("link"),
                published=_parse_datetime(entry.get("published_parsed")),
                updated=_parse_datetime(entry.get("updated_parsed")),
                summary=entry_summary,
                summary_plain=entry_summary_plain,
                content=entry_content,
                content_plain=entry_content_plain,
                author=_parse_author(entry.get("author_detail")),
                tags=entry_tags,
                enclosures=enclosures,
                # 暂时不映射 source，聚合源极少，而且太麻烦了
            )
        )

    _logger.debug("订阅源 %s 抓取完成，共 %d 条", url, len(result.entries))
    return result
