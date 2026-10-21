#!/bin/sh
# Install WordPress and WooCommerce for one provisioned store.
# Credentials come from the environment. This script does not echo them.
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
require WOOCOMMERCE_CONSUMER_KEY
require WOOCOMMERCE_CONSUMER_SECRET

if wp core is-installed >/dev/null 2>&1; then
  echo "WordPress is already installed."
else
  wp core install \
    --url="$WORDPRESS_URL" \
    --title="${WORDPRESS_TITLE:-Store}" \
    --admin_user="$WORDPRESS_ADMIN_USER" \
    --admin_password="$WORDPRESS_ADMIN_PASSWORD" \
    --admin_email="$WORDPRESS_ADMIN_EMAIL" \
    --skip-email
fi

if wp plugin is-installed woocommerce >/dev/null 2>&1; then
  wp plugin activate woocommerce >/dev/null
else
  wp plugin install woocommerce --activate
fi

wp option update permalink_structure '/%postname%/' >/dev/null
wp wc tool run install_pages --user="$WORDPRESS_ADMIN_USER" >/dev/null

wp eval '
$key = getenv("WOOCOMMERCE_CONSUMER_KEY");
$secret = getenv("WOOCOMMERCE_CONSUMER_SECRET");
if (!is_string($key) || !is_string($secret) || $key === "" || $secret === "") {
    fwrite(STDERR, "WooCommerce credentials are missing.\n");
    exit(1);
}
if (!function_exists("wc_api_hash")) {
    fwrite(STDERR, "WooCommerce is not loaded.\n");
    exit(1);
}
global $wpdb;
$table = $wpdb->prefix . "woocommerce_api_keys";
$existing = $wpdb->get_var($wpdb->prepare("SELECT key_id FROM {$table} WHERE description = %s", "urumi-gateway"));
if ($existing) {
    exit(0);
}
$inserted = $wpdb->insert($table, array(
    "user_id" => 1,
    "description" => "urumi-gateway",
    "permissions" => "read_write",
    "consumer_key" => wc_api_hash($key),
    "consumer_secret" => wc_api_hash($secret),
    "truncated_key" => substr($key, -7),
));
if (!$inserted) {
    fwrite(STDERR, "WooCommerce credential insert failed.\n");
    exit(1);
}
' --user="$WORDPRESS_ADMIN_USER"

echo "WooCommerce is active. Credentials were not printed."
