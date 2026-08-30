import { eventBus } from "./event-bus.js";

const pfs = globalThis.PFS || {};
const ui = pfs.ui || {};

pfs.ui = ui;
globalThis.PFS = pfs;

export const uiRegistry = ui;

export function registerUiIsland(name, api) {
  const previous = uiRegistry[name] || null;
  uiRegistry[name] = api || null;
  eventBus.emit(
    api ? "ui:registered" : "ui:unregistered",
    Object.freeze({
      name,
      api: uiRegistry[name],
      previous,
    }),
  );
  return uiRegistry[name];
}

export function getUiIsland(name) {
  return uiRegistry[name] || null;
}
