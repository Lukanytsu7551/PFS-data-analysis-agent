import { $ } from "./runtime.js";

// The model and personal-skill pickers are transient modal surfaces. Keeping
// the scrim state in one small module prevents one picker from reopening a
// stale scrim after the other picker has been closed.
export function setPickerBackdrop(open) {
  // A document-level outside-click listener from the other picker can run
  // after a new picker has opened. Derive the final state from both surfaces
  // so that closing an already-closed picker never removes the active scrim.
  const active =
    Boolean(open) ||
    Boolean(document.querySelector("#model-picker.open, #composer-skill-picker.open"));
  const backdrop = $("pfs-picker-backdrop");
  if (backdrop) {
    backdrop.classList.toggle("is-active", active);
    backdrop.setAttribute("aria-hidden", String(!active));
    if (active) backdrop.removeAttribute("inert");
    else backdrop.setAttribute("inert", "");
  }
  document.body.classList.toggle("pfs-picker-open", active);
}
