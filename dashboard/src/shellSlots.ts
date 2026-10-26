import { createContext, useContext } from "react";

/**
 * DOM nodes owned by the application shell. Panels render read-only summaries into
 * them with portals so the data stays in the panel that loaded it. A panel rendered
 * outside the shell sees null and keeps its inline layout.
 */
export type ShellSlots = {
  session: HTMLElement | null;
  discoverySummary: HTMLElement | null;
  auditSummary: HTMLElement | null;
};

const EMPTY: ShellSlots = { session: null, discoverySummary: null, auditSummary: null };

export const ShellSlotsContext = createContext<ShellSlots>(EMPTY);

export function useShellSlots(): ShellSlots {
  return useContext(ShellSlotsContext);
}
