import { $, state } from "../core/runtime.js";
import * as datasource from "../legacy/datasource.js";

function text(key, fallback) {
  const value = window.t?.(key);
  return value && value !== key ? value : fallback;
}

// ── Active panel singleton ─────────────────────────────────────────
// Only one of sb-panel-skills / sb-panel-knowledge / sb-panel-mcp can be
// open at any time. Tracked here so openPanel / closePanel can close
// the currently open one first without needing an argument.
let _activePanel = null; // "skills" | "knowledge" | "mcp" | null
let _activeSurface = null; // "drawer" | "skills" | "knowledge" | "mcp" | null
let _panelOpenRequest = { id: 0, name: null };
const _surfaceTriggers = new Map();
const SIDEBAR_RAIL_STORAGE_KEY = "pfs.sidebar.collapsed";
const SIDE_SURFACE_SELECTOR = ".pfs-side-surface";
let _sidebarRailPreference = false;

function rememberSurfaceTrigger(surface, trigger) {
  if (surface && trigger) _surfaceTriggers.set(surface, trigger);
}

function restoreSurfaceFocus(surface) {
  const trigger = _surfaceTriggers.get(surface);
  _surfaceTriggers.delete(surface);
  if (!trigger) return;
  if (!trigger.isConnected || trigger.disabled) return;
  if (trigger.closest("[hidden], [inert], .hidden")) return;
  trigger.focus({ preventScroll: true });
}

// Some Chromium surfaces can leave a CSS transition pending at currentTime 0
// when a dynamically mounted panel changes visibility. Commit the state
// synchronously so a panel can never remain visually hidden after opening.
function setSurfaceCollapsed(el, collapsed) {
  if (!el) return;
  if (collapsed && el.contains(document.activeElement)) document.activeElement.blur();
  if (typeof el.getAnimations === "function") {
    el.getAnimations().forEach((animation) => animation.cancel());
  }
  el.style.transition = "none";
  el.classList.toggle("collapsed", collapsed);
  el.classList.toggle("is-active", !collapsed);
  el.setAttribute("aria-hidden", String(collapsed));
  if (typeof el.toggleAttribute === "function") el.toggleAttribute("inert", collapsed);
  void el.offsetWidth;
  el.style.transition = "";
}

function isSurfaceOpen(el) {
  return Boolean(
    el
      && !el.classList.contains("collapsed")
      && !el.classList.contains("hidden")
      && el.getAttribute("aria-hidden") !== "true",
  );
}

function closeCompetingSurfaces(keep = null) {
  document.querySelectorAll(SIDE_SURFACE_SELECTOR).forEach((surface) => {
    if (surface !== keep) {
      setSurfaceCollapsed(surface, true);
      if (surface.dataset.sideSurface) _surfaceTriggers.delete(surface.dataset.sideSurface);
    }
  });
}

function readSidebarRailPreference() {
  try {
    return window.localStorage?.getItem(SIDEBAR_RAIL_STORAGE_KEY) === "1";
  } catch (_) {
    return false;
  }
}

function writeSidebarRailPreference(collapsed) {
  try {
    window.localStorage?.setItem(SIDEBAR_RAIL_STORAGE_KEY, collapsed ? "1" : "0");
  } catch (_) {
    // Private browsing or a restricted embedded context may deny storage.
  }
}

function cancelSidebarRailMotion() {
  document.querySelectorAll(".layout, #app-sidebar, #app-sidebar .sb-main, #sidebar-rail-toggle")
    .forEach((el) => {
      if (typeof el.getAnimations === "function") {
        el.getAnimations().forEach((animation) => animation.cancel());
      }
    });
}

function isCompactSidebarViewport() {
  return !!window.matchMedia?.("(max-width: 960px)").matches;
}

function setSidebarRailCollapsed(collapsed, { persist = true } = {}) {
  const sidebarEl = document.getElementById("app-sidebar");
  const layout = document.querySelector(".layout");
  const toggle = document.getElementById("sidebar-rail-toggle");
  if (!sidebarEl || !document.body) return false;

  const next = Boolean(collapsed) && !isCompactSidebarViewport();
  cancelSidebarRailMotion();
  const transitionTargets = [layout, sidebarEl].filter(Boolean);
  transitionTargets.forEach((el) => { el.style.transition = "none"; });
  document.body.classList.toggle("pfs-sidebar-rail-collapsed", next);
  sidebarEl.classList.toggle("sidebar-rail-collapsed", next);
  if (toggle) {
    toggle.setAttribute("aria-expanded", String(!next));
    const label = next ? "展开侧栏" : "收起侧栏";
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
  }
  void (layout?.offsetWidth || sidebarEl.offsetWidth);
  transitionTargets.forEach((el) => { el.style.transition = ""; });
  if (persist) {
    _sidebarRailPreference = next;
    writeSidebarRailPreference(next);
  }
  return next;
}

function toggleSidebarRail() {
  if (isCompactSidebarViewport()) return false;
  // Secondary surfaces must not become detached from the new rail edge.
  closeSidebarSurfaces({ restoreFocus: false });
  closeAddSrcDropdown();
  closeKbInlineForm();
  const sidebarEl = document.getElementById("app-sidebar");
  const collapsed = document.body.classList.contains("pfs-sidebar-rail-collapsed")
    || sidebarEl?.classList.contains("sidebar-rail-collapsed");
  return setSidebarRailCollapsed(!collapsed);
}

function initSidebarRail() {
  const sidebarEl = document.getElementById("app-sidebar");
  if (!sidebarEl) return false;
  if (globalThis.__pfsSidebarRailRegistered) return true;
  globalThis.__pfsSidebarRailRegistered = true;
  _sidebarRailPreference = readSidebarRailPreference();
  setSidebarRailCollapsed(_sidebarRailPreference, { persist: false });

  const media = window.matchMedia?.("(max-width: 960px)");
  if (media) {
    const syncViewport = () => setSidebarRailCollapsed(_sidebarRailPreference, { persist: false });
    if (typeof media.addEventListener === "function") media.addEventListener("change", syncViewport);
    else if (typeof media.addListener === "function") media.addListener(syncViewport);
  }
  return true;
}

function invalidatePanelOpenRequest() {
  _panelOpenRequest = { id: _panelOpenRequest.id + 1, name: null };
}

function beginPanelOpen(name) {
  _panelOpenRequest = { id: _panelOpenRequest.id + 1, name };
  return _panelOpenRequest.id;
}

function isPanelOpenRequestCurrent(name, id) {
  return _panelOpenRequest.id === id && _panelOpenRequest.name === name;
}

function syncMobileSurfaceState() {
  const backdrop = document.getElementById("pfs-mobile-nav-backdrop");
  const openSurfaces = Array.from(document.querySelectorAll(SIDE_SURFACE_SELECTOR))
    .filter(isSurfaceOpen);
  const preferred = openSurfaces.find(
    (surface) => surface.dataset.sideSurface === _activeSurface,
  ) || openSurfaces[0] || null;
  openSurfaces.forEach((surface) => {
    if (surface !== preferred) setSurfaceCollapsed(surface, true);
  });
  _activeSurface = preferred?.dataset.sideSurface || null;
  const open = Boolean(preferred);
  document.body.classList.toggle("pfs-nav-surface-open", open);
  if (backdrop) {
    if (open) backdrop.removeAttribute("inert");
    else if (backdrop.contains(document.activeElement)) document.activeElement.blur();
    backdrop.classList.toggle("is-active", open);
    backdrop.setAttribute("aria-hidden", String(!open));
    if (!open) backdrop.setAttribute("inert", "");
  }
}

// ── Sidebar nav highlight ──────────────────────────────────────────
function setSidebarNav(nav = "agent") {
  if (nav === "agent") {
    invalidatePanelOpenRequest();

    const drawer = $("sb-drawer");
    if (drawer && !drawer.classList.contains("collapsed")) {
      setSurfaceCollapsed(drawer, true);
      _surfaceTriggers.delete("drawer");
      if (_activeSurface === "drawer") _activeSurface = null;
    }

    if (_activePanel) {
      _closePanel(_activePanel, { restoreNav: false, restoreFocus: false });
    }
  }

  document.querySelectorAll(".sb-nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.sidebarNav === nav);
  });
}

// ── Open / close independent panel (skills / knowledge / mcp) ─────

function openPanel(name, trigger = null) {
  const el = document.getElementById(`sb-panel-${name}`);
  if (!el) return;
  rememberSurfaceTrigger(name, trigger);

  // Clicking the same panel toggles it closed, otherwise it becomes the only
  // active top-level surface beside the rail.
  if (_activePanel === name && isSurfaceOpen(el)) {
    _closePanel(name);
    return;
  }

  const drawer = $("sb-drawer");
  if (drawer && !drawer.classList.contains("collapsed")) {
    setSurfaceCollapsed(drawer, true);
    _surfaceTriggers.delete("drawer");
    if (_activeSurface === "drawer") _activeSurface = null;
  }
  // Close the current panel if it is a different one.
  if (_activePanel && _activePanel !== name) {
    _closePanel(_activePanel, { restoreNav: false, restoreFocus: false });
  }
  closeCompetingSurfaces(el);
  _activePanel = name;
  _activeSurface = name;
  setSurfaceCollapsed(el, false);
  setSidebarNav(name);
  syncMobileSurfaceState();
}

function _closePanel(name, { restoreNav = true, restoreFocus = true } = {}) {
  const el = document.getElementById(`sb-panel-${name}`);
  invalidatePanelOpenRequest();
  if (restoreFocus) restoreSurfaceFocus(name);
  if (el) setSurfaceCollapsed(el, true);
  if (_activePanel === name) _activePanel = null;
  if (_activeSurface === name) _activeSurface = null;
  if (restoreNav) setSidebarNav("agent");
  // Cascade-close skill drawer when skills panel closes
  if (name === "skills") {
    const drawer = document.getElementById("skill-modal-overlay");
    if (drawer && !drawer.classList.contains("hidden")) {
      drawer.classList.add("hidden");
      // Move drawer back to body and clear inline style
      if (drawer.parentElement !== document.body) {
        drawer.style.left = "";
        document.body.appendChild(drawer);
      }
    }
  }
  syncMobileSurfaceState();
  if (!restoreFocus) _surfaceTriggers.delete(name);
}

function closePanel(name) {
  _closePanel(name);
}

function initPanelKeyClose() {
  if (globalThis.__pfsPanelKeyCloseRegistered) return;
  globalThis.__pfsPanelKeyCloseRegistered = true;
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const drawer = $("sb-drawer");
    const drawerOpen = !!drawer && !drawer.classList.contains("collapsed");
    // A lazy Vue island may still be loading before its panel becomes visible.
    // Escape must invalidate that pending open request as well as close surfaces
    // that have already committed their visible state.
    if (_activeSurface || _activePanel || drawerOpen || _panelOpenRequest.name) {
      closeSidebarSurfaces();
    }
  });
}

// ── KB inline form open / close ────────────────────────────────────

function openKbInlineForm() {
  const form = document.getElementById("kb-inline-form");
  if (form) form.classList.remove("collapsed");
}

function closeKbInlineForm() {
  const form = document.getElementById("kb-inline-form");
  if (form) form.classList.add("collapsed");
}

// ── Sessions / Sources drawer (legacy) ────────────────────────────

function setDrawerTab(tab = "sessions") {
  const drawer = $("sb-drawer");
  if (!drawer) return;

  drawer.dataset.drawerPanel = tab;
  const title = $("sb-drawer-title");
  if (title) title.textContent = tab === "sources" ? text("sidebar.drawer.sources", "数据链接") : text("sidebar.drawer.sessions", "会话文件");

  document.querySelectorAll(".sb-drawer-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.drawerTab === tab);
  });
  document.querySelectorAll(".sb-drawer-page").forEach((page) => {
    page.classList.toggle("active", page.dataset.drawerPage === tab);
  });

  if (tab === "sources") datasource.loadWarehouseList();
}

function openSidebarDrawer(tab = "sessions", trigger = null) {
  const drawer = $("sb-drawer");
  if (!drawer) return;
  rememberSurfaceTrigger("drawer", trigger);
  invalidatePanelOpenRequest();
  // Collapse any open panel first.
  if (_activePanel) _closePanel(_activePanel, { restoreNav: false, restoreFocus: false });
  closeCompetingSurfaces(drawer);
  setDrawerTab(tab);
  _activeSurface = "drawer";
  setSurfaceCollapsed(drawer, false);
  if (tab === "sessions") setSidebarNav("history");
  syncMobileSurfaceState();
}

function closeSidebarDrawer() {
  const drawer = $("sb-drawer");
  if (!drawer) return;
  const surface = _activeSurface || "drawer";
  invalidatePanelOpenRequest();
  restoreSurfaceFocus(surface);
  setSurfaceCollapsed(drawer, true);
  if (_activeSurface === "drawer") _activeSurface = null;
  setSidebarNav("agent");
  syncMobileSurfaceState();
}

function closeSidebarSurfaces({ restoreFocus = true } = {}) {
  const drawer = $("sb-drawer");
  const surface = _activeSurface || (_activePanel ? _activePanel : drawer && !drawer.classList.contains("collapsed") ? "drawer" : null);
  invalidatePanelOpenRequest();
  if (surface && restoreFocus) restoreSurfaceFocus(surface);
  else if (surface) _surfaceTriggers.delete(surface);
  if (_activePanel) _closePanel(_activePanel, { restoreNav: false, restoreFocus: false });
  document.querySelectorAll(SIDE_SURFACE_SELECTOR).forEach((surface) => {
    setSurfaceCollapsed(surface, true);
  });
  if (drawer) setSurfaceCollapsed(drawer, true);
  _activePanel = null;
  _activeSurface = null;
  setSidebarNav("agent");
  syncMobileSurfaceState();
}

// ── Focus mode ────────────────────────────────────────────────────

function toggleFocusMode() {
  const enabled = !document.body.classList.contains("focus-mode");
  document.body.classList.toggle("focus-mode", enabled);
  const button = $("btn-focus-mode");
  if (!button) return;
  button.classList.toggle("active", enabled);
  const label = button.querySelector(".hdr-btn-label");
  if (label) label.textContent = enabled ? text("sidebar.focus.exit", "退出专注") : text("sidebar.focus.enter", "专注对话");
  button.title = enabled ? text("sidebar.focus.restore_title", "恢复完整界面") : text("sidebar.focus.enter_title", "隐藏左侧面板，专注对话");
  button.setAttribute("aria-label", button.title);
}

document.addEventListener("langchange", () => {
  const drawer = $("sb-drawer");
  if (drawer && !drawer.classList.contains("collapsed")) {
    setDrawerTab(drawer.dataset.drawerPanel || "sessions");
  }
  const focusButton = $("btn-focus-mode");
  if (focusButton) {
    const enabled = document.body.classList.contains("focus-mode");
    const label = focusButton.querySelector(".hdr-btn-label");
    if (label) label.textContent = enabled ? text("sidebar.focus.exit", "退出专注") : text("sidebar.focus.enter", "专注对话");
    focusButton.title = enabled ? text("sidebar.focus.restore_title", "恢复完整界面") : text("sidebar.focus.enter_title", "隐藏左侧面板，专注对话");
    focusButton.setAttribute("aria-label", focusButton.title);
  }
});
// ── Add-source dropdown ───────────────────────────────────────────

function closeAddSrcDropdown() {
  const dropdown = $("sb-add-src");
  if (!dropdown) return;
  dropdown.classList.remove("open");
  const button = dropdown.querySelector(".sb-btn-primary");
  if (button) button.setAttribute("aria-expanded", "false");
}

function toggleAddSrc() {
  const dropdown = $("sb-add-src");
  if (!dropdown) return;
  const button = dropdown.querySelector(".sb-btn-primary");
  const open = dropdown.classList.toggle("open");
  if (button) button.setAttribute("aria-expanded", String(open));
}

function openDataSource(trigger = null) {
  openSidebarDrawer("sources", trigger);
  if (state.srcConnected) return;

  const dropdown = $("sb-add-src");
  if (!dropdown || dropdown.classList.contains("open")) return;
  dropdown.classList.add("open");
  const button = dropdown.querySelector(".sb-btn-primary");
  if (button) button.setAttribute("aria-expanded", "true");
}

function initAddSourceDropdown() {
  if (globalThis.__pfsAddSourceDropdownRegistered) return;
  globalThis.__pfsAddSourceDropdownRegistered = true;

  document.addEventListener("click", (event) => {
    const dropdown = $("sb-add-src");
    if (!dropdown || !dropdown.classList.contains("open")) return;

    if (event.target.closest(".sb-dropdown-item")) {
      setTimeout(closeAddSrcDropdown, 0);
      return;
    }
    if (!dropdown.contains(event.target)) closeAddSrcDropdown();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAddSrcDropdown();
  });
}

export const sidebar = Object.freeze({
  closeAddSrcDropdown,
  closePanel,
  closeSidebarDrawer,
  closeSidebarSurfaces,
  closeKbInlineForm,
  initAddSourceDropdown,
  initPanelKeyClose,
  initSidebarRail,
  beginPanelOpen,
  isPanelOpenRequestCurrent,
  rememberSurfaceTrigger,
  openDataSource,
  openKbInlineForm,
  openPanel,
  openSidebarDrawer,
  setDrawerTab,
  setSidebarNav,
  toggleAddSrc,
  toggleFocusMode,
  toggleSidebarRail,
});
