import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { AdminPanel } from "./admin/AdminPanel";
import { fetchStatus, StatusRequestError } from "./api";
import { ShellSlotsContext, type ShellSlots } from "./shellSlots";
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

type NavItem = { label: string; targets: string[] };

const NAV_GROUPS: Array<{ id: string; label: string; items: NavItem[] }> = [
  {
    id: "nav-group-gateway",
    label: "Gateway",
    items: [
      { label: "Overview", targets: ["overview"] },
      { label: "Connect / Status", targets: ["status"] },
    ],
  },
  {
    id: "nav-group-administration",
    label: "Administration",
    items: [
      { label: "Stores", targets: ["stores", "team-administration"] },
      { label: "Upstreams", targets: ["upstreams", "status-upstreams", "team-administration"] },
      { label: "Tool Discovery", targets: ["discovery", "team-administration"] },
      { label: "Members", targets: ["members", "team-administration"] },
      { label: "Chat", targets: ["chat", "team-administration"] },
      { label: "API Keys", targets: ["api-keys", "team-administration"] },
      { label: "Audit History", targets: ["audit-history", "team-administration"] },
    ],
  },
];

const NAV_ITEMS = NAV_GROUPS.flatMap((group) => group.items);

const SPY_OFFSET_PX = 140;

function firstTarget(item: NavItem): HTMLElement | null {
  for (const id of item.targets) {
    const element = document.getElementById(id);
    if (element) {
      return element;
    }
  }
  return null;
}

export function App() {
  const apiKeyRef = useRef<string | null>(null);
  const inFlight = useRef(false);
  const generation = useRef(0);
  const [connected, setConnected] = useState(false);
  const [report, setReport] = useState<StatusReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSuccess, setLastSuccess] = useState<Date | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const [activeNav, setActiveNav] = useState(NAV_ITEMS[0].label);
  const navToggle = useRef<HTMLButtonElement>(null);
  const [sessionSlot, setSessionSlot] = useState<HTMLElement | null>(null);
  const [discoverySlot, setDiscoverySlot] = useState<HTMLElement | null>(null);
  const [auditSlot, setAuditSlot] = useState<HTMLElement | null>(null);
  const slots = useMemo<ShellSlots>(
    () => ({ session: sessionSlot, discoverySummary: discoverySlot, auditSummary: auditSlot }),
    [sessionSlot, discoverySlot, auditSlot],
  );

  useEffect(() => {
    let frame = 0;
    function update() {
      frame = 0;
      let current = NAV_ITEMS[0].label;
      let best = Number.NEGATIVE_INFINITY;
      for (const item of NAV_ITEMS) {
        const element = document.getElementById(item.targets[0]);
        if (!element) {
          continue;
        }
        const top = element.getBoundingClientRect().top;
        if (top <= SPY_OFFSET_PX && top > best) {
          best = top;
          current = item.label;
        }
      }
      setActiveNav(current);
    }
    function onScroll() {
      if (frame === 0) {
        frame = window.requestAnimationFrame(update);
      }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    update();
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame !== 0) {
        window.cancelAnimationFrame(frame);
      }
    };
  }, []);

  function navigate(event: MouseEvent<HTMLAnchorElement>, item: NavItem) {
    setNavOpen(false);
    const target = firstTarget(item);
    if (!target) {
      return;
    }
    event.preventDefault();
    setActiveNav(item.label);
    if (!target.hasAttribute("tabindex")) {
      target.setAttribute("tabindex", "-1");
    }
    target.focus({ preventScroll: true });
    if (typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ block: "start" });
    }
  }

  function closeNavOnEscape(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && navOpen) {
      setNavOpen(false);
      navToggle.current?.focus();
    }
  }

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
    <ShellSlotsContext.Provider value={slots}>
      <div className="app-shell">
        <header className="sidebar" data-nav-open={navOpen ? "true" : "false"} onKeyDown={closeNavOnEscape}>
          <div className="brand">
            <h1>Urumi MCP Gateway</h1>
            <p className="subtitle">Operational status dashboard</p>
          </div>
          <button
            ref={navToggle}
            type="button"
            className="btn-ghost nav-toggle"
            aria-expanded={navOpen}
            aria-controls="sidebar-panel"
            onClick={() => setNavOpen((open) => !open)}
          >
            Sections
          </button>
          <div id="sidebar-panel" className="sidebar-panel">
            <nav aria-label="Dashboard sections">
              {NAV_GROUPS.map((group) => (
                <div key={group.id} className="nav-group">
                  <p id={group.id} className="nav-group-label">
                    {group.label}
                  </p>
                  <ul aria-labelledby={group.id}>
                    {group.items.map((item) => (
                      <li key={item.label}>
                        <a
                          href={`#${item.targets[0]}`}
                          className="nav-link"
                          aria-current={activeNav === item.label ? "location" : undefined}
                          onClick={(event) => navigate(event, item)}
                        >
                          {item.label}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </nav>
            <p className="sidebar-meta">
              <span className={connected ? "dot dot-on" : "dot"} aria-hidden="true" />
              {connected ? "Status API key loaded for this tab" : "No status API key loaded"}
            </p>
          </div>
          <div ref={setSessionSlot} className="sidebar-session" />
        </header>
        <div className="app-main">
          <main>
            <SummarySection
              report={report}
              lastSuccess={lastSuccess}
              discoverySlotRef={setDiscoverySlot}
              auditSlotRef={setAuditSlot}
            />
            <div id="status" className="status-area">
              {connected ? (
                <div className="status-toolbar">
                  <p className="status-toolbar-label">Read-only status report</p>
                  <div className="header-actions">
                    <button type="button" onClick={() => void refresh()} disabled={loading}>
                      Refresh status
                    </button>
                    <button type="button" className="btn-secondary" onClick={disconnect}>
                      Disconnect
                    </button>
                  </div>
                </div>
              ) : null}
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
            </div>
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
      </div>
    </ShellSlotsContext.Provider>
  );
}

function SummarySection({
  report,
  lastSuccess,
  discoverySlotRef,
  auditSlotRef,
}: {
  report: StatusReport | null;
  lastSuccess: Date | null;
  discoverySlotRef: (node: HTMLElement | null) => void;
  auditSlotRef: (node: HTMLElement | null) => void;
}) {
  const overview = report ? overviewOf(report) : null;
  const connections = report ? report.upstreams.map(connectionLabel) : [];
  const count = (label: string) => connections.filter((item) => item === label).length;

  return (
    <section id="overview" className="summary" aria-labelledby="summary-heading">
      <h2 id="summary-heading">Gateway overview</h2>
      <p className="hint">
        {lastSuccess ? (
          <>
            Status figures come from the report loaded at{" "}
            <time dateTime={lastSuccess.toISOString()}>{formatRefreshTime(lastSuccess)}</time> and change only when it
            is refreshed.
          </>
        ) : (
          "Status figures appear after a status report loads."
        )}{" "}
        Discovery and audit figures come from the team administration panels and change when those panels refresh.
      </p>
      <ul className="summary-grid" aria-label="Gateway summary">
        <li className="summary-card">
          <p className="summary-label">Upstream connectivity</p>
          {report ? (
            <>
              <p className="summary-value">
                {count("Connected")} / {report.upstreams.length}
              </p>
              <p className="summary-detail">
                Connected. {count("Unreachable")} unreachable, {count("Disabled")} disabled.
              </p>
            </>
          ) : (
            <p className="summary-empty">Load a status report to see upstream connectivity.</p>
          )}
        </li>
        <li className="summary-card">
          <p className="summary-label">Advertised tools</p>
          {overview ? (
            <>
              <p className="summary-value">{formatCount(overview.advertisedTools)}</p>
              <p className="summary-detail">
                {overview.advertisedTools === null
                  ? "The total could not be confirmed from the report."
                  : "Total reported by the gateway."}
              </p>
            </>
          ) : (
            <p className="summary-empty">Load a status report to see the advertised tool total.</p>
          )}
        </li>
        <li className="summary-card">
          <p className="summary-label">Discovery collisions</p>
          <div ref={discoverySlotRef} className="summary-slot" />
          <p className="summary-empty summary-placeholder">
            Sign in to team administration as an administrator to load the discovery summary.
          </p>
        </li>
        <li className="summary-card">
          <p className="summary-label">Recent audit activity</p>
          <div ref={auditSlotRef} className="summary-slot" />
          <p className="summary-empty summary-placeholder">Sign in to team administration to load audit history.</p>
        </li>
      </ul>
    </section>
  );
}

function KeyForm({ onSubmit }: { onSubmit: (key: string) => void }) {
  const [draft, setDraft] = useState("");

  return (
    <form
      className="panel connect-card"
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
      <section id="status-upstreams" className="panel" aria-labelledby="upstreams-heading">
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
