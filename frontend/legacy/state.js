// Centralized mutable state shared across compatibility modules.
// The chat entry installs the PFS namespace before this compatibility module.
(function () {
  const pfs = globalThis.PFS || {};
  globalThis.PFS = pfs;
  const storage = pfs.storage;
  const state = {
    SID: null,
    sessionName: "新会话",
    loadedSessionFilename: "",
    srcConnected: false,
    srcName: "",
    srcHintKey: 'sidebar.hint.noconn',
    schemaText: "",
    // Multi-source: [{id, name, type, active}]
    sources: [],
    isStreaming: false,
    _stopRequested: false,
    askUserPending: false,
    activeTurn: null,
    silentContinuation: false,
    pendingMessages: [],
    editingQueuedId: "",
    promptSuggestionEnabled: storage.get("prompt_suggestion_enabled", "1") !== "0",
    teamsEnabled: storage.get("teams_enabled", "0") === "1",
    autoMatchSkill: storage.get("auto_match_skill", "1") !== "0",
    memoryEnabled: storage.get("memory_enabled", "1") !== "0",
    promptSuggestionRequestId: 0,
    promptSuggestionText: "",
    _applyingPromptSuggestion: false,
    activeCommand: "",
    activeSkill: "",
    feishuConversation: { connected: false, chatName: "", chatId: "" },
    slashPopupIndex: 0,
    skillPickerIndex: 0,
    tokenState: { promptTokens: 0, totalInput: 0, totalOutput: 0, contextWindow: null },
    modelConfigs: {},
    _streamReader: null,
    _editingCustomProvider: null,
    _previewData: null,
    _previewCache: {},
    _previewSid: null,
    // Table explicitly selected from Data Preview for subsequent Agent turns.
    analysisContext: null,
    // Opt-in bridge for explicit deterministic PFS report questions.
    pfsDeterministicMode: false,
    _modalResizing: false,
  };

  pfs.state = state;
  window.PFS = pfs;
})();
