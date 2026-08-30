import { ApiError, apiClient } from "./api-client.js";

export function installLegacyBridge(appName) {
  const pfs = globalThis.PFS || {};
  pfs.api = pfs.api || apiClient;
  pfs.ApiError = pfs.ApiError || ApiError;
  pfs.frontend = Object.freeze({
    ...(pfs.frontend || {}),
    build: "vite",
    entry: appName,
  });
  globalThis.PFS = pfs;
  return pfs;
}
