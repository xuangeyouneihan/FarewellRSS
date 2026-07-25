from enum import Enum


class Filtering(str, Enum):
    READ = "read"
    UNREAD = "unread"
    STARRED = "starred"
