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
