import { $ } from "./dom.js";
import { appStore, state } from "./app-store.js";

export { $ };
export { appStore, state };
export const api = () => globalThis.PFS.api;
export const namespace = () => globalThis.PFS;
