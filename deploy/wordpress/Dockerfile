# WordPress image with a pinned WooCommerce plugin already unpacked.
# The official entrypoint copies /usr/src/wordpress into an empty volume,
# including wp-content/plugins/woocommerce. The store init job then activates
# that copy and does not download the plugin.
# Build this only for a disposable cluster. Do not retag it onto the demo store.
FROM wordpress:6.8-php8.2-apache

ARG WOOCOMMERCE_VERSION=9.4.3
ADD https://downloads.wordpress.org/plugin/woocommerce.${WOOCOMMERCE_VERSION}.zip /tmp/woocommerce.zip
RUN apt-get update \
    && apt-get install -y --no-install-recommends unzip \
    && unzip -q /tmp/woocommerce.zip -d /usr/src/wordpress/wp-content/plugins \
    && rm /tmp/woocommerce.zip \
    && rm -rf /var/lib/apt/lists/* \
    && test -f /usr/src/wordpress/wp-content/plugins/woocommerce/woocommerce.php
