#!/bin/sh
# Install WordPress and the WooCommerce plugin on the shared html volume.
# This script does not create or print REST API keys.
# Keep this copy identical to helm/urumi/scripts/init-store.sh and deploy/wordpress/init-store.sh.
set -eu

cd /var/www/html

attempts="${WORDPRESS_CONFIG_WAIT_ATTEMPTS:-60}"
i=0
while [ ! -f wp-config.php ]; do
  i=$((i + 1))
  if [ "$i" -gt "$attempts" ]; then
    echo "wp-config.php was not created." >&2
    exit 1
  fi
  sleep 2
done

require() {
  value=$(printenv "$1" || true)
  if [ -z "$value" ]; then
    echo "Set $1 before starting wordpress-init." >&2
    exit 1
  fi
}

require WORDPRESS_URL
require WORDPRESS_ADMIN_USER
require WORDPRESS_ADMIN_PASSWORD
require WORDPRESS_ADMIN_EMAIL

if wp core is-installed >/dev/null 2>&1; then
  echo "WordPress is already installed. Core is not updated on upgrade."
else
  wp core install \
    --url="$WORDPRESS_URL" \
    --title="${WORDPRESS_TITLE:-Local Store}" \
    --admin_user="$WORDPRESS_ADMIN_USER" \
    --admin_password="$WORDPRESS_ADMIN_PASSWORD" \
    --admin_email="$WORDPRESS_ADMIN_EMAIL" \
    --skip-email
  # The image tag can lag the current release. Update once, during first install.
  wp core update
fi

if wp plugin is-installed woocommerce >/dev/null 2>&1; then
  wp plugin activate woocommerce >/dev/null
else
  wp plugin install woocommerce --activate
fi

wp option update permalink_structure '/%postname%/' >/dev/null
wp wc tool run install_pages --user="$WORDPRESS_ADMIN_USER" >/dev/null

# Optional demonstration gateway. Empty leaves payment methods unchanged.
# This does not create products, orders, or customers.
demo=$(printenv WOOCOMMERCE_DEMO_PAYMENT || true)
if [ "$demo" = "cod" ]; then
  wp eval '
    $settings = get_option("woocommerce_cod_settings");
    if (!is_array($settings)) { $settings = array(); }
    $settings["enabled"] = "yes";
    if (empty($settings["title"])) { $settings["title"] = "Cash on delivery"; }
    update_option("woocommerce_cod_settings", $settings);
  ' >/dev/null
  echo "Cash on delivery is enabled for demonstration. No order was created."
elif [ -n "$demo" ]; then
  echo "WOOCOMMERCE_DEMO_PAYMENT must be empty or cod." >&2
  exit 1
fi

echo "WooCommerce is active. Create REST API keys in the admin UI. This script does not print them."
