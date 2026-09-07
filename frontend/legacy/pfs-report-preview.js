import { closeOverlay, openOverlay } from "../core/overlay.js";

const REPORT_ENDPOINT = "/api/pfs/fixture";
const state = {
  loading: false,
  exporting: false,
  result: null,
  sourceValue: "fixture",
  sources: [],
  question: "",
  deliveryFormat: "",
  deliveryArtifacts: [],
  currentRun: null,
};

function translate(key, fallback, vars) {
  return globalThis.PFS?.i18n?.t?.(key, vars) || fallback;
}

function currentLanguage() {
  return globalThis.PFS?.i18n?.getLang?.() || "zh";
}

function makeElement(tag, className = "", content = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined && content !== null) element.textContent = String(content);
  return element;
}

function formatNumber(value) {
  return new Intl.NumberFormat(currentLanguage() === "en" ? "en-US" : "zh-CN").format(
    Number(value) || 0,
  );
}

function controls() {
  return {
    source: document.getElementById("pfs-report-source-select"),
    worksheet: document.getElementById("pfs-report-worksheet"),
    value: document.getElementById("pfs-report-value-column"),
    date: document.getElementById("pfs-report-date-column"),
    dimension: document.getElementById("pfs-report-dimension-column"),
  };
}

function setStatus(status) {
  const element = document.getElementById("pfs-report-status");
  if (!element) return;
  element.dataset.state = status;
  const labels = {
    idle: translate("pfs_report.idle", "未运行"),
    running: translate("pfs_report.running", "计算中"),
    completed: translate("pfs_report.completed", "已完成"),
    error: translate("pfs_report.error", "分析预览加载失败"),
    canceled: translate("pfs_report.canceled", "已取消"),
  };
  element.textContent = labels[status] || status;
}

function renderLoading() {
  const content = document.getElementById("pfs-report-content");
  if (!content) return;
  content.replaceChildren(
    makeElement("div", "pfs-report-loading", translate("pfs_report.loading", "正在读取样例…")),
  );
}

function renderCanceled() {
  const content = document.getElementById("pfs-report-content");
  if (!content) return;
  content.replaceChildren(
    makeElement(
      "div",
      "pfs-report-canceled",
      translate("pfs_report.canceled_message", "分析已取消，未生成结论或证据记录。"),
    ),
  );
}

function setCancelAvailability(available) {
  const button = document.getElementById("pfs-report-cancel");
  if (!button) return;
  button.hidden = !available;
  button.classList.toggle("hidden", !available);
  button.disabled = !available || Boolean(state.currentRun?.cancelRequested);
  button.setAttribute("aria-disabled", String(button.disabled));
  button.setAttribute("aria-busy", state.currentRun?.cancelRequested ? "true" : "false");
}

function createRun(prefix, sid = "", serverCancelable = false) {
  const suffix =
    globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const run = {
    runId: `${prefix}-${suffix}`.slice(0, 120),
    sid,
    serverCancelable,
    cancelRequested: false,
    controller: new AbortController(),
  };
  state.currentRun = run;
  setCancelAvailability(true);
  return run;
}

function finishRun(run) {
  if (state.currentRun !== run) return;
  state.currentRun = null;
  setCancelAvailability(false);
}

function wasCanceled(error, run) {
  return (
    run?.cancelRequested ||
    error?.name === "AbortError" ||
    error?.code === "pfs_analysis_canceled"
  );
}

function waitMilliseconds(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function requestServerCancel(run) {
  const retryDelays = [0, 50, 100, 200, 400, 800];
  for (const delay of retryDelays) {
    if (delay) await waitMilliseconds(delay);
    if (state.currentRun !== run || !run.cancelRequested) return false;
    try {
      const response = await fetch(
        `/api/session/${encodeURIComponent(run.sid)}/pfs/runs/${encodeURIComponent(run.runId)}/cancel`,
        { method: "POST", keepalive: true },
      );
      if (response.ok) return true;
      if (response.status !== 404) return false;
    } catch {
      return false;
    }
  }
  return false;
}

async function cancelCurrentRun() {
  const run = state.currentRun;
  if (!run || run.cancelRequested) return;
  run.cancelRequested = true;
  setCancelAvailability(true);
  if (run.serverCancelable && run.sid) {
    const accepted = await requestServerCancel(run);
    if (!accepted) {
      run.cancelRequested = false;
      setCancelAvailability(state.currentRun === run);
      return;
    }
  }
  run.controller.abort();
  state.result = null;
  setExportAvailability(false);
  setDeliveryAvailability(false);
  renderCanceled();
  setStatus("canceled");
}

function renderIdle() {
  const content = document.getElementById("pfs-report-content");
  const source = document.getElementById("pfs-report-source");
  if (content) {
    content.replaceChildren(
      makeElement(
        "div",
        "pfs-report-loading",
        translate("pfs_report.ready_to_run", "数据源已切换，请运行分析。"),
      ),
    );
  }
  if (source) {
    source.textContent = "";
    source.title = "";
  }
  setInterpretation("");
  setExportStatus("");
  setExportAvailability(false);
  resetDelivery();
}

function errorGuidance(code, message) {
  const guidance = {
    worksheet_required: translate("pfs_report.error_worksheet_required", "请选择要分析的工作表，然后重新运行。"),
    worksheet_not_found: translate("pfs_report.error_worksheet_missing", "工作表已变化，请刷新数据源后重新选择。"),
    source_header_missing: translate("pfs_report.error_header_missing", "请在第一行补充完整且不为空的字段名。"),
    source_has_no_rows: translate("pfs_report.error_no_rows", "请在表头下至少保留一行有效数据。"),
    source_columns_missing: translate("pfs_report.error_columns_missing", "请确认指标列、日期列和分组列都存在于数据源中。"),
    source_date_invalid: translate("pfs_report.error_date_invalid", "请将日期列统一为 YYYY-MM 或 YYYY-MM-DD，并修正无效日期。"),
    date_filter_invalid: translate("pfs_report.error_date_filter_invalid", "请检查起止日期格式，并确保开始日期不晚于结束日期。"),
    date_range_invalid: translate("pfs_report.error_date_filter_invalid", "请检查起止日期格式，并确保开始日期不晚于结束日期。"),
    metric_value_not_numeric: translate("pfs_report.error_not_numeric", "请清理指标列中的文本或改选数值列。"),
    delivery_table_missing: translate("pfs_report.error_delivery_table", "当前数据源没有可交付的表，请重新选择有效工作表。"),
    upload_file_too_large: translate("pfs_report.error_file_too_large", "请压缩文件或拆分后再上传，单文件上限为 100 MB。"),
    model_not_configured: translate("pfs_report.error_model_not_configured", "请先在模型设置中配置 DeepSeek 或其他可用模型，再运行 Agent。"),
    delivery_generation_failed: translate("pfs_report.error_delivery_generation", "请检查输出目录权限后重试。"),
    delivery_table_ambiguous: translate("pfs_report.error_delivery_ambiguous", "请先明确选择一个工作表，再生成交付物。"),
  };
  return guidance[code] ? `${message} ${guidance[code]}` : message;
}

function renderError(message, code = "") {
  const content = document.getElementById("pfs-report-content");
  if (!content) return;
  const box = makeElement("div", "pfs-report-error");
  box.append(
    makeElement(
      "strong",
      "pfs-report-error-title",
      translate("pfs_report.error", "分析预览加载失败"),
    ),
    makeElement("span", "pfs-report-error-message", errorGuidance(code, message)),
  );
  content.replaceChildren(box);
}

function setInterpretation(text) {
  const box = document.getElementById("pfs-report-interpretation");
  const value = document.getElementById("pfs-report-interpretation-text");
  if (!box || !value) return;
  value.textContent = text || "";
  box.hidden = !text;
}

function setQuestionLoading(loading) {
  const button = document.getElementById("pfs-report-question-run");
  if (!button) return;
  button.disabled = loading;
  button.setAttribute("aria-busy", loading ? "true" : "false");
}

function setExportAvailability(available) {
  for (const format of ["json", "csv"]) {
    const button = document.getElementById(`pfs-report-export-${format}`);
    if (!button) continue;
    button.disabled = !available || state.exporting;
    button.setAttribute("aria-disabled", String(button.disabled));
  }
}

function setExportStatus(message = "", status = "") {
  const element = document.getElementById("pfs-report-export-status");
  if (!element) return;
  element.textContent = message;
  element.dataset.state = status;
}

function deliveryLabel(format) {
  return (
    {
      xlsx: translate("pfs_report.delivery_excel", "Excel 数据"),
      docx: translate("pfs_report.delivery_word", "Word 文档"),
      pptx: translate("pfs_report.delivery_ppt", "PPT 演示"),
      dashboard: translate("pfs_report.delivery_dashboard", "分析看板"),
    }[format] || format
  );
}

function setDeliveryAvailability(available) {
  for (const button of document.querySelectorAll("[data-pfs-delivery-format]")) {
    const isCurrent = button.dataset.pfsDeliveryFormat === state.deliveryFormat;
    button.disabled = !available || Boolean(state.deliveryFormat);
    button.setAttribute("aria-disabled", String(button.disabled));
    button.setAttribute("aria-busy", isCurrent ? "true" : "false");
  }
}

function setDeliveryStatus(message = "", status = "") {
  const element = document.getElementById("pfs-report-delivery-status");
  if (!element) return;
  element.textContent = message;
  element.dataset.state = status;
}

function renderDeliveryArtifacts() {
  const list = document.getElementById("pfs-report-delivery-list");
  if (!list) return;
  list.replaceChildren();
  for (const artifact of state.deliveryArtifacts) {
    const item = makeElement("div", "pfs-report-delivery-item");
    const details = makeElement("div", "pfs-report-delivery-item-copy");
    details.append(
      makeElement("strong", "", artifact.label || artifact.name || "PFS 交付物"),
      makeElement("span", "", artifact.name || artifact.type || ""),
    );
    const lineage = [
      artifact.run_id ? "运行 " + artifact.run_id : "",
      artifact.included_rows != null ? "覆盖 " + artifact.included_rows + " 行" : "",
      artifact.worksheet ? "工作表 " + artifact.worksheet : "",
      artifact.source_sha256 ? "快照 " + (artifact.source_sha256 || "").slice(0, 12) + "…" : "",
    ].filter(Boolean).join(" · ");
    if (lineage) details.append(makeElement("small", "pfs-report-delivery-trace", lineage));
    const link = makeElement(
      "a",
      "btn-sm btn-sm-ghost",
      artifact.action === "open"
        ? translate("pfs_report.delivery_open", "打开")
        : translate("pfs_report.delivery_download", "下载"),
    );
    link.href = artifact.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    item.append(details, link);
    list.append(item);
  }
  list.hidden = state.deliveryArtifacts.length === 0;
}

function resetDelivery() {
  state.deliveryFormat = "";
  state.deliveryArtifacts = [];
  setDeliveryStatus("");
  renderDeliveryArtifacts();
  setDeliveryAvailability(false);
}

function selectedSource() {
  const value = controls().source?.value || state.sourceValue || "fixture";
  if (value === "fixture") return null;
  return state.sources.find((source) => `csv:${source.source_id}` === value) || null;
}

function selectedUploadedSource() {
  return selectedSource();
}

function selectedWorksheet() {
  return controls().worksheet?.value || "";
}

function selectedColumns() {
  const source = selectedSource();
  const worksheet = selectedWorksheet();
  const sheet = source?.worksheets?.find((item) => item.name === worksheet);
  return sheet?.columns?.length ? sheet.columns : source?.columns || [];
}

function toggleDeterministicMode() {
  const pfs = globalThis.PFS;
  const appState = pfs?.state;
  if (!appState) return false;
  appState.pfsDeterministicMode = !appState.pfsDeterministicMode;
  const button = document.getElementById("pfs-chat-mode-toggle");
  if (button) {
    button.setAttribute("aria-pressed", String(appState.pfsDeterministicMode));
    button.classList.toggle("is-active", appState.pfsDeterministicMode);
    button.title = appState.pfsDeterministicMode
      ? translate("pfs_report.chat_mode_on", "聊天将按分析口径运行")
      : translate("pfs_report.chat_mode_off", "关闭分析口径模式");
    button.setAttribute("aria-label", button.title);
  }
  return appState.pfsDeterministicMode;
}

function chooseDefault(columns, preferred, fallbackIndex = 0) {
  if (columns.includes(preferred)) return preferred;
  return columns[fallbackIndex] || columns[0] || "";
}

function fillSelect(select, columns, preferred) {
  if (!select) return;
  const previous = select.value;
  select.replaceChildren();
  for (const column of columns) select.append(makeElement("option", "", column));
  const selected = columns.includes(previous) ? previous : chooseDefault(columns, preferred);
  if (selected) select.value = selected;
}

function updateColumnControls() {
  const current = controls();
  const source = selectedSource();
  const columns = source ? selectedColumns() : ["month", "region", "sales_amount"];
  fillSelect(current.value, columns, chooseDefault(columns, "sales_amount", columns.length - 1));
  fillSelect(current.date, columns, chooseDefault(columns, "month", 0));
  fillSelect(
    current.dimension,
    columns,
    chooseDefault(columns, "region", Math.min(1, columns.length - 1)),
  );
  const isFixture = !source;
  for (const select of [current.value, current.date, current.dimension]) {
    if (select) select.disabled = isFixture;
  }
  const note = document.getElementById("pfs-report-note");
  if (note) {
    note.textContent = isFixture
      ? translate(
          "pfs_report.fixture_note",
          "该预览使用本地固定 fixture，不调用模型、不连接外部数据源。",
        )
      : translate(
          "pfs_report.upload_note",
          "该分析读取当前会话中上传的 CSV/XLSX，只执行显式指标列的确定性求和，不调用模型。",
        );
  }
}

function updateWorksheetControl() {
  const source = selectedSource();
  const select = controls().worksheet;
  const container = document.getElementById("pfs-report-worksheet-control");
  if (!select || !container) return;
  const worksheets = Array.isArray(source?.worksheets) ? source.worksheets : [];
  const previous = select.value;
  select.replaceChildren();
  for (const sheet of worksheets) {
    const label = sheet.error
      ? `${sheet.name} · ${translate("pfs_report.worksheet_unavailable", "不可分析")}`
      : sheet.validation_error
        ? `${sheet.name} · ${translate("pfs_report.needs_repair", "需修复")}`
      : `${sheet.name} · ${formatNumber(sheet.row_count)} ${translate("pfs_report.rows", "行")}`;
    const option = makeElement("option", "", label);
    option.value = sheet.name;
    option.disabled = Boolean(sheet.error || !sheet.columns?.length);
    select.append(option);
  }
  const usable = worksheets.filter((sheet) => !sheet.error && sheet.columns?.length);
  if (usable.some((sheet) => sheet.name === previous)) select.value = previous;
  else if (usable.length) select.value = usable[0].name;
  container.hidden = worksheets.length === 0;
  select.disabled = worksheets.length === 0 || usable.length === 0;
}

function renderSourceOptions() {
  const select = controls().source;
  if (!select) return;
  const previous = state.sourceValue || select.value || "fixture";
  select.replaceChildren();
  select.append(makeElement("option", "", "PFS 固定示例销售数据"));
  select.options[0].value = "fixture";
  for (const source of state.sources) {
    const repairLabel = source.validation_error
      ? ` · ${translate("pfs_report.needs_repair", "需修复")}`
      : "";
    const option = makeElement(
      "option",
      "",
      `${source.name || source.file_name} · ${formatNumber(source.row_count)} rows${repairLabel}`,
    );
    option.value = `csv:${source.source_id}`;
    select.append(option);
  }
  const valid = ["fixture", ...state.sources.map((source) => `csv:${source.source_id}`)];
  select.value = valid.includes(previous) ? previous : "fixture";
  state.sourceValue = select.value;
  updateWorksheetControl();
  updateColumnControls();
}

async function loadSources() {
  const sid = globalThis.PFS?.state?.SID;
  if (!sid) {
    state.sources = [];
    renderSourceOptions();
    return;
  }
  try {
    const response = await fetch(`/api/session/${encodeURIComponent(sid)}/pfs/sources`, {
      cache: "no-store",
    });
    const payload = await response.json();
    state.sources =
      response.ok && payload.ok && Array.isArray(payload.sources) ? payload.sources : [];
  } catch (_) {
    state.sources = [];
  }
  renderSourceOptions();
}

function renderSummary(result) {
  const summary = makeElement("div", "pfs-report-summary");
  const metric = result.metric || {};
  const snapshot = result.snapshot || {};
  const request = result.request || {};
  const dateRange = `${request.date_from || snapshot.min_date || "—"} → ${request.date_to || snapshot.max_date || "—"}`;
  const cards = [
    [
      metric.label || translate("pfs_report.total", "指标合计"),
      formatNumber(result.total),
      metric.formula || "",
    ],
    [
      translate("pfs_report.coverage", "数据覆盖"),
      `${formatNumber(snapshot.row_count)} ${translate("pfs_report.rows", "纳入行数")}`,
      dateRange,
    ],
    [
      translate("pfs_report.snapshot", "数据快照"),
      snapshot.file_name || "—",
      `${translate("pfs_report.hash", "内容哈希")}: ${(snapshot.content_sha256 || "").slice(0, 16)}…`,
    ],
  ];
  for (const [label, value, note] of cards) {
    const card = makeElement("div", "pfs-report-summary-card");
    card.append(makeElement("span", "pfs-report-summary-label", label));
    card.append(makeElement("strong", "pfs-report-summary-value", value));
    card.append(makeElement("small", "pfs-report-summary-note", note));
    summary.append(card);
  }
  return summary;
}

function renderGroups(result) {
  const section = makeElement("section", "pfs-report-section");
  section.append(
    makeElement("h3", "pfs-report-section-title", translate("pfs_report.groups", "分组结果")),
  );
  const table = makeElement("table", "pfs-report-table");
  const head = makeElement("thead");
  const headRow = makeElement("tr");
  for (const label of [
    translate("pfs_report.rank", "排名"),
    result.metric?.dimension || translate("pfs_report.dimension", "分组"),
    translate("pfs_report.value", "金额"),
  ])
    headRow.append(makeElement("th", "", label));
  head.append(headRow);
  const body = makeElement("tbody");
  for (const group of result.groups || []) {
    const row = makeElement("tr");
    row.append(makeElement("td", "pfs-report-rank", group.rank));
    row.append(makeElement("td", "", group.dimension));
    row.append(makeElement("td", "pfs-report-number", formatNumber(group.value)));
    body.append(row);
  }
  table.append(head, body);
  section.append(table);
  return section;
}

function claimStatusLabel(status) {
  const labels = {
    supported: translate("pfs_report.claim_supported", "数据支持"),
    supported_with_caveat: translate(
      "pfs_report.claim_supported_with_caveat",
      "数据支持（含提示）",
    ),
    needs_review: translate("pfs_report.claim_needs_review", "待业务确认"),
    pass: translate("pfs_report.claim_pass", "通过"),
    fail: translate("pfs_report.claim_fail", "未通过"),
    unverified: translate("pfs_report.claim_unverified", "待补充来源"),
  };
  return labels[status] || String(status || "—");
}

function renderClaims(result) {
  const section = makeElement("section", "pfs-report-section");
  section.append(
    makeElement("h3", "pfs-report-section-title", translate("pfs_report.claims", "关键结论")),
  );
  const list = makeElement("ul", "pfs-report-claims");
  const claims = Array.isArray(result.claims) ? result.claims : [];
  if (!claims.length)
    list.append(
      makeElement("li", "pfs-report-empty", translate("pfs_report.no_claims", "暂无分析结论")),
    );
  for (const claim of claims) {
    const item = makeElement("li", "pfs-report-claim");
    const head = makeElement("div", "pfs-report-claim-head");
    head.append(makeElement("strong", "pfs-report-claim-text", claim.text || claim.claim || "—"));
    head.append(
      makeElement(
        "span",
        "pfs-report-claim-status",
        claimStatusLabel(claim.business_status || claim.status),
      ),
    );
    const evidenceIds = Array.isArray(claim.evidence_ids) ? claim.evidence_ids : [];
    const meta = makeElement(
      "small",
      "pfs-report-claim-meta",
      `${translate("pfs_report.confidence", "置信度")} ${Math.round((Number(claim.confidence) || 0) * 100)}% · ${evidenceIds.length} ${translate("pfs_report.evidence_count", "条来源")}`,
    );
    item.append(head, meta);
    const details = makeElement("div", "pfs-report-claim-details");
    if (claim.claim_id)
      details.append(makeElement("code", "pfs-report-claim-detail", claim.claim_id));
    if (claim.basis || claim.verification_reason)
      details.append(
        makeElement(
          "span",
          "pfs-report-claim-detail",
          `${translate("pfs_report.basis", "计算说明")}: ${claim.basis || claim.verification_reason}`,
        ),
      );
    if (evidenceIds.length)
      details.append(
        makeElement(
          "span",
          "pfs-report-claim-detail",
          `${translate("pfs_report.source_refs", "来源引用")}: ${evidenceIds.join("、")}`,
        ),
      );
    if (details.childElementCount) item.append(details);
    list.append(item);
  }
  section.append(list);
  return section;
}
function renderEvidence(result) {
  const section = makeElement("section", "pfs-report-section");
  section.append(
    makeElement("h3", "pfs-report-section-title", translate("pfs_report.evidence", "来源留痕")),
  );
  const list = makeElement("div", "pfs-report-evidence-list");
  for (const evidence of result.evidence || []) {
    const card = makeElement("div", "pfs-report-evidence");
    card.append(makeElement("strong", "pfs-report-evidence-id", evidence.evidence_id || "—"));
    card.append(
      makeElement(
        "span",
        "pfs-report-evidence-kind",
        `${evidence.kind || "evidence"} · ${evidence.source_id || "—"}`,
      ),
    );
    card.append(makeElement("code", "pfs-report-evidence-locator", evidence.locator || "—"));
    if (evidence.source_url || evidence.title || evidence.publisher || evidence.captured_at)
      card.append(makeElement("small", "pfs-report-evidence-details",
        [evidence.title, evidence.publisher, evidence.published_at ? "发布 " + evidence.published_at : "",
          evidence.captured_at ? "抓取 " + evidence.captured_at : "", evidence.file_name ? "文件 " + evidence.file_name : "",
          evidence.worksheet ? "工作表 " + evidence.worksheet : "",
          evidence.included_rows != null ? "纳入 " + evidence.included_rows + " 行" : "",
          evidence.source_url].filter(Boolean).join(" · ")));
    if (evidence.content_sha256 || evidence.locator || evidence.columns?.length)
      card.append(makeElement("small", "pfs-report-evidence-details",
        [evidence.content_sha256 ? "SHA-256 " + evidence.content_sha256 : "",
          evidence.locator ? "定位 " + evidence.locator : "",
          evidence.columns?.length ? "字段 " + evidence.columns.join(", ") : ""].filter(Boolean).join(" · ")));
    card.append(makeElement("p", "pfs-report-evidence-excerpt", evidence.excerpt || ""));
    list.append(card);
  }
  section.append(list);
  return section;
}

function renderWarnings(result) {
  if (!(result.warnings || []).length) return null;
  const section = makeElement("section", "pfs-report-section pfs-report-warnings");
  section.append(
    makeElement("h3", "pfs-report-section-title", translate("pfs_report.warning", "注意")),
  );
  const list = makeElement("ul");
  for (const warning of result.warnings) list.append(makeElement("li", "", warning));
  section.append(list);
  return section;
}

function renderResult(result) {
  const content = document.getElementById("pfs-report-content");
  const source = document.getElementById("pfs-report-source");
  if (!content) return;
  const children = [
    renderSummary(result),
    renderGroups(result),
    renderClaims(result),
    renderEvidence(result),
  ];
  const warnings = renderWarnings(result);
  if (warnings) children.push(warnings);
  content.replaceChildren(...children);
  if (source) {
    source.textContent = `${translate("pfs_report.source", "来源")}: ${result.snapshot?.file_name || "—"} · ${result.metric?.version || ""}`;
    source.title = result.snapshot?.content_sha256 || "";
  }
  setExportAvailability(true);
}

async function load() {
  if (state.loading) return;
  let run = null;
  state.loading = true;
  state.question = "";
  setExportAvailability(false);
  setExportStatus("");
  resetDelivery();
  setStatus("running");
  renderLoading();
  setInterpretation("");
  try {
    await loadSources();
    const sourceValue = controls().source?.value || "fixture";
    let response;
    if (sourceValue === "fixture") {
      run = createRun("pfs-fixture");
      response = await fetch(REPORT_ENDPOINT, {
        cache: "no-store",
        signal: run.controller.signal,
      });
    } else {
      const sid = globalThis.PFS?.state?.SID;
      const source = selectedSource();
      if (!sid || !source) throw new Error("未找到可分析的上传数据源");
      run = createRun("pfs-report", sid, true);
      response = await fetch(`/api/session/${encodeURIComponent(sid)}/pfs/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: run.controller.signal,
        body: JSON.stringify({
          source_id: source.source_id,
          run_id: run.runId,
          worksheet: selectedWorksheet(),
          value_column: controls().value?.value,
          date_column: controls().date?.value,
          dimension: controls().dimension?.value,
          label: controls().value?.value,
        }),
      });
    }
    const payload = await response.json();
    if (!response.ok || !payload.ok || !payload.result) {
      const error = new Error(payload.error || `HTTP ${response.status}`);
      error.code = payload.code || "";
      throw error;
    }
    state.result = payload.result;
    state.sourceValue = sourceValue;
    renderResult(state.result);
    setDeliveryAvailability(true);
    setStatus("completed");
  } catch (error) {
    state.result = null;
    setExportAvailability(false);
    if (wasCanceled(error, run)) {
      renderCanceled();
      setStatus("canceled");
    } else {
      renderError(String(error?.message || error), error?.code);
      setStatus("error");
    }
  } finally {
    state.loading = false;
    finishRun(run);
  }
}

async function loadFromQuestion() {
  if (state.loading) return;
  let run = null;
  const source = selectedSource();
  const input = document.getElementById("pfs-report-question-input");
  const question = input?.value?.trim();
  const sid = globalThis.PFS?.state?.SID;
  if (!source || !sid) {
    renderError(
      translate("pfs_report.question_requires_source", "请先上传并选择一个 CSV 或 XLSX 数据源。"),
    );
    setStatus("error");
    return;
  }
  if (!question) {
    renderError(translate("pfs_report.question_required", "请先输入一个明确的分析问题。"));
    setStatus("error");
    input?.focus();
    return;
  }
  state.loading = true;
  state.question = "";
  setExportAvailability(false);
  setExportStatus("");
  resetDelivery();
  setQuestionLoading(true);
  setStatus("running");
  renderLoading();
  setInterpretation("");
  try {
    run = createRun("pfs-question", sid, true);
    const response = await fetch(`/api/session/${encodeURIComponent(sid)}/pfs/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: run.controller.signal,
      body: JSON.stringify({
        source_id: source.source_id,
        worksheet: selectedWorksheet(),
        question,
        run_id: run.runId,
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok || !payload.result) {
      const error = new Error(payload.error || `HTTP ${response.status}`);
      error.code = payload.code || "";
      throw error;
    }
    state.result = payload.result;
    state.sourceValue = `csv:${source.source_id}`;
    state.question = question;
    setInterpretation(payload.interpretation?.interpretation);
    renderResult(state.result);
    setDeliveryAvailability(true);
    setStatus("completed");
  } catch (error) {
    state.result = null;
    setExportAvailability(false);
    if (wasCanceled(error, run)) {
      renderCanceled();
      setStatus("canceled");
    } else {
      renderError(String(error?.message || error), error?.code);
      setStatus("error");
    }
  } finally {
    state.loading = false;
    setQuestionLoading(false);
    finishRun(run);
  }
}

function exportPayload(format) {
  const result = state.result || {};
  const request = result.request || {};
  const metric = result.metric || {};
  const source = selectedSource();
  const payload = {
    format,
    source_id: source?.source_id || "fixture",
    worksheet: source ? selectedWorksheet() : "",
    run_id: request.run_id || `pfs-export-${Date.now()}`,
    date_from: request.date_from || "",
    date_to: request.date_to || "",
    value_column: metric.value_column || "sales_amount",
    date_column: metric.date_column || "month",
    dimension: metric.dimension || "region",
    label: metric.label || "销售额",
  };
  if (state.question) payload.question = state.question;
  return payload;
}

function responseFilename(response, fallback) {
  const header = response.headers.get("Content-Disposition") || "";
  const match = header.match(/filename="([A-Za-z0-9_.-]+)"/);
  return match?.[1] || fallback;
}

async function exportReport(format = "json") {
  if (!state.result || state.exporting || !["json", "csv"].includes(format)) return;
  const source = selectedSource();
  const sid = globalThis.PFS?.state?.SID;
  if (source && !sid) {
    setExportStatus(translate("pfs_report.export_failed", "导出失败：当前会话不可用"), "error");
    return;
  }
  state.exporting = true;
  setExportAvailability(true);
  setExportStatus(translate("pfs_report.exporting", "正在准备下载…"), "running");
  try {
    const endpoint = source
      ? `/api/session/${encodeURIComponent(sid)}/pfs/export`
      : "/api/pfs/export";
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(exportPayload(format)),
    });
    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      const error = new Error(errorPayload.error || `HTTP ${response.status}`);
      error.code = errorPayload.code || "";
      throw error;
    }
    const blob = await response.blob();
    const fallback = `pfs-report.${format}`;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = responseFilename(response, fallback);
    link.rel = "noopener";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    setExportStatus(translate("pfs_report.export_ready", "已下载"), "success");
  } catch (error) {
    setExportStatus(
      `${translate("pfs_report.export_failed", "导出失败")}: ${String(error?.message || error)}`,
      "error",
    );
  } finally {
    state.exporting = false;
    setExportAvailability(Boolean(state.result));
  }
}

async function generateDelivery(format) {
  if (
    !state.result ||
    state.deliveryFormat ||
    !["xlsx", "docx", "pptx", "dashboard"].includes(format)
  )
    return;
  const source = selectedSource();
  const sid = globalThis.PFS?.state?.SID;
  if (source && !sid) {
    setDeliveryStatus(translate("pfs_report.export_failed", "导出失败：当前会话不可用"), "error");
    return;
  }
  state.deliveryFormat = format;
  setDeliveryAvailability(true);
  setDeliveryStatus(
    translate("pfs_report.delivery_running", `正在生成 ${deliveryLabel(format)}…`, {
      format: deliveryLabel(format),
    }),
    "running",
  );
  try {
    const endpoint = source
      ? `/api/session/${encodeURIComponent(sid)}/pfs/deliver`
      : "/api/pfs/deliver";
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(exportPayload(format)),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok || !Array.isArray(payload.artifacts)) {
      const error = new Error(payload.error || `HTTP ${response.status}`);
      error.code = payload.code || "";
      throw error;
    }
    const knownUrls = new Set(state.deliveryArtifacts.map((artifact) => artifact.url));
    for (const artifact of payload.artifacts) {
      if (artifact?.url && !knownUrls.has(artifact.url)) {
        state.deliveryArtifacts.push(artifact);
        knownUrls.add(artifact.url);
      }
    }
    renderDeliveryArtifacts();
    setDeliveryStatus(
      translate("pfs_report.delivery_ready", `${deliveryLabel(format)} 已生成`, {
        format: deliveryLabel(format),
      }),
      "success",
    );
  } catch (error) {
    setDeliveryStatus(
      `${translate("pfs_report.delivery_failed", "生成失败")}: ${errorGuidance(error?.code, String(error?.message || error))}`,
      "error",
    );
  } finally {
    state.deliveryFormat = "";
    setDeliveryAvailability(Boolean(state.result));
  }
}

function open() {
  openOverlay("ov-pfs-report");
  void load();
}

function init() {
  const pfs = globalThis.PFS || {};
  pfs.pfsReport = {
    load,
    loadFromQuestion,
    cancelCurrentRun,
    exportReport,
    generateDelivery,
    open,
    close: () => closeOverlay("ov-pfs-report"),
    selectedUploadedSource,
    toggleDeterministicMode,
  };
  globalThis.PFS = pfs;
  const source = controls().source;
  if (source)
    source.addEventListener("change", () => {
      state.sourceValue = source.value;
      state.result = null;
      updateWorksheetControl();
      updateColumnControls();
      renderIdle();
      setStatus("idle");
    });
  controls().worksheet?.addEventListener("change", () => {
    state.result = null;
    updateColumnControls();
    renderIdle();
    setStatus("idle");
  });
  document.getElementById("pfs-report-question-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void loadFromQuestion();
    }
  });
  for (const button of document.querySelectorAll("[data-pfs-delivery-format]")) {
    button.addEventListener("click", () => void generateDelivery(button.dataset.pfsDeliveryFormat));
  }
  document.addEventListener("langchange", () => {
    setStatus(state.result ? "completed" : "idle");
    updateColumnControls();
    if (state.result) {
      renderResult(state.result);
    }
  });
  setExportAvailability(false);
  setDeliveryAvailability(false);
  setCancelAvailability(false);
  renderSourceOptions();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}
