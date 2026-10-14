import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { AdminPanel } from "./AdminPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("administration controls call the gateway and do not store secrets", async () => {
  const user = userEvent.setup();
  const calls: Array<{ url: string; method: string; body: string | null; csrf: string | null }> = [];
  const issued = "urk_once.secret-shown-once";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? init.body : null;
      const headers = new Headers(init?.headers);
      calls.push({ url, method, body, csrf: headers.get("X-Urumi-Request") });
      if (url === "/admin/me") {
        return json(401, { error: "invalid_token" });
      }
      if (url === "/admin/login") {
        return json(200, { user_id: "abc", username: "ada", role: "admin", disabled: false });
      }
      if (url === "/admin/upstreams" && method === "GET") {
        return json(200, { upstreams: [] });
      }
      if (url === "/admin/users" && method === "GET") {
        return json(200, {
          users: [
            { user_id: "abc", username: "ada", role: "admin", disabled: false },
            { user_id: "def", username: "bea", role: "member", disabled: false },
          ],
        });
      }
      if (url === "/admin/users" && method === "POST") {
        return json(201, {
          user_id: "def",
          username: "bea",
          role: "member",
          disabled: false,
          initial_password: "shown-once",
        });
      }
      if (url === "/admin/keys" && method === "GET") {
        return json(200, {
          keys: [
            { key_id: "0011223344556677", user_id: "def", revoked: false, permissions: ["support__search_notes"] },
            { key_id: "aabbccddeeff0011", user_id: null, revoked: false, permissions: [] },
            { key_id: "ffeeddccbbaa9988", user_id: "missinguser000000", revoked: false, permissions: [] },
          ],
        });
      }
      if (url === "/admin/keys" && method === "POST") {
        return json(201, { key_id: "0011223344556677", api_key: issued, user_id: "abc" });
      }
      if (url === "/admin/audit") {
        return json(200, {
          entries: [
            {
              actor: "0011223344556677",
              user_id: "abc",
              upstream_id: "utility",
              tool_name: "utility__convert_units",
              outcome: "success",
              occurred_at: "2026-10-11T00:00:00+00:00",
              duration_ms: 12,
            },
          ],
        });
      }
      return json(400, { error_description: "The request was rejected." });
    }),
  );

  render(<AdminPanel />);
  await user.click(screen.getByRole("button", { name: "Open team administration" }));
  expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  await user.type(screen.getByLabelText("Username"), "ada");
  await user.type(screen.getByLabelText("Password"), "temporary");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText(/Signed in as ada/)).toBeInTheDocument();
  await user.type(screen.getByLabelText("New username"), "bea");
  await user.click(screen.getByRole("button", { name: "Create member" }));
  expect(await screen.findByText(/Initial password, shown once: shown-once/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Dismiss password" }));
  expect(screen.queryByText(/shown-once/)).not.toBeInTheDocument();
  const owner = await screen.findByLabelText("User for new key");
  expect(owner).toHaveValue("");
  expect(screen.getByRole("option", { name: "ada (admin)" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "bea (member)" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Issue API key" })).toBeDisabled();
  expect(screen.getByText("bea (def)")).toBeInTheDocument();
  expect(screen.getByText("unassigned")).toBeInTheDocument();
  expect(screen.getByText("unknown user (missinguser000000)")).toBeInTheDocument();
  await user.selectOptions(owner, "def");
  await user.click(screen.getByRole("button", { name: "Issue API key" }));
  expect(await screen.findByText(`API key, shown once: ${issued}`)).toBeInTheDocument();
  expect(await screen.findByText("utility__convert_units")).toBeInTheDocument();
  expect(calls.every((call) => call.csrf === "1")).toBe(true);
  expect(calls.some((call) => call.url === "/admin/keys" && call.method === "POST" && call.body === JSON.stringify({ user_id: "def" }))).toBe(true);
  expect(calls.some((call) => call.body?.includes("abc") && call.url === "/admin/keys" && call.method === "POST")).toBe(false);
  expect(window.localStorage.length).toBe(0);
  expect(window.sessionStorage.length).toBe(0);
});

test("an empty or failed user list does not issue a key for the administrator", async () => {
  const user = userEvent.setup();
  for (const mode of ["empty", "error"] as const) {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url === "/admin/keys" && method === "POST") {
          calls.push(typeof init?.body === "string" ? init.body : "");
        }
        if (url === "/admin/me") {
          return json(401, { error: "invalid_token" });
        }
        if (url === "/admin/login") {
          return json(200, { user_id: "abc", username: "ada", role: "admin", disabled: false });
        }
        if (url === "/admin/users" && method === "GET") {
          if (mode === "empty") {
            return json(200, { users: [] });
          }
          return json(503, { error_description: "The request was rejected." });
        }
        if (url === "/admin/keys" && method === "GET") {
          return json(200, { keys: [{ key_id: "0011223344556677", user_id: "abc", revoked: false, permissions: [] }] });
        }
        if (url === "/admin/upstreams" && method === "GET") {
          return json(200, { upstreams: [] });
        }
        if (url === "/admin/audit") {
          return json(200, { entries: [] });
        }
        return json(400, { error_description: "The request was rejected." });
      }),
    );
    render(<AdminPanel />);
    await user.click(screen.getByRole("button", { name: "Open team administration" }));
    await user.type(screen.getByLabelText("Username"), "ada");
    await user.type(screen.getByLabelText("Password"), "temporary");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/Signed in as ada/)).toBeInTheDocument();
    expect(await screen.findByLabelText("User for new key")).toHaveValue("");
    expect(screen.queryByRole("option", { name: "ada (admin)" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Issue API key" })).toBeDisabled();
    expect(screen.getByText("unknown user (abc)")).toBeInTheDocument();
    expect(calls).toEqual([]);
    cleanup();
    vi.unstubAllGlobals();
  }
});

test("upstream diagnostics show a collision and an empty state", async () => {
  const user = userEvent.setup();
  const responses = [
    {
      upstreams: [{ id: "dup", url: "https://mcp.example.test/mcp", enabled: true, source: "dynamic", has_credential: false, listing_state: "listing_ok", tools: [] }],
      diagnostics: [
        {
          code: "tool_excluded",
          upstream_id: "dup",
          tool_name: "dup__echo",
          message: "Duplicate public tool name dup__echo. Conflicting registration excluded.",
        },
      ],
    },
    { upstreams: [], diagnostics: [] },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/admin/me") {
        return json(401, { error: "invalid_token" });
      }
      if (url === "/admin/login") {
        return json(200, { user_id: "abc", username: "ada", role: "admin", disabled: false });
      }
      if (url === "/admin/upstreams" && method === "GET") {
        return json(200, responses.shift() ?? { upstreams: [], diagnostics: [] });
      }
      if (url === "/admin/users" && method === "GET") {
        return json(200, { users: [] });
      }
      if (url === "/admin/keys" && method === "GET") {
        return json(200, { keys: [] });
      }
      if (url === "/admin/audit") {
        return json(200, { entries: [] });
      }
      return json(400, { error_description: "The request was rejected." });
    }),
  );

  render(<AdminPanel />);
  await user.click(screen.getByRole("button", { name: "Open team administration" }));
  await user.type(screen.getByLabelText("Username"), "ada");
  await user.type(screen.getByLabelText("Password"), "temporary");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  const diagnostics = await screen.findByRole("heading", { name: "Diagnostics" });
  const section = diagnostics.closest("section");
  expect(section).toHaveTextContent("tool_excluded");
  expect(section).toHaveTextContent("Duplicate public tool name dup__echo. Conflicting registration excluded.");
  expect(section).toHaveTextContent("Upstream: dup");
  expect(section).toHaveTextContent("Tool: dup__echo");
  expect(screen.getByRole("button", { name: "Disable dup" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Refresh upstreams" }));
  expect(await screen.findByText("No diagnostics reported.")).toBeInTheDocument();
  expect(screen.queryByText("tool_excluded")).not.toBeInTheDocument();
});

test("a member can issue only their own API key", async () => {
  const user = userEvent.setup();
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/admin/keys" && method === "POST") {
        calls.push(typeof init?.body === "string" ? init.body : "");
        return json(201, { key_id: "ffeeddccbbaa9988", api_key: "urk_member.shown-once", user_id: "def" });
      }
      if (url === "/admin/me") {
        return json(401, { error: "invalid_token" });
      }
      if (url === "/admin/login") {
        return json(200, { user_id: "def", username: "bea", role: "member", disabled: false });
      }
      if (url === "/admin/keys" && method === "GET") {
        return json(200, { keys: [] });
      }
      if (url === "/admin/audit") {
        return json(200, { entries: [] });
      }
      return json(403, { error_description: "The request was rejected." });
    }),
  );

  render(<AdminPanel />);
  await user.click(screen.getByRole("button", { name: "Open team administration" }));
  await user.type(screen.getByLabelText("Username"), "bea");
  await user.type(screen.getByLabelText("Password"), "temporary");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText(/Signed in as bea \(member\)/)).toBeInTheDocument();
  expect(screen.queryByLabelText("User for new key")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Create member" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Issue my API key" }));
  expect(await screen.findByText("API key, shown once: urk_member.shown-once")).toBeInTheDocument();
  expect(calls).toEqual([JSON.stringify({ user_id: "def" })]);
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
