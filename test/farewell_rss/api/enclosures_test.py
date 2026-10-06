"""附件的两个出口：`enclosure` 数组的形状，以及拼进正文的播放器。

`enclosure` 在 Google Reader API 里**没有正式文档**，形状是从客户端实现反推的（详见
`api/_enclosures.py` 的模块注释），所以这里的断言不是审美，是钉住那份兼容性：
键序（`href` 在 `type` 前）、没附件不发键、只发绝对 http(s)。

拼进正文那段（`render_enclosures`）是给"不读 `enclosure` 字段、只渲染 HTML"的客户端用的
（ReadYou / Reeder / NetNewsWire 都是这样），所以它必须：元素选对、**转义不出错**、
正文已有同地址时不重复。
"""

from farewell_rss.api._enclosures import build_enclosures, render_enclosures
from farewell_rss.db.models import Enclosure


def _enclosure(
    href: str, *, length: int | None = None, type: str | None = None
) -> Enclosure:
    return Enclosure(href=href, length=length, type=type)


def test_shape_and_key_order():
    """键序不能改：`href` 必须在 `type` 前面。

    News+ 是「见到 `type` 才分派 image/video/audio，用之前记下的 `href`」——
    `type` 排在它前面，客户端拿到的就是 null。
    """
    result = build_enclosures([
        _enclosure("https://example.com/ep1.mp3", length=1234, type="audio/mpeg")
    ])

    assert result == [
        {
            "href": "https://example.com/ep1.mp3",
            "type": "audio/mpeg",
            "length": 1234,
        }
    ]
    assert list(result[0]) == ["href", "type", "length"]


def test_length_omitted_when_unknown():
    """`length` 未知（库里是 0）时不发这个键 —— 和 FreshRSS 一致"""
    result = build_enclosures([
        _enclosure("https://example.com/ep1.mp3", length=0, type="audio/mpeg")
    ])

    assert list(result[0]) == ["href", "type"]


def test_missing_type_is_empty_string():
    """类型缺失时发空字符串，**不按扩展名替源站猜**：`type` 是个 MIME 字段

    写「audio」这种假值会被按 `type.split("/")` 用的客户端吃出问题；而且 `type` 是
    RSS 2.0 里 enclosure 的必填项，缺它本身就不合规。渲染成什么元素由
    `render_enclosures` 按扩展名兜底（那是渲染决定，不是数据）。
    """
    result = build_enclosures([_enclosure("https://example.com/ep1.mp3", type=None)])

    assert result == [{"href": "https://example.com/ep1.mp3", "type": ""}]


def test_rejects_non_playable_hrefs():
    """绝对 http(s) 之外的一律不发。

    这个数组会被客户端直接塞进播放器，安全性不能替客户端判断 —— 和前端 iframe 占位器
    的 `playableUrl` 是同一条规矩。
    """
    result = build_enclosures([
        _enclosure("javascript:alert(1)"),
        _enclosure("data:audio/mpeg;base64,AAAA"),
        _enclosure("/local/ep.mp3"),
        _enclosure("ftp://example.com/ep.mp3"),
        _enclosure("https:///ep.mp3"),  # 有 scheme、没主机
        _enclosure("https://example.com/ok.mp3"),
    ])

    assert result == [{"href": "https://example.com/ok.mp3", "type": ""}]


def test_href_is_stripped():
    """两头带空白的地址照样能用（urlsplit 会把带空格的判成没有 scheme）"""
    result = build_enclosures([_enclosure("  https://example.com/ep.mp3  ")])

    assert result == [{"href": "https://example.com/ep.mp3", "type": ""}]


def test_no_enclosures():
    """没有附件 → 空列表，调用方据此**不加** `enclosure` 这个键"""
    assert build_enclosures([]) == []


# ─── 拼进正文的播放器 ────────────────────────────────────────────────

CONTENT = "<p>本集聊炒饭</p>"


def render(*enclosures: Enclosure, content: str = CONTENT) -> str:
    return render_enclosures(content, list(enclosures))


def test_audio_is_appended_after_content():
    """音频：`<audio preload="none">` + 💾，**追加在正文之后**（FreshRSS 的位置）"""
    result = render(_enclosure("https://e.com/ep1.mp3", type="audio/mpeg"))

    assert result.startswith(CONTENT)
    assert (
        '<audio preload="none" controls="controls" src="https://e.com/ep1.mp3"></audio>'
        in result
    )
    assert '<a href="https://e.com/ep1.mp3" target="_blank"' in result
    assert "💾" in result
    assert '<figure class="enclosure">' in result


def test_video_and_image_and_file():
    """按 MIME 选元素：video → `<video>`；image → `<img>`（不加保存链接）；其它 → 只给 💾"""
    video = render(_enclosure("https://e.com/v.mp4", type="video/mp4"))
    image = render(_enclosure("https://e.com/c.jpg", type="image/jpeg"))
    pdf = render(_enclosure("https://e.com/a.pdf", type="application/pdf"))

    assert (
        '<video preload="none" controls="controls" src="https://e.com/v.mp4">' in video
    )
    assert '<img src="https://e.com/c.jpg" alt="" />' in image
    assert "💾" not in image  # 图本身就是内容，再给个保存链接是噪音
    assert pdf.count("<a ") == 1
    assert "<img" not in pdf
    assert "<audio" not in pdf


def test_kind_falls_back_to_extension():
    """`type` 缺失（或写了个怪值）时按扩展名兜底 —— RSS 2.0 里 type 本该必填，现实会缺"""
    assert "<audio" in render(_enclosure("https://e.com/ep1.MP3", type=""))
    assert "<video" in render(
        _enclosure("https://e.com/v.mp4", type="binary/octet-stream")
    )
    assert "<img" in render(_enclosure("https://e.com/c.webp", type=None))
    assert "💾" in render(_enclosure("https://e.com/x.bin", type="")), (
        "判不出来就退成链接"
    )


def test_rejects_non_playable_hrefs_in_content():
    """不可用的地址一个字都不拼（正文原样返回）"""
    for href in (
        "javascript:alert(1)",
        "data:audio/mpeg;base64,AAAA",
        "/local/ep.mp3",
        "https:///ep.mp3",
    ):
        assert render(_enclosure(href, type="audio/mpeg")) == CONTENT


def test_href_is_escaped():
    """自己拼 HTML，转义不能漏 —— 否则一个带引号的 URL 就能在客户端页面上插属性"""
    result = render(
        _enclosure('https://e.com/x" onmouseover="alert(1)', type="audio/mpeg")
    )

    assert '" onmouseover=' not in result  # 没有裸引号能断出属性
    assert "&quot; onmouseover=&quot;" in result
    assert "&amp;" in render(
        _enclosure("https://e.com/ep.mp3?a=1&b=2", type="audio/mpeg")
    )


def test_skips_when_content_already_has_the_url():
    """正文里已经有同一个地址（源站自己嵌了播放器）→ 不重复拼一个"""
    plain = '<audio controls src="https://e.com/ep1.mp3"></audio>'
    escaped = '<audio src="https://e.com/ep.mp3?a=1&amp;b=2"></audio>'

    assert (
        render(_enclosure("https://e.com/ep1.mp3", type="audio/mpeg"), content=plain)
        == plain
    )
    assert (
        render(
            _enclosure("https://e.com/ep.mp3?a=1&b=2", type="audio/mpeg"),
            content=escaped,
        )
        == escaped
    ), "正文里的 & 是 &amp;，也要能认出来"


def test_no_enclosures_leaves_content_untouched():
    assert render_enclosures(CONTENT, []) == CONTENT
    assert render_enclosures(CONTENT, [_enclosure("javascript:alert(1)")]) == CONTENT


def test_multiple_enclosures_keep_order():
    result = render(
        _enclosure("https://e.com/ep1.mp3", type="audio/mpeg"),
        _enclosure("https://e.com/ep2.mp3", type="audio/mpeg"),
    )

    assert result.index("ep1.mp3") < result.index("ep2.mp3")
