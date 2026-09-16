"""组合根：从一个数据库 session 组装整套 service。

需要这套装配的地方有三处：API 请求（api/deps.py）、定时任务（scheduler）、
后台作业（jobs）。集中在这里有两个意义：

1. 装配顺序只定义一次——否则三处必须手工保持同步；
2. 后台代码必须**自建 session**：请求作用域的 session 在请求结束后会被提交
   并关闭，而 AsyncSession 不允许被两个任务并发使用（后台任务与请求收尾会
   互相踩踏）。自建 session 后就没有共享，也就没有竞态。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .db.repositories.entry import EntryRepository
from .db.repositories.feed import FeedRepository
from .db.repositories.label import LabelRepository
from .db.repositories.read_state import ReadStateRepository
from .db.repositories.star_state import StarStateRepository
from .db.repositories.subscription import SubscriptionRepository
from .db.repositories.user import UserRepository
from .services.entry import EntryService
from .services.feed import FeedService
from .services.label import LabelService
from .services.read_batch import ReadBatchService
from .services.read_state import ReadStateService
from .services.star_state import StarStateService
from .services.subscription import SubscriptionService
from .services.user import UserService


@dataclass
class ServiceBundle:
    """一整套共享同一个 session 的 service"""

    read_state: ReadStateService
    star_state: StarStateService
    entry: EntryService
    feed: FeedService
    subscription: SubscriptionService
    label: LabelService
    read_batch: ReadBatchService
    user: UserService


def build_services(session: AsyncSession) -> ServiceBundle:
    """按依赖顺序组装 service 图

    注意：传进来的 session 决定这套 service 的事务边界。请求里传请求 session，
    后台任务里传自建 session，**不要混用**。
    """
    read_state = ReadStateService(repository=ReadStateRepository(session=session))
    star_state = StarStateService(repository=StarStateRepository(session=session))
    entry = EntryService(
        repository=EntryRepository(session=session),
        read_state_service=read_state,
        star_state_service=star_state,
    )
    feed = FeedService(repository=FeedRepository(session=session), entry_service=entry)
    subscription = SubscriptionService(
        repository=SubscriptionRepository(session=session),
        feed_service=feed,
        read_state_service=read_state,
        star_state_service=star_state,
    )
    label = LabelService(
        repository=LabelRepository(session=session),
        subscription_service=subscription,
        star_state_service=star_state,
        entry_service=entry,
    )
    read_batch = ReadBatchService(
        read_state_service=read_state,
        entry_service=entry,
        feed_service=feed,
        subscription_service=subscription,
        star_state_service=star_state,
    )
    user = UserService(
        repository=UserRepository(session=session),
        subscription_service=subscription,
        label_service=label,
        read_state_service=read_state,
        star_state_service=star_state,
    )
    return ServiceBundle(
        read_state=read_state,
        star_state=star_state,
        entry=entry,
        feed=feed,
        subscription=subscription,
        label=label,
        read_batch=read_batch,
        user=user,
    )
