import builtins
from abc import ABC


class ServiceError(Exception, ABC):
    log_message: str

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not hasattr(cls, "log_message") or not isinstance(cls.log_message, str):
            raise NotImplementedError(
                f"{cls.__name__} 必须定义 log_message 属性且必须是字符串类型"
            )

    def __init__(self, message: str | None = None):
        super().__init__(message or self.log_message)


class ConflictError(ServiceError, ABC):
    log_message = "资源冲突错误"


class RegisterExistingUserError(ConflictError):
    log_message = "尝试注册已存在的用户"

    @classmethod
    def from_username(cls, username: str) -> RegisterExistingUserError:
        return cls(f"{cls.log_message}：{username}")


class PermissionError(ServiceError, builtins.PermissionError, ABC):
    log_message = "权限错误"


class ListUsersPermissionError(PermissionError):
    log_message = "非管理员没有权限查看用户列表"


class UpdateAdminStatePermissionError(PermissionError):
    log_message = "非管理员没有权限修改用户权限"


class UserDeletionPermissionError(PermissionError):
    log_message = "非管理员没有权限删除其他用户"


class NotAllowedError(ServiceError, ABC):
    log_message = "操作不允许"


class RegisterDisabledError(NotAllowedError):
    log_message = "当前不允许注册"


class InvalidInviteCodeError(NotAllowedError):
    log_message = "邀请码错误"


class LastAdminDeletionError(NotAllowedError):
    log_message = "不允许删除最后一个管理员"

    @classmethod
    def from_username(cls, username: str) -> LastAdminDeletionError:
        return cls(f"{cls.log_message}（{username}）")


class ValueError(ServiceError, builtins.ValueError, ABC):
    log_message = "值错误"


class SlashInUsernameError(ValueError):
    log_message = "用户名中不允许包含斜杠（/）"


class InvalidSearchQueryError(ValueError):
    """搜索查询串 FTS5 解析不了（引号不闭合、运算符用错、括号不配对……）

    是用户打错了，不是服务器故障，所以 API 层对应 400。
    """

    log_message = "搜索查询语法错误"

    @classmethod
    def from_query(cls, query: str) -> InvalidSearchQueryError:
        return cls(f"{cls.log_message}（{query}）")


class FeedFetchFailedError(ServiceError):
    """订阅一个源时抓不到，而且**重试也不会好**

    两种情况走这里：对方回了 4xx（403 被 WAF 拒、404 源已失效、410 已删），或者
    抓到的根本不是 feed（2xx 但一条条目都没有）。

    网络层失败（超时/DNS/TLS）与 5xx/429 **不**走这里 —— 那是「上游现在不行」，
    会把订阅先建起来、内容交给调度器重试（见 `SubscriptionService.subscribe`）。
    """

    log_message = "订阅源抓取失败"
