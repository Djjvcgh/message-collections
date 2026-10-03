"""信源注册表。

type 名 -> 实现类。配置 config/sources.yml 里的 sources[].type 即取这里。
"""
from .feed import RssSource
from .radar import RadarSource
from .telegram import TelegramChannelSource

SOURCE_TYPES = {
    "radar": RadarSource,
    "rss": RssSource,
    "telegram": TelegramChannelSource,
}

__all__ = ["SOURCE_TYPES", "RadarSource", "RssSource", "TelegramChannelSource"]
