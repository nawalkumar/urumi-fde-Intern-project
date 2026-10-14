export interface DiagnosticRecord {
  code: string;
  upstream_id: string | null;
  tool_name: string | null;
  message: string;
}

export interface UpstreamStatus {
  id: string;
  enabled: boolean;
  listing_state: string;
  advertised_tool_count: number | null;
  excluded_tool_count: number | null;
}

export interface StatusReport {
  status: string;
  advertised_tool_count: number | null;
  upstreams: UpstreamStatus[];
  diagnostics: DiagnosticRecord[];
}

export interface Overview {
  configured: number;
  enabled: number;
  listingSucceeded: number;
  listingFailed: number;
  advertisedTools: number | null;
  advertisedReason: "shown" | "missing" | "incomplete" | "mismatch";
}

const LISTING_LABELS: Record<string, string> = {
  listing_ok: "Listing succeeded",
  listing_failed: "Listing failed",
  disabled: "Disabled",
  unknown: "Unknown",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseCount(value: unknown): number | null | undefined {
  if (value === null) {
    return null;
  }
  if (typeof value === "number" && Number.isInteger(value) && value >= 0) {
    return value;
  }
  return undefined;
}

function parseOptionalText(value: unknown): string | null | undefined {
  if (value === null) {
    return null;
  }
  if (typeof value === "string") {
    return value;
  }
  return undefined;
}

export function parseStatusReport(value: unknown): StatusReport | null {
  if (!isRecord(value)) {
    return null;
  }
  if (typeof value.status !== "string" || value.status.length === 0) {
    return null;
  }
  const advertisedToolCount = parseCount(value.advertised_tool_count);
  if (advertisedToolCount === undefined) {
    return null;
  }
  if (!Array.isArray(value.upstreams) || !Array.isArray(value.diagnostics)) {
    return null;
  }

  const upstreams: UpstreamStatus[] = [];
  for (const item of value.upstreams) {
    if (!isRecord(item)) {
      return null;
    }
    if (typeof item.id !== "string" || item.id.length === 0) {
      return null;
    }
    if (typeof item.enabled !== "boolean") {
      return null;
    }
    if (typeof item.listing_state !== "string" || item.listing_state.length === 0) {
      return null;
    }
    const advertised = parseCount(item.advertised_tool_count);
    const excluded = parseCount(item.excluded_tool_count);
    if (advertised === undefined || excluded === undefined) {
      return null;
    }
    upstreams.push({
      id: item.id,
      enabled: item.enabled,
      listing_state: item.listing_state,
      advertised_tool_count: advertised,
      excluded_tool_count: excluded,
    });
  }

  const diagnostics: DiagnosticRecord[] = [];
  for (const item of value.diagnostics) {
    if (!isRecord(item)) {
      return null;
    }
    if (typeof item.code !== "string" || item.code.length === 0) {
      return null;
    }
    if (typeof item.message !== "string") {
      return null;
    }
    const upstreamId = parseOptionalText(item.upstream_id);
    const toolName = parseOptionalText(item.tool_name);
    if (upstreamId === undefined || toolName === undefined) {
      return null;
    }
    diagnostics.push({
      code: item.code,
      upstream_id: upstreamId,
      tool_name: toolName,
      message: item.message,
    });
  }

  return {
    status: value.status,
    advertised_tool_count: advertisedToolCount,
    upstreams,
    diagnostics,
  };
}

export function overviewOf(report: StatusReport): Overview {
  const advertised = interpretableAdvertisedTotal(report);
  return {
    configured: report.upstreams.length,
    enabled: report.upstreams.filter((upstream) => upstream.enabled).length,
    listingSucceeded: report.upstreams.filter((upstream) => upstream.listing_state === "listing_ok").length,
    listingFailed: report.upstreams.filter((upstream) => upstream.listing_state === "listing_failed").length,
    advertisedTools: advertised.count,
    advertisedReason: advertised.reason,
  };
}

function interpretableAdvertisedTotal(report: StatusReport): {
  count: number | null;
  reason: Overview["advertisedReason"];
} {
  if (report.advertised_tool_count === null) {
    return { count: null, reason: "missing" };
  }
  let sum = 0;
  for (const upstream of report.upstreams) {
    if (upstream.advertised_tool_count === null) {
      return { count: null, reason: "incomplete" };
    }
    sum += upstream.advertised_tool_count;
  }
  if (sum !== report.advertised_tool_count) {
    return { count: null, reason: "mismatch" };
  }
  return { count: report.advertised_tool_count, reason: "shown" };
}

export function formatCount(value: number | null): string {
  return value === null ? "Unknown" : String(value);
}

export function listingLabel(state: string): string {
  return LISTING_LABELS[state] ?? "Unrecognized";
}

export function connectionLabel(upstream: UpstreamStatus): "Connected" | "Unreachable" | "Disabled" {
  if (!upstream.enabled || upstream.listing_state === "disabled") {
    return "Disabled";
  }
  if (upstream.listing_state === "listing_ok") {
    return "Connected";
  }
  return "Unreachable";
}

export function listingClass(state: string): string {
  switch (state) {
    case "listing_ok":
      return "state state-ok";
    case "listing_failed":
      return "state state-failed";
    case "disabled":
      return "state state-disabled";
    default:
      return "state state-unknown";
  }
}

export function upstreamNotice(upstream: UpstreamStatus, diagnostics: DiagnosticRecord[]): string | null {
  if (upstream.listing_state === "listing_failed") {
    const match = diagnostics.find(
      (item) => item.code === "upstream_list_failed" && item.upstream_id === upstream.id && item.message.trim() !== "",
    );
    return match?.message ?? "The latest tool listing failed.";
  }
  if (upstream.listing_state === "unknown") {
    return "Listing status is unknown. No current tool inventory is available.";
  }
  if (upstream.listing_state !== "listing_ok" && upstream.listing_state !== "disabled") {
    return "Listing status is unrecognized.";
  }
  return null;
}

export function reportStatusText(status: string): string {
  if (status === "ok") {
    return "Report status: ok. The report was built. This does not mean every upstream is healthy.";
  }
  return `Report status: ${status}.`;
}

export function advertisedTotalHint(reason: Overview["advertisedReason"]): string | null {
  switch (reason) {
    case "missing":
      return "Advertised tool total was not reported. A missing count is not zero.";
    case "incomplete":
      return "Advertised tool total is unknown because at least one upstream count is unavailable. Unavailable counts are not treated as zero.";
    case "mismatch":
      return "Advertised tool total is unknown because the reported counts do not agree.";
    default:
      return null;
  }
}

export function formatRefreshTime(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
