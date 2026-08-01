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
        logger.info("Using AkShareProvider (live A-share data via EastMoney)")
        return provider
    except Exception as exc:  # noqa: BLE001
        logger.warning("AkShare unavailable (%s)", exc)
        return None


def _try_tencent() -> DataProvider | None:
    try:
        from app.providers.tencent_provider import TencentProvider

        provider = TencentProvider()
        # Quick connectivity probe — one small call.
        if not provider.get_indices():
            raise RuntimeError("empty index response")
        logger.info("Using TencentProvider (live A-share data via Tencent/Sina)")
        return provider
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tencent/Sina unavailable (%s)", exc)
        return None


def _try_eastmoney() -> DataProvider | None:
    try:
        from app.providers.eastmoney_provider import EastmoneyProvider

        provider = EastmoneyProvider()
        # Quick connectivity probe — one small call.
        if not provider.get_indices():
            raise RuntimeError("empty index response")
        logger.info("Using EastmoneyProvider (live A-share data via EastMoney delayed feed)")
        return provider
    except Exception as exc:  # noqa: BLE001
        logger.warning("EastMoney unavailable (%s)", exc)
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
    elif choice == "tencent":
        _provider = _try_tencent() or SeedProvider()
    elif choice == "eastmoney":
        _provider = _try_eastmoney() or SeedProvider()
    else:  # auto
        _provider = _try_akshare() or _try_tencent() or _try_eastmoney() or SeedProvider()

    logger.info("Data provider resolved to: %s", _provider.name)
    return _provider
