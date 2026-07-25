from datetime import datetime
from enum import Enum

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    """用户类"""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(1023), unique=True
    )  # 登录名，需保证唯一
    password_hash: Mapped[str]
    friendly_name: Mapped[str | None] = mapped_column(Text)  # 用户昵称，可选
    is_admin: Mapped[bool] = mapped_column(default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        default=None
    )  # 用户被删除的时间，若为 None 则表示未被删除


class Feed(Base):
    """RSS 源的固有属性，不会随用户的订阅偏好而改变"""

    __tablename__ = "feeds"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    href: Mapped[str] = mapped_column(
        String(1023), unique=True
    )  # RSS 源的 URL 地址，需保证唯一
    etag: Mapped[str | None]  # ETag，增量更新用
    modified: Mapped[str | None]  # Last-Modified，增量更新用
    title: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)  # RSS 源页面的链接，给人看的
    subtitle: Mapped[str | None] = mapped_column(Text)  # RSS 源副标题/描述
    published: Mapped[datetime | None]  # RSS 源发布的 UTC 时间
    updated: Mapped[datetime | None]  # RSS 源更新的 UTC 时间
    fetched: Mapped[datetime]  # RSS 源最后一次抓取的 UTC 时间
    author_name: Mapped[str | None] = mapped_column(Text)
    author_href: Mapped[str | None] = mapped_column(Text)
    author_email: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[
        str | None
    ]  # RSS 源的图标地址，只存 URL，不存数据，让前端去请求图标地址
    rights: Mapped[str | None] = mapped_column(Text)  # RSS 源版权信息
    tags: Mapped[str | None] = mapped_column(
        Text
    )  # 存储为 JSON 字符串，用户在前端订阅时可以看到 RSS 源自己声明的标签列表
    ttl: Mapped[int | None]  # RSS 源缓存时间


class LabelType(str, Enum):
    FOLDER = "folder"
    TAG = "tag"


class Label(Base):
    """用户自定义的文件夹 / 标签"""

    __tablename__ = "labels"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(1023))
    type: Mapped[LabelType] = mapped_column(default=LabelType.FOLDER)

    __table_args__ = (
        UniqueConstraint("user_id", "name", "type"),
    )  # 约束同一个用户下的文件夹 / 标签名唯一


class Subscription(Base):
    """用户订阅的 RSS 源的偏好设置"""

    __tablename__ = "subscriptions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id"), primary_key=True)
    title: Mapped[str | None] = mapped_column(
        Text
    )  # 用户自定义的订阅标题，若为 None 则表示使用 RSS 源的标题
    subtitle: Mapped[str | None] = mapped_column(
        Text
    )  # 用户自定义的订阅副标题，若为 None 则表示使用 RSS 源的副标题
    link: Mapped[str | None] = mapped_column(
        Text
    )  # 用户自定义的页面链接，若为 None 则为默认
    icon: Mapped[
        bytes | None
    ]  # 用户自定义的订阅图标，存数据，让用户上传的图标可以在前端直接显示
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("labels.id")
    )  # 所属文件夹 ID，若为 None 则表示未分类


class Entry(Base):
    """RSS 条目的固有属性，不会随用户的订阅偏好而改变"""

    __tablename__ = "entries"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    feed_id: Mapped[int] = mapped_column(
        ForeignKey("feeds.id")
    )  # 该条目所属的 RSS 源的 ID
    guid: Mapped[str] = mapped_column(String(1023))  # RSS 条目唯一标识符
    title: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(
        Text
    )  # RSS 条目链接，可以给人看或者用在条目本身没多少内容的时候
    published: Mapped[datetime | None]  # RSS 条目发布的 UTC 时间
    updated: Mapped[datetime | None]  # RSS 条目更新的 UTC 时间
    fetched: Mapped[datetime]  # RSS 条目最后一次抓取的 UTC 时间
    summary: Mapped[str | None] = mapped_column(Text)  # RSS 条目摘要/描述（HTML）
    summary_plain: Mapped[str | None] = mapped_column(
        Text
    )  # RSS 条目摘要/描述（纯文本）
    content: Mapped[str | None] = mapped_column(Text)  # RSS 条目正文（HTML）
    content_plain: Mapped[str | None] = mapped_column(Text)  # RSS 条目正文（纯文本）
    author_name: Mapped[str | None] = mapped_column(Text)
    author_href: Mapped[str | None] = mapped_column(Text)
    author_email: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(
        Text
    )  # 存储为 JSON 字符串，用户在前端可以看到 RSS 条目自己声明的标签
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("feeds.id")
    )  # 聚合源中文章的原始来源的 RSS 条目 ID，目前暂时忽略
    __table_args__ = (
        UniqueConstraint("feed_id", "guid"),
    )  # 约束同一个 RSS 源下的条目 guid 唯一


class Enclosure(Base):
    """RSS 条目的附件信息，通常是音频、视频、图片等媒体文件，但不存数据，让前端去请求附件地址"""

    __tablename__ = "enclosures"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id"))  # 所属 RSS 条目 ID
    href: Mapped[str] = mapped_column(Text)  # 附件地址
    length: Mapped[int | None]  # 附件大小，单位为字节
    type: Mapped[str | None] = mapped_column(Text)  # 附件 MIME 类型

    __table_args__ = (UniqueConstraint("entry_id", "href"),)


class ReadState(Base):
    """记录用户对 RSS 条目的阅读状态，有即为已读，没有则为未读"""

    __tablename__ = "read_states"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id"), primary_key=True)
    timestamp: Mapped[
        datetime | None
    ]  # 用户最后一次阅读该条目的时间，若为 None 则表示标为已读但不显示在历史记录里
    # 不过之后可能会取消标为已读这个操作，目前还不确定


class StarState(Base):
    """记录用户对 RSS 条目的收藏状态，有即为已收藏，没有则为未收藏"""

    __tablename__ = "star_states"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id"), primary_key=True)
    tag_id: Mapped[int | None] = mapped_column(
        ForeignKey("labels.id")
    )  # 收藏时用户选择的标签 ID，若为 None 则表示未选择标签
    timestamp: Mapped[datetime]  # 用户收藏该条目的时间
