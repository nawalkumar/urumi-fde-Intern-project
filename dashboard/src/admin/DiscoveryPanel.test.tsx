import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { DiscoveryPanel } from "./DiscoveryPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

test("discovery panel shows an empty summary", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      json(200, {
        candidate_count: 0,
        advertised_count: 0,
        excluded: { collision: 0, disabled_upstream: 0, listing_failed: 0 },
        collisions: [],
        upstreams: [],
        store_registrations: [],
        last: null,
      }),
    ),
  );
  render(<DiscoveryPanel />);
  expect(screen.getByText("Loading discovery…")).toBeInTheDocument();
  expect(await screen.findByText("No upstreams registered.")).toBeInTheDocument();
  expect(screen.getByText("No tool-name collisions.")).toBeInTheDocument();
  expect(screen.getByText(/Candidates 0/)).toBeInTheDocument();
});

test("discovery panel shows a collision and a search explanation", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/admin/discovery" && method === "GET") {
        return json(200, {
          candidate_count: 2,
          advertised_count: 1,
          excluded: { collision: 1, disabled_upstream: 0, listing_failed: 0 },
          collisions: [
            {
              category: "duplicate_public_name",
              tool_name: "dup__echo",
              upstream_ids: ["dup"],
              resolution: "excluded",
              affects: ["discovery", "dispatch"],
            },
          ],
          upstreams: [
            { id: "dup", enabled: true, listing_state: "listing_ok", advertised_tool_count: 0, excluded_tool_count: 1 },
          ],
          store_registrations: [{ upstream_id: "store_0123456789abcdef", enabled: true, registration: "not_canonical" }],
          last: { correlation_id: "corr-1", included: 1, error: "", at: "2026-10-20T00:00:00+00:00" },
        });
      }
      return json(200, {
        tools: [{ name: "utility__convert_units", description: "convert", score: 20, matched: ["name"] }],
        scope: "advertised",
      });
    }),
  );
  render(<DiscoveryPanel />);
  expect(await screen.findByText(/dup__echo excluded/)).toBeInTheDocument();
  expect(screen.getByText("collision: 1")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("not pinned");
  await user.type(screen.getByLabelText("Search advertised tools"), "convert");
  await user.click(screen.getByRole("button", { name: "Search tools" }));
  expect(await screen.findByText(/utility__convert_units score 20/)).toBeInTheDocument();
});

test("discovery panel shows an error without inventing tools", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(503, { error_description: "Authentication is unavailable." })));
  render(<DiscoveryPanel />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Authentication is unavailable.");
  expect(screen.queryByText(/score/)).not.toBeInTheDocument();
});
