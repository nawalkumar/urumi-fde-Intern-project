import { describe, expect, test } from "vitest";

import {
  GATEWAY_UNAVAILABLE_BODY,
  STATUS_PROXY_TIMEOUT_MS,
  configureStatusProxy,
  proxyFailureStatus,
  redactProxyFailure,
  sendGatewayUnavailable,
  type ProxyFailure,
  type StatusProxyRequest,
  type StatusProxyResponse,
} from "./proxyError";

class FakeResponse implements StatusProxyResponse {
  headersSent = false;
  writableEnded = false;
  status = 0;
  headers: Record<string, string | number> = {};
  body = "";

  writeHead(status: number, headers: Record<string, string | number>): void {
    this.status = status;
    this.headers = headers;
    this.headersSent = true;
  }

  end(body?: string): void {
    this.body = body ?? "";
    this.writableEnded = true;
  }
}

function failure(code: string, message: string): ProxyFailure {
  const error = new Error(message) as ProxyFailure;
  error.code = code;
  error.address = "10.1.2.3";
  error.hostname = "gateway.internal";
  error.port = 8000;
  error.stack = `Error: ${message}\n    at secret`;
  return error;
}

describe("status proxy failures", () => {
  test("classifies connection and DNS failures as 502 and timeouts as 504", () => {
    expect(proxyFailureStatus({ code: "ECONNREFUSED" })).toBe(502);
    expect(proxyFailureStatus({ code: "ENOTFOUND" })).toBe(502);
    expect(proxyFailureStatus({ code: "EAI_AGAIN" })).toBe(502);
    expect(proxyFailureStatus({ code: "ETIMEDOUT" })).toBe(504);
    expect(proxyFailureStatus({ code: "ESOCKETTIMEDOUT" })).toBe(504);
    expect(proxyFailureStatus({})).toBe(502);
  });

  test("writes a fixed JSON body and removes target details from the error", () => {
    const response = new FakeResponse();
    const error = failure("ECONNREFUSED", "connect ECONNREFUSED 10.1.2.3:8000");

    sendGatewayUnavailable(response, error);
    redactProxyFailure(error);

    expect(response.status).toBe(502);
    expect(response.headers["Content-Type"]).toBe("application/json; charset=utf-8");
    expect(response.headers["Cache-Control"]).toBe("no-store");
    expect(JSON.parse(response.body)).toEqual(GATEWAY_UNAVAILABLE_BODY);
    expect(response.body).not.toContain("10.1.2.3");
    expect(response.body).not.toContain("gateway.internal");
    expect(response.body).not.toContain("ECONNREFUSED");
    expect(error.message).toBe("gateway proxy failure");
    expect(error.stack).toBeUndefined();
    expect(error.address).toBeUndefined();
    expect(error.hostname).toBeUndefined();
    expect(error.port).toBeUndefined();
  });

  test("does not write a second response", () => {
    const response = new FakeResponse();
    response.headersSent = true;
    sendGatewayUnavailable(response, { code: "ENOTFOUND" });
    expect(response.body).toBe("");
    expect(response.writableEnded).toBe(false);
  });

  test("destroys a stalled proxy request as a timeout", () => {
    let onError: ((error: ProxyFailure, req: unknown, res: StatusProxyResponse) => void) | undefined;
    let onProxyReq: ((proxyReq: StatusProxyRequest) => void) | undefined;
    const proxy = {
      on(event: "error" | "proxyReq", listener: (...args: never[]) => void) {
        if (event === "error") {
          onError = listener as typeof onError;
        } else {
          onProxyReq = listener as typeof onProxyReq;
        }
      },
    };
    configureStatusProxy(proxy);

    let timeoutMs = 0;
    let onTimeout: (() => void) | undefined;
    let destroyed: Error | undefined;
    const proxyReq: StatusProxyRequest = {
      setTimeout(ms, callback) {
        timeoutMs = ms;
        onTimeout = callback;
      },
      destroy(error) {
        destroyed = error;
      },
    };
    onProxyReq?.(proxyReq);
    expect(timeoutMs).toBe(STATUS_PROXY_TIMEOUT_MS);
    onTimeout?.();
    expect(destroyed).toMatchObject({ code: "ETIMEDOUT" });
    expect(destroyed?.message).not.toContain("127.0.0.1");

    const response = new FakeResponse();
    const error = destroyed as ProxyFailure;
    onError?.(error, { headers: { authorization: "Bearer secret-token" } }, response);
    expect(response.status).toBe(504);
    expect(response.body).not.toContain("secret-token");
    expect(response.body).not.toContain("Bearer");
    expect(error.message).toBe("gateway proxy failure");
  });
});
