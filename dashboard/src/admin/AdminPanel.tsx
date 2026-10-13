import { useEffect, useState, type FormEvent } from "react";
import { adminRequest, errorText } from "./adminClient";
import { ChatPanel } from "./ChatPanel";

type Role = "admin" | "member";

type SessionUser = {
  user_id: string;
  username: string;
  role: Role;
  disabled: boolean;
};

type UpstreamRow = {
  id: string;
  url: string;
  enabled: boolean;
  source: string;
  has_credential: boolean;
  listing_state: string | null;
  tools: string[];
};

type DiagnosticRow = {
  code: string;
  message: string;
  upstream_id: string | null;
  tool_name: string | null;
};

type MemberRow = SessionUser;

type KeyRow = {
  key_id: string;
  user_id: string | null;
  revoked: boolean;
  permissions: string[];
};

type AuditRow = {
  actor: string;
  user_id: string | null;
  upstream_id: string | null;
  tool_name: string;
  outcome: string;
  occurred_at: string;
  duration_ms: number | null;
  target?: string | null;
};

export function AdminPanel() {
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <section className="admin-panel" aria-label="Team administration">
        <h2>Team administration</h2>
        <button type="button" onClick={() => setOpen(true)}>
          Open team administration
        </button>
      </section>
    );
  }
  return <AdminSession />;
}

function AdminSession() {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void adminRequest("/admin/me", "GET").then((result) => {
      if (cancelled) {
        return;
      }
      setUser(result.status === 200 ? asUser(result.body) : null);
      setReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function logout() {
    await adminRequest("/admin/logout", "POST");
    setUser(null);
    setNotice(null);
  }

  return (
    <section className="admin-panel" aria-label="Team administration">
      <h2>Team administration</h2>
      {!ready ? <p>Checking session…</p> : null}
      {ready && user === null ? <LoginForm onSignedIn={setUser} /> : null}
      {user ? (
        <>
          <p>
            Signed in as {user.username} ({user.role}).
          </p>
          <button type="button" onClick={() => void logout()}>
            Sign out
          </button>
          {notice ? <p role="status">{notice}</p> : null}
          {user.role === "admin" ? <UpstreamManager onNotice={setNotice} /> : null}
          {user.role === "admin" ? <MemberManager onNotice={setNotice} /> : null}
          <ChatPanel />
          <KeyManager actor={user} onNotice={setNotice} />
          <AuditHistory />
        </>
      ) : null}
    </section>
  );
}

function LoginForm({ onSignedIn }: { onSignedIn: (user: SessionUser) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await adminRequest("/admin/login", "POST", { username, password });
    setPassword("");
    const signedIn = asUser(result.body);
    if (result.status !== 200 || signedIn === null) {
      setError(errorText(result.body));
      return;
    }
    setError(null);
    setUsername("");
    onSignedIn(signedIn);
  }

  return (
    <form onSubmit={(event) => void submit(event)}>
      <label>
        Username
        <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
      </label>
      <label>
        Password
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
        />
      </label>
      <button type="submit">Sign in</button>
      {error ? <p role="alert">{error}</p> : null}
    </form>
  );
}

function UpstreamManager({ onNotice }: { onNotice: (message: string) => void }) {
  const [rows, setRows] = useState<UpstreamRow[]>([]);
  const [diagnostics, setDiagnostics] = useState<DiagnosticRow[]>([]);
  const [id, setId] = useState("");
  const [url, setUrl] = useState("");
  const [credential, setCredential] = useState("");
  const [editUrl, setEditUrl] = useState<Record<string, string>>({});

  async function load() {
    const result = await adminRequest("/admin/upstreams", "GET");
    if (result.status === 200 && isRecord(result.body) && Array.isArray(result.body.upstreams)) {
      setRows(result.body.upstreams.filter(isUpstream));
      const reported = Array.isArray(result.body.diagnostics) ? result.body.diagnostics : [];
      setDiagnostics(reported.filter(isDiagnostic));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function add(event: FormEvent) {
    event.preventDefault();
    const result = await adminRequest("/admin/upstreams", "POST", {
      id,
      url,
      enabled: true,
      credential,
    });
    setCredential("");
    if (result.status !== 201) {
      onNotice(errorText(result.body));
      return;
    }
    setId("");
    setUrl("");
    onNotice("Upstream saved.");
    await load();
  }

  async function save(row: UpstreamRow, enabled: boolean) {
    const body: Record<string, unknown> = { enabled };
    if (row.source === "dynamic") {
      body.url = editUrl[row.id] ?? row.url;
    }
    const result = await adminRequest(`/admin/upstreams/${encodeURIComponent(row.id)}`, "POST", body);
    onNotice(result.status === 200 ? "Upstream updated." : errorText(result.body));
    await load();
  }

  async function remove(row: UpstreamRow) {
    const result = await adminRequest(`/admin/upstreams/${encodeURIComponent(row.id)}/delete`, "POST");
    onNotice(result.status === 200 ? "Upstream removed." : errorText(result.body));
    await load();
  }

  return (
    <section>
      <h3>Upstreams</h3>
      <button type="button" onClick={() => void load()}>
        Refresh upstreams
      </button>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Id</th>
            <th>URL</th>
            <th>Status</th>
            <th>Tools</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>
                {row.id} ({row.source})
              </td>
              <td>
                {row.source === "dynamic" ? (
                  <input
                    aria-label={`URL for ${row.id}`}
                    value={editUrl[row.id] ?? row.url}
                    onChange={(event) => setEditUrl({ ...editUrl, [row.id]: event.target.value })}
                  />
                ) : (
                  row.url
                )}
              </td>
              <td>
                {row.enabled ? "enabled" : "disabled"} / {row.listing_state ?? "unknown"}
                {row.has_credential ? " / credential stored" : ""}
              </td>
              <td>{row.tools.join(", ") || "none registered"}</td>
              <td>
                <button type="button" onClick={() => void save(row, !row.enabled)}>
                  {row.enabled ? `Disable ${row.id}` : `Enable ${row.id}`}
                </button>
                {row.source === "dynamic" ? (
                  <>
                    <button type="button" onClick={() => void save(row, row.enabled)}>
                      Save {row.id}
                    </button>
                    <button type="button" onClick={() => void remove(row)}>
                      Remove {row.id}
                    </button>
                  </>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <section aria-labelledby="upstream-diagnostics-heading">
        <h4 id="upstream-diagnostics-heading">Diagnostics</h4>
        {diagnostics.length === 0 ? (
          <p className="empty">No diagnostics reported.</p>
        ) : (
          <ul className="diagnostics">
            {diagnostics.map((item, index) => (
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
      <form onSubmit={(event) => void add(event)}>
        <h4>Add upstream</h4>
        <label>
          Upstream id
          <input value={id} onChange={(event) => setId(event.target.value)} />
        </label>
        <label>
          Upstream URL
          <input value={url} onChange={(event) => setUrl(event.target.value)} />
        </label>
        <label>
          Upstream credential
          <input
            type="password"
            value={credential}
            autoComplete="off"
            onChange={(event) => setCredential(event.target.value)}
          />
        </label>
        <button type="submit">Add upstream</button>
      </form>
    </section>
  );
}

function MemberManager({ onNotice }: { onNotice: (message: string) => void }) {
  const [rows, setRows] = useState<MemberRow[]>([]);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [initialPassword, setInitialPassword] = useState<string | null>(null);

  async function load() {
    const result = await adminRequest("/admin/users", "GET");
    if (result.status === 200 && isRecord(result.body) && Array.isArray(result.body.users)) {
      setRows(result.body.users.flatMap((item) => {
        const user = asUser(item);
        return user ? [user] : [];
      }));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    const result = await adminRequest("/admin/users", "POST", { username, role });
    if (result.status !== 201 || !isRecord(result.body) || typeof result.body.initial_password !== "string") {
      onNotice(errorText(result.body));
      return;
    }
    setInitialPassword(result.body.initial_password);
    setUsername("");
    await load();
  }

  async function setMemberRole(member: MemberRow, next: Role) {
    const result = await adminRequest(`/admin/users/${encodeURIComponent(member.user_id)}/role`, "POST", { role: next });
    onNotice(result.status === 200 ? "Role updated." : errorText(result.body));
    await load();
  }

  async function setDisabled(member: MemberRow, disabled: boolean) {
    const action = disabled ? "disable" : "enable";
    const result = await adminRequest(`/admin/users/${encodeURIComponent(member.user_id)}/${action}`, "POST");
    onNotice(result.status === 200 ? "Member updated." : errorText(result.body));
    await load();
  }

  return (
    <section>
      <h3>Members</h3>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Username</th>
            <th>Role</th>
            <th>State</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((member) => (
            <tr key={member.user_id}>
              <td>{member.username}</td>
              <td>{member.role}</td>
              <td>{member.disabled ? "disabled" : "active"}</td>
              <td>
                <button type="button" onClick={() => void setMemberRole(member, member.role === "admin" ? "member" : "admin")}>
                  Make {member.username} {member.role === "admin" ? "member" : "admin"}
                </button>
                <button type="button" onClick={() => void setDisabled(member, !member.disabled)}>
                  {member.disabled ? `Enable ${member.username}` : `Disable ${member.username}`}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {initialPassword ? (
        <div className="secret-once" role="status">
          <p>Initial password, shown once: {initialPassword}</p>
          <button type="button" onClick={() => setInitialPassword(null)}>
            Dismiss password
          </button>
        </div>
      ) : null}
      <form onSubmit={(event) => void create(event)}>
        <label>
          New username
          <input value={username} onChange={(event) => setUsername(event.target.value)} />
        </label>
        <label>
          Role
          <select value={role} onChange={(event) => setRole(event.target.value === "admin" ? "admin" : "member")}>
            <option value="member">member</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <button type="submit">Create member</button>
      </form>
    </section>
  );
}

type UserList =
  | { status: "loading" }
  | { status: "ready"; users: MemberRow[] }
  | { status: "empty" }
  | { status: "error"; message: string };

function KeyManager({ actor, onNotice }: { actor: SessionUser; onNotice: (message: string) => void }) {
  const [rows, setRows] = useState<KeyRow[]>([]);
  const [userList, setUserList] = useState<UserList>({ status: "loading" });
  const [userId, setUserId] = useState("");
  const [issued, setIssued] = useState<string | null>(null);
  const [grants, setGrants] = useState<Record<string, string>>({});
  const users = userList.status === "ready" ? userList.users : [];
  const selectedUser = users.some((user) => user.user_id === userId);

  async function load() {
    const result = await adminRequest("/admin/keys", "GET");
    if (result.status === 200 && isRecord(result.body) && Array.isArray(result.body.keys)) {
      setRows(result.body.keys.filter(isKey));
    }
  }

  async function loadUsers() {
    const result = await adminRequest("/admin/users", "GET");
    if (result.status === 200 && isRecord(result.body) && Array.isArray(result.body.users)) {
      const next = result.body.users.flatMap((item) => {
        const user = asUser(item);
        return user ? [user] : [];
      });
      setUserList(next.length === 0 ? { status: "empty" } : { status: "ready", users: next });
      return;
    }
    setUserList({ status: "error", message: errorText(result.body) });
  }

  useEffect(() => {
    void load();
    void loadUsers();
  }, []);

  async function issue(event: FormEvent) {
    event.preventDefault();
    if (!selectedUser) {
      return;
    }
    const result = await adminRequest("/admin/keys", "POST", { user_id: userId });
    if (result.status !== 201 || !isRecord(result.body) || typeof result.body.api_key !== "string") {
      onNotice(errorText(result.body));
      return;
    }
    setIssued(result.body.api_key);
    await load();
  }

  async function revoke(keyId: string) {
    const result = await adminRequest(`/admin/keys/${encodeURIComponent(keyId)}/revoke`, "POST");
    onNotice(result.status === 200 ? "Key revoked." : errorText(result.body));
    await load();
  }

  async function grant(keyId: string) {
    const toolName = grants[keyId] ?? "";
    const result = await adminRequest(`/admin/keys/${encodeURIComponent(keyId)}/permissions`, "POST", { tool_name: toolName });
    onNotice(result.status === 200 ? "Permission granted." : errorText(result.body));
    await load();
  }

  async function revokePermission(keyId: string, toolName: string) {
    const result = await adminRequest(`/admin/keys/${encodeURIComponent(keyId)}/permissions/revoke`, "POST", { tool_name: toolName });
    onNotice(result.status === 200 ? "Permission removed." : errorText(result.body));
    await load();
  }

  return (
    <section>
      <h3>API keys</h3>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Key id</th>
            <th>User</th>
            <th>Permissions</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key_id}>
              <td>
                {row.key_id}
                {row.revoked ? " (revoked)" : ""}
              </td>
              <td>{keyOwnerLabel(row, users)}</td>
              <td>
                {row.permissions.map((permission) => (
                  <span key={permission}>
                    {permission}{" "}
                    {actor.role === "admin" && !row.revoked ? (
                      <button type="button" onClick={() => void revokePermission(row.key_id, permission)}>
                        Remove {permission}
                      </button>
                    ) : null}
                  </span>
                ))}
              </td>
              <td>
                {row.revoked ? null : (
                  <button type="button" onClick={() => void revoke(row.key_id)}>
                    Revoke {row.key_id}
                  </button>
                )}
                {actor.role === "admin" && !row.revoked ? (
                  <>
                    <input
                      aria-label={`Permission for ${row.key_id}`}
                      value={grants[row.key_id] ?? ""}
                      onChange={(event) => setGrants({ ...grants, [row.key_id]: event.target.value })}
                    />
                    <button type="button" onClick={() => void grant(row.key_id)}>
                      Grant {row.key_id}
                    </button>
                  </>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {issued ? (
        <div className="secret-once" role="status">
          <p>API key, shown once: {issued}</p>
          <button type="button" onClick={() => setIssued(null)}>
            Dismiss API key
          </button>
        </div>
      ) : null}
      {actor.role === "admin" ? (
        <form onSubmit={(event) => void issue(event)}>
          <label htmlFor="key-owner">
            User for new key
            <select
              id="key-owner"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              disabled={userList.status !== "ready"}
            >
              <option value="">Select a user</option>
              {users.map((user) => (
                <option key={user.user_id} value={user.user_id}>
                  {user.username} ({user.role})
                </option>
              ))}
            </select>
          </label>
          {userList.status === "loading" ? <p>Loading users…</p> : null}
          {userList.status === "empty" ? <p>No users are available.</p> : null}
          {userList.status === "error" ? <p role="alert">{userList.message}</p> : null}
          <button type="submit" disabled={!selectedUser}>
            Issue API key
          </button>
        </form>
      ) : null}
    </section>
  );
}

function AuditHistory() {
  const [rows, setRows] = useState<AuditRow[]>([]);

  async function load() {
    const result = await adminRequest("/admin/audit", "GET");
    if (result.status === 200 && isRecord(result.body) && Array.isArray(result.body.entries)) {
      setRows(result.body.entries.filter(isAudit));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <section>
      <h3>Audit history</h3>
      <button type="button" onClick={() => void load()}>
        Refresh audit
      </button>
      <table className="admin-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Actor</th>
            <th>User</th>
            <th>Upstream</th>
            <th>Tool</th>
            <th>Target</th>
            <th>Duration</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.occurred_at}-${row.actor}-${index}`}>
              <td>{row.occurred_at}</td>
              <td>{row.actor}</td>
              <td>{row.user_id ?? ""}</td>
              <td>{row.upstream_id ?? ""}</td>
              <td>{row.tool_name}</td>
              <td>{row.target ?? ""}</td>
              <td>{row.duration_ms === null ? "" : `${row.duration_ms} ms`}</td>
              <td>{row.outcome}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function keyOwnerLabel(row: KeyRow, users: MemberRow[]): string {
  if (!row.user_id) {
    return "unassigned";
  }
  const match = users.find((user) => user.user_id === row.user_id);
  if (!match) {
    return `unknown user (${row.user_id})`;
  }
  return `${match.username} (${row.user_id})`;
}

function asUser(value: unknown): SessionUser | null {
  if (!isRecord(value)) {
    return null;
  }
  if (typeof value.user_id !== "string" || typeof value.username !== "string") {
    return null;
  }
  if (value.role !== "admin" && value.role !== "member") {
    return null;
  }
  if (typeof value.disabled !== "boolean") {
    return null;
  }
  return { user_id: value.user_id, username: value.username, role: value.role, disabled: value.disabled };
}

function isDiagnostic(value: unknown): value is DiagnosticRow {
  if (!isRecord(value) || typeof value.code !== "string" || value.code.length === 0) {
    return false;
  }
  if (typeof value.message !== "string") {
    return false;
  }
  if (value.upstream_id !== null && value.upstream_id !== undefined && typeof value.upstream_id !== "string") {
    return false;
  }
  if (value.tool_name !== null && value.tool_name !== undefined && typeof value.tool_name !== "string") {
    return false;
  }
  return true;
}

function isUpstream(value: unknown): value is UpstreamRow {
  if (!isRecord(value) || typeof value.id !== "string" || typeof value.url !== "string") {
    return false;
  }
  return typeof value.enabled === "boolean" && typeof value.source === "string" && Array.isArray(value.tools);
}

function isKey(value: unknown): value is KeyRow {
  if (!isRecord(value) || typeof value.key_id !== "string" || typeof value.revoked !== "boolean") {
    return false;
  }
  return Array.isArray(value.permissions);
}

function isAudit(value: unknown): value is AuditRow {
  return isRecord(value) && typeof value.actor === "string" && typeof value.outcome === "string" && typeof value.tool_name === "string";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
