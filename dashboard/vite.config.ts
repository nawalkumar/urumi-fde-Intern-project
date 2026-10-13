import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import { configureStatusProxy } from "./src/proxyError";

declare const process: {
  env: Record<string, string | undefined>;
};

const gatewayOrigin = resolveGatewayOrigin(process.env.GATEWAY_ORIGIN);

// Proxy administration routes, including status. The browser stays on this origin.
const statusProxy = {
  "/admin": {
    target: gatewayOrigin,
    changeOrigin: true,
    configure: configureStatusProxy,
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: statusProxy,
  },
  preview: {
    host: "127.0.0.1",
    port: 5173,
    proxy: statusProxy,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});

function resolveGatewayOrigin(raw: string | undefined): string {
  const configured = raw?.trim();
  if (!configured) {
    return "http://127.0.0.1:8000";
  }
  let url: URL;
  try {
    url = new URL(configured);
  } catch {
    throw new Error("GATEWAY_ORIGIN must be an absolute http or https URL.");
  }
  if (url.username || url.password) {
    throw new Error("GATEWAY_ORIGIN must not include credentials.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("GATEWAY_ORIGIN must be an absolute http or https URL.");
  }
  return url.origin;
}
