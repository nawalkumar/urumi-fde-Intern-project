import { useState, type FormEvent } from "react";
import { adminRequest, errorText } from "./adminClient";

type Activity = { tool: string; outcome: string };
type Turn = { role: "user" | "assistant"; content: string; activity: Activity[] };

const HISTORY_LIMIT = 12;

export function ChatPanel() {
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(history: Turn[]) {
    setPending(true);
    setError(null);
    const messages = history.slice(-HISTORY_LIMIT).map((turn) => ({
      role: turn.role,
      content: turn.content,
    }));
    const result = await adminRequest("/admin/chat", "POST", { messages });
    setPending(false);
    const body = result.body;
    if (result.status !== 200 || !isChatBody(body)) {
      setError(errorText(body));
      return;
    }
    const activity = Array.isArray(body.activity) ? body.activity.filter(isActivity) : [];
    const assistant: Turn = { role: "assistant", content: body.message, activity };
    setTurns([...history, assistant].slice(-HISTORY_LIMIT));
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || pending) {
      return;
    }
    const history = [...turns, { role: "user" as const, content: text, activity: [] }].slice(-HISTORY_LIMIT);
    setTurns(history);
    setDraft("");
    await send(history);
  }

  return (
    <section className="chat-panel" aria-label="Chat">
      <h3>Chat</h3>
      <p>The last {HISTORY_LIMIT} messages are sent. Conversations are not stored.</p>
      <ol>
        {turns.map((turn, index) => (
          <li key={`${turn.role}-${index}`}>
            <strong>{turn.role === "user" ? "You" : "Assistant"}</strong>
            <p>{turn.content}</p>
            {turn.activity.length > 0 ? (
              <ul>
                {turn.activity.map((item, activityIndex) => (
                  <li key={`${item.tool}-${activityIndex}`}>
                    {item.tool}: {item.outcome}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ol>
      {pending ? <p role="status">Sending…</p> : null}
      {error ? (
        <p role="alert">
          {error}{" "}
          <button type="button" onClick={() => void send(turns)} disabled={pending}>
            Retry
          </button>
        </p>
      ) : null}
      <form onSubmit={(event) => void onSubmit(event)}>
        <label>
          Message
          <textarea value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={2000} />
        </label>
        <button type="submit" disabled={pending || draft.trim().length === 0}>
          Send
        </button>
      </form>
    </section>
  );
}

function isChatBody(value: unknown): value is { message: string; activity?: unknown } {
  if (!value || typeof value !== "object" || !("message" in value)) {
    return false;
  }
  return typeof value.message === "string";
}

function isActivity(value: unknown): value is Activity {
  if (!value || typeof value !== "object") {
    return false;
  }
  const row = value as { tool?: unknown; outcome?: unknown };
  return typeof row.tool === "string" && typeof row.outcome === "string";
}
