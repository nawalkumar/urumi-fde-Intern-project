import { useEffect, useState, type FormEvent } from "react";
import { adminRequest, errorText } from "./adminClient";

export type StoreState = "Provisioning" | "Ready" | "Failed" | "Deleting" | "Deleted";

export type StoreRow = {
  store_id: string;
  name: string;
  state: StoreState;
  url: string | null;
  created_at: string;
  progress: string;
  error_description: string | null;
  namespace: string;
  upstream_id: string;
  tool_names: string[];
};

const ACTIVE = new Set<StoreState>(["Provisioning", "Deleting"]);

export function StorePanel() {
  const [stores, setStores] = useState<StoreRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [name, setName] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [confirm, setConfirm] = useState<StoreRow | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load() {
    const result = await adminRequest("/admin/stores", "GET");
    if (result.status !== 200 || !isStoreList(result.body)) {
      setError(errorText(result.body));
      setStores(null);
      setLoading(false);
      return;
    }
    setError(null);
    setStores(result.body.stores);
    setLoading(false);
  }

  useEffect(() => {
    let cancelled = false;
    void load().then(() => {
      if (cancelled) {
        return;
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const active = Boolean(stores?.some((store) => ACTIVE.has(store.state)));
  useEffect(() => {
    if (!active) {
      return;
    }
    let ticks = 0;
    const timer = window.setInterval(() => {
      ticks += 1;
      if (ticks > 150) {
        window.clearInterval(timer);
        setNotice("Status refresh paused. Use Refresh stores to continue.");
        return;
      }
      void load();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [active]);

  async function create(event: FormEvent) {
    event.preventDefault();
    if (pending) {
      return;
    }
    setPending(true);
    setNotice(null);
    const result = await adminRequest("/admin/stores", "POST", { name, idempotency_key: idempotencyKey });
    setPending(false);
    if (result.status !== 200 && result.status !== 202) {
      setError(errorText(result.body));
      return;
    }
    setName("");
    setIdempotencyKey("");
    setError(null);
    await load();
  }

  async function remove(store: StoreRow) {
    if (pending) {
      return;
    }
    setPending(true);
    const result = await adminRequest(`/admin/stores/${store.store_id}/delete`, "POST");
    setPending(false);
    setConfirm(null);
    if (result.status !== 200 && result.status !== 202) {
      setError(errorText(result.body));
      return;
    }
    setError(null);
    await load();
  }

  async function retry(store: StoreRow) {
    if (pending) {
      return;
    }
    setPending(true);
    const result = await adminRequest(`/admin/stores/${store.store_id}/retry`, "POST");
    setPending(false);
    if (result.status !== 202) {
      setError(errorText(result.body));
      return;
    }
    setError(null);
    await load();
  }

  return (
    <section className="store-panel" aria-label="Stores">
      <h3>Stores</h3>
      {loading ? <p>Loading stores…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {stores && stores.length === 0 ? <p>No stores yet.</p> : null}
      {stores && stores.length > 0 ? (
        <table className="admin-table">
          <caption className="sr-only">Provisioned stores</caption>
          <thead>
            <tr>
              <th>Name</th>
              <th>Id</th>
              <th>Status</th>
              <th>URL</th>
              <th>Created</th>
              <th>Details</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {stores.map((store) => (
              <tr key={store.store_id}>
                <td>{store.name}</td>
                <td>{store.store_id}</td>
                <td>{store.state}</td>
                <td>{store.url ? <a href={store.url}>{store.url}</a> : "Not ready"}</td>
                <td>{store.created_at}</td>
                <td>
                  <span>{store.progress}</span>
                  {store.error_description ? <span>{store.error_description}</span> : null}
                </td>
                <td>
                  {store.state === "Failed" ? (
                    <button type="button" onClick={() => void retry(store)} disabled={pending}>
                      Retry {store.name}
                    </button>
                  ) : null}
                  <button type="button" onClick={() => setConfirm(store)} disabled={pending || store.state === "Deleting"}>
                    Delete {store.name}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <form aria-label="Create store" onSubmit={(event) => void create(event)}>
        <label>
          Store name
          <input
            aria-label="Store name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            maxLength={40}
          />
        </label>
        <label>
          Idempotency key
          <input
            aria-label="Idempotency key"
            value={idempotencyKey}
            onChange={(event) => setIdempotencyKey(event.target.value)}
            required
            minLength={8}
            maxLength={64}
          />
        </label>
        <button type="submit" disabled={pending}>
          Create store
        </button>
      </form>
      <button type="button" onClick={() => void load()}>
        Refresh stores
      </button>
      {confirm ? (
        <div role="dialog" aria-label={`Delete ${confirm.name}`}>
          <p>
            Delete {confirm.name} ({confirm.store_id})? This removes that store only.
          </p>
          <button type="button" onClick={() => setConfirm(null)} disabled={pending}>
            Cancel deletion
          </button>
          <button type="button" onClick={() => void remove(confirm)} disabled={pending}>
            Confirm delete {confirm.name}
          </button>
        </div>
      ) : null}
    </section>
  );
}

function isStoreList(body: unknown): body is { stores: StoreRow[] } {
  if (!body || typeof body !== "object" || !("stores" in body)) {
    return false;
  }
  return Array.isArray(body.stores);
}
