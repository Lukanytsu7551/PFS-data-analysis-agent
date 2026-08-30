import { appStore, state } from "./app-store.js";
import {
  appendMsg,
  bindBubbleImages,
  clearMessages,
  showStatus,
  updateTokenBar,
} from "../legacy/msg.js";
import { renderMd } from "../legacy/markdown.js";

const pfs = globalThis.PFS || {};

if (!pfs.dom || !pfs.slash) {
  throw new Error("Chat stream runtime dependencies are not ready");
}

export { appStore, state };
export { appendMsg, bindBubbleImages, clearMessages, renderMd, showStatus, updateTokenBar };
export const { $, esc, scrollBottom, scrollReset, hideWelcome, showWelcome } = pfs.dom;
export const clearCmd = pfs.slash.clearCmd;
