const STORAGE_KEY = "pfs_theme";

function readStoredTheme() {
  return localStorage.getItem(STORAGE_KEY);
}

export function getTheme() {
  return readStoredTheme() === "dark" ? "dark" : "light";
}

export function applyTheme(theme) {
  const html = document.documentElement;
  if (theme === "dark") html.setAttribute("data-theme", "dark");
  else html.removeAttribute("data-theme");

  const button = document.getElementById("theme-toggle");
  if (!button) return;

  const label =
    theme === "dark"
      ? globalThis.t
        ? globalThis.t("theme.to_light")
        : "Light mode"
      : globalThis.t
        ? globalThis.t("theme.to_dark")
        : "Dark mode";
  button.dataset.theme = theme;
  button.title = label;
  button.setAttribute("aria-label", label);
}

export function setTheme(theme) {
  localStorage.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
}

export function toggleTheme() {
  setTheme(getTheme() === "dark" ? "light" : "dark");
}

export const theme = Object.freeze({
  getTheme,
  setTheme,
  toggleTheme,
  applyTheme,
});

applyTheme(getTheme());
document.addEventListener("langchange", () => applyTheme(getTheme()));
