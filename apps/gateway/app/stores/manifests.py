"""Kubernetes objects for one store namespace.

The provisioner applies these dicts. They are not part of the live Helm
release. Secret values are passed in only when a Secret object is built, and
they are not copied into labels, names, or log text.
"""

import base64
from dataclasses import dataclass
from pathlib import Path

from apps.gateway.app.stores.model import owned_namespace

_INIT_SCRIPT = Path(__file__).with_name("init_store.sh").read_text(encoding="utf-8")


@dataclass(frozen=True)
class StoreSpec:
    store_id: str
    owner_id: str
    namespace: str
    host: str
    title: str
    wordpress_image: str
    mariadb_image: str
    cli_image: str
    mcp_image: str
    gateway_namespace: str
    ingress_namespace: str
    ingress_class: str
    storage_class: str
    allow_plugin_egress: bool
    probe_cidrs: tuple[str, ...]


@dataclass(frozen=True)
class SecretMaterial:
    mariadb_root: str
    mariadb_password: str
    admin_password: str
    consumer_key: str
    consumer_secret: str

    def __repr__(self) -> str:
        return "SecretMaterial(redacted)"


def labels(spec: StoreSpec, component: str) -> dict[str, str]:
    return {
        "urumi.dev/store-id": spec.store_id,
        "urumi.dev/owner-id": spec.owner_id,
        "urumi.dev/store": "true",
        "app.kubernetes.io/name": "urumi-store",
        "app.kubernetes.io/component": component,
        "app.kubernetes.io/managed-by": "urumi-store-provisioner",
        "app.kubernetes.io/part-of": "urumi",
    }


def _meta(spec: StoreSpec, name: str, component: str, *, namespaced: bool = True) -> dict[str, object]:
    if namespaced and not owned_namespace(spec.store_id, spec.namespace):
        raise ValueError("Store namespace is not owned by this store.")
    metadata: dict[str, object] = {"name": name, "labels": labels(spec, component)}
    if namespaced:
        metadata["namespace"] = spec.namespace
    return metadata


def _selector(component: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": "urumi-store",
        "app.kubernetes.io/component": component,
    }


def _resources(requests: dict[str, str], limits: dict[str, str]) -> dict[str, object]:
    return {"requests": requests, "limits": limits}


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _probe_from(spec: StoreSpec) -> list[dict[str, object]]:
    return [{"ipBlock": {"cidr": cidr}} for cidr in spec.probe_cidrs]


def _dns_egress() -> dict[str, object]:
    return {
        "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}],
        "ports": [
            {"protocol": "UDP", "port": 53},
            {"protocol": "TCP", "port": 53},
        ],
    }


def _public_https_egress() -> dict[str, object]:
    return {
        "to": [
            {
                "ipBlock": {
                    "cidr": "0.0.0.0/0",
                    "except": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"],
                }
            }
        ],
        "ports": [{"protocol": "TCP", "port": 443}],
    }


def workload_resources(spec: StoreSpec) -> list[dict[str, object]]:
    """Every object except Secrets. Safe to compare in tests."""
    return [
        _namespace(spec),
        _service_account(spec),
        _quota(spec),
        _limit_range(spec),
        *_network_policies(spec),
        _config(spec),
        _init_config(spec),
        _wordpress_pvc(spec),
        _db_service(spec),
        _db_statefulset(spec),
        _wordpress_service(spec),
        _wordpress_deployment(spec),
        _mcp_service(spec),
        _mcp_deployment(spec),
        _init_job(spec),
        _ingress(spec),
    ]


def secret_resources(spec: StoreSpec, material: SecretMaterial) -> list[dict[str, object]]:
    return [_db_secret(spec, material), _woo_secret(spec, material)]


def _namespace(spec: StoreSpec) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": _meta(spec, spec.namespace, "namespace", namespaced=False),
    }


def _service_account(spec: StoreSpec) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": _meta(spec, "store-workload", "workload"),
        "automountServiceAccountToken": False,
    }


def _quota(spec: StoreSpec) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": _meta(spec, "store", "quota"),
        "spec": {
            "hard": {
                "requests.cpu": "2",
                "requests.memory": "2Gi",
                "limits.cpu": "4",
                "limits.memory": "4Gi",
                "pods": "8",
                "persistentvolumeclaims": "2",
                "services": "6",
            }
        },
    }


def _limit_range(spec: StoreSpec) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "LimitRange",
        "metadata": _meta(spec, "store", "limits"),
        "spec": {
            "limits": [
                {
                    "type": "Container",
                    "default": {"cpu": "250m", "memory": "256Mi"},
                    "defaultRequest": {"cpu": "25m", "memory": "64Mi"},
                    "max": {"cpu": "1", "memory": "768Mi"},
                }
            ]
        },
    }


def _network_policies(spec: StoreSpec) -> list[dict[str, object]]:
    ingress_peers: list[dict[str, object]] = [
        {"podSelector": {"matchLabels": _selector("woo-mcp")}},
        {"podSelector": {"matchLabels": _selector("wordpress-init")}},
        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": spec.ingress_namespace}}},
        *_probe_from(spec),
    ]
    db_peers: list[dict[str, object]] = [
        {"podSelector": {"matchLabels": _selector("wordpress")}},
        {"podSelector": {"matchLabels": _selector("wordpress-init")}},
    ]
    mcp_peers: list[dict[str, object]] = [
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": spec.gateway_namespace}},
            "podSelector": {"matchLabels": {"urumi.dev/gateway": "allowed"}},
        },
        *_probe_from(spec),
    ]
    init_egress: list[dict[str, object]] = [
        {
            "to": [{"podSelector": {"matchLabels": _selector("store-db")}}],
            "ports": [{"protocol": "TCP", "port": 3306}],
        },
        _dns_egress(),
    ]
    if spec.allow_plugin_egress:
        init_egress.append(_public_https_egress())
    return [
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": _meta(spec, "default-deny", "default-deny"),
            "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": _meta(spec, "wordpress", "wordpress-net"),
            "spec": {
                "podSelector": {"matchLabels": _selector("wordpress")},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{"from": ingress_peers, "ports": [{"protocol": "TCP", "port": 80}]}],
                "egress": [
                    {
                        "to": [{"podSelector": {"matchLabels": _selector("store-db")}}],
                        "ports": [{"protocol": "TCP", "port": 3306}],
                    },
                    _dns_egress(),
                ],
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": _meta(spec, "store-db", "store-db-net"),
            "spec": {
                "podSelector": {"matchLabels": _selector("store-db")},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{"from": db_peers, "ports": [{"protocol": "TCP", "port": 3306}]}],
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": _meta(spec, "woo-mcp", "woo-mcp-net"),
            "spec": {
                "podSelector": {"matchLabels": _selector("woo-mcp")},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{"from": mcp_peers, "ports": [{"protocol": "TCP", "port": 8000}]}],
                "egress": [
                    {
                        "to": [{"podSelector": {"matchLabels": _selector("wordpress")}}],
                        "ports": [{"protocol": "TCP", "port": 80}],
                    },
                    _dns_egress(),
                ],
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": _meta(spec, "wordpress-init", "wordpress-init-net"),
            "spec": {
                "podSelector": {"matchLabels": _selector("wordpress-init")},
                "policyTypes": ["Ingress", "Egress"],
                "egress": init_egress,
            },
        },
    ]


def _config(spec: StoreSpec) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _meta(spec, "store-config", "config"),
        "data": {
            "WORDPRESS_DB_HOST": "store-db",
            "WORDPRESS_DB_NAME": "wordpress",
            "WORDPRESS_DB_USER": "wordpress",
            "WORDPRESS_URL": f"http://{spec.host}",
            "WORDPRESS_TITLE": spec.title[:40],
            "WORDPRESS_CONFIG_WAIT_ATTEMPTS": "180",
        },
    }


def _init_config(spec: StoreSpec) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _meta(spec, "store-init", "init"),
        "data": {"init-store.sh": _INIT_SCRIPT},
    }


def _wordpress_pvc(spec: StoreSpec) -> dict[str, object]:
    spec_body: dict[str, object] = {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": "2Gi"}},
    }
    if spec.storage_class:
        spec_body["storageClassName"] = spec.storage_class
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": _meta(spec, "wordpress-data", "wordpress-data"),
        "spec": spec_body,
    }


def _db_service(spec: StoreSpec) -> dict[str, object]:
    return _service(spec, "store-db", "store-db", 3306)


def _wordpress_service(spec: StoreSpec) -> dict[str, object]:
    return _service(spec, "wordpress", "wordpress", 80)


def _mcp_service(spec: StoreSpec) -> dict[str, object]:
    return _service(spec, "woo-mcp", "woo-mcp", 8000)


def _service(spec: StoreSpec, name: str, component: str, port: int) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(spec, name, component),
        "spec": {
            "selector": _selector(component),
            "ports": [{"name": "tcp", "port": port, "targetPort": port, "protocol": "TCP"}],
        },
    }


def _db_secret(spec: StoreSpec, material: SecretMaterial) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _meta(spec, "store-db", "store-db-secret"),
        "type": "Opaque",
        "data": {
            "MARIADB_ROOT_PASSWORD": _b64(material.mariadb_root),
            "MARIADB_PASSWORD": _b64(material.mariadb_password),
            "WORDPRESS_ADMIN_PASSWORD": _b64(material.admin_password),
        },
    }


def _woo_secret(spec: StoreSpec, material: SecretMaterial) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _meta(spec, "woocommerce-credentials", "woocommerce-secret"),
        "type": "Opaque",
        "data": {
            "WOOCOMMERCE_CONSUMER_KEY": _b64(material.consumer_key),
            "WOOCOMMERCE_CONSUMER_SECRET": _b64(material.consumer_secret),
        },
    }


def _pod_security_db() -> dict[str, object]:
    return {
        "fsGroup": 999,
        "fsGroupChangePolicy": "OnRootMismatch",
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def _pod_security_wordpress() -> dict[str, object]:
    return {
        "fsGroup": 33,
        "fsGroupChangePolicy": "OnRootMismatch",
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def _container_security() -> dict[str, object]:
    return {"allowPrivilegeEscalation": False}


def _env_from_config(key: str) -> dict[str, object]:
    return {
        "name": key,
        "valueFrom": {"configMapKeyRef": {"name": "store-config", "key": key}},
    }


def _env_from_secret(name: str, key: str, secret_name: str) -> dict[str, object]:
    return {
        "name": name,
        "valueFrom": {"secretKeyRef": {"name": secret_name, "key": key}},
    }


def _db_statefulset(spec: StoreSpec) -> dict[str, object]:
    claim: dict[str, object] = {
        "metadata": {"name": "data"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "2Gi"}},
        },
    }
    if spec.storage_class:
        claim["spec"]["storageClassName"] = spec.storage_class  # type: ignore[index]
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": _meta(spec, "store-db", "store-db"),
        "spec": {
            "serviceName": "store-db",
            "replicas": 1,
            "selector": {"matchLabels": _selector("store-db")},
            "template": {
                "metadata": {"labels": labels(spec, "store-db")},
                "spec": {
                    "serviceAccountName": "store-workload",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": _pod_security_db(),
                    "containers": [
                        {
                            "name": "mariadb",
                            "image": spec.mariadb_image,
                            "imagePullPolicy": "IfNotPresent",
                            "ports": [{"name": "mysql", "containerPort": 3306}],
                            "env": [
                                _env_from_secret("MARIADB_ROOT_PASSWORD", "MARIADB_ROOT_PASSWORD", "store-db"),
                                _env_from_secret("MARIADB_PASSWORD", "MARIADB_PASSWORD", "store-db"),
                                {"name": "MARIADB_DATABASE", "value": "wordpress"},
                                {"name": "MARIADB_USER", "value": "wordpress"},
                            ],
                            "securityContext": _container_security(),
                            "resources": _resources(
                                {"cpu": "100m", "memory": "256Mi"},
                                {"cpu": "500m", "memory": "512Mi"},
                            ),
                            "readinessProbe": {"tcpSocket": {"port": 3306}, "periodSeconds": 10},
                        }
                    ],
                },
            },
            "volumeClaimTemplates": [claim],
        },
    }


def _wordpress_deployment(spec: StoreSpec) -> dict[str, object]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(spec, "wordpress", "wordpress"),
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": _selector("wordpress")},
            "template": {
                "metadata": {"labels": labels(spec, "wordpress")},
                "spec": {
                    "serviceAccountName": "store-workload",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": _pod_security_wordpress(),
                    "containers": [
                        {
                            "name": "wordpress",
                            "image": spec.wordpress_image,
                            "imagePullPolicy": "IfNotPresent",
                            "ports": [{"name": "http", "containerPort": 80}],
                            "env": [
                                _env_from_config("WORDPRESS_DB_HOST"),
                                _env_from_config("WORDPRESS_DB_NAME"),
                                _env_from_config("WORDPRESS_DB_USER"),
                                _env_from_secret("WORDPRESS_DB_PASSWORD", "MARIADB_PASSWORD", "store-db"),
                            ],
                            "securityContext": _container_security(),
                            "resources": _resources(
                                {"cpu": "100m", "memory": "256Mi"},
                                {"cpu": "500m", "memory": "512Mi"},
                            ),
                            "readinessProbe": {"httpGet": {"path": "/", "port": 80}, "periodSeconds": 10},
                            "volumeMounts": [{"name": "data", "mountPath": "/var/www/html"}],
                        }
                    ],
                    "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "wordpress-data"}}],
                },
            },
        },
    }


def _mcp_deployment(spec: StoreSpec) -> dict[str, object]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(spec, "woo-mcp", "woo-mcp"),
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": _selector("woo-mcp")},
            "template": {
                "metadata": {"labels": labels(spec, "woo-mcp")},
                "spec": {
                    "serviceAccountName": "store-workload",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "mcp",
                            "image": spec.mcp_image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": [
                                "uvicorn",
                                "apps.woocommerce_mcp.app.main:app",
                                "--host",
                                "0.0.0.0",
                                "--port",
                                "8000",
                            ],
                            "ports": [{"name": "http", "containerPort": 8000}],
                            "env": [
                                {"name": "WOOCOMMERCE_BASE_URL", "value": "http://wordpress"},
                                {"name": "WOOCOMMERCE_SITE_URL", "value": f"http://{spec.host}"},
                                {"name": "WOOCOMMERCE_TIMEOUT_SECONDS", "value": "8"},
                                _env_from_secret(
                                    "WOOCOMMERCE_CONSUMER_KEY",
                                    "WOOCOMMERCE_CONSUMER_KEY",
                                    "woocommerce-credentials",
                                ),
                                _env_from_secret(
                                    "WOOCOMMERCE_CONSUMER_SECRET",
                                    "WOOCOMMERCE_CONSUMER_SECRET",
                                    "woocommerce-credentials",
                                ),
                            ],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": _resources(
                                {"cpu": "25m", "memory": "64Mi"},
                                {"cpu": "250m", "memory": "256Mi"},
                            ),
                            "readinessProbe": {"httpGet": {"path": "/health", "port": 8000}, "periodSeconds": 10},
                        }
                    ],
                },
            },
        },
    }


def _init_job(spec: StoreSpec) -> dict[str, object]:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": _meta(spec, "wordpress-init", "wordpress-init"),
        "spec": {
            "backoffLimit": 3,
            "activeDeadlineSeconds": 1800,
            "ttlSecondsAfterFinished": 86400,
            "template": {
                "metadata": {"labels": labels(spec, "wordpress-init")},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": "store-workload",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 33,
                        "runAsGroup": 33,
                        "fsGroup": 33,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "wordpress-init",
                            "image": spec.cli_image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["sh", "/scripts/init-store.sh"],
                            "env": [
                                _env_from_config("WORDPRESS_DB_HOST"),
                                _env_from_config("WORDPRESS_DB_NAME"),
                                _env_from_config("WORDPRESS_DB_USER"),
                                _env_from_config("WORDPRESS_URL"),
                                _env_from_config("WORDPRESS_TITLE"),
                                _env_from_config("WORDPRESS_CONFIG_WAIT_ATTEMPTS"),
                                _env_from_secret("WORDPRESS_DB_PASSWORD", "MARIADB_PASSWORD", "store-db"),
                                {"name": "WORDPRESS_ADMIN_USER", "value": "storeadmin"},
                                _env_from_secret("WORDPRESS_ADMIN_PASSWORD", "WORDPRESS_ADMIN_PASSWORD", "store-db"),
                                {"name": "WORDPRESS_ADMIN_EMAIL", "value": f"{spec.store_id}@stores.local"},
                                _env_from_secret(
                                    "WOOCOMMERCE_CONSUMER_KEY",
                                    "WOOCOMMERCE_CONSUMER_KEY",
                                    "woocommerce-credentials",
                                ),
                                _env_from_secret(
                                    "WOOCOMMERCE_CONSUMER_SECRET",
                                    "WOOCOMMERCE_CONSUMER_SECRET",
                                    "woocommerce-credentials",
                                ),
                            ],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": _resources(
                                {"cpu": "50m", "memory": "128Mi"},
                                {"cpu": "500m", "memory": "512Mi"},
                            ),
                            "volumeMounts": [
                                {"name": "data", "mountPath": "/var/www/html"},
                                {"name": "script", "mountPath": "/scripts"},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "data", "persistentVolumeClaim": {"claimName": "wordpress-data"}},
                        {"name": "script", "configMap": {"name": "store-init", "defaultMode": 0o555}},
                    ],
                },
            },
        },
    }


def _ingress(spec: StoreSpec) -> dict[str, object]:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": _meta(spec, "store", "ingress"),
        "spec": {
            "ingressClassName": spec.ingress_class,
            "rules": [
                {
                    "host": spec.host,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {"service": {"name": "wordpress", "port": {"number": 80}}},
                            }
                        ]
                    },
                }
            ],
        },
    }


def mcp_url(namespace: str) -> str:
    return f"http://woo-mcp.{namespace}.svc.cluster.local:8000/mcp"


def public_url(host: str) -> str:
    return f"http://{host}"
