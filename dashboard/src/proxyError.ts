/**
 * Turn a failed status-proxy connection into a fixed response.
 * Vite's default proxy handler answers these failures with an empty HTTP 500.
 * The body and the error object must not carry the target host or the client request.
 */

export const STATUS_PROXY_TIMEOUT_MS = 75_000;

export const GATEWAY_UNAVAILABLE_BODY = {
  error: "gateway_unavailable",
  error_description: "The gateway could not be reached.",
} as const;

const TIMEOUT_CODES = new Set(["ETIMEDOUT", "ESOCKETTIMEDOUT"]);

export interface ProxyFailure extends Error {
  code?: string;
  address?: string;
  hostname?: string;
  port?: number;
}

export interface StatusProxyResponse {
  headersSent: boolean;
  writableEnded: boolean;
  writeHead(status: number, headers: Record<string, string | number>): void;
  end(body?: string): void;
}

export interface StatusProxyRequest {
  setTimeout(timeoutMs: number, callback: () => void): void;
  destroy(error?: Error): void;
}

interface StatusProxy {
  on(event: "error", listener: (error: ProxyFailure, req: unknown, res: StatusProxyResponse) => void): void;
  on(event: "proxyReq", listener: (proxyReq: StatusProxyRequest) => void): void;
}

export function proxyFailureStatus(error: { code?: string }): 502 | 504 {
  if (error.code !== undefined && TIMEOUT_CODES.has(error.code)) {
    return 504;
  }
  return 502;
}

export function configureStatusProxy(proxy: StatusProxy): void {
  proxy.on("proxyReq", (proxyReq) => {
    proxyReq.setTimeout(STATUS_PROXY_TIMEOUT_MS, () => {
      proxyReq.destroy(timeoutFailure());
    });
  });
  proxy.on("error", (error, _req, response) => {
    sendGatewayUnavailable(response, error);
    redactProxyFailure(error);
  });
}

export function sendGatewayUnavailable(response: StatusProxyResponse | undefined, error: { code?: string }): void {
  if (response === undefined || response.headersSent || response.writableEnded) {
    return;
  }
  const payload = JSON.stringify(GATEWAY_UNAVAILABLE_BODY);
  response.writeHead(proxyFailureStatus(error), {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": payload.length,
    "Cache-Control": "no-store",
  });
  response.end(payload);
}

export function redactProxyFailure(error: ProxyFailure): void {
  error.message = "gateway proxy failure";
  error.stack = undefined;
  delete error.address;
  delete error.hostname;
  delete error.port;
}

function timeoutFailure(): ProxyFailure {
  const error = new Error("timeout") as ProxyFailure;
  error.code = "ETIMEDOUT";
  return error;
}
