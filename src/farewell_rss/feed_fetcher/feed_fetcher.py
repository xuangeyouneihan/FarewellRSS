import asyncio
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser

import feedparser  # type: ignore[import-untyped]

_logger = logging.getLogger(__name__)


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
        default_factory=lambda: datetime.now(tz=datetime.UTC)
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
        default_factory=lambda: datetime.now(tz=datetime.UTC)
    )  # RSS 源最后一次抓取的 UTC 时间
    author: FetchedAuthor | None = None  # RSS 源作者信息
    icon: str | None = (
        None  # RSS 源图标地址，对于 feedparser 来说是从 icon、logo、image 中选取的第一个
    )
    rights: str | None = None  # RSS 源版权信息
    tags: list[FetchedTag] = field(
        default_factory=list
    )  # RSS 源自己声明的标签列表，不知道有啥用，但还是存着吧，或许前端会用到
    ttl: int | None = None  # RSS 源缓存时间，在 feedparser 中是分钟单位，返回时转换为秒单位
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
        tzinfo=datetime.UTC,
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
    url: str, etag: str | None = None, modified: str | None = None
) -> FetchedFeed | None:
    """获取 RSS 源"""
    raw_feed = None
    try:
        raw_feed = await asyncio.to_thread(
            feedparser.parse, url, etag=etag, modified=modified
        )
    except Exception:
        _logger.exception("订阅源 %s 获取失败", url)
        return None

    if raw_feed.status == 304:
        _logger.debug("订阅源 %s 未修改", url)
        return None
    if not raw_feed.entries:
        _logger.info("订阅源 %s 没有条目", url)
        return None
    if raw_feed.bozo:
        _logger.warning(
            "订阅源 %s 解析异常，bozo_exception: %s", url, raw_feed.bozo_exception
        )

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
        href=raw_feed.href,
        etag=raw_feed.get("etag"),
        modified=raw_feed.get("modified"),
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
