"""Test double for the store provisioner. Not used by the gateway process."""

from apps.gateway.app.stores.cluster import assert_owned_resource, assert_store_namespace
from apps.gateway.app.stores.model import OwnershipError


class FakeCluster:
    def __init__(self) -> None:
        self.items: dict[tuple[str | None, str, str], dict] = {}
        self.ready = False
        self.fail_deployments = False
        self.on_apply = None

    def _key(self, resource: dict) -> tuple[str | None, str, str]:
        namespace = resource["metadata"].get("namespace")
        return (namespace, str(resource["kind"]), str(resource["metadata"]["name"]))

    async def apply(self, resource: dict) -> None:
        if self.on_apply is not None:
            await self.on_apply(resource)
        assert_owned_resource(resource)
        store_id = resource["metadata"]["labels"]["urumi.dev/store-id"]
        key = self._key(resource)
        existing = self.items.get(key)
        if existing is not None and existing["metadata"]["labels"].get("urumi.dev/store-id") != store_id:
            raise OwnershipError("Store resource is not owned by this store.")
        if resource["kind"] == "Secret" and existing is not None:
            return
        if self.fail_deployments and resource["kind"] == "Deployment":
            raise RuntimeError("deployment apply failed")
        self.items[key] = resource

    async def delete_namespace(self, name: str, store_id: str) -> None:
        assert_store_namespace(name, store_id)
        existing = self.items.get((None, "Namespace", name))
        if existing is None:
            return
        if existing["metadata"]["labels"].get("urumi.dev/store-id") != store_id:
            raise OwnershipError("Store namespace is not owned by this store.")
        for key in list(self.items):
            namespace, kind, item_name = key
            if (kind == "Namespace" and item_name == name) or namespace == name:
                self.items.pop(key, None)

    async def namespace_exists(self, name: str, store_id: str) -> bool:
        assert_store_namespace(name, store_id)
        existing = self.items.get((None, "Namespace", name))
        if existing is None:
            return False
        return existing["metadata"]["labels"].get("urumi.dev/store-id") == store_id

    async def secret_exists(self, namespace: str, name: str, store_id: str) -> bool:
        assert_store_namespace(namespace, store_id)
        existing = self.items.get((namespace, "Secret", name))
        if existing is None:
            return False
        return existing["metadata"]["labels"].get("urumi.dev/store-id") == store_id

    async def workloads_ready(self, namespace: str, store_id: str) -> bool:
        assert_store_namespace(namespace, store_id)
        if not self.ready:
            return False
        required = (
            (namespace, "Deployment", "wordpress"),
            (namespace, "StatefulSet", "store-db"),
            (namespace, "Deployment", "woo-mcp"),
            (namespace, "Job", "wordpress-init"),
        )
        return all(key in self.items for key in required)
