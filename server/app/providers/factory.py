from __future__ import annotations

import logging

from app.core.config import settings
from app.providers.base import DataProvider
from app.providers.seed import SeedProvider

logger = logging.getLogger("kairos.provider")

_provider: DataProvider | None = None


def _try_akshare() -> DataProvider | None:
    try:
        from app.providers.akshare_provider import AkShareProvider

        provider = AkShareProvider()
        # Quick connectivity probe — one small call.
        provider.get_indices()
        logger.info("Using AkShareProvider (live A-share data)")
        return provider
    except Exception as exc:  # noqa: BLE001
        logger.warning("AkShare unavailable (%s); falling back to SeedProvider", exc)
        return None


def get_provider() -> DataProvider:
    global _provider
    if _provider is not None:
        return _provider

    choice = settings.provider.lower()
    if choice == "seed":
        _provider = SeedProvider()
    elif choice == "akshare":
        _provider = _try_akshare() or SeedProvider()
    else:  # auto
        _provider = _try_akshare() or SeedProvider()

    logger.info("Data provider resolved to: %s", _provider.name)
    return _provider
