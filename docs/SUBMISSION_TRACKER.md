# Submission tracker

Single tracker for the Urumi AI FDE Round 1 submission sprint. Status date: 2026-10-26. Branch `submission`, HEAD `b4b813c568ca70b45d28944a44de4e8e770f874d`, 94 files staged, nothing committed or pushed in this sprint.

## 1. Sprint goal

Within the next 2–3 hours, make the repository documentation consistent, verify safe local checks, prepare a truthful dashboard demo-video plan, and make GitHub submission materials reviewer-ready.

## 2. Status legend

| Status | Meaning |
| --- | --- |
| `DONE — verified` | A check was run or observed, and the evidence is named in the row. |
| `PARTIAL — some evidence exists` | Part of the requirement has evidence. The missing part is named. |
| `IN PROGRESS` | Being worked in this sprint. |
| `BLOCKED` | Cannot proceed without an approval, an environment change, or a dependency. |
| `NOT STARTED` | No work yet. |
| `NOT VERIFIED` | Implemented or written, with no run or observation behind it. |

Implementation alone never earns `DONE`. Evidence layers used below:

1. **Source** — implemented in this tree.
2. **Tests** — covered by automated tests that were run.
3. **Test cluster** — demonstrated on `kind-urumi-k8s-test` (Helm revisions 1–11, 2026-10-25 and 2026-10-26).
4. **Dev cluster (older)** — demonstrated only on `kind-urumi-k8s-dev` on 2026-10-18 or 2026-10-19, with images older than this tree (`docs/assignment-tracker.md`).
5. **Not demonstrated** — no live evidence.

### Evidence sources

| Short name | Location |
| --- | --- |
| Ledger | `docs/verification/multi-store-acceptance.md`, especially "Test-cluster live pass" and "Reconciliation on 2026-10-26" |
| Evidence JSON | `docs/verification/evidence/multi-store/*.json` |
| Dev tracker | `docs/assignment-tracker.md` Modules 1–12 (dev cluster, 2026-10-18 and 2026-10-19) |
| 2026-10-26 audit | Read-only audit of this tree and of `kind-urumi-k8s-test` on 2026-10-26, summarized in the ledger section "Reconciliation on 2026-10-26". Raw command output was not saved as an evidence file. |

## 3. Requirement tracker

| # | Workstream | Status | Evidence | What remains | Next concrete action | Fits this sprint? | Dependency or risk |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Documentation consistency | `IN PROGRESS` | This pass edited README, design, threat model, multi-store, chart README, dev tracker, ledger | Owner review of the unstaged doc diff | Read `git diff` for the seven doc files | Yes | Edits on staged files sit as unstaged changes until the owner stages them |
| 2 | Automated backend tests | `DONE — verified` | 2026-10-26 audit: `uv run pytest` without the two Docker-backed files, 327 passed; the two PostgreSQL files, 17 passed | CI has not run on this tree | Optional rerun after any code change (none planned) | Yes | CI run `38537165909` covers only `d7ddff1` |
| 3 | Dashboard tests and TypeScript | `DONE — verified` | 2026-10-26 audit: `npx vitest run`, 6 files, 34 passed; `npx tsc --noEmit` printed no errors | `npm run build` was not run (it writes `dist/`) | Optional: run `npm run build` before recording | Yes | — |
| 4 | Helm/chart validation | `DONE — verified` (render only) | 2026-10-26 audit: `helm lint` of defaults with placeholder passwords and of `values-vps.yaml`, 0 failed. `enabled` without `admit` and an empty probe CIDR both fail the render | Lint and render are not installs | None | Yes | — |
| 5 | Local deployment instructions | `PARTIAL — some evidence exists` | README "Local setup", "Docker Compose", "Kubernetes"; chart README | The test-cluster overlay is gitignored, so a reviewer cannot reproduce that cluster from the repo alone | Keep README test-cluster section; mention the overlay keys by name only | Yes | — |
| 6 | VPS / production-like deployment | `NOT VERIFIED` | `values-vps.yaml` is a worksheet of `replace-me` placeholders; it lints and renders | No VPS, registry, DNS, or TLS. Never installed | Outline is in `helm/urumi/README.md` "Local to VPS outline". Do not install it | Outline yes, deploy no | Needs a VPS and a registry |
| 7 | Persistent storage and data safety | `PARTIAL — some evidence exists` | Test cluster: seven volumes Bound, StorageClass `standard` reclaim `Delete` (2026-10-26 audit). Dev cluster (older): order 14 survived pod restarts | No backup procedure; `Delete` reclaim means deleting a claim deletes data | Keep the chart README warnings | Yes (docs) | Data loss on manual PVC delete |
| 8 | Gateway aggregation and upstream CRUD | `PARTIAL — some evidence exists` | Tests: `test_admin_api.py`, `test_upstream_controls.py`. Dev cluster (older): one key listed and called three upstreams. Test cluster: disable and enable audited 2026-10-26 (rows 61–69) | Add, edit, and remove were not shown on the current build | Show in demo only with approval (writes data) | Demo approval needed | — |
| 9 | Collision handling and diagnostics | `PARTIAL — some evidence exists` | Tests only: `test_shared_original_names_stay_distinct`, `test_duplicate_public_tool_names_are_excluded`, `test_discovery_policy.py` | No live collision | Show the tests and the admin "No diagnostics reported" view | Yes | No upstream in the cluster lists a tool twice |
| 10 | Dead-upstream isolation and timeout | `PARTIAL — some evidence exists` | Tests: `test_timeout_skips_slow_list_and_fails_slow_call`, `test_unavailable_upstream_is_isolated`. Dev cluster (older): Utility pod deleted, WooCommerce kept answering, Unreachable shown | Not repeated on the test cluster | P2: delete the Utility pod on test with approval | No | Pod delete is a cluster mutation |
| 11 | Roles, API keys, revocation, audit | `PARTIAL — some evidence exists` | Tests: `test_admin_api.py`, `test_gateway_auth.py`, `test_tool_permissions.py`. Test cluster: audit rows for key issue, grant, and revoke actions, and denied calls (ledger, `negative-permission-tests-*.json`). Dev cluster (older) only: member keys, and a revoked key rejected with 401 on the next call | A post-revocation authentication test and a member key flow on the current build; dashboard audit screen capture | Capture the audit screen during recording | Screen yes, member flow no | Issuing a key shows a raw key once; hide it |
| 12 | Logged-in-user chat tool loop | `PARTIAL — some evidence exists` | Tests: `test_chat.py`. Test cluster revision 9 image: rows 40–42 on 2026-10-25 12:15Z, two store `list_orders` calls plus `chat` (`gateway-multistore-verification-*.json`). Dev cluster (older): Groq created products 17 and 19 | **Tool calling on revision 11 is not demonstrated.** The only revision-11 chat (audit row 76) has no tool row. No ChatPanel screenshot or answer text exists | P2: one approved chat on revision 11 asking for today's orders in both stores | Run itself ~10 min, approval needed | Calls the Groq provider; egress is pinned to two `/32` addresses |
| 13 | Storefront checkout and order verification | `PARTIAL — some evidence exists` | Dev cluster (older): browser COD order 20; read from `wp_wc_orders` only. Test cluster: fee-only REST orders in Stores B and C (`synthetic-orders-*.json`), not a storefront checkout | WooCommerce admin screen was never opened. No checkout on the test cluster | P2: one storefront checkout on the test storefront with approval | No | Creates an order |
| 14 | Two-user acceptance flow | `PARTIAL — some evidence exists` (older deployment only) | Dev tracker Module 12, 2026-10-18 | **Not demonstrated on the current submission build.** Test cluster has one admin and one member with no keys | P2 run on the test cluster (2–3 h) | No | Creates users' keys, a product, an order, a pod restart |
| 15 | Multi-store provisioning, isolation, quotas, cleanup | `PARTIAL — some evidence exists` | Ledger test-cluster matrix, reconciled: 48 `PROVEN — TEST CLUSTER`, 23 `PARTIAL`, 14 `UNTESTED` of 85 | Cross-owner denial, concurrency, timeout, Retain sweep, restart during delete, chat answer text | None in this sprint | No | Creating stores uses ~576 MiB requests each |
| 16 | Dashboard source/image parity | `BLOCKED` | 2026-10-26 audit: running `urumi-dashboard:disposable-pkg-20261024` was created 2026-10-24 22:32 IST; staged dashboard files were modified 2026-10-26 14:18–16:30 | Rebuild and roll the dashboard image | P2: build from a clean commit, `kind load`, reviewed `helm upgrade` | No | Needs approval; Helm upgrade restarts the dashboard |
| 17 | Security caveats and threat model | `IN PROGRESS` | `docs/threat-model.md` updated this pass. Test cluster: denied calls with no upstream contact (revision 10), one admission dry-run denial | Owner review | Review the "Provisioned stores" and "Uncertainties" sections | Yes | — |
| 18 | Demo-video checklist and evidence | `IN PROGRESS` | Section 5 below. No recording or screenshot exists | Rehearsal and recording | Rehearse section 5 against the running dashboard | Rehearsal yes; recording is the owner's | Running dashboard is the older build |
| 19 | README and GitHub readiness | `IN PROGRESS` | README updated this pass; secret scan clean on 2026-10-26 (`scripts/scan_secrets.py`: no high-confidence matches) | Owner review, then staging | Section 6 checklist | Yes | README is not in the staged set |
| 20 | Final commit/push and Google Form | `NOT STARTED` | — | Commit, push, form | Owner approval, then section 6 last steps | Only with approval | Form: https://forms.gle/nftwgyNeenSDmiRb6 |

## 4. Two-to-three-hour priority list

Times are estimates. P0 plus P1 fit in about 2 h 30 min. P2 does not fit and needs separate approval.

### P0 — Must do now (about 1 h 30 min)

| Item | Estimate | Completion condition |
| --- | --- | --- |
| P0-1 Reconcile documentation contradictions | 45 min (done in this pass) | No file says provisioning was never enabled, the admission policy was never installed, or the chart was never upgraded, except in sections labelled as history |
| P0-2 Create this tracker | 20 min (done in this pass) | This file exists and every row names its evidence |
| P0-3 Safe local doc checks | 10 min | `git diff --check` exits 0; relative links in edited docs resolve; `scripts/scan_secrets.py` reports no high-confidence matches |
| P0-4 Owner reviews the doc diff | 20 min | Owner accepts or edits the seven files and this tracker |
| P0-5 Decide what to stage | 5 min | Owner stages the doc edits (and `README.md`) or rejects them. The agent does not stage |

### P1 — Do if time permits (about 1 h)

| Item | Estimate | Completion condition |
| --- | --- | --- |
| P1-1 Evidence inventory for the video | 15 min | A list of which evidence JSON files and audit rows will be shown on screen |
| P1-2 Read-only screen captures from the running dashboard | 20 min | Screenshots of the upstream list, store list, discovery panel, and audit history. No key, password, or token visible |
| P1-3 Rehearse section 5 once | 25 min | Every step either works or has its backup line ready |
| P1-4 Optional local rerun of tests on camera | 5 min | `uv run pytest -q` and `npx vitest run` shown passing |

### P2 — Requires separate approval or more time (does not fit this sprint unless noted)

| Item | Estimate | Why it needs approval |
| --- | --- | --- |
| P2-1 One revision-11 chat that calls both store `list_orders` tools, with screenshot of the activity list and answer | 10–15 min | Calls the model provider and writes audit rows. Highest value for the video |
| P2-2 Commit, rebuild all images from the clean commit, `kind load`, reviewed `helm upgrade` on the test cluster | 60 min | Commit, Docker, and cluster mutation. Fixes dashboard parity and makes image labels `dirty=false` |
| P2-3 Two-user definition-of-done flow on the test cluster | 2–3 h, does not fit | Creates keys, a product, an order, and restarts a pod |
| P2-4 Storefront checkout and WooCommerce admin order screen | 20 min | Creates an order |
| P2-5 Disable-upstream and dead-pod checks on the current build | 20 min | Mutates flags and deletes a pod |
| P2-6 Additional admission dry-runs (namespace delete outside `st-<id>`) | 15 min | Sends API requests as the provisioner service account |
| P2-7 `helm rollback` and roll-forward on the test cluster | 30 min | Restarts workloads |
| P2-8 VPS deployment | Half a day or more, does not fit | Needs a VPS, registry, DNS, TLS |
| P2-9 Push and Google Form submission | 10 min | Owner action only |

## 5. Dashboard demo-video preparation

### Labels

- `SAFE-NOW` — can be shown from the repository or from the running test-cluster dashboard without changing data. Signing in creates a session row; nothing else is written.
- `LOCAL-SETUP` — needs a local command or a fresh build that has not been tried in this sprint.
- `LIVE-APPROVAL` — writes data or touches the cluster. Needs separate approval before recording.
- `NOT-DEMONSTRATED` — no evidence that it works on the current build. Do not show it as working.

The running dashboard is `http://127.0.0.1:18081/` through a port-forward to `svc/urumi-dashboard` on `kind-urumi-k8s-test` (`dashboard-disposable-store-lifecycle-*.json`). It is the 2026-10-24 build and does not contain the staged dashboard changes. Say so on camera.

### Recording checklist

- [ ] Close terminals and files that show passwords, keys, `.env`, or `~/.cursor/mcp.json`.
- [ ] Do not run `cat` on any password file. Earlier evidence records a password in terminal scrollback; use a fresh terminal.
- [ ] Confirm the dashboard and gateway port-forwards are up (`127.0.0.1:18081`, `127.0.0.1:18082`).
- [ ] Have the ledger and the evidence JSON files open in the editor for the backup lines.
- [ ] If a key is issued on camera, blur or cut the one-time raw key.
- [ ] Record the date, the Helm revision, and the dashboard image tag in the video description.

### Ordered walkthrough

| Step | What to show | Label | Backup if it cannot be shown |
| --- | --- | --- | --- |
| 1 | Architecture: README diagram, repo layout, gateway as the only path to MCP servers | `SAFE-NOW` | — |
| 2 | Dashboard entry point and admin sign-in; Users list with roles (`acceptance-admin` admin, one member) | `SAFE-NOW` (view). Creating a user: `LIVE-APPROVAL` | Show `test_admin_api.py` role tests |
| 3 | Upstreams table with Connected / Unreachable / Disabled, tool names | `SAFE-NOW` (view). Toggling enable: `LIVE-APPROVAL`. Tools disappearing for a client: `NOT-DEMONSTRATED` on this build | Dev tracker Module 12 disable row and test audit rows 61–69 |
| 4 | Discovery panel and collision diagnostics | `SAFE-NOW` (old panel build). Staged panel: `LOCAL-SETUP`. A live collision: `NOT-DEMONSTRATED` | Run `uv run pytest -q tests/test_upstream_controls.py tests/test_discovery_policy.py` on camera |
| 5 | API keys: list key ids and grants; revoke | `SAFE-NOW` (view). Issue or revoke: `LIVE-APPROVAL` | `negative-permission-tests-*.json` and the revoke rows in the ledger |
| 6 | Chat: ask for today's orders in both stores; show the activity list and the audit rows | `LIVE-APPROVAL`. Not demonstrated on revision 11 | Show audit rows 40–42 from `gateway-multistore-verification-*.json` and say they are from the revision 9 image |
| 7 | Audit history: actor, user, upstream, tool, duration, outcome | `SAFE-NOW` | — |
| 8 | Stores: list with Ready, URL, id. Create and delete | List `SAFE-NOW`. Create/delete `LIVE-APPROVAL` | `dashboard-disposable-store-lifecycle-*.json` (dashboard create and delete on revision 10) and `store-isolation-*.json` for packet tests |
| 9 | Tests and Helm artifacts | `SAFE-NOW` locally: `uv run pytest -q`, `npx vitest run`, `helm lint`, `helm template` with placeholder passwords | — |
| 10 | Limitations: section 7 of this file | `SAFE-NOW` | — |

Showing the staged dashboard source without a rebuild is possible in principle with `cd dashboard && GATEWAY_ORIGIN=http://127.0.0.1:18082 npm run dev`, which proxies `/admin` to the port-forwarded test gateway. That path was not tried in this sprint. Label it `LOCAL-SETUP` and test it before recording.

### Narration outline

1. Problem: one authenticated MCP endpoint for a team, with per-user keys, exact grants, and audit.
2. Architecture: gateway, three upstreams, WooCommerce MCP over the store REST API, dashboard, PostgreSQL, Helm.
3. Security model: hashed keys, exact grants, denied calls never reach an upstream, upstream credentials sealed, chat runs as the signed-in user.
4. Live walkthrough (table above), stating the image and revision on screen.
5. Multi-store bonus: separate provisioner, admission policy, per-store namespaces and NetworkPolicies, evidence files.
6. Honest limits: what was shown only on the older dev cluster, what is not demonstrated, the VPS file is a template.

## 6. GitHub submission checklist

- [ ] README overview and architecture current (edited this pass; owner review pending).
- [ ] Safe local setup in README "Local setup" and "Docker Compose".
- [ ] Environment variables named, never valued. README "Environment variables" lists the names.
- [ ] Test commands and last results with dates: section 3 rows 2–4.
- [ ] Helm values explained: chart README "Values files" and "Local to VPS outline".
- [ ] Design and trade-offs: `docs/design.md`. Threat model: `docs/threat-model.md`.
- [ ] Multi-store limitations: `docs/multi-store.md` "Known gaps" and the ledger.
- [ ] Evidence location: `docs/verification/`. Demo-video plan: section 5. Video link: not recorded yet.
- [ ] Sensitive files excluded: `.env`, `helm/urumi/values-*.local.yaml`, and `values-local.yaml` are in `.gitignore`. Run `python3 scripts/scan_secrets.py` before committing.
- [ ] `PROJECT_GAP_TRACKER.md` stays untracked and out of the commit.
- [ ] Review staged versus unstaged: `git status --short`, `git diff --stat`, `git diff --cached --stat`. The doc edits from this sprint are unstaged.
- [ ] Pending approval: owner stages the doc edits and `README.md`.
- [ ] Pending approval: commit and push `submission`.
- [ ] Pending approval: submit the Google Form at https://forms.gle/nftwgyNeenSDmiRb6 with the repository URL and the video link.

## 7. Final exit criteria

**Complete and verified**

- Backend tests (327 plus 17), dashboard tests (34), TypeScript, Helm lint and render guards, run on 2026-10-26 against this tree.
- The running gateway, provisioner, Support, Utility, and WooCommerce MCP pods on the test cluster run Python source byte-identical to this tree (2026-10-26 audit, file hashes). The dashboard pod does not.
- On the test cluster: store create, Ready, automatic registration, exact grants, denied calls with no upstream contact, cross-store packet isolation, quota enforcement, store deletion with namespace and volume cleanup, an external MCP client (Cursor) calling two store upstreams.

**Implemented but not demonstrated on the current build**

- Chat tool calling on revision 11.
- Upstream add, edit, remove; collision exclusion; timeout and dead-pod isolation; member key flow; rejection of a revoked key; `update_order_status`; Helm rollback (no rollback execution is recorded in the saved evidence).
- Multi-store cross-owner denial, concurrency, provisioning timeout, Retain-volume sweep, restart during delete.

**Only supported by older deployment evidence (`kind-urumi-k8s-dev`, 2026-10-18)**

- Storefront COD checkout, chat-created products, the two-user definition-of-done flow, rejection of a revoked key on the next call, disable, dead-pod isolation, Ingress on `.local` hosts. (The test cluster records revoke actions in its audit log, but not a post-revocation authentication test.)

**Blocked or unsafe without approval**

- Dashboard image rebuild and rollout, any Helm upgrade or rollback, any key, order, product, store, or pod change, VPS deployment, commit, push, form submission.

**Single next action with the highest value**

Owner reviews and stages this sprint's documentation edits. Then, with approval, run one chat on revision 11 that asks for today's orders in both stores and capture the activity list, the answer, and the two audit rows (P2-1).
