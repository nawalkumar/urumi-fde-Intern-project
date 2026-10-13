import { parseStatusReport, type StatusReport } from "./status";

export type StatusErrorKind =
  | "unauthorized"
  | "forbidden"
  | "store_unavailable"
  | "network"
  | "malformed"
  | "schema"
  | "unexpected";

const MESSAGES: Record<StatusErrorKind, string> = {
  unauthorized: "The API key was rejected.",
  forbidden: "You do not have permission to view gateway status.",
  store_unavailable: "The gateway key store is unavailable.",
  network: "The gateway could not be reached.",
  malformed: "The status response was not valid JSON.",
  schema: "The status response did not match the expected report.",
  unexpected: "The gateway returned an unexpected response.",
};

export class StatusRequestError extends Error {
  readonly kind: StatusErrorKind;

  constructor(kind: StatusErrorKind) {
    super(MESSAGES[kind]);
    this.name = "StatusRequestError";
    this.kind = kind;
  }
}

export async function fetchStatus(apiKey: string, fetchImpl: typeof fetch = fetch): Promise<StatusReport> {
  let response: Response;
  try {
    response = await fetchImpl("/admin/status", {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      cache: "no-store",
      credentials: "same-origin",
    });
  } catch {
    throw new StatusRequestError("network");
  }

  if (response.status === 401) {
    throw new StatusRequestError("unauthorized");
  }
  if (response.status === 403) {
    throw new StatusRequestError("forbidden");
  }
  if (response.status === 502 || response.status === 504) {
    throw new StatusRequestError("network");
  }
  if (response.status === 503) {
    throw new StatusRequestError("store_unavailable");
  }
  if (!response.ok) {
    throw new StatusRequestError("unexpected");
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new StatusRequestError("malformed");
  }

  const report = parseStatusReport(body);
  if (report === null) {
    throw new StatusRequestError("schema");
  }
  return report;
}
