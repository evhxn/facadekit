import { create } from "zustand";

/**
 * One calibration scan per project at a time, across every button that can start one
 * ("Start scan", "Scan again", "Retry", "Re-scan and re-align"). Held for the whole job, also
 * when the step that started it is left and opened again. The server refuses a second scan
 * as well (CALIBRATION_BUSY); this keeps the second click from being sent at all.
 */
interface ScanGuardState {
  active: Record<string, true>;
}

const useGuard = create<ScanGuardState>(() => ({ active: {} }));

/** Claim the project's scan slot. False means a scan is already starting or running. */
export function beginScan(projectId: string): boolean {
  if (useGuard.getState().active[projectId]) return false;
  useGuard.setState((s) => ({ active: { ...s.active, [projectId]: true } }));
  return true;
}

export function endScan(projectId: string): void {
  useGuard.setState((s) => {
    const rest = { ...s.active };
    delete rest[projectId];
    return { active: rest };
  });
}

export function useScanBusy(projectId: string): boolean {
  return useGuard((s) => s.active[projectId] === true);
}

/** For tests. */
export function resetScanGuard(): void {
  useGuard.setState({ active: {} });
}
