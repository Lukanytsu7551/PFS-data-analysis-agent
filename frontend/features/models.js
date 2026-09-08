// Model selector + Settings panel (built-in providers + custom models).
import { $, state } from "../core/runtime.js";
import { getUiIsland } from "../core/ui-registry.js";
import { iconSpan } from "../core/icons.js";
import { setPickerBackdrop } from "../core/picker-overlay.js";

const pfs = () => globalThis.PFS;

  const COMMON_ICON = "/static/Images/pfs-mark.svg";
  const BUILTIN_META = {
    deepseek:       { label: "DeepSeek",            icon: COMMON_ICON },
    kimi:           { label: "Kimi",                icon: COMMON_ICON },
    kimi_coding:    { label: "Kimi Coding Plan",    icon: COMMON_ICON },
    glm:            { label: "GLM",                 icon: COMMON_ICON },
    glm_coding:     { label: "GLM Coding Plan",     icon: COMMON_ICON },
    minimax:        { label: "MiniMax",             icon: COMMON_ICON },
    minimax_coding: { label: "MiniMax Coding Plan", icon: COMMON_ICON },
  };
  const VISIBLE_BUILTIN_PROVIDERS = new Set(Object.keys(BUILTIN_META));
  const MODEL_PICKER = {
    index: 0,
    models: [],
    trigger: null,
  };

  // 判断 base_url 是否本地地址（与后端 _is_local_base_url 保持一致）
  function _isLocalBaseUrl(url) {
    if (!url) return false;
    const u = String(url).toLowerCase();
    return ["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "0:0:0:0:0:0:0:1"]
      .some(m => u.includes(m));
  }

  // 首次加载标志 — loadModels 第一次运行时为 true，此后为 false。
  // 只有首次才允许自动选中第一个模型；后续刷新（保存配置、删除模型等触发）
  // 必须保留用户当前的选择，绝不重置。
  let _firstLoad = true;

  function _modelSelectors() {
    return [$("model-sel"), $("model-sel-sidebar")].filter(Boolean);
  }

  function _modelLabel(key, cfg) {
    if (!key) return t('sidebar.model_placeholder') || "— 选择模型 —";
    return cfg?.is_custom
      ? (cfg.name || cfg.model || key)
      : (BUILTIN_META[key]?.label || cfg?.model || key);
  }

  function _modelDescription(key, cfg) {
    if (!cfg) return key || "";
    const parts = [];
    if (cfg.model) parts.push(cfg.model);
    if (cfg.base_url) parts.push(cfg.base_url);
    return parts.join(" · ") || key;
  }

  function _isVisibleProvider(key, cfg) {
    return !!cfg?.is_custom || VISIBLE_BUILTIN_PROVIDERS.has(key);
  }

  function _availableModels(models = state.modelConfigs || {}) {
    return Object.entries(models)
      .filter(([key, cfg]) => cfg?.has_api_key && _isVisibleProvider(key, cfg))
      .map(([key, cfg]) => ({
        key,
        label: _modelLabel(key, cfg),
        description: _modelDescription(key, cfg),
        source: cfg.is_custom ? "自定义" : "内置",
      }));
  }

  function _syncModelLabels(value) {
    const label = _modelLabel(value, state.modelConfigs?.[value]);
    for (const id of ["model-picker-label", "model-picker-label-sidebar"]) {
      const el = $(id);
      if (el) el.textContent = label;
    }
    for (const id of ["model-picker-trigger", "model-picker-trigger-sidebar"]) {
      const el = $(id);
      if (!el) continue;
      el.classList.toggle("has-value", !!value);
      el.setAttribute("aria-expanded", String(isModelPickerOpen()));
    }
  }

  function _syncModelSelectors(value) {
    for (const select of _modelSelectors()) select.value = value || "";
    _syncModelLabels(value || "");
    renderModelPicker($("model-picker-search")?.value || "");
  }

  async function loadModels() {
    const r = await fetch("/api/models");
    const models = await r.json();
    state.modelConfigs = models;
    const selectors = _modelSelectors();
    const prevValue = selectors.map(select => select.value).find(Boolean) || "";
    for (const sel of selectors) {
      sel.innerHTML = `<option value="">${t('sidebar.model_placeholder')}</option>`;
      for (const [key, cfg] of Object.entries(models)) {
        if (!cfg.has_api_key || !_isVisibleProvider(key, cfg)) continue;
        const opt = document.createElement("option");
        opt.value = key;
        opt.textContent = cfg.is_custom
          ? (cfg.name || cfg.model || key)
          : (BUILTIN_META[key]?.label || cfg.model || key);
        sel.appendChild(opt);
      }
    }

    const primary = $("model-sel");
    if (prevValue && primary && [...primary.options].some(o => o.value === prevValue)) {
      _syncModelSelectors(prevValue);
    } else if (_firstLoad && primary && primary.options.length > 1) {
      // 仅首次加载且之前没有选中值时，才自动选第一个并通知后端
      _syncModelSelectors(primary.options[1].value);
      onModelChange(primary.options[1].value);
    } else {
      _syncModelSelectors(primary?.value || "");
    }
    // 后续刷新时若 prevValue 已不存在（模型被删除），保持空选择，不强制切换
    _firstLoad = false;
  }

  async function onModelChange(value) {
    const v = value !== undefined ? value : $("model-sel").value;
    _syncModelSelectors(v);
    closeModelPicker();
    // Switching the model invalidates any previous "tested OK" indicator.
    _resetModelDot();
    if (!v || !state.SID) return;
    const response = await fetch(`/api/session/${state.SID}/model`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: v }),
    });
    if (response.ok) await pfs()?.slash?.loadCommands?.();
    // Auto-test the freshly selected model. Fire-and-forget — the function
    // owns its own UI feedback (dot colour + failure modal).
    testModel(v);
  }

  function isModelPickerOpen() {
    return Boolean($("model-picker")?.classList.contains("open"));
  }

  function _esc(value) {
    return String(value || "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[ch]);
  }

  function renderModelPicker(filter = "") {
    const list = $("model-picker-list");
    if (!list) return;
    const term = String(filter || "").trim().toLowerCase();
    const availableModels = _availableModels();
    MODEL_PICKER.models = availableModels.filter(item => !term
      || item.label.toLowerCase().includes(term)
      || item.description.toLowerCase().includes(term)
      || item.key.toLowerCase().includes(term));
    list.innerHTML = "";
    if (!MODEL_PICKER.models.length) {
      const empty = document.createElement("div");
      empty.className = "model-picker-empty";

      const message = document.createElement("p");
      message.textContent = availableModels.length && term
        ? (t("model_picker.empty") || "没有匹配的模型")
        : (t("model_picker.empty_unconfigured") || "还没有已配置的模型");
      empty.appendChild(message);

      if (!availableModels.length) {
        const manage = document.createElement("button");
        manage.type = "button";
        manage.className = "model-picker-empty-action";
        manage.textContent = t("model_picker.configure") || "前往模型设置";
        manage.addEventListener("click", () => {
          closeModelPicker();
          globalThis.openOverlay?.("ov-settings");
        });
        empty.appendChild(manage);
      }
      list.appendChild(empty);
      return;
    }
    const selected = $("model-sel")?.value || "";
    MODEL_PICKER.models.forEach((model, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = [
        "skill-picker-item",
        "model-picker-item",
        model.key === selected ? "selected" : "",
        index === MODEL_PICKER.index ? "active" : "",
      ].filter(Boolean).join(" ");
      button.dataset.model = model.key;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(model.key === selected));
      button.innerHTML = `
        ${iconSpan("spark", { className: "skill-picker-icon model-picker-icon", size: 17 })}
        <span class="skill-picker-copy">
          <strong>${_esc(model.label)}</strong>
          <small>${_esc(model.description || model.key)}</small>
        </span>
        <span class="skill-picker-source">${_esc(model.source)}</span>`;
      button.addEventListener("click", () => selectModel(model.key));
      list.appendChild(button);
    });
  }

  function _limitPickerList(picker, list, maxHeight) {
    if (!picker || !list || !Number.isFinite(maxHeight)) return;
    const pickerStyle = getComputedStyle(picker);
    const listStyle = getComputedStyle(list);
    const verticalPadding = (parseFloat(pickerStyle.paddingTop) || 0)
      + (parseFloat(pickerStyle.paddingBottom) || 0);
    const listPadding = (parseFloat(listStyle.paddingTop) || 0)
      + (parseFloat(listStyle.paddingBottom) || 0);
    const headerHeight = picker.querySelector(".skill-picker-head")?.offsetHeight || 0;
    const searchHeight = picker.querySelector(".skill-picker-search")?.offsetHeight || 0;
    const available = Math.floor(maxHeight - verticalPadding - listPadding - headerHeight - searchHeight - 2);
    list.style.maxHeight = `${Math.max(1, available)}px`;
  }

  function _positionModelPicker(trigger) {
    const picker = $("model-picker");
    if (!picker || !trigger) return;

    const rect = trigger.getBoundingClientRect();
    const viewportWidth = Math.max(280, window.innerWidth || document.documentElement.clientWidth);
    const viewportHeight = Math.max(240, window.innerHeight || document.documentElement.clientHeight);
    const viewportGap = 12;
    const gap = 10;
    const width = Math.min(460, Math.max(320, rect.width + 160));
    const sidebarTrigger = trigger.id === "model-picker-trigger-sidebar";
    const sidebarRight = document.querySelector("#app-sidebar .sb-main")
      ?.getBoundingClientRect?.().right || 0;

    let preferredLeft = sidebarTrigger
      ? Math.max(rect.right + gap, sidebarRight + gap)
      : rect.left;
    let actualWidth = Math.min(width, viewportWidth - viewportGap * 2);
    if (sidebarTrigger) {
      // Keep a sidebar-launched picker entirely in the main canvas. On narrow
      // windows it becomes narrower instead of sliding back over the status rail.
      const availableRight = viewportWidth - preferredLeft - viewportGap;
      if (availableRight >= 240) actualWidth = Math.min(actualWidth, availableRight);
      else {
        preferredLeft = Math.max(viewportGap, sidebarRight + 8);
        actualWidth = Math.max(220, viewportWidth - preferredLeft - viewportGap);
      }
    }
    const left = Math.max(viewportGap, Math.min(preferredLeft, viewportWidth - actualWidth - viewportGap));
    picker.style.right = "auto";
    picker.style.bottom = "auto";
    picker.style.width = `${Math.round(actualWidth)}px`;
    picker.style.left = `${Math.round(left)}px`;

    const composer = sidebarTrigger
      ? null
      : trigger.closest(".composer-shell") || document.querySelector(".composer-shell");
    const composerRect = composer?.getBoundingClientRect?.() || null;
    const boundaryTop = composerRect
      ? Math.max(viewportGap, composerRect.top - gap)
      : Math.max(viewportGap, rect.top - gap);
    const availableAbove = Math.max(1, boundaryTop - viewportGap);
    const availableBelow = Math.max(1, viewportHeight - (composerRect?.bottom || rect.bottom) - viewportGap);
    const maxPanelHeight = Math.min(460, viewportHeight - viewportGap * 2);

    // The picker is measured after its width is known. Reserve a complete
    // slot first, then cap the scrollable list to that slot so the panel never
    // covers the writing surface or the sidebar status block.
    const placeBelow = !sidebarTrigger
      && availableAbove < 160
      && availableBelow > availableAbove;
    const slotHeight = sidebarTrigger
      ? viewportHeight - viewportGap * 2
      : (placeBelow ? availableBelow : availableAbove);
    const constrainedHeight = Math.max(1, Math.min(maxPanelHeight, slotHeight));
    picker.style.maxHeight = `${Math.round(constrainedHeight)}px`;
    _limitPickerList(picker, $("model-picker-list"), constrainedHeight);

    const pickerHeight = Math.min(
      picker.getBoundingClientRect().height || picker.scrollHeight || 320,
      maxPanelHeight,
    );
    if (sidebarTrigger) {
      picker.style.top = `${Math.max(viewportGap, Math.min(rect.top, viewportHeight - pickerHeight - viewportGap))}px`;
      return;
    }
    if (!placeBelow) {
      picker.style.top = `${Math.max(viewportGap, boundaryTop - pickerHeight)}px`;
    } else {
      picker.style.top = `${Math.min(viewportHeight - pickerHeight - viewportGap, (composerRect?.bottom || rect.bottom) + gap)}px`;
    }
  }

  async function openModelPicker(trigger) {
    pfs()?.skills?.close?.();
    pfs()?.slash?.closeSlashPopup?.();
    pfs()?.sidebar?.closeSidebarSurfaces?.({ restoreFocus: false });
    const button = trigger?.closest?.("#model-picker-trigger, #model-picker-trigger-sidebar")
      || $("model-picker-trigger");
    MODEL_PICKER.trigger = button;
    MODEL_PICKER.index = 0;
    if (!Object.keys(state.modelConfigs || {}).length) await loadModels();
    const search = $("model-picker-search");
    if (search) search.value = "";
    renderModelPicker();
    const picker = $("model-picker");
    picker?.classList.add("open");
    picker?.setAttribute("aria-modal", "true");
    setPickerBackdrop(true);
    if (picker) {
      picker.style.visibility = "hidden";
      picker.style.removeProperty("max-height");
      $("model-picker-list")?.style.removeProperty("max-height");
    }
    _positionModelPicker(button);
    if (picker) picker.style.visibility = "";
    _syncModelLabels($("model-sel")?.value || "");
    search?.focus();
  }

  function closeModelPicker() {
    $("model-picker")?.classList.remove("open");
    $("model-picker")?.removeAttribute("style");
    $("model-picker-list")?.style.removeProperty("max-height");
    $("model-picker")?.removeAttribute("aria-modal");
    setPickerBackdrop(false);
    for (const id of ["model-picker-trigger", "model-picker-trigger-sidebar"]) {
      $(id)?.setAttribute("aria-expanded", "false");
    }
    MODEL_PICKER.trigger = null;
  }

  function selectModel(key) {
    onModelChange(key);
  }

  function refreshModelPickerLabels() {
    _syncModelLabels($("model-sel")?.value || "");
  }

  function onModelSearch(event) {
    MODEL_PICKER.index = 0;
    renderModelPicker(event.target.value);
  }

  function onModelKeyDown(event) {
    const items = [...document.querySelectorAll("#model-picker-list .model-picker-item")];
    if (event.key === "Escape") { event.preventDefault(); closeModelPicker(); return; }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      MODEL_PICKER.index = Math.max(0, Math.min(items.length - 1, MODEL_PICKER.index + delta));
      renderModelPicker(event.currentTarget.value);
      document.querySelectorAll("#model-picker-list .model-picker-item")[MODEL_PICKER.index]
        ?.scrollIntoView({ block: "nearest" });
      return;
    }
    if (event.key === "Enter" && items[MODEL_PICKER.index]) {
      event.preventDefault();
      selectModel(items[MODEL_PICKER.index].dataset.model);
    }
  }

  // ── Model connection test ─────────────────────────────────────────
  // Sidebar model-dot states:
  //   default → blue (.sb-status-dot--info)  "model selected, not yet tested"
  //   testing → blue + pulsing aura via .testing class
  //   success → green (.on)                  "tested OK in this session"
  //   failure → blue (.--info) + failure modal with full error
  //
  // Test entry points:
  //   1. onModelChange()        — auto-runs after the user picks a model in the sidebar
  //   2. testProvider(key) action — manual "测试" button in the Settings modal
  //
  // Both go through `testModel(provider)`. The sidebar dot only updates when
  // the provider being tested matches the one currently selected in the sidebar.
  function _setDotState(provider, st) {
    const dot = $("model-dot");
    if (!dot) return;
    // Only mutate sidebar dot if the test is for the currently-selected provider.
    const current = $("model-sel")?.value;
    if (provider !== current) return;
    dot.classList.toggle("testing", st === "testing");
    dot.classList.toggle("on",      st === "ok");
  }
  function _resetModelDot() {
    const dot = $("model-dot");
    if (dot) dot.classList.remove("on", "testing");
  }

  function _setProviderRowState(provider, st, message) {
    const vs = getUiIsland("settings");
    if (!vs || !vs.isAvailable()) {
      return;
    }
    if (st === "testing") {
      vs.setProviderBusy(provider, "test");
      vs.setProviderStatus(provider, "", t('settings.testing') || "测试中…");
    } else if (st === "ok") {
      vs.setProviderBusy(provider, null);
      vs.setProviderStatus(provider, "ok", message || (t('settings.test_ok') || "连接成功"));
    } else if (st === "fail") {
      vs.setProviderBusy(provider, null);
      vs.setProviderStatus(provider, "err", message || (t('settings.test_fail') || "连接失败"));
    }
  }

  async function testModel(provider) {
    provider = provider || $("model-sel").value;
    if (!provider) {
      pfs()?.overlay?.toast?.(t('sidebar.model_test_no_select') || "请先选择模型", "err");
      return;
    }
    _setDotState(provider, "testing");
    _setProviderRowState(provider, "testing");

    // 优先使用 Vue state 中的 fields（未保存状态也能测试）；
    // fields 不存在（如从侧边栏触发）时用已保存配置。
    const body = { provider };
    const vs = getUiIsland("settings");
    const fields = vs && vs.isAvailable() ? vs.getProviderFields(provider) : null;
    if (fields) {
      if (fields.apiKey.trim())  body.api_key  = fields.apiKey.trim();
      if (fields.baseUrl.trim()) body.base_url = fields.baseUrl.trim();
      if (fields.model.trim())   body.model    = fields.model.trim();
    }

    let data;
    try {
      const r = await fetch("/api/models/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      data = await r.json();
    } catch (e) {
      data = { success: false, message: String(e), provider };
    }

    if (data.success) {
      _setDotState(provider, "ok");
      _setProviderRowState(provider, "ok",
        t('settings.test_ok_with_model', { model: data.model || provider })
          || `连接成功 · ${data.model || provider}`);
      pfs()?.overlay?.toast?.(
        t('sidebar.model_test_ok', { model: data.model || provider })
          || `${data.model || provider} 连接成功`,
        "ok"
      );
    } else {
      _setDotState(provider, "default");   // back to blue
      _setProviderRowState(provider, "fail",
        (t('settings.test_fail') || "连接失败"));
      // Show a modal so the user can read the full error (LLM API errors are long).
      const meta = $("model-test-meta");
      const err  = $("model-test-error");
      if (meta) {
        const modelLabel = data.model || provider;
        meta.textContent = `${t('sidebar.model') || '模型'}: ${modelLabel}`;
      }
      if (err) err.textContent = data.message || "Unknown error";
      window.openOverlay("ov-model-test");
    }
  }

  async function loadBuiltinProviders() {
    const [cfgR, defR] = await Promise.all([
      fetch("/api/models"), fetch("/api/models/defaults"),
    ]);
    const configs  = await cfgR.json();
    const defaults = await defR.json();
    renderBuiltinProviders(configs, defaults);
    renderCustomList(configs);
  }

  function renderBuiltinProviders(configs, defaults) {
    if (!getUiIsland("settings") || !getUiIsland("settings").isAvailable()) {
      console.warn("[models] vueSettings unavailable, renderBuiltinProviders skipped");
      return;
    }
    getUiIsland("settings").sync(configs, defaults, {
      onSave:        (key) => saveBuiltin(key),
      onTest:        (key) => testModel(key),
      onClear:       (key) => clearBuiltin(key),
      onEditCustom:  (key) => editCustomModel(key),
      onDeleteCustom:(key) => deleteCustom(key),
      onTestCustom:  (key) => testModel(key),
      onSubmitForm:  () => addCustomModel(),
      onCancelForm:  () => toggleAddCustom(),
    });
  }

  function renderCustomList(configs) {
    if (!getUiIsland("settings") || !getUiIsland("settings").isAvailable()) {
      console.warn("[models] vueSettings unavailable, renderCustomList skipped");
      return;
    }
    getUiIsland("settings").refreshCustoms(configs);
  }

  function editCustomModel(provider) {
    const vs = getUiIsland("settings");
    if (!vs || !vs.isAvailable()) {
      console.warn("[models] vueSettings unavailable, editCustomModel skipped");
      return;
    }
    state._editingCustomProvider = provider;
    fetch("/api/models").then(r => r.json()).then(configs => {
      const cfg = configs[provider];
      if (!cfg) return;
      vs.openForm(provider, cfg);
    });
  }

  async function addCustomModel() {
    const vs = getUiIsland("settings");

    // Fallback: 从静态 DOM 读值（vueSettings 未加载时）
    function _domValues() {
      const _v = (id) => { const el = document.getElementById(id); return el ? el.value : ""; };
      const _chk = (id) => { const el = document.getElementById(id); return el ? el.checked : false; };
      return {
        name: _v("ac-name"), url: _v("ac-url"), model: _v("ac-model"),
        key: _v("ac-key"), ctx: _v("ac-ctx"), output: _v("ac-output"),
        inputPrice: _v("ac-input-price"), outputPrice: _v("ac-output-price"),
        think: _chk("ac-think"), budget: _v("ac-budget"), editingKey: null,
      };
    }
    function _setMsg(err, ok) {
      const e = document.getElementById("ac-err");
      const o = document.getElementById("ac-ok");
      if (e) e.textContent = err || "";
      if (o) o.textContent = ok || "";
    }
    function _closeForm() {
      const f = document.getElementById("add-custom-form");
      if (f) f.classList.remove("show");
    }

    const f = (!vs || !vs.isAvailable()) ? _domValues() : vs.getFormValues();
    const ctxRaw    = f.ctx.trim();
    const outRaw    = f.output.trim();
    const budgetRaw = f.budget.trim();
    const inputPriceRaw = f.inputPrice.trim();
    const outputPriceRaw = f.outputPrice.trim();
    (vs ? vs.setFormMsg : _setMsg)("", "");

    if (f.editingKey) {
      const body = {
        provider:        f.editingKey,
        base_url:        f.url.trim(),
        model_name:      f.model.trim(),
        api_key:         f.key.trim(),
        enable_thinking: f.think,
        thinking_budget: budgetRaw ? parseInt(budgetRaw) : 8000,
        ...(ctxRaw ? { context_window:    parseInt(ctxRaw) } : {}),
        ...(outRaw ? { max_output_tokens: parseInt(outRaw) } : {}),
        input_price_per_million: inputPriceRaw === "" ? null : Number(inputPriceRaw),
        output_price_per_million: outputPriceRaw === "" ? null : Number(outputPriceRaw),
      };
      const r = await fetch("/api/models/update", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (d.error) {
        (vs ? vs.setFormMsg : _setMsg)(d.error, "");
      } else {
        (vs ? vs.setFormMsg : _setMsg)("", d.message || t('settings.save_ok'));
        state._editingCustomProvider = null;
        await Promise.all([loadModels(), loadBuiltinProviders()]);
        setTimeout(() => { vs ? vs.closeForm() : _closeForm(); }, 1200);
      }
      return;
    }

    const data = {
      name:            f.name.trim(),
      base_url:        f.url.trim(),
      model_name:      f.model.trim(),
      api_key:         f.key.trim(),
      enable_thinking: f.think,
      thinking_budget: budgetRaw ? parseInt(budgetRaw) : 8000,
      ...(ctxRaw ? { context_window:    parseInt(ctxRaw) } : {}),
      ...(outRaw ? { max_output_tokens: parseInt(outRaw) } : {}),
      ...(inputPriceRaw || outputPriceRaw ? {
        input_price_per_million: inputPriceRaw === "" ? null : Number(inputPriceRaw),
        output_price_per_million: outputPriceRaw === "" ? null : Number(outputPriceRaw),
      } : {}),
    };
    const r = await fetch("/api/models/add", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const d = await r.json();
    if (d.error) {
      (vs ? vs.setFormMsg : _setMsg)(d.error, "");
    } else {
      (vs ? vs.setFormMsg : _setMsg)("", d.message);
      await Promise.all([loadModels(), loadBuiltinProviders()]);
      setTimeout(() => { vs ? vs.closeForm() : _closeForm(); }, 1200);
    }
  }

  function toggleAddCustom() {
    console.log("[models] toggleAddCustom entered");
    // 防抖：防止同一物理点击触发两个 listener（缓存导致旧脚本重复绑定）
    if (state._toggleAddCustomPending) {
      console.log("[models] toggleAddCustom debounced, ignore");
      return;
    }
    state._toggleAddCustomPending = true;
    setTimeout(() => { state._toggleAddCustomPending = false; }, 150);

    try {
      const vs = getUiIsland("settings");
      console.log("[models] toggleAddCustom vs:", !!vs, "available:", vs?.isAvailable?.());
      if (!vs || !vs.isAvailable()) {
        console.log("[models] toggleAddCustom fallback, island available:", !!vs);
        // Fallback: 直接操作静态 DOM（vueSettings 未加载时的降级路径）
        state._editingCustomProvider = null;
        const form = document.getElementById("add-custom-form");
        console.log("[models] toggleAddCustom fallback, form found:", !!form, "classList:", form?.className);
        if (!form) return;
        const isOpen = form.classList.toggle("show");
        console.log("[models] toggleAddCustom fallback, isOpen now:", isOpen, "form.display:", getComputedStyle(form).display);
        // 清空表单值（添加模式）
        ["ac-name","ac-url","ac-model","ac-key","ac-ctx","ac-output","ac-input-price","ac-output-price","ac-budget"].forEach(id => {
          const el = document.getElementById(id);
          if (el) { el.value = ""; }
        });
        const cb = document.getElementById("ac-think");
        if (cb) cb.checked = false;
        const row = document.getElementById("ac-budget-row");
        if (row) row.classList.add("hidden");
        const errEl = document.getElementById("ac-err");
        const okEl  = document.getElementById("ac-ok");
        if (errEl) errEl.textContent = "";
        if (okEl)  okEl.textContent = "";
        if (isOpen) {
          requestAnimationFrame(() => {
            const modal = form.closest(".modal");
            if (modal && modal.scrollHeight > modal.clientHeight) {
              modal.scrollTop = modal.scrollHeight;
            } else {
              form.scrollIntoView({ behavior: "smooth", block: "end" });
            }
          });
        }
        return;
      }
      state._editingCustomProvider = null;
      console.log("[models] toggleAddCustom calling vs.toggleForm()");
      vs.toggleForm();
      console.log("[models] toggleAddCustom vs.toggleForm() returned");
    } catch (err) {
      console.error("[models] toggleAddCustom error:", err);
    }
  }

  async function saveBuiltin(key) {
    const vs = getUiIsland("settings");
    if (!vs || !vs.isAvailable()) {
      console.warn("[models] vueSettings unavailable, saveBuiltin skipped");
      return;
    }
    const f = vs.getProviderFields(key);
    if (!f) return;
    const apiKey   = f.apiKey.trim();
    const baseUrl  = f.baseUrl.trim();
    const model    = f.model.trim();
    const ctxRaw   = f.ctx.trim();
    const outRaw   = f.output.trim();
    const think    = f.think;
    const budgetRaw = f.budget.trim();
    const inputPriceRaw = f.inputPrice.trim();
    const outputPriceRaw = f.outputPrice.trim();

    // 本地兼容端点无需 API Key；其余 provider 必填
    const isLocalProvider = _isLocalBaseUrl(baseUrl);
    if (!apiKey && !isLocalProvider) {
      vs.setProviderStatus(key, "err", t('settings.api_key_empty'));
      return;
    }

    vs.setProviderBusy(key, "save");
    vs.setProviderStatus(key, "", t('settings.saving'));

    const body = {
      provider: key, api_key: apiKey, base_url: baseUrl, model,
      enable_thinking: think,
      thinking_budget: budgetRaw ? parseInt(budgetRaw) : 8000,
    };
    if (ctxRaw) body.context_window    = parseInt(ctxRaw);
    if (outRaw) body.max_output_tokens = parseInt(outRaw);
    body.input_price_per_million = inputPriceRaw === "" ? null : Number(inputPriceRaw);
    body.output_price_per_million = outputPriceRaw === "" ? null : Number(outputPriceRaw);
    const r = await fetch("/api/models/set-builtin", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.ok) {
      vs.setProviderBusy(key, null);
      vs.setProviderStatus(key, "ok", t('settings.save_ok'));
      await loadModels();
      await loadBuiltinProviders();   // 刷新 hasKey + fields（保留用户输入，仅清 apiKey）
      vs.clearProviderApiKey(key);
    } else {
      vs.setProviderBusy(key, null);
      vs.setProviderStatus(key, "err", d.error || t('update.fail'));
    }
  }

  async function clearBuiltin(key) {
    if (!await pfs()?.ui?.confirm?.({
      title: t('confirm.title'),
      message: t('confirm.clear_builtin', { label: BUILTIN_META[key]?.label || key }),
      danger: true,
    })) return;
    const vs = getUiIsland("settings");
    if (!vs || !vs.isAvailable()) {
      console.warn("[models] vueSettings unavailable, clearBuiltin skipped");
      return;
    }
    const r = await fetch("/api/models/clear-builtin", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: key }),
    });
    const d = await r.json();
    if (d.ok) {
      vs.setProviderStatus(key, "ok", t('settings.cleared'));
      await loadModels();
      await loadBuiltinProviders();   // 刷新 hasKey=false，setProviders 检测到清除会重置 fields
    }
  }

  async function deleteCustom(provider) {
    if (!await pfs()?.ui?.confirm?.({
      title: t('confirm.title'), message: t('confirm.delete_custom'), danger: true,
    })) return;
    await fetch("/api/models/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    await Promise.all([loadModels(), loadBuiltinProviders()]);
  }

  function toggleThinkBudget(key) {
    const cb  = $(`pthink-${key}`);
    const row = $(`pbudget-row-${key}`);
    if (cb && row) row.style.display = cb.checked ? "flex" : "none";
  }

  document.addEventListener("click", event => {
    if (!event.target.closest("#model-picker, #model-picker-trigger, #model-picker-trigger-sidebar")) {
      closeModelPicker();
    }
  });
  const modelSearch = $("model-picker-search");
  modelSearch?.addEventListener("input", onModelSearch);
  modelSearch?.addEventListener("keydown", onModelKeyDown);
  window.addEventListener("resize", closeModelPicker);
  window.addEventListener("scroll", closeModelPicker, true);

export const models = Object.freeze({
    loadModels, onModelChange, loadBuiltinProviders, renderBuiltinProviders, renderCustomList,
    editCustomModel, addCustomModel, toggleAddCustom, saveBuiltin, clearBuiltin, deleteCustom,
    toggleThinkBudget, testModel, openModelPicker, closeModelPicker, renderModelPicker,
    selectModel, refreshModelPickerLabels,
});
