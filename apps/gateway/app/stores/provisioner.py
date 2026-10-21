"""Cluster reconciler for provisioned stores.

This process is not the public gateway. It is started only from the
store-provisioner Deployment, which is the only pod that mounts the
provisioner service account. The gateway process does not import this as
its server and does not call it on startup.
"""

import asyncio
import logging

from apps.gateway.app.catalog import refresh_registry
from apps.gateway.app.config import Settings
from apps.gateway.app.main import _store_from_settings
from apps.gateway.app.registry import Registry
from apps.gateway.app.stores.cluster import incluster_client
from apps.gateway.app.stores.service import StoreService

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings.from_env()
    if not settings.stores.enabled or not settings.stores.cluster_reconciler:
        logger.error("Store cluster reconciler is not enabled.")
        raise SystemExit(2)
    client = incluster_client()
    if client is None:
        logger.error("Kubernetes service account is not mounted.")
        raise SystemExit(2)
    store = _store_from_settings(settings)
    registry = Registry(settings.upstreams)
    service = StoreService(
        store,
        registry,
        settings,
        client,
        cluster_reconciler=True,
    )
    async with store.lifespan():
        try:
            await refresh_registry(registry, store, settings)
        except Exception as exc:
            logger.warning("Store catalog was not loaded: %s", type(exc).__name__)
        await service.worker()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
