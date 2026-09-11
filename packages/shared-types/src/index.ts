export type * from "./contracts.ts";

/** Tauri event emitted by the desktop core on every worker lifecycle change (src-tauri/src/lib.rs). */
export const WORKER_STATUS_EVENT = "worker://status";
