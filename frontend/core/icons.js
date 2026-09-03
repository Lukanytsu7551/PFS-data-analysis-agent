// One semantic icon vocabulary for the PFS workbench.
// Keep icons local and dependency-free so every surface shares the same
// stroke, viewBox, sizing and baseline rules.
const PATHS = Object.freeze({
  activity: ['<path d="M3 12h4l2-8 4 16 2-8h6"/>'],
  arrowDown: ['<path d="M12 5v14m-6-6 6 6 6-6"/>'],
  arrowLeft: ['<path d="m15 18-6-6 6-6"/>'],
  arrowRight: ['<path d="m9 18 6-6-6-6"/>'],
  arrowUp: ['<path d="M12 19V5m-6 6 6-6 6 6"/>'],
  check: ['<path d="m5 12 4 4L19 6"/>'],
  checkCircle: ['<circle cx="12" cy="12" r="9"/>', '<path d="m8 12 2.5 2.5L16.5 9"/>'],
  circle: ['<circle cx="12" cy="12" r="8"/>'],
  circleAlert: ['<circle cx="12" cy="12" r="9"/>', '<path d="M12 7v5M12 16h.01"/>'],
  circleX: ['<circle cx="12" cy="12" r="9"/>', '<path d="m9 9 6 6M15 9l-6 6"/>'],
  chevronDown: ['<path d="m6 9 6 6 6-6"/>'],
  chevronLeft: ['<path d="m15 18-6-6 6-6"/>'],
  chevronRight: ['<path d="m9 6 6 6-6 6"/>'],
  chevronUp: ['<path d="m6 15 6-6 6 6"/>'],
  clipboard: [
    '<rect x="5" y="4" width="14" height="17" rx="2"/>',
    '<path d="M9 4.5V3h6v1.5M8 10h8M8 14h6"/>',
  ],
  circleHelp: [
    '<circle cx="12" cy="12" r="9"/>',
    '<path d="M9.8 9a2.3 2.3 0 1 1 3.8 1.7c-1 .8-1.6 1.2-1.6 2.8"/>',
    '<path d="M12 17h.01"/>',
  ],
  close: ['<path d="m6 6 12 12M18 6 6 18"/>'],
  cpu: [
    '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    '<path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  ],
  database: [
    '<ellipse cx="12" cy="5" rx="8" ry="3"/>',
    '<path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  ],
  download: ['<path d="M12 3v12m-5-5 5 5 5-5M5 21h14"/>'],
  edit: [
    '<path d="m4 16-.8 4.8L8 20l10.8-10.8a2.8 2.8 0 0 0-4-4L4 16Z"/>',
    '<path d="m13.5 6.5 4 4"/>',
  ],
  expand: ['<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/>'],
  collapse: ['<path d="M9 3v6H3M15 3v6h6M9 21v-6H3M15 21v-6h6"/>'],
  external: [
    '<path d="M14 5h5v5M19 5l-8 8"/>',
    '<path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>',
  ],
  file: [
    '<path d="M6 3h8l4 4v14H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/>',
    '<path d="M14 3v5h5M8 13h8M8 17h6"/>',
  ],
  filePlus: [
    '<path d="M6 3h8l4 4v14H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/>',
    '<path d="M14 3v5h5M12 12v6M9 15h6"/>',
  ],
  folder: [
    '<path d="M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z"/>',
  ],
  grid: [
    '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
  ],
  history: ['<circle cx="12" cy="12" r="9"/>', '<path d="M12 7v5l3 2M4 7V3m0 0h4"/>'],
  info: ['<circle cx="12" cy="12" r="9"/>', '<path d="M12 11v5M12 8h.01"/>'],
  language: [
    '<circle cx="12" cy="12" r="9"/>',
    '<path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  ],
  layout: ['<rect x="3" y="4" width="18" height="16" rx="2"/>', '<path d="M9 4v16M9 9h12"/>'],
  link: [
    '<path d="M10 13a5 5 0 0 0 7.5.5l2.5-2.5a5 5 0 0 0-7-7L11.5 5.5"/>',
    '<path d="M14 11a5 5 0 0 0-7.5-.5L4 13a5 5 0 0 0 7 7l1.5-1.5"/>',
  ],
  lock: [
    '<rect x="5" y="10" width="14" height="11" rx="2"/>',
    '<path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  ],
  logOut: [
    '<path d="M10 17l5-5-5-5M15 12H3"/>',
    '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"/>',
  ],
  message: [
    '<path d="M20 11.5a7.5 7.5 0 0 1-8 7.5 8.7 8.7 0 0 1-3.2-.6L4 20l1.6-3.8A7.2 7.2 0 0 1 4 11.5 7.5 7.5 0 0 1 12 4a7.5 7.5 0 0 1 8 7.5Z"/>',
  ],
  moon: ['<path d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5 8.5 8.5 0 1 0 20.5 14.5Z"/>'],
  more: [
    '<circle cx="5" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none"/>',
  ],
  panelLeft: [
    '<rect x="3" y="4" width="18" height="16" rx="2"/>',
    '<path d="M9 4v16M6.5 12h9M9.5 12l3-3M9.5 12l3 3"/>',
  ],
  pencil: ['<path d="m4 16-.8 4.8L8 20l10.8-10.8a2.8 2.8 0 0 0-4-4L4 16Z"/>'],
  plug: ['<path d="M9 3v6M15 3v6M6 9h12v3a6 6 0 0 1-12 0V9ZM9 18v3M15 18v3"/>'],
  plus: ['<path d="M12 5v14M5 12h14"/>'],
  refresh: ['<path d="M20 11a8 8 0 1 0 1 4"/>', '<path d="M20 4v7h-7"/>'],
  save: [
    '<path d="M5 3h11l3 3v15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/>',
    '<path d="M7 3v6h8V3M7 21v-8h10v8"/>',
  ],
  search: ['<circle cx="10.8" cy="10.8" r="6.8"/>', '<path d="m16 16 5 5"/>'],
  settings: [
    '<circle cx="12" cy="12" r="3"/>',
    '<path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1-2.8 2.8-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5v.1h-4v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1-2.8-2.8.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3v-4h.3a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1 2.8-2.8.1.1a1.6 1.6 0 0 0 1.8.3 1.6 1.6 0 0 0 1-1.5V3h4v.3a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1 2.8 2.8-.1.1a1.6 1.6 0 0 0-.3 1.8 1.6 1.6 0 0 0 1.5 1h.3v4h-.3a1.6 1.6 0 0 0-1.5 1Z"/>',
  ],
  shield: ['<path d="M12 21s7-3.5 7-9V5l-7-3-7 3v7c0 5.5 7 9 7 9Z"/>'],
  spark: [
    '<path d="m12 3 1.6 5.4L19 10l-5.4 1.6L12 17l-1.6-5.4L5 10l5.4-1.6L12 3ZM19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z"/>',
  ],
  stop: ['<rect x="7" y="7" width="10" height="10" rx="1.5"/>'],
  sun: [
    '<circle cx="12" cy="12" r="4"/>',
    '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  ],
  table: [
    '<rect x="3" y="4" width="18" height="16" rx="2"/>',
    '<path d="M3 10h18M3 15h18M9 4v16M15 4v16"/>',
  ],
  terminal: [
    '<rect x="3" y="4" width="18" height="16" rx="2"/>',
    '<path d="m7 9 3 3-3 3M13 15h4"/>',
  ],
  upload: ['<path d="M12 16V4m-5 5 5-5 5 5M5 20h14"/>'],
  users: [
    '<path d="M16 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM17 11a3 3 0 0 0 0-6M16 14a4 4 0 0 1 4 4v2"/>',
  ],
  chart: ['<path d="M4 19V5M4 19h16"/>', '<path d="m7 15 3-4 3 2 5-7"/>'],
  ruler: ['<path d="m4 17 13-13 3 3L7 20H4v-3Z"/>', '<path d="m10 11 2 2m1-5 2 2m-7 5 2 2"/>'],
  trash: ['<path d="M4 7h16M10 11v6M14 11v6M6 7l1 14h10l1-14M9 7V4h6v3"/>'],
  presentation: [
    '<rect x="3" y="4" width="18" height="14" rx="2"/>',
    '<path d="M8 21h8M12 18v3M7 8h4M7 12h7M16 8h1"/>',
  ],
  lightbulb: [
    '<path d="M9 18h6M10 21h4M8 14.5A6 6 0 1 1 16 14c-.8.7-1 1.5-1 2H9c0-.6-.3-1.1-1-1.5Z"/>',
  ],
});

function escapeAttr(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function svgMarkup(name, options = {}) {
  const className = options.className || "pfs-icon";
  const size = options.size || 18;
  const label = options.label || "";
  const strokeWidth = options.strokeWidth || 1.8;
  const paths = PATHS[name] || PATHS.spark;
  const safe = String(label).replace(/[<&>]/g, "");
  const safeClassName = escapeAttr(className);
  const safeSize = escapeAttr(size);
  const safeStrokeWidth = escapeAttr(strokeWidth);
  const safeLabel = escapeAttr(safe);
  const title = label ? "<title>" + safe + "</title>" : "";
  const labelAttrs = label ? ' role="img" aria-label="' + safeLabel + '"' : "";
  return (
    '<svg class="' +
    safeClassName +
    '" width="' +
    safeSize +
    '" height="' +
    safeSize +
    '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' +
    safeStrokeWidth +
    '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="' +
    (label ? "false" : "true") +
    '"' +
    labelAttrs +
    ">" +
    title +
    paths.join("") +
    "</svg>"
  );
}

export function iconSpan(name, options = {}) {
  const className = options.className || "pfs-icon-box";
  return (
    '<span class="' + className + '" aria-hidden="true">' + svgMarkup(name, options) + "</span>"
  );
}

export function iconVNode(h, name, options = {}) {
  const size = options.size || 18;
  const label = options.label || "";
  return h(
    "svg",
    {
      class: options.className || "pfs-icon",
      width: size,
      height: size,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": options.strokeWidth || 1.8,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": label ? "false" : "true",
      ...(label ? { role: "img", "aria-label": label } : {}),
    },
    (PATHS[name] || PATHS.spark).map((markup) => h("g", { innerHTML: markup })),
  );
}

export const ICON_NAMES = Object.freeze(Object.keys(PATHS));

export function setIcon(element, name, options = {}) {
  if (!element) return element;
  const size = options.size || element.dataset?.iconSize || 18;
  const label = options.label || element.dataset?.pfsIconLabel || "";
  element.innerHTML = svgMarkup(name, {
    ...options,
    className: options.className || "pfs-icon",
    size,
    label,
  });
  element.dataset.pfsIconReady = "true";
  if (label) {
    element.setAttribute("role", "img");
    element.setAttribute("aria-label", label);
    element.removeAttribute("aria-hidden");
  } else {
    element.setAttribute("aria-hidden", "true");
  }
  return element;
}

export function hydrateIcons(root = document) {
  if (!root?.querySelectorAll) return 0;
  const slots = [];
  if (root.matches?.("[data-pfs-icon]")) slots.push(root);
  slots.push(...root.querySelectorAll("[data-pfs-icon]"));
  slots.forEach((slot) => {
    const name = slot.dataset.pfsIcon;
    if (name) setIcon(slot, name);
  });
  return slots.length;
}

globalThis.PFSIcons = Object.freeze({
  svgMarkup,
  iconSpan,
  iconVNode,
  setIcon,
  hydrateIcons,
  ICON_NAMES,
});
