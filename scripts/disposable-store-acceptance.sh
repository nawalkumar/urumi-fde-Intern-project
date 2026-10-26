#!/bin/sh
# Disposable multi-store checks.
# This script never targets kind-urumi-k8s-dev or kind-urumi-k8s-test.
# It does not create a cluster, install a release, or delete a namespace.
# --unit runs Helm and in-process tests only.
# --execute talks to a cluster only after the context checks pass, and only
# with kubectl --context. It does not create stores.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
mode="${1:-}"

if [ "$mode" != "--unit" ] && [ "$mode" != "--execute" ]; then
  echo "Refusing to call kubectl. Use --unit for Helm and in-process tests, or --execute for a disposable cluster."
  echo "See docs/multi-store.md and docs/verification/multi-store-acceptance.md."
  exit 2
fi

helm_check() {
  helm lint "$root/helm/urumi" --set database.password=lint-placeholder --set store.enabled=false >/tmp/urumi-helm-lint-default.txt
  if helm template urumi "$root/helm/urumi" --set database.password=lint-placeholder --set store.enabled=false | grep -q "kind: ValidatingAdmissionPolicy"; then
    echo "Default values rendered a store admission policy."
    exit 1
  fi
  helm template urumi "$root/helm/urumi" --set storeProvisioner.enabled=true --set storeProvisioner.admit=false --set database.password=lint-placeholder >/tmp/urumi-helm-enabled-without-admit.txt 2>&1 && {
    echo "enabled without admit was accepted."
    exit 1
  }
  helm template urumi "$root/helm/urumi" -f "$root/helm/urumi/values-disposable.yaml" --set database.password=lint-placeholder >/tmp/urumi-helm-disposable.txt
  if ! grep -q "kind: ValidatingAdmissionPolicy" /tmp/urumi-helm-disposable.txt; then
    echo "Disposable values did not render the admission policy."
    exit 1
  fi
  if grep -q "0.0.0.0/0" /tmp/urumi-helm-disposable.txt; then
    echo "Disposable render contains broad public egress."
    exit 1
  fi
}

if [ "$mode" = "--unit" ]; then
  helm_check
  cd "$root"
  uv run pytest -q --tb=line \
    tests/test_stores.py \
    tests/test_store_kube.py \
    tests/test_store_manifests.py \
    tests/test_store_routing.py \
    tests/test_chat.py \
    tests/test_discovery_policy.py
  echo "Unit and Helm checks finished. No cluster was contacted."
  exit 0
fi

context="${URUMI_DISPOSABLE_KUBE_CONTEXT:-}"
case "$context" in
  *disposable*) ;;
  *)
    echo "URUMI_DISPOSABLE_KUBE_CONTEXT must contain disposable."
    exit 2
    ;;
esac
case "$context" in
  *urumi-k8s-dev*|*urumi-k8s-test*)
    echo "Refusing a protected Kind context."
    exit 2
    ;;
esac
current="$(kubectl config current-context 2>/dev/null || true)"
if [ "$context" != "$current" ]; then
  echo "The current kubectl context does not match URUMI_DISPOSABLE_KUBE_CONTEXT."
  exit 2
fi

k() {
  kubectl --context "$context" "$@"
}

echo "Context $context passed the name check. Reading it. No store will be created."
k config get-contexts "$context" >/dev/null
k get namespace kube-system -o name
k get validatingadmissionpolicy
k get networkpolicy -A
echo "Preflight read finished. Store creation is still a separate dashboard step."
