from __future__ import annotations

from .base import Adapter
from .fx import FxAdapter
from .google_trends import GoogleTrendsAdapter
from .manual import OwnSalesAdapter, PasteBoxAdapter, ScreenshotAdapter
from .telegram_channels import TelegramChannelsAdapter
from .vidiq_import import VidiqImportAdapter
from .youtube import YouTubeAdapter


def build_registry() -> dict[str, Adapter]:
    adapters: list[Adapter] = [VidiqImportAdapter(), YouTubeAdapter(), GoogleTrendsAdapter(), TelegramChannelsAdapter(),
                               PasteBoxAdapter(), ScreenshotAdapter(), OwnSalesAdapter(), FxAdapter()]
    return {a.name: a for a in adapters}


REGISTRY = build_registry()
