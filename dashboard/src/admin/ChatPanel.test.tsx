import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ChatPanel } from "./ChatPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("sending a message shows loading, then the answer and tool activity", async () => {
  const user = userEvent.setup();
  let release: (value: Response) => void = () => undefined;
  const pending = new Promise<Response>((resolve) => {
    release = resolve;
  });
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push(String(init?.body));
      expect(String(input)).toBe("/admin/chat");
      expect(init?.method).toBe("POST");
      expect(new Headers(init?.headers).get("X-Urumi-Request")).toBe("1");
      return pending;
    }),
  );

  render(<ChatPanel />);
  await user.type(screen.getByLabelText("Message"), "How many orders arrived today?");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(screen.getByRole("status")).toHaveTextContent("Sending…");
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  release(
    json(200, {
      message: "There is one order.",
      activity: [{ tool: "woocommerce__list_orders", outcome: "success" }],
    }),
  );
  expect(await screen.findByText("There is one order.")).toBeInTheDocument();
  expect(screen.getByText("woocommerce__list_orders: success")).toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(calls[0]).toContain("How many orders arrived today?");
  expect(window.localStorage.length).toBe(0);
});

test("a chat API error can be retried", async () => {
  const user = userEvent.setup();
  let attempts = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) {
        return json(504, { error_description: "The chat provider timed out." });
      }
      return json(200, { message: "Recovered.", activity: [] });
    }),
  );

  render(<ChatPanel />);
  await user.type(screen.getByLabelText("Message"), "List products");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The chat provider timed out.");
  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Recovered.")).toBeInTheDocument();
  expect(attempts).toBe(2);
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
