// PFS bootstrap + global event delegation.
// Replaces all HTML inline on* handlers. Modules under /static/js/modules/ register
// themselves through the PFS runtime namespace.

// Global 401 handler — redirect to /login when session expires (cloud mode)
(function () {
  const _fetch = window.fetch;
  if (!_fetch || _fetch.__authWrapped) return;
  const wrapped = function (input, init) {
    return _fetch.call(this, input, init).then(function (resp) {
      if (resp.status === 401) {
        resp
          .clone()
          .json()
          .then(function (d) {
            if (d && d.needs_auth) window.location.href = "/login";
          })
          .catch(function () {});
      }
      return resp;
    });
  };
  wrapped.__authWrapped = true;
  window.fetch = wrapped;
})();

import * as appSettings from "./app_settings.js";
import * as autosave from "./autosave.js";
import * as datasource from "./datasource.js";
import * as jobHistory from "./job_history.js";
import { sidebar } from "../features/sidebar.js";
import { renderMd } from "./markdown.js";
import * as preview from "./preview.js";
import * as sessions from "./sessions.js";
import { runUpdate } from "./update.js";

// If a second stale chat-app.js copy is somehow running, skip all listener
// registration here so there are no duplicate event handlers. This guard must
// be local to app.js because ES module imports run before chat-app.js body code.
if (globalThis.__pfsAppDelegationRegistered) {
  console.warn("[app.js] duplicate registration skipped");
} else {
  globalThis.__pfsAppDelegationRegistered = true;

  (function () {
    const pfs = () => globalThis.PFS;
    const { $ } = pfs().dom;
    const state = pfs().state;

    function syncSessionStatus() {
      const el = $("session-status-text");
      if (!el) return;
      el.textContent = state.sessionName || "新会话";
    }

    function setSessionName(name, filename = "") {
      state.sessionName = String(name || "").trim() || "新会话";
      if (filename !== undefined) state.loadedSessionFilename = filename || "";
      syncSessionStatus();
    }

    pfs().sidebar = { ...sidebar, setSessionName, syncSessionStatus };

    // ── Action registry (data-action="name[:arg]") ─────────────────────
    // Resolved at click time so modules registered after app.js still work.
    const ACTIONS = {
      // Slash / chat
      onSendOrStop: () => pfs().chatStream.onSendOrStop(),
      clearCmd: () => pfs().slash.clearCmd(),
      clearSkill: () => pfs().skills.clearSkill(),
      openSkillPicker: (el) => {
        sidebar.rememberSurfaceTrigger("skills", el);
        pfs().skills.open();
      },
      closeSkillPicker: () => sidebar.closePanel("skills"),
      closeSkillModal: () => pfs().skills?.closeSkillModal?.(),
      openModelPicker: (el) => pfs().models.openModelPicker(el),
      closeModelPicker: () => pfs().models.closeModelPicker(),
      openModelSettings: () => {
        pfs().models.closeModelPicker();
        return globalThis.openOverlay("ov-settings");
      },
      fillHint: (el) => pfs().slash.fillHint(el),
      toggleComposerExpanded: () => {
        const shell = document.querySelector(".composer-shell");
        const button = $("composer-expand-btn");
        const input = $("msg-input");
        const expanded = !shell.classList.contains("expanded");
        shell.classList.toggle("expanded", expanded);
        button.setAttribute("aria-expanded", String(expanded));
        button.title = t(expanded ? "composer.collapse" : "composer.expand");
        button.setAttribute("aria-label", button.title);
        if (expanded) input.style.height = "220px";
        else pfs().slash.autoResize(input);
        input.focus();
      },
      newChat: () => pfs().chatStream.newChat(),
      retryStream: () => pfs().chatStream.retryLast?.(),
      togglePfsChatMode: () => pfs().pfsReport?.toggleDeterministicMode?.(),

      // Independent side panels (skills / knowledge / mcp) — island loading via openSidePanel
      openPanel: (el, name) => {
        sidebar.rememberSurfaceTrigger(name, el);
        if (name === "skills") {
          pfs().skills.open();
        } else {
          globalThis.openSidePanel?.(name);
        }
      },
      closePanel: (_el, name) => sidebar.closePanel(name),
      // KB inline form
      kbCancelForm: () => {
        pfs().knowledge?.kbCancelForm?.();
        sidebar.closeKbInlineForm();
      },

      // Overlay
      openOverlay: (_el, id) => window.openOverlay(id),
      closeOverlay: (_el, id) => pfs().overlay.closeOverlay(id),

      // Sidebar / header
      disconnectSrc: () => datasource.disconnectSrc(),
      openSaveWarehouseDialog: () => datasource.openSaveWarehouseDialog(),
      loadWarehouseList: () => datasource.loadWarehouseList(),
      saveDataWarehouse: () => datasource.saveDataWarehouse(),
      loadDataWarehouse: (el) => datasource.loadDataWarehouse(el.dataset.filename, el.dataset.name),
      deleteDataWarehouse: (el, event) => {
        event?.stopPropagation?.();
        datasource.deleteDataWarehouse(el.dataset.filename, el.dataset.name);
      },
      openSchemaView: () => preview.openSchemaView(),
      openPfsReport: () => pfs().pfsReport?.open(),
      refreshPfsReport: () => pfs().pfsReport?.load(),
      analyzePfsQuestion: () => pfs().pfsReport?.loadFromQuestion(),
      cancelPfsReport: () => pfs().pfsReport?.cancelCurrentRun(),
      exportPfsReport: (_el, format) => pfs().pfsReport?.exportReport(format),
      openJobHistory: () => jobHistory.open(),
      toggleFocusMode: () => sidebar.toggleFocusMode(),
      toggleSidebarRail: () => sidebar.toggleSidebarRail(),
      openSaveDialog: () => sessions.openSaveDialog(),
      loadSavedList: () => sessions.loadSavedList(),
      setSidebarNav: (_el, nav) => sidebar.setSidebarNav(nav || "agent"),
      openSidebarDrawer: (el, tab) => sidebar.openSidebarDrawer(tab || "sessions", el),
      closeSidebarDrawer: () => sidebar.closeSidebarDrawer(),
      closeSidebarSurfaces: () => sidebar.closeSidebarSurfaces(),
      setDrawerTab: (_el, tab) => sidebar.setDrawerTab(tab || "sessions"),
      openMcpSettings: (el) => {
        sidebar.rememberSurfaceTrigger("mcp", el);
        pfs().mcp.openMcpSettings();
      },
      loadMcpServers: () => pfs().mcp.loadMcpServers(),
      toggleLang: () => pfs().i18n.setLang(pfs().i18n.getLang() === "zh" ? "en" : "zh"),
      toggleTheme: () => pfs().theme.toggleTheme(),
      togglePromptSuggestion: (el) => appSettings.setPromptSuggestionEnabled(el.checked),

      // Data source modals
      uploadXl: () => datasource.uploadXl(),
      loadSample: () => datasource.loadSample(),
      connectDB: () => datasource.connectDB(),
      connectAPI: () => datasource.connectAPI(),
      cloudLogout: () => {
        fetch("/api/auth/logout", { method: "POST" }).finally(() => {
          window.location.href = "/login";
        });
      },

      // Settings — model providers
      toggleAddCustom: () => pfs().models.toggleAddCustom(),
      addCustomModel: () => pfs().models.addCustomModel(),
      saveBuiltin: (_el, key) => pfs().models.saveBuiltin(key),
      clearBuiltin: (_el, key) => pfs().models.clearBuiltin(key),
      editCustom: (_el, key) => pfs().models.editCustomModel(key),
      deleteCustom: (_el, key) => pfs().models.deleteCustom(key),
      toggleThinkBudget: (_el, key) => pfs().models.toggleThinkBudget(key),
      testProvider: (_el, key) => pfs().models.testModel(key),
      toggleAcBudget: () => {
        const cb = $("ac-think");
        const row = $("ac-budget-row");
        if (cb && row) row.classList.toggle("hidden", !cb.checked);
      },

      // Saved sessions
      saveSession: () => sessions.saveSession(),
      loadSession: (el) => sessions.loadSavedSession(el.dataset.filename, el.dataset.name),
      cancelLoadSession: () => sessions.cancelLoadSession(),
      renameSession: (el) => sessions.renameSavedSession(el.dataset.filename, el.dataset.name),
      submitRenameSession: () => sessions.submitRenameSession(),
      deleteSession: (el) => sessions.deleteSavedSession(el.dataset.filename, el.dataset.name),
      confirmDeleteSession: () => sessions.confirmDeleteSavedSession(),

      // Update modal
      runUpdate: () => runUpdate(),

      // Workspace (workdir mount)
      openWorkspace: () => pfs().workspace.openModal(),
      openTeams: () => pfs().teams.openPanel(),
      mountWorkspace: () => pfs().workspace.doMount(),
      pickWorkdir: () => pfs().workspace.pickWorkdir(),

      // MCP server form
      toggleMcpAddForm: () => pfs().mcp.toggleMcpAddForm(),
      addMcpServer: () => pfs().mcp.addMcpServer(),
      switchMcpTab: (_el, tab) => pfs().mcp.switchMcpTab(tab),
      scanLocalMcp: () => pfs().mcp.scanLocalMcp(),
      parseMcpConfig: () => pfs().mcp.parseMcpConfig(),
      updateMcpCmdPreview: () => pfs().mcp.updateMcpCmdPreview(),

      // Knowledge base
      kbOpenForm: (_el, type) => {
        pfs().knowledge.kbOpenForm(type);
        sidebar.openKbInlineForm();
      },
      kbRefresh: (_el, type) => pfs().knowledge.kbRefresh(type),
      kbSwitchTab: (el, tab) => pfs().knowledge.kbSwitchTab(tab, el),
      kbOpenImport: () => pfs().knowledge.kbOpenImport(),
      kbLoadFiles: () => pfs().knowledge.kbLoadFiles(),
      kbCancelImport: () => pfs().knowledge.kbCancelImport(),
      kbConfirmImport: () => pfs().knowledge.kbConfirmImport(),
      kbSubmitForm: () => pfs().knowledge.kbSubmitForm(),
      kbPickFile: () => $("kb-file-input").click(),
      kbOnFileSelect: (el, event) =>
        pfs().knowledge?.kbOnFileSelect?.(event || { target: el }),
      kbDeleteFile: (el) => pfs().knowledge?.kbDeleteFile?.(el.dataset.filename || ""),
      kbPreviewRemove: (el) => pfs().knowledge?.kbPreviewRemove?.(Number(el.dataset.idx)),
      kbPreviewUpdate: (el) => pfs().knowledge?.kbPreviewUpdate?.(el),

      // Temporary per-session prompt
      tpSaveRaw: () => pfs().tempPrompt.tpSave(false),
      tpRefine: () => pfs().tempPrompt.tpSave(true),
      tpToggle: () => pfs().tempPrompt.tpToggle(),
      tpClear: () => pfs().tempPrompt.tpClear(),
      tpUpdateCount: () => pfs().tempPrompt.tpUpdateCount(),

      // Data-source modal sub-controls
      toggleApiAuthValue: () => datasource.toggleApiAuthValue(),

      // Sidebar — open the user-facing Instruction.md doc in a modal,
      // rendered with marked + DOMPurify (same pipeline as chat messages).
      openInstruction: async () => {
        const body = $("instruction-body");
        window.openOverlay("ov-instruction");
        // Fetch on every open so doc edits show up without a page reload.
        // The desktop build serves this from a local server that may have just
        // resumed from a backgrounded tab, so retry once before surfacing an
        // error and keep the message actionable instead of a raw TypeError.
        const loadOnce = async () => {
          const r = await fetch("/api/instruction", { cache: "no-store" });
          return r.json();
        };
        try {
          let d;
          try {
            d = await loadOnce();
          } catch (_firstError) {
            await new Promise((resolve) => setTimeout(resolve, 400));
            d = await loadOnce();
          }
          if (d.ok && d.markdown) {
            body.innerHTML = renderMd(d.markdown);
          } else {
            body.innerHTML = `<div class="instruction-loading">${pfs().dom.esc(
              d.error || "Instruction.md not found",
            )}</div>`;
          }
        } catch (e) {
          body.innerHTML =
            `<div class="instruction-loading">` +
            `${pfs().dom.esc(t("modal.instruction.load_fail") || "文档加载失败，请稍后重试。")}` +
            `<br><small>${pfs().dom.esc(String(e))}</small></div>`;
        }
      },

      // Sidebar — "Add data source" dropdown
      toggleAddSrc: () => sidebar.toggleAddSrc(),

      // Sidebar — datasource row click. Behaviour depends on connection state:
      //   connected    → open data preview modal
      //   disconnected → open the "Add data source" dropdown
      openDataSource: (el) => sidebar.openDataSource(el),
    };

    sidebar.initAddSourceDropdown();
    sidebar.initPanelKeyClose();
    sidebar.initSidebarRail();

    // Click delegation
    document.addEventListener("click", (e) => {
      const el = e.target.closest("[data-action]");
      if (!el) return;
      if (el.dataset.sidebarNav) sidebar.setSidebarNav(el.dataset.sidebarNav);
      const [name, ...args] = el.dataset.action.split(":");
      const fn = ACTIONS[name];
      if (!fn) {
        console.warn("[PFS] unknown action:", name);
        return;
      }
      try {
        const result = fn(el, ...args, e);
        if (result && typeof result.then === "function") {
          result.catch((error) => {
            console.error(`[PFS] action ${name} failed:`, error);
            pfs().overlay?.toast?.("该功能暂时无法打开，请重试", "err");
          });
        }
      } catch (error) {
        console.error(`[PFS] action ${name} failed:`, error);
        pfs().overlay?.toast?.("该功能暂时无法打开，请重试", "err");
      }
    });

    // Direct fallback for "添加自定义模型" toggle — some users report event delegation not firing
    // for this element, so attach a direct listener as well.
    const _directToggle = () => {
      const el = document.querySelector(".add-custom-toggle[data-action='toggleAddCustom']");
      if (!el || el.dataset._pfsDirectToggleBound) return;
      el.dataset._pfsDirectToggleBound = "1";
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        try {
          pfs().models.toggleAddCustom();
        } catch (err) {
          console.error("[PFS] direct toggleAddCustom error:", err);
        }
      });
    };
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", _directToggle);
    } else {
      _directToggle();
    }

    // Change delegation (selects / checkboxes / file inputs)
    document.addEventListener("change", (e) => {
      const el = e.target.closest("[data-change]");
      if (!el) return;
      const [name, ...args] = el.dataset.change.split(":");
      const fn = ACTIONS[name];
      if (!fn) {
        console.warn("[PFS] unknown change action:", name);
        return;
      }
      fn(el, ...args);
    });

    // Input delegation (for live previews/counters)
    document.addEventListener("input", (e) => {
      const el = e.target.closest("[data-input]");
      if (!el) return;
      const [name, ...args] = el.dataset.input.split(":");
      const fn = ACTIONS[name];
      if (!fn) {
        console.warn("[PFS] unknown input action:", name);
        return;
      }
      fn(el, ...args);
    });

    // Drag & drop on knowledge base import zone
    const dropZone = document.getElementById("kb-drop-zone");
    if (dropZone) {
      dropZone.addEventListener("dragover", (e) => e.preventDefault());
      dropZone.addEventListener(
        "drop",
        (e) => pfs().knowledge.kbOnDrop && pfs().knowledge.kbOnDrop(e),
      );
    }
    const kbFileInput = document.getElementById("kb-file-input");
    if (kbFileInput && !kbFileInput.dataset.change) {
      kbFileInput.addEventListener(
        "change",
        (e) => pfs().knowledge.kbOnFileSelect && pfs().knowledge.kbOnFileSelect(e),
      );
    }

    // Temp-prompt textarea — live character counter
    const tpTextarea = document.getElementById("tp-textarea");
    if (tpTextarea) {
      tpTextarea.addEventListener(
        "input",
        () => pfs().tempPrompt && pfs().tempPrompt.tpUpdateCount(),
      );
    }

    // Textarea — slash popup driver
    const msgInput = document.getElementById("msg-input");
    if (msgInput) {
      msgInput.addEventListener("input", (e) => {
        pfs().chatStream?.onComposerInput?.(e);
        pfs().slash.onInput(e);
      });
      msgInput.addEventListener("keydown", (e) => pfs().slash.onKeyDown(e));

      document.getElementById("feishu-session-indicator")?.addEventListener("click", () => {
        const input = document.getElementById("msg-input");
        if (!input) return;
        input.value = "/robot ";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        pfs().chatStream?.onSendOrStop?.();
      });
    }

    // Model select change
    const modelSel = document.getElementById("model-sel");
    if (modelSel) {
      modelSel.addEventListener("change", (e) =>
        pfs().models.onModelChange(e.currentTarget.value),
      );
    }
    const sidebarModelSel = document.getElementById("model-sel-sidebar");
    if (sidebarModelSel) {
      sidebarModelSel.addEventListener("change", (e) =>
        pfs().models.onModelChange(e.currentTarget.value),
      );
    }

    const workspacePermission = document.getElementById("workspace-permission-select");
    if (workspacePermission) {
      workspacePermission.addEventListener("change", (e) => {
        pfs().workspace.onPermissionChange(e.currentTarget.value);
      });
    }

    // Custom dropdown for workspace permission in composer toolbar
    const permWrap = document.getElementById("composer-permission-wrap");
    if (permWrap) {
      const trigger = permWrap.querySelector(".composer-permission-trigger");
      const menu = permWrap.querySelector(".composer-permission-menu");
      const options = permWrap.querySelectorAll(".composer-permission-option");
      const label = document.getElementById("composer-permission-label");
      const nativeSelect = document.getElementById("workspace-permission-select");

      const updatePermissionUI = (value) => {
        const active = permWrap.querySelector(".composer-permission-option.active");
        if (active) active.classList.remove("active");
        const next = permWrap.querySelector(`.composer-permission-option[data-value="${value}"]`);
        if (next) {
          next.classList.add("active");
          if (label) label.textContent = next.textContent.trim();
        }
        if (nativeSelect) nativeSelect.value = value;
      };

      if (trigger) {
        trigger.addEventListener("click", (e) => {
          e.preventDefault();
          if (trigger.disabled) return;
          const willOpen = !permWrap.classList.contains("open");
          if (willOpen) {
            // Position the fixed menu above the trigger
            const r = trigger.getBoundingClientRect();
            if (menu) {
              menu.style.left = `${r.left}px`;
              menu.style.bottom = `${window.innerHeight - r.top + 6}px`;
            }
          }
          permWrap.classList.toggle("open", willOpen);
          trigger.setAttribute("aria-expanded", String(willOpen));
        });
      }

      options.forEach((opt) => {
        opt.addEventListener("click", () => {
          const value = opt.dataset.value;
          updatePermissionUI(value);
          permWrap.classList.remove("open");
          if (trigger) trigger.setAttribute("aria-expanded", "false");
          if (nativeSelect) {
            nativeSelect.value = value;
            nativeSelect.dispatchEvent(new Event("change", { bubbles: true }));
          }
        });
      });

      document.addEventListener("click", (e) => {
        if (!permWrap.contains(e.target)) {
          permWrap.classList.remove("open");
          if (trigger) trigger.setAttribute("aria-expanded", "false");
        }
      });
      // Close menu on scroll/resize since fixed positioning won't track the trigger
      window.addEventListener(
        "scroll",
        () => {
          if (permWrap.classList.contains("open")) {
            permWrap.classList.remove("open");
            if (trigger) trigger.setAttribute("aria-expanded", "false");
          }
        },
        { passive: true },
      );
      window.addEventListener("resize", () => {
        if (permWrap.classList.contains("open")) {
          permWrap.classList.remove("open");
          if (trigger) trigger.setAttribute("aria-expanded", "false");
        }
      });

      // Sync custom dropdown UI from workspace.js — listen to "perm:sync" (UI-only),
      // NOT "change" (which triggers onPermissionChange and may open the mount modal).
      const syncFromNative = (e) => {
        const value =
          (e && e.detail && e.detail.permission) || (nativeSelect && nativeSelect.value);
        if (value) updatePermissionUI(value);
      };
      if (nativeSelect) {
        nativeSelect.addEventListener("perm:sync", syncFromNative);
        syncFromNative();
      }
    }

    // Excel file picker change
    const xlFile = document.getElementById("xl-file");
    if (xlFile) {
      xlFile.addEventListener("change", () => datasource.onXlFile());
    }

    // API auth-type select change
    const apiAuthType = document.getElementById("api-auth-type");
    if (apiAuthType) {
      apiAuthType.addEventListener("change", () => datasource.toggleApiAuthValue());
    }

    // MCP transport radios
    document.querySelectorAll('input[name="mcp-transport"]').forEach((r) => {
      r.addEventListener(
        "change",
        () => window.onMcpTransportChange && window.onMcpTransportChange(),
      );
    });

    // Language change — re-sync dynamic UI state.
    document.addEventListener("langchange", () => {
      if (!state.srcConnected) {
        $("src-name").textContent = t("sidebar.disconnected");
        $("src-hint").textContent = t("sidebar.hint.noconn");
        $("hdr-sub").textContent = t("header.subtitle");
      } else {
        $("src-hint").textContent = t(state.srcHintKey);
        $("hdr-sub").textContent = t("connected_to", { name: state.srcName });
      }
      for (const sel of [$("model-sel"), $("model-sel-sidebar")]) {
        if (sel && sel.options.length > 0 && sel.options[0].value === "") {
          sel.options[0].textContent = t("sidebar.model_placeholder");
        }
      }
      if (pfs().models?.renderModelPicker) {
        pfs().models.renderModelPicker();
        pfs().models.refreshModelPickerLabels?.();
      }
      const sendBtn = $("send-btn");
      if (sendBtn && !sendBtn.classList.contains("stopping")) {
        sendBtn.title = t("send.title");
        sendBtn.setAttribute("aria-label", sendBtn.title);
      }
      const expandButton = $("composer-expand-btn");
      if (expandButton) {
        expandButton.title = t(
          expandButton.getAttribute("aria-expanded") === "true"
            ? "composer.collapse"
            : "composer.expand",
        );
        expandButton.setAttribute("aria-label", expandButton.title);
      }
      if (pfs().chatStream?.syncComposerPlaceholder) {
        pfs().chatStream.syncComposerPlaceholder();
      } else {
        const input = $("msg-input");
        if (input) input.placeholder = t("input.placeholder");
      }
      const savedEmpty = document.querySelector("#saved-list .saved-empty");
      if (savedEmpty) savedEmpty.textContent = t("saved_empty");
      // Re-sync workspace sidebar status text if unmounted (mounted shows path segment, no need to update)
      if (!document.getElementById("ws-dot")?.classList.contains("on")) {
        const wsTxt = $("ws-status-text");
        if (wsTxt) wsTxt.textContent = t("workspace.unmounted");
      }
      if (pfs().slash.isSlashOpen()) pfs().slash.buildSlashPopup();
      if (pfs().skills?.isOpen()) pfs().skills.render();
    });

    // ── Bootstrap ─────────────────────────────────────────────────────
    (async () => {
      // Try to reuse the previous session (so autosave + in-memory history survive refresh)
      const prevSID = window.PFS.storage.get("session_id");
      let sessionRestored = false;
      if (prevSID) {
        try {
          const ping = await fetch(`/api/session/${prevSID}/ping`);
          if (ping.ok) {
            const { alive } = await ping.json();
            if (alive) {
              state.SID = prevSID;
              sessionRestored = true;
            }
          }
          if (!sessionRestored) {
            const hasJobs = await jobHistory.hasHistory(prevSID);
            if (hasJobs) {
              state.SID = prevSID;
              sessionRestored = true;
            }
          }
        } catch (_) {
          /* session gone — fall through to new */
        }
      }

      if (!sessionRestored) {
        const r = await fetch("/api/session/new", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ memory_enabled: state.memoryEnabled !== false }),
        });
        state.SID = (await r.json()).session_id;
      }
      window.PFS.storage.set("session_id", state.SID);
      window.PFS.storage.sessionSet("session_id", state.SID);
      await pfs().chatStream?.loadFeishuConversationStatus?.();
      setSessionName(state.sessionName || "新会话", state.loadedSessionFilename || "");
      await Promise.all([pfs().slash.loadCommands(), pfs().skills.loadSkills()]);
      await jobHistory.init(state.SID);
      await pfs().models.loadModels();
      await sessions.loadSavedList();
      await datasource.loadWarehouseList();
      await datasource.loadDatasourceConfigs();
      // Reflect packaged builds without bundled MCP resources immediately;
      // external MCP configuration remains available through the settings panel.
      if (pfs().mcp) await pfs().mcp.loadMcpServers();
      // Restore any sources that survived a page reload (new session = empty, that's fine)
      try {
        const sr = await fetch(`/api/session/${state.SID}/sources`);
        const sd = await sr.json();
        if (sd.sources && sd.sources.length > 0) {
          // Always render the list, regardless of active state
          datasource.renderSourceList(sd.sources);
          const active = sd.sources.find((s) => s.active);
          if (active) {
            // At least one source is active → connected
            datasource.setSrc(active.name, "src.hint.file", true);
          } else {
            // Sources exist but none active → still show list, connected=false
            datasource.setSrc(sd.sources[0].name, "src.hint.file", false);
          }
        }
      } catch {
        /* non-critical */
      }

      // Sync workspace mount state (sidebar dot + modal Vue state)
      if (pfs().workspace) pfs().workspace.loadStatus();

      // Check for a resumable auto-save from the previous session
      autosave.checkAutosaveOnLoad();
    })();
  })();
}
