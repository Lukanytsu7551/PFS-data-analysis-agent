// Apply the persisted theme before styles render to avoid a light-mode flash.
(function () {
  try {
    const theme = localStorage.getItem("pfs_theme");
    if (theme === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  } catch (_) {
    // Storage may be unavailable in hardened/private contexts; light is safe.
  }
})();
