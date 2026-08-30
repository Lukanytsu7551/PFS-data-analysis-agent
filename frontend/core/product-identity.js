// PFS is the single browser namespace for the product.
(function establishPfsNamespace(global) {
  const pfs = global.PFS || {};
  global.PFS = pfs;
  pfs.product = Object.freeze({
    shortName: "PFS",
    name: "PFS 数据分析 Agent",
    storagePrefix: "pfs_",
  });
  const prefixed = function (key) {
    return "pfs_" + key;
  };
  pfs.storage = Object.freeze({
    get: function (key, fallback) {
      const value = global.localStorage.getItem(prefixed(key));
      return value === null ? (fallback === undefined ? null : fallback) : value;
    },
    set: function (key, value) {
      global.localStorage.setItem(prefixed(key), String(value));
    },
    remove: function (key) {
      global.localStorage.removeItem(prefixed(key));
    },
    sessionGet: function (key, fallback) {
      const value = global.sessionStorage.getItem(prefixed(key));
      return value === null ? (fallback === undefined ? null : fallback) : value;
    },
    sessionSet: function (key, value) {
      global.sessionStorage.setItem(prefixed(key), String(value));
    },
    sessionRemove: function (key) {
      global.sessionStorage.removeItem(prefixed(key));
    },
  });
})(globalThis);
