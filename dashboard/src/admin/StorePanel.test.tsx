import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { StorePanel, type StoreRow } from "./StorePanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test("shows an empty store list", async () => {
  stub([list([])]);
  render(<StorePanel />);
  expect(await screen.findByText("No stores yet.")).toBeInTheDocument();
});

test("shows a load error", async () => {
  stub([json(503, { error_description: "Store status is unavailable." })]);
  render(<StorePanel />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Store status is unavailable.");
});

test("creates a store and renders ready details without marking a failed store ready", async () => {
  const user = userEvent.setup();
  const calls: string[] = [];
  const responses = [
    list([]),
    json(202, store({ state: "Provisioning", url: null, error_description: null })),
    list([store({ state: "Provisioning", url: null, error_description: null })]),
    list([store({ state: "Ready", url: "http://0123456789abcdef.stores.local", error_description: null })]),
  ];
  stub(responses, calls);
  render(<StorePanel />);
  expect(await screen.findByText("No stores yet.")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Store name"), "North Shop");
  await user.type(screen.getByLabelText("Idempotency key"), "idem-north-1");
  await user.click(screen.getByRole("button", { name: "Create store" }));
  expect(await screen.findByText("Provisioning")).toBeInTheDocument();
  expect(screen.getByText("Not ready")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Refresh stores" }));
  expect(await screen.findByText("Ready")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "http://0123456789abcdef.stores.local" })).toBeInTheDocument();
  expect(calls.some((body) => body.includes("idem-north-1"))).toBe(true);
});

test("shows a validation error and ignores a second submit while the first is pending", async () => {
  const user = userEvent.setup();
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const bodies: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/admin/stores" && (init?.method ?? "GET") === "GET") {
        return json(200, { stores: [] });
      }
      bodies.push(typeof init?.body === "string" ? init.body : "");
      await gate;
      return json(400, { error_description: "The store name or idempotency key was rejected." });
    }),
  );
  render(<StorePanel />);
  expect(await screen.findByText("No stores yet.")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Store name"), "North Shop");
  await user.type(screen.getByLabelText("Idempotency key"), "idem-north-1");
  const button = screen.getByRole("button", { name: "Create store" });
  await user.click(button);
  expect(button).toBeDisabled();
  await user.click(button);
  release?.();
  expect(await screen.findByRole("alert")).toHaveTextContent("The store name or idempotency key was rejected.");
  expect(bodies).toHaveLength(1);
});

test("confirms deletion of the named store and renders a failure reason", async () => {
  const user = userEvent.setup();
  const calls: string[] = [];
  const failed = store({
    state: "Failed",
    url: null,
    error_description: "Provisioning timed out before the store became ready.",
  });
  stub(
    [
      list([failed]),
      json(202, { ...failed, state: "Deleting" }),
      list([]),
    ],
    calls,
  );
  render(<StorePanel />);
  expect(await screen.findByText("Provisioning timed out before the store became ready.")).toBeInTheDocument();
  expect(screen.queryByText("Ready")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Delete North Shop" }));
  expect(screen.getByRole("dialog", { name: "Delete North Shop" })).toHaveTextContent("0123456789abcdef");
  await user.click(screen.getByRole("button", { name: "Confirm delete North Shop" }));
  expect(await screen.findByText("No stores yet.")).toBeInTheDocument();
  expect(calls.some((body) => body.includes("/delete"))).toBe(true);
});

function store(overrides: Partial<StoreRow> = {}): StoreRow {
  return { ...baseStore(), ...overrides };
}

function baseStore(): StoreRow {
  return {
    store_id: "0123456789abcdef",
    name: "North Shop",
    state: "Provisioning",
    url: null,
    created_at: "2026-10-20T00:00:00+00:00",
    progress: "Waiting for the store workloads.",
    error_description: null,
    namespace: "st-0123456789abcdef",
    upstream_id: "store_0123456789abcdef",
    tool_names: [],
  };
}

function list(stores: unknown[]) {
  return json(200, { stores });
}

function stub(responses: Response[], calls?: string[]) {
  const queue = [...responses];
  let last = json(500, { error_description: "missing response" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls?.push(`${init?.method ?? "GET"} ${String(input)} ${typeof init?.body === "string" ? init.body : ""}`);
      if (queue.length > 0) {
        last = queue.shift() ?? last;
      }
      return last.clone();
    }),
  );
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
