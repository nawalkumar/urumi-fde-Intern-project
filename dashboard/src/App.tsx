import { useRef, useState } from "react";
import { AdminPanel } from "./admin/AdminPanel";
import { fetchStatus, StatusRequestError } from "./api";
import {
  advertisedTotalHint,
  connectionLabel,
  formatCount,
  formatRefreshTime,
  listingClass,
  listingLabel,
  overviewOf,
  reportStatusText,
  upstreamNotice,
  type DiagnosticRecord,
  type StatusReport,
  type UpstreamStatus,
} from "./status";

export function App() {
  const apiKeyRef = useRef<string | null>(null);
  const inFlight = useRef(false);
  const generation = useRef(0);
  const [connected, setConnected] = useState(false);
  const [report, setReport] = useState<StatusReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSuccess, setLastSuccess] = useState<Date | null>(null);

  async function refresh() {
    const key = apiKeyRef.current;
    if (!key || inFlight.current) {
      return;
    }
    inFlight.current = true;
    const generationAtStart = generation.current;
    setLoading(true);
    setError(null);
    try {
      const next = await fetchStatus(key);
      if (generation.current !== generationAtStart) {
        return;
      }
      setReport(next);
      setLastSuccess(new Date());
      setError(null);
    } catch (caught) {
      if (generation.current !== generationAtStart) {
        return;
      }
      const message = caught instanceof StatusRequestError ? caught.message : "The status report could not be loaded.";
      setError(message);
    } finally {
      if (generation.current === generationAtStart) {
        inFlight.current = false;
        setLoading(false);
      }
    }
  }

  function connect(key: string) {
    apiKeyRef.current = key;
    setConnected(true);
    void refresh();
  }

  function disconnect() {
    generation.current += 1;
    inFlight.current = false;
    apiKeyRef.current = null;
    setConnected(false);
    setReport(null);
    setError(null);
    setLastSuccess(null);
    setLoading(false);
  }

  const visibleError = error === null ? null : report ? `The latest refresh failed. ${error}` : error;

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1>Urumi MCP Gateway</h1>
          <p className="subtitle">Operational status dashboard</p>
        </div>
        {connected ? (
          <div className="header-actions">
            <button type="button" onClick={() => void refresh()} disabled={loading}>
              Refresh status
            </button>
            <button type="button" onClick={disconnect}>
              Disconnect
            </button>
          </div>
        ) : null}
      </header>
      <main>
        {connected ? (
          <Dashboard
            report={report}
            loading={loading}
            error={visibleError}
            lastSuccess={lastSuccess}
          />
        ) : (
          <KeyForm onSubmit={connect} />
        )}
        <AdminPanel />
      </main>
      <footer>
        <p>
          The API key stays in memory for this tab. It is sent only to this origin in the Authorization header.
          Production deployments should use dedicated dashboard authorization. Any live gateway API key can read
          status today; the gateway does not check tool grants for this report.
        </p>
      </footer>
    </div>
  );
}

function KeyForm({ onSubmit }: { onSubmit: (key: string) => void }) {
  const [draft, setDraft] = useState("");

  return (
    <form
      className="panel"
      onSubmit={(event) => {
        event.preventDefault();
        const key = draft.trim();
        if (!key) {
          return;
        }
        onSubmit(key);
      }}
    >
      <h2>Connect</h2>
      <p id="key-handling-note" className="hint">
        Paste a live gateway API key to load the read-only status report. This page cannot enable or disable
        upstreams. The key is not saved in browser storage.
      </p>
      <label htmlFor="gateway-api-key">Gateway API key</label>
      <input
        id="gateway-api-key"
        type="password"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        autoComplete="off"
        spellCheck={false}
        autoCapitalize="off"
        required
        aria-describedby="key-handling-note"
      />
      <button type="submit">Load status</button>
    </form>
  );
}

function Dashboard({
  report,
  loading,
  error,
  lastSuccess,
}: {
  report: StatusReport | null;
  loading: boolean;
  error: string | null;
  lastSuccess: Date | null;
}) {
  return (
    <div aria-busy={loading}>
      {loading ? <p role="status">{report ? "Refreshing status…" : "Loading status…"}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {lastSuccess ? (
        <p>
          Last successful refresh: <time dateTime={lastSuccess.toISOString()}>{formatRefreshTime(lastSuccess)}</time>
        </p>
      ) : null}
      {report ? <ReportView report={report} /> : loading ? null : <p className="empty">No status report is available.</p>}
    </div>
  );
}

function ReportView({ report }: { report: StatusReport }) {
  const overview = overviewOf(report);
  const totalHint = advertisedTotalHint(overview.advertisedReason);

  return (
    <>
      <section className="panel" aria-labelledby="overview-heading">
        <h2 id="overview-heading">Overview</h2>
        <p>{reportStatusText(report.status)}</p>
        <p className="hint">
          Listing succeeded means the latest tool list completed. It does not mean the upstream service is fully
          healthy. This page cannot enable or disable upstreams.
        </p>
        <ul className="overview" aria-label="Overview">
          <OverviewItem label="Configured upstreams" value={String(overview.configured)} />
          <OverviewItem label="Enabled" value={String(overview.enabled)} />
          <OverviewItem label="Listing succeeded" value={String(overview.listingSucceeded)} />
          <OverviewItem label="Listing failed" value={String(overview.listingFailed)} />
          <OverviewItem label="Advertised tools" value={formatCount(overview.advertisedTools)} />
        </ul>
        {totalHint ? <p className="hint">{totalHint}</p> : null}
      </section>
      <section className="panel" aria-labelledby="upstreams-heading">
        <h2 id="upstreams-heading">Upstreams</h2>
        {report.upstreams.length === 0 ? (
          <p className="empty">No upstreams are configured.</p>
        ) : (
          <div className="cards">
            {report.upstreams.map((upstream, index) => (
              <UpstreamCard key={`${upstream.id}-${index}`} upstream={upstream} diagnostics={report.diagnostics} index={index} />
            ))}
          </div>
        )}
      </section>
      <section className="panel" aria-labelledby="diagnostics-heading">
        <h2 id="diagnostics-heading">Diagnostics</h2>
        {report.diagnostics.length === 0 ? (
          <p className="empty">No diagnostics reported.</p>
        ) : (
          <ul className="diagnostics">
            {report.diagnostics.map((item, index) => (
              <li key={`${item.code}-${item.upstream_id ?? ""}-${item.tool_name ?? ""}-${index}`} className="diagnostic">
                <p className="diagnostic-code">{item.code}</p>
                <p>{item.message}</p>
                {item.upstream_id ? <p>Upstream: {item.upstream_id}</p> : null}
                {item.tool_name ? <p>Tool: {item.tool_name}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function OverviewItem({ label, value }: { label: string; value: string }) {
  return (
    <li>
      <span className="label">{label}</span>
      <span className="value">{value}</span>
    </li>
  );
}

function UpstreamCard({
  upstream,
  diagnostics,
  index,
}: {
  upstream: UpstreamStatus;
  diagnostics: DiagnosticRecord[];
  index: number;
}) {
  const headingId = `upstream-${index}`;
  const notice = upstreamNotice(upstream, diagnostics);

  return (
    <article className="card" aria-labelledby={headingId}>
      <h3 id={headingId}>{upstream.id}</h3>
      <p>
        Connection: <strong>{connectionLabel(upstream)}</strong>
      </p>
      <p>
        Enabled state: <strong>{upstream.enabled ? "Enabled" : "Disabled"}</strong>
      </p>
      <p>
        Listing state: <span className={listingClass(upstream.listing_state)}>{listingLabel(upstream.listing_state)}</span>
      </p>
      <p>Advertised tools: {formatCount(upstream.advertised_tool_count)}</p>
      <p>Excluded tools: {formatCount(upstream.excluded_tool_count)}</p>
      {notice ? <p className="card-note">{notice}</p> : null}
    </article>
  );
}
