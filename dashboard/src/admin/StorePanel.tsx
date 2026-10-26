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

const STATE_CLASS: Record<StoreState, string> = {
  Provisioning: "state state-pending",
  Ready: "state state-ok",
  Failed: "state state-failed",
  Deleting: "state state-pending",
  Deleted: "state state-disabled",
};

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
    <section id="stores" className="store-panel panel" aria-label="Stores">
      <div className="section-head">
        <h3>Stores</h3>
        <button type="button" className="btn-secondary btn-sm" onClick={() => void load()}>
          Refresh stores
        </button>
      </div>
      {loading ? <p>Loading stores…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {stores && stores.length === 0 ? <p className="empty">No stores yet.</p> : null}
      {stores && stores.length > 0 ? (
        <div className="table-wrap" role="region" aria-label="Stores table" tabIndex={0}>
          <table className="admin-table">
            <caption className="sr-only">Provisioned stores</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Id</th>
                <th scope="col">Status</th>
                <th scope="col">URL</th>
                <th scope="col">Created</th>
                <th scope="col">Details</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {stores.map((store) => (
                <tr key={store.store_id}>
                  <td>{store.name}</td>
                  <td className="mono">{store.store_id}</td>
                  <td>
                    <span className={STATE_CLASS[store.state] ?? "state state-unknown"}>{store.state}</span>
                  </td>
                  <td className="mono cell-wrap">{store.url ? <a href={store.url}>{store.url}</a> : "Not ready"}</td>
                  <td className="nowrap">{store.created_at}</td>
                  <td className="cell-wrap store-details">
                    <span>{store.progress}</span>
                    {store.error_description ? <span className="store-error">{store.error_description}</span> : null}
                  </td>
                  <td>
                    <div className="row-actions">
                      {store.state === "Failed" ? (
                        <button type="button" className="btn-secondary btn-sm" onClick={() => void retry(store)} disabled={pending}>
                          Retry {store.name}
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="btn-danger btn-sm"
                        onClick={() => setConfirm(store)}
                        disabled={pending || store.state === "Deleting"}
                      >
                        Delete {store.name}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <form className="inset-form" aria-label="Create store" onSubmit={(event) => void create(event)}>
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
      {confirm ? (
        <div role="dialog" aria-label={`Delete ${confirm.name}`} className="confirm-dialog">
          <p>
            Delete {confirm.name} ({confirm.store_id})? This removes that store only.
          </p>
          <div className="row-actions">
            <button type="button" className="btn-secondary" onClick={() => setConfirm(null)} disabled={pending}>
              Cancel deletion
            </button>
            <button type="button" className="btn-danger-solid" onClick={() => void remove(confirm)} disabled={pending}>
              Confirm delete {confirm.name}
            </button>
          </div>
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
