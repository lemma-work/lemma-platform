from enum import Enum


class SearchVertical(str, Enum):
    IMAGES = "images"
    NEWS = "news"
    VIDEOS = "videos"
    WEB = "web"

    def __str__(self) -> str:
        return str(self.value)
