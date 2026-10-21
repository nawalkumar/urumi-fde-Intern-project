#!/bin/sh
# Print the disposable two-store check. This script does not touch a cluster
# unless --execute is passed and the context checks below succeed.
# Do not point it at kind-urumi-k8s-dev or kind-urumi-k8s-test.
set -eu

if [ "${1:-}" != "--execute" ]; then
  echo "Refusing to call kubectl. Pass --execute only after the context checks in this file are true."
  echo "See docs/multi-store.md for the prerequisites and the steps."
  exit 2
fi

context="${URUMI_DISPOSABLE_KUBE_CONTEXT:-}"
current="$(kubectl config current-context 2>/dev/null || true)"
case "$context" in
  *disposable*) ;;
  *)
    echo "URUMI_DISPOSABLE_KUBE_CONTEXT must contain disposable."
    exit 2
    ;;
esac
if [ "$context" != "$current" ]; then
  echo "The current kubectl context does not match URUMI_DISPOSABLE_KUBE_CONTEXT."
  exit 2
fi
case "$current" in
  kind-urumi-k8s-dev|kind-urumi-k8s-test)
    echo "Refusing the existing Kind context."
    exit 2
    ;;
esac

echo "Context $current is eligible. This copy does not create stores."
echo "Create two stores from the dashboard, wait for Ready, compare list_orders, delete the first, and repeat that delete."
echo "Record namespaces, policy names, and tool names. Do not print Secret data."
exit 0
