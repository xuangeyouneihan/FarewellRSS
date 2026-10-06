"""条目附件的两件事：API 的 `enclosure` 数组，以及**拼进正文的播放器**。

## `enclosure` 数组（`build_enclosures`）

**这个字段没有正式文档**，形状只能从"真实客户端怎么读"反推 —— item 对象那一页
（`StreamContents`）在那份非官方规范里从来没写出来：`mihaip/google-reader-api` 的 wiki 里
只有指向它的断链，code.google.com 的归档也取不到这一页；FeedHQ 的文档同样一个字不提。
已知的两处实现：

* News+（FreshRSS 兼容列表里的参考客户端）在 Google Reader / Bazqux / Inoreader 三个后端
  用的是同一段解析：`enclosure` 是数组，元素取 `href` 和 `type`，并按 `type` 前缀分派
  image / video / audio。注意它是「见到 `type` 才分派、用之前记下的 `href`」——
  **所以 `href` 必须排在 `type` 前面，反了它拿到的是 null。**
* FreshRSS 的 `Entry::toGReader()` 在所有模式下都发这个数组，元素是 `{href, type}`，
  `length` 有则带（0/未知则不发），没有附件时**整个键都不出现**。

所以键序和"没附件就不发键"都照抄它们。也**不往里加自有字段**：GReader 只给了这三个键，
FreshRSS 要加自己的东西时用的是 `frss:` 前缀。

## 拼进正文的播放器（`render_enclosures`）

**为什么不能只靠上面那个字段**：实测我们手上的三个客户端（ReadYou、Reeder、
NetNewsWire）**都不读它** —— ReadYou 的 GReader DTO 里压根没有 `enclosure`（Gson 直接忽略
未知字段），另外两个也没见出播放器。而三家都会把 `summary.content` 当 HTML 渲染。所以把
播放器拼进正文，等于"凡是能渲染 HTML 的客户端就都能播"，这也是 FreshRSS 的做法
（它的 `Entry::content(withEnclosures)`）。字段仍然照发：它是标准位置，给以后真读它的
客户端用，成本也只是一个数组。
"""

import html
import logging
from urllib.parse import urlsplit

from ..db.models import Enclosure

_logger = logging.getLogger(__name__)

_PLAYABLE_SCHEMES = frozenset({"http", "https"})

#: MIME 缺失（或写了个怪值）时按扩展名兜底 —— RSS 2.0 里 `type` 本该必填，现实里会缺
_EXTENSION_KINDS: dict[str, tuple[str, ...]] = {
    "audio": (
        "mp3",
        "m4a",
        "m4b",
        "aac",
        "ogg",
        "oga",
        "opus",
        "flac",
        "wav",
        "wma",
        "mka",
    ),
    "video": ("mp4", "m4v", "webm", "ogv", "mov", "mkv", "avi"),
    "image": ("jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "svg"),
}


def _is_playable(href: str) -> bool:
    """只看地址本身：绝对的 http(s)。

    `javascript:` / `data:` / 相对地址一律不发 —— 这个数组是要被客户端塞进播放器的，
    我们不能替客户端判断安全性。和前端 `playableUrl`（iframe 那个入口）是同一条规矩。
    """
    parts = urlsplit(href)
    return parts.scheme in _PLAYABLE_SCHEMES and bool(parts.netloc)


def build_enclosures(enclosures: list[Enclosure]) -> list[dict]:
    """把附件行转成 `enclosure` 数组；没有可用的就返回空列表。

    调用方据此决定**加不加** `enclosure` 这个键（空列表不加，见模块注释）。
    """
    result: list[dict] = []
    for enclosure in enclosures:
        # 顺手去掉两头的空白：源站给 " https://…" 这种值时不算「不能用」，但 urlsplit
        # 会把它判成没有 scheme。去空白只影响我们发出去的值，不动库里的原文。
        href = enclosure.href.strip()
        if not _is_playable(href):
            _logger.debug("跳过不可播放的附件地址: %s", href)
            continue
        # 键序不能改：href 必须在 type 前面（News+ 见到 type 才分派）
        #
        # 类型缺失时就发空字符串，**不按扩展名替源站猜一个**：这里是个 MIME 字段，
        # 写「audio」这种假值会被按 `type.split("/")` 用的客户端吃出问题；而 type
        # 是 RSS 2.0 里 enclosure 的必填项，缺它本身就不合规。渲染成什么元素由下面的
        # `render_enclosures` 按扩展名兜底（那是渲染决定，不是数据）。
        media: dict = {"href": href, "type": enclosure.type or ""}
        if enclosure.length:
            media["length"] = enclosure.length
        result.append(media)
    return result


def render_enclosures(content: str, enclosures: list[Enclosure]) -> str:
    """把附件渲染成 HTML **追加到正文末尾**，返回新的正文。

    形状照抄 FreshRSS（`figure.enclosure` + `p.enclosure-content`）：

    * 音频 / 视频：`<audio|video preload="none" controls>` + 一个 💾 保存链接；
    * 图片：`<img>`（图本身就是内容，不再加保存链接）；
    * 其它类型：只给 💾 链接。

    `preload="none"` 不能省：不按播放就一个字节都不请求（否则打开一篇文章就会去拉
    100 MB 音频的头部）。

    💾 而不是文字标签是刻意的 —— 它不需要 i18n，任何客户端都认得。`target="_blank"`
    也必需：`download` 属性跨站会被浏览器忽略，没有 target 的话点一下就把阅读器导航走了。
    """
    blocks = [
        block
        for enclosure in enclosures
        if (block := _render_one(content, enclosure)) is not None
    ]
    if not blocks:
        return content
    return f"{content}\n{''.join(blocks)}"


def _render_one(content: str, enclosure: Enclosure) -> str | None:
    """一个附件 → 一段 HTML；不该拼（地址不可用 / 正文里已有）就返回 None"""
    href = enclosure.href.strip()
    if not _is_playable(href):
        _logger.debug("正文附件跳过不可播放的地址: %s", href)
        return None

    # 正文里已经有同一个地址（源站自己嵌了播放器）就别再拼一个 —— 比对两种形态：
    # 正文是 HTML，URL 里的 `&` 在那里是 `&amp;`。
    if href in content or html.escape(href, quote=True) in content:
        _logger.debug("正文里已有该附件地址，跳过: %s", href)
        return None

    escaped = html.escape(href, quote=True)  # 自己拼 HTML，转义这一步不能省
    kind = _kind(enclosure)
    if kind == "audio":
        inner = f'<audio preload="none" controls="controls" src="{escaped}"></audio>'
    elif kind == "video":
        inner = f'<video preload="none" controls="controls" src="{escaped}"></video>'
    elif kind == "image":
        inner = f'<img src="{escaped}" alt="" />'
    else:
        inner = ""
    if kind != "image":
        save = f'<a href="{escaped}" target="_blank" rel="noopener noreferrer">💾</a>'
        inner = f"{inner} {save}" if inner else save
    return (
        f'<figure class="enclosure"><p class="enclosure-content">{inner}</p></figure>'
    )


def _kind(enclosure: Enclosure) -> str:
    """该渲染成什么：`audio` / `video` / `image` / `file`（MIME 优先，扩展名兜底）"""
    mime = (enclosure.type or "").strip().lower()
    for kind in _EXTENSION_KINDS:
        if mime.startswith(f"{kind}/"):
            return kind
    extension = _extension_of(enclosure.href)
    for kind, extensions in _EXTENSION_KINDS.items():
        if extension in extensions:
            return kind
    return "file"


def _extension_of(href: str) -> str:
    name = urlsplit(href).path.rsplit("/", 1)[-1]
    _, dot, extension = name.rpartition(".")
    return extension.lower() if dot else ""
