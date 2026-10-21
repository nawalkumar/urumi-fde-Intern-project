import { useEffect, useState, type FormEvent } from "react";
import { adminRequest, errorText } from "./adminClient";

type UpstreamCount = {
  id: string;
  enabled: boolean;
  listing_state: string | null;
  advertised_tool_count: number | null;
  excluded_tool_count: number | null;
};

type CollisionRow = {
  category: string;
  tool_name: string;
  upstream_ids: string[];
  resolution: string;
  affects: string[];
};

type DiscoverySummary = {
  candidate_count: number;
  advertised_count: number;
  excluded: Record<string, number>;
  collisions: CollisionRow[];
  upstreams: UpstreamCount[];
  store_registrations: Array<{ upstream_id: string; enabled: boolean; registration: string }>;
  last: { correlation_id?: string; included?: number; error?: string; at?: string } | null;
};

type SearchHit = {
  name: string;
  description: string;
  score: number;
  matched: string[];
};

function isSummary(value: unknown): value is DiscoverySummary {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const row = value as DiscoverySummary;
  return typeof row.candidate_count === "number" && typeof row.advertised_count === "number" && Array.isArray(row.collisions);
}

export function DiscoveryPanel() {
  const [summary, setSummary] = useState<DiscoverySummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    const result = await adminRequest("/admin/discovery", "GET");
    if (result.status !== 200 || !isSummary(result.body)) {
      setSummary(null);
      setError(result.status === 200 ? "Discovery is unavailable." : errorText(result.body));
      setLoading(false);
      return;
    }
    setError(null);
    setSummary(result.body);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  async function search(event: FormEvent) {
    event.preventDefault();
    setSearchError(null);
    const result = await adminRequest("/admin/discovery/search", "POST", { query });
    if (result.status !== 200 || typeof result.body !== "object" || result.body === null || !Array.isArray((result.body as { tools?: unknown }).tools)) {
      setHits([]);
      setSearchError(errorText(result.body));
      return;
    }
    const tools = (result.body as { tools: SearchHit[] }).tools.filter(
      (item) => typeof item?.name === "string" && typeof item.score === "number",
    );
    setHits(tools);
  }

  return (
    <section className="discovery-panel" aria-label="Tool discovery">
      <h3>Tool discovery</h3>
      <p>Advertised names still need an exact grant before a client can list or call them. This view does not grant tools.</p>
      <button type="button" onClick={() => void load()}>
        Refresh discovery
      </button>
      {loading ? <p>Loading discovery…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {summary ? (
        <>
          <p>
            Candidates {summary.candidate_count}. Advertised {summary.advertised_count}.
            {summary.last?.at ? ` Last refresh ${summary.last.at}.` : " No discovery refresh recorded."}
          </p>
          <ul>
            {Object.entries(summary.excluded).map(([reason, count]) => (
              <li key={reason}>
                {reason}: {count}
              </li>
            ))}
          </ul>
          <table className="admin-table">
            <thead>
              <tr>
                <th>Upstream</th>
                <th>Status</th>
                <th>Advertised</th>
                <th>Excluded</th>
              </tr>
            </thead>
            <tbody>
              {summary.upstreams.length === 0 ? (
                <tr>
                  <td colSpan={4}>No upstreams registered.</td>
                </tr>
              ) : (
                summary.upstreams.map((row) => (
                  <tr key={row.id}>
                    <td>{row.id}</td>
                    <td>{row.enabled ? row.listing_state ?? "unknown" : "disabled"}</td>
                    <td>{row.advertised_tool_count ?? "—"}</td>
                    <td>{row.excluded_tool_count ?? "—"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
          {summary.collisions.length === 0 ? (
            <p>No tool-name collisions.</p>
          ) : (
            <ul>
              {summary.collisions.map((row) => (
                <li key={row.tool_name}>
                  {row.tool_name} excluded ({row.resolution}) across {row.upstream_ids.join(", ") || "unknown upstream"}
                </li>
              ))}
            </ul>
          )}
          {summary.store_registrations.some((row) => row.registration !== "canonical") ? (
            <p role="status">A store upstream is not pinned to its canonical endpoint.</p>
          ) : null}
        </>
      ) : null}
      <form onSubmit={(event) => void search(event)}>
        <label>
          Search advertised tools
          <input value={query} onChange={(event) => setQuery(event.target.value)} maxLength={200} />
        </label>
        <button type="submit">Search tools</button>
      </form>
      {searchError ? <p role="alert">{searchError}</p> : null}
      {hits && hits.length === 0 ? <p>No matching advertised tools.</p> : null}
      {hits && hits.length > 0 ? (
        <ul>
          {hits.map((hit) => (
            <li key={hit.name}>
              {hit.name} score {hit.score} ({hit.matched.join(", ")})
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
