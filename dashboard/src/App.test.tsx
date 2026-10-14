import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { App } from "./App";

const API_KEY = "urk_example.secret-value";

const healthy = {
  status: "ok",
  advertised_tool_count: 2,
  upstreams: [
    {
      id: "woocommerce",
      enabled: true,
      listing_state: "listing_ok",
      advertised_tool_count: 1,
      excluded_tool_count: 0,
    },
    {
      id: "utility",
      enabled: true,
      listing_state: "listing_ok",
      advertised_tool_count: 1,
      excluded_tool_count: 0,
    },
  ],
  diagnostics: [],
};

const mixed = {
  status: "ok",
  advertised_tool_count: 1,
  upstreams: [
    {
      id: "utility",
      enabled: true,
      listing_state: "listing_ok",
      advertised_tool_count: 1,
      excluded_tool_count: 0,
    },
    {
      id: "flaky",
      enabled: true,
      listing_state: "listing_failed",
      advertised_tool_count: null,
      excluded_tool_count: 0,
    },
    {
      id: "support",
      enabled: false,
      listing_state: "disabled",
      advertised_tool_count: 0,
      excluded_tool_count: 0,
    },
  ],
  diagnostics: [
    {
      code: "upstream_list_failed",
      upstream_id: "flaky",
      tool_name: null,
      message: "Upstream flaky was omitted from tool discovery.",
    },
    {
      code: "upstream_disabled",
      upstream_id: "support",
      tool_name: null,
      message: "Upstream support is disabled.",
    },
  ],
};

const unknownInventory = {
  status: "ok",
  advertised_tool_count: 0,
  upstreams: [
    {
      id: "utility",
      enabled: true,
      listing_state: "unknown",
      advertised_tool_count: null,
      excluded_tool_count: null,
    },
  ],
  diagnostics: [],
};

const collision = {
  status: "ok",
  advertised_tool_count: 1,
  upstreams: [
    {
      id: "dup",
      enabled: true,
      listing_state: "listing_ok",
      advertised_tool_count: 0,
      excluded_tool_count: 1,
    },
    {
      id: "other",
      enabled: true,
      listing_state: "listing_ok",
      advertised_tool_count: 1,
      excluded_tool_count: 0,
    },
  ],
  diagnostics: [
    {
      code: "tool_excluded",
      upstream_id: "dup",
      tool_name: "dup__echo",
      message: "Duplicate public tool name dup__echo. Conflicting registration excluded.",
    },
  ],
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  for (const call of fetchMock.mock.calls) {
    const url = String(call[0]);
    expect(url).toBe("/admin/status");
    expect(url).not.toContain(API_KEY);
    const init = call[1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${API_KEY}`);
    expect(init?.credentials).toBe("same-origin");
  }
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, { status });
}

async function load(body: unknown, status = 200) {
  fetchMock.mockResolvedValueOnce(jsonResponse(body, status));
  const user = userEvent.setup();
  render(<App />);
  await user.type(screen.getByLabelText("Gateway API key"), API_KEY);
  await user.click(screen.getByRole("button", { name: "Load status" }));
  return user;
}

function metric(label: string): string {
  const overview = screen.getByRole("list", { name: "Overview" });
  const item = within(overview).getByText(label).closest("li");
  if (!item) {
    throw new Error(`Missing overview item ${label}`);
  }
  return item.querySelector(".value")?.textContent ?? "";
}

describe("status dashboard", () => {
  test("renders a valid status response", async () => {
    await load(healthy);

    expect(screen.getByRole("heading", { name: "Urumi MCP Gateway" })).toBeInTheDocument();
    expect(screen.getByText("Operational status dashboard")).toBeInTheDocument();
    expect(screen.getByText(/The report was built/)).toBeInTheDocument();
    expect(metric("Configured upstreams")).toBe("2");
    expect(metric("Enabled")).toBe("2");
    expect(metric("Listing succeeded")).toBe("2");
    expect(metric("Listing failed")).toBe("0");
    expect(metric("Advertised tools")).toBe("2");

    const woo = screen.getByRole("article", { name: "woocommerce" });
    expect(woo).toHaveTextContent("Connection: Connected");
    expect(woo).toHaveTextContent("Enabled state: Enabled");
    expect(woo).toHaveTextContent("Listing state: Listing succeeded");
    expect(woo).toHaveTextContent("Advertised tools: 1");
    expect(woo).toHaveTextContent("Excluded tools: 0");
    expect(screen.getByText("No diagnostics reported.")).toBeInTheDocument();
    expect(screen.queryByText(/convert_units/)).not.toBeInTheDocument();
    expect(screen.getByText(/Last successful refresh:/)).toBeInTheDocument();
  });

  test("shows a disabled upstream separately from a listing failure", async () => {
    await load(mixed);

    const flaky = screen.getByRole("article", { name: "flaky" });
    const support = screen.getByRole("article", { name: "support" });

    expect(flaky).toHaveTextContent("Connection: Unreachable");
    expect(flaky).toHaveTextContent("Enabled state: Enabled");
    expect(flaky).toHaveTextContent("Listing state: Listing failed");
    expect(flaky).toHaveTextContent("Advertised tools: Unknown");
    expect(flaky).toHaveTextContent("Excluded tools: 0");
    expect(flaky).toHaveTextContent("Upstream flaky was omitted from tool discovery.");
    expect(flaky.querySelector(".state-failed")).not.toBeNull();

    expect(support).toHaveTextContent("Connection: Disabled");
    expect(support).toHaveTextContent("Enabled state: Disabled");
    expect(support).toHaveTextContent("Listing state: Disabled");
    expect(support).toHaveTextContent("Advertised tools: 0");
    expect(support).toHaveTextContent("Excluded tools: 0");
    expect(support).not.toHaveTextContent("Listing failed");
    expect(support).not.toHaveTextContent("omitted from tool discovery");
    expect(support.querySelector(".state-disabled")).not.toBeNull();

    expect(metric("Configured upstreams")).toBe("3");
    expect(metric("Enabled")).toBe("2");
    expect(metric("Listing succeeded")).toBe("1");
    expect(metric("Listing failed")).toBe("1");
    expect(metric("Advertised tools")).toBe("Unknown");
    expect(screen.getByText(/at least one upstream count is unavailable/)).toBeInTheDocument();
  });

  test("does not treat a null tool count as zero", async () => {
    await load(unknownInventory);

    const utility = screen.getByRole("article", { name: "utility" });
    expect(utility).toHaveTextContent("Listing state: Unknown");
    expect(utility).toHaveTextContent("Advertised tools: Unknown");
    expect(utility).toHaveTextContent("Excluded tools: Unknown");
    expect(utility).toHaveTextContent("No current tool inventory is available.");
    expect(metric("Advertised tools")).toBe("Unknown");
    expect(screen.queryByText("Advertised tools: 0")).not.toBeInTheDocument();
    expect(screen.getByText("No diagnostics reported.")).toBeInTheDocument();
  });

  test("renders diagnostic codes, upstreams, tool names, and the empty state", async () => {
    await load(collision);

    const diagnostics = screen.getByRole("heading", { name: "Diagnostics" }).closest("section");
    if (!diagnostics) {
      throw new Error("Diagnostics section is missing");
    }
    expect(diagnostics).toHaveTextContent("tool_excluded");
    expect(diagnostics).toHaveTextContent("Duplicate public tool name dup__echo. Conflicting registration excluded.");
    expect(diagnostics).toHaveTextContent("Upstream: dup");
    expect(diagnostics).toHaveTextContent("Tool: dup__echo");
    expect(metric("Advertised tools")).toBe("1");

    cleanup();
    fetchMock.mockResolvedValueOnce(jsonResponse(healthy));
    render(<App />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Gateway API key"), API_KEY);
    await user.click(screen.getByRole("button", { name: "Load status" }));
    expect(await screen.findByText("No diagnostics reported.")).toBeInTheDocument();
    expect(screen.queryByText("tool_excluded")).not.toBeInTheDocument();
  });

  test("treats HTTP 401 as an authentication problem", async () => {
    await load(
      {
        error: "invalid_token",
        error_description: "Authentication required.",
        debug: "raw-secret-body",
      },
      401,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("The API key was rejected.");
    expect(screen.getByText("No status report is available.")).toBeInTheDocument();
    expect(screen.queryByText("raw-secret-body")).not.toBeInTheDocument();
    expect(screen.queryByText("invalid_token")).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  test("maps HTTP 403 to a status permission message without the response body", async () => {
    const user = await load(
      {
        error: "forbidden",
        error_description: "Status access was denied.",
        detail: "Bearer secret-token gateway__read_status",
      },
      403,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have permission to view gateway status.");
    expect(screen.getByText("No status report is available.")).toBeInTheDocument();
    expect(screen.queryByText(/secret-token/)).not.toBeInTheDocument();
    expect(screen.queryByText(/gateway__read_status/)).not.toBeInTheDocument();
    expect(screen.queryByText("forbidden")).not.toBeInTheDocument();
    expect(screen.queryByText("The API key was rejected.")).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    fetchMock.mockResolvedValueOnce(jsonResponse(healthy));
    await user.type(screen.getByLabelText("Gateway API key"), API_KEY);
    await user.click(screen.getByRole("button", { name: "Load status" }));
    expect(await screen.findByRole("article", { name: "woocommerce" })).toBeInTheDocument();
    const refreshedAt = screen.getByText(/Last successful refresh:/).textContent;

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: "forbidden", authorization: "Bearer refresh-secret" }, 403),
    );
    await user.click(screen.getByRole("button", { name: "Refresh status" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The latest refresh failed. You do not have permission to view gateway status.",
    );
    expect(screen.queryByText(/refresh-secret/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Bearer/)).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: "woocommerce" })).toBeInTheDocument();
    expect(metric("Advertised tools")).toBe("2");
    expect(screen.getByText(/Last successful refresh:/).textContent).toBe(refreshedAt);
  });

  test("maps proxy connection and timeout failures to an unreachable message", async () => {
    const user = await load(
      {
        error: "gateway_unavailable",
        error_description: "connect ECONNREFUSED 10.0.0.9 leaked-upstream-body",
      },
      502,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("The gateway could not be reached.");
    expect(screen.getByText("No status report is available.")).toBeInTheDocument();
    expect(screen.queryByText(/leaked-upstream-body/)).not.toBeInTheDocument();
    expect(screen.queryByText(/10\.0\.0\.9/)).not.toBeInTheDocument();
    expect(screen.queryByText(/ECONNREFUSED/)).not.toBeInTheDocument();
    expect(screen.queryByText("The API key was rejected.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    fetchMock.mockResolvedValueOnce(jsonResponse(healthy));
    await user.type(screen.getByLabelText("Gateway API key"), API_KEY);
    await user.click(screen.getByRole("button", { name: "Load status" }));
    expect(await screen.findByRole("article", { name: "woocommerce" })).toBeInTheDocument();

    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "gateway_unavailable", token: "Bearer secret-token" }, 504));
    await user.click(screen.getByRole("button", { name: "Refresh status" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The latest refresh failed. The gateway could not be reached.",
    );
    expect(screen.queryByText(/secret-token/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Bearer/)).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: "woocommerce" })).toBeInTheDocument();
  });

  test("treats HTTP 503 as an unavailable key store", async () => {
    await load(
      {
        error: "unavailable",
        error_description: "Authentication is unavailable.",
        detail: "postgres password leaked",
      },
      503,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("The gateway key store is unavailable.");
    expect(screen.queryByText(/postgres password leaked/)).not.toBeInTheDocument();
    expect(screen.queryByText("error_description")).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  test("keeps the last snapshot when a refresh cannot reach the gateway", async () => {
    const user = await load(healthy);
    const refreshedAt = screen.getByText(/Last successful refresh:/).textContent;
    fetchMock.mockRejectedValueOnce(new Error("socket hang up"));

    await user.click(screen.getByRole("button", { name: "Refresh status" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The latest refresh failed. The gateway could not be reached.",
    );
    expect(screen.queryByText("socket hang up")).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: "woocommerce" })).toBeInTheDocument();
    expect(metric("Advertised tools")).toBe("2");
    expect(screen.getByText(/Last successful refresh:/).textContent).toBe(refreshedAt);
  });

  test("refreshes once at a time and shows the newer snapshot", async () => {
    const user = await load(healthy);
    let release: (response: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    fetchMock.mockImplementationOnce(() => pending);

    const refresh = screen.getByRole("button", { name: "Refresh status" });
    fireEvent.click(refresh);
    fireEvent.click(refresh);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(refresh).toBeDisabled();
    expect(screen.getByText("Refreshing status…")).toBeInTheDocument();

    const updated = {
      ...healthy,
      advertised_tool_count: 3,
      upstreams: [
        ...healthy.upstreams,
        {
          id: "support",
          enabled: true,
          listing_state: "listing_ok",
          advertised_tool_count: 1,
          excluded_tool_count: 0,
        },
      ],
    };
    release(jsonResponse(updated));

    expect(await screen.findByRole("article", { name: "support" })).toBeInTheDocument();
    expect(metric("Advertised tools")).toBe("3");
    expect(metric("Configured upstreams")).toBe("3");
    expect(screen.getByRole("button", { name: "Refresh status" })).toBeEnabled();

    const refreshAgain = screen.getByRole("button", { name: "Refresh status" });
    refreshAgain.focus();
    fetchMock.mockResolvedValueOnce(jsonResponse(healthy));
    await user.keyboard("{Enter}");
    await waitFor(() => expect(metric("Advertised tools")).toBe("2"));
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  test("does not persist or log the API key", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const consoleMethods = ["log", "info", "debug", "warn", "error"] as const;
    for (const method of consoleMethods) {
      vi.spyOn(console, method).mockImplementation(() => {});
    }
    const open = vi.fn();
    if (globalThis.indexedDB && typeof globalThis.indexedDB.open === "function") {
      vi.spyOn(globalThis.indexedDB, "open").mockImplementation(open as typeof globalThis.indexedDB.open);
    }

    const user = await load(healthy);
    expect(setItem).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
    expect(window.location.href).not.toContain(API_KEY);
    expect(open).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain(API_KEY);
    for (const method of consoleMethods) {
      for (const args of vi.mocked(console[method]).mock.calls) {
        const blob = JSON.stringify(args);
        expect(blob).not.toContain(API_KEY);
        expect(blob).not.toContain("Authorization");
        expect(blob).not.toContain("Bearer");
      }
    }

    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(screen.getByLabelText("Gateway API key")).toHaveValue("");
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
    expect(window.location.href).not.toContain(API_KEY);
  });

  test("ignores a response that arrives after credentials are cleared", async () => {
    let release: (response: Response) => void = () => {};
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Gateway API key"), API_KEY);
    await user.click(screen.getByRole("button", { name: "Load status" }));
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    release(jsonResponse(healthy));
    await screen.findByLabelText("Gateway API key");
    expect(screen.queryByRole("article", { name: "woocommerce" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Gateway API key")).toHaveValue("");
  });

  test("reports malformed JSON and an unexpected schema without showing the body", async () => {
    fetchMock.mockResolvedValueOnce(new Response("not-json {{{ secret-fragment", { status: 200 }));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Gateway API key"), API_KEY);
    await user.click(screen.getByRole("button", { name: "Load status" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The status response was not valid JSON.");
    expect(screen.queryByText(/secret-fragment/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "ok", leaked: "schema-secret" }));
    await user.type(screen.getByLabelText("Gateway API key"), API_KEY);
    await user.click(screen.getByRole("button", { name: "Load status" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The status response did not match the expected report.",
    );
    expect(screen.queryByText("schema-secret")).not.toBeInTheDocument();
  });
});
