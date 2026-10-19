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

test("a partial response shows a warning and keeps the tool activity", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      json(200, {
        partial: true,
        message: "Partial result: the chat provider stopped after a tool ran. This is not a completed answer.",
        activity: [{ tool: "utility__convert_units", outcome: "success" }],
      }),
    ),
  );

  render(<ChatPanel />);
  await user.type(screen.getByLabelText("Message"), "Convert 1 kg to g");
  await user.click(screen.getByRole("button", { name: "Send" }));
  const warning = await screen.findByRole("alert");
  expect(warning).toHaveTextContent("PARTIAL RESULT.");
  expect(warning).toHaveTextContent("The tool activity may have completed, but the assistant's answer is incomplete.");
  expect(
    screen.getByText("Partial result: the chat provider stopped after a tool ran. This is not a completed answer."),
  ).toBeInTheDocument();
  expect(screen.getByText("utility__convert_units: success")).toBeInTheDocument();
  expect(screen.getByText("Assistant")).toBeInTheDocument();
});

test("a normal answer does not show the partial warning", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => json(200, { message: "There is one order.", activity: [] })),
  );

  render(<ChatPanel />);
  await user.type(screen.getByLabelText("Message"), "Any orders?");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("There is one order.")).toBeInTheDocument();
  expect(screen.queryByText("PARTIAL RESULT.")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("partial false is shown as a normal answer", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      json(200, {
        partial: false,
        message: "Converted.",
        activity: [{ tool: "utility__convert_units", outcome: "success" }],
      }),
    ),
  );

  render(<ChatPanel />);
  await user.type(screen.getByLabelText("Message"), "Convert 1 kg to g");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("Converted.")).toBeInTheDocument();
  expect(screen.getByText("utility__convert_units: success")).toBeInTheDocument();
  expect(screen.queryByText("PARTIAL RESULT.")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
