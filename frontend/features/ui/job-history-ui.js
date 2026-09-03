import { registerUiIsland } from "../../core/ui-registry.js";
import { iconVNode } from "../../core/icons.js";

// Durable Job history panel (B5), mounted on first use.
export function mountJobHistoryUi() {
  const pfs = globalThis.PFS || {};
  globalThis.PFS = pfs;
  const Vue = window.Vue;
  const root = document.getElementById("job-history-root");
  if (!root || !Vue?.h || !Vue?.render || !Vue?.reactive) {
    registerUiIsland("jobHistory", null);
    return;
  }

  const { h, render, reactive } = Vue;
  const state = reactive({
    open: false, loading: false, error: "", jobs: [], registeredArtifacts: [], focusJobId: "",
    audit: null, auditLoading: false, auditError: "",
    auditFilters: { type: "all", status: "all", query: "", from: "", to: "" },
  });
  let callbacks = {};

  function text(key, fallback, params) {
    if (!window.t) return fallback;
    const value = window.t(key, params);
    return value && value !== key ? value : fallback;
  }

  function normalizeSteps(events) {
    const steps = new Map();
    for (const event of (Array.isArray(events) ? events : [])) {
      const id = event.step_id || `step-${event.step_number || steps.size + 1}`;
      const current = steps.get(id) || {
        id, number: Number(event.step_number) || steps.size + 1,
        tool: event.tool || "unknown", display: event.display || event.tool || "unknown",
        status: "running", elapsed: null, error: "",
      };
      if (event.type === "conversation_step_finished") {
        current.status = event.status || "succeeded";
        current.elapsed = Number(event.elapsed_seconds) || 0;
        current.error = event.error || "";
      }
      steps.set(id, current);
    }
    return [...steps.values()].sort((a, b) => a.number - b.number);
  }

  function normalize(job) {
    return {
      id: job.id || job.job_id || "",
      type: job.type || job.job_type || "",
      label: job.label || "",
      status: job.status || "created",
      progress: Number(job.progress) || 0,
      message: job.message || "",
      error: job.error || "",
      result: job.result || null,
      activation: job.activation || job.result?.activation || null,
      workspace: job.workspace || null,
      workspaceId: job.workspace_id || "",
      artifacts: Array.isArray(job.artifacts) ? [...job.artifacts] : [],
      steps: normalizeSteps(job.steps),
      createdAt: job.created_at || "",
      updatedAt: job.updated_at || "",
      finishedAt: job.finished_at || "",
      cancelPending: false,
      expanded: Boolean(job.expanded),
    };
  }

  function findJob(jobId) {
    return state.jobs.find(job => job.id === jobId);
  }

  function sortJobs() {
    state.jobs.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
  }

  function setJobs(jobs, nextCallbacks = {}) {
    const old = new Map(state.jobs.map(job => [job.id, job]));
    state.jobs = (Array.isArray(jobs) ? jobs : []).map(raw => {
      const job = normalize(raw);
      const previous = old.get(job.id);
      if (previous?.artifacts?.length) job.artifacts = previous.artifacts;
      if (previous) job.expanded = previous.expanded;
      return job;
    });
    callbacks = nextCallbacks || callbacks;
    sortJobs();
    draw();
  }

  function applyEvent(ev) {
    if (!ev?.job_id) return false;
    let job = findJob(ev.job_id);
    if (!job) {
      job = normalize({
        id: ev.job_id,
        type: ev.job_type,
        label: ev.label,
        status: ev.status,
        created_at: ev.created_at,
      });
      state.jobs.unshift(job);
    }
    if (ev.job_type) job.type = ev.job_type;
    if (ev.label) job.label = ev.label;
    if (ev.status) job.status = ev.status;
    if (ev.progress !== undefined) job.progress = Number(ev.progress) || 0;
    if (ev.message !== undefined) job.message = ev.message || "";
    if (ev.type === "conversation_activation" && ev.activation) job.activation = ev.activation;
    if (ev.created_at && !job.createdAt) job.createdAt = ev.created_at;
    if (ev.type === "conversation_step_started") {
      if (!job.steps.some(step => step.id === ev.step_id)) {
        job.steps.push({
          id: ev.step_id, number: Number(ev.step_number) || job.steps.length + 1,
          tool: ev.tool || "unknown", display: ev.display || ev.tool || "unknown",
          status: "running", elapsed: null, error: "",
        });
      }
    } else if (ev.type === "conversation_step_finished") {
      const step = job.steps.find(item => item.id === ev.step_id);
      if (step) {
        step.status = ev.status || "succeeded";
        step.elapsed = Number(ev.elapsed_seconds) || 0;
        step.error = ev.error || "";
      }
    }
    if (ev.type === "artifact_created" && ev.artifact) {
      const signature = JSON.stringify(ev.artifact);
      if (!job.artifacts.some(item => JSON.stringify(item) === signature)) {
        job.artifacts.push(ev.artifact);
      }
    } else if (ev.type === "job_done") {
      job.status = ev.status || "succeeded";
      job.progress = 100;
      job.result = ev.result;
      if (ev.result?.activation) job.activation = ev.result.activation;
      job.cancelPending = false;
    } else if (ev.type === "job_error") {
      job.status = ev.status || "failed";
      job.error = ev.error || "Job failed";
      job.cancelPending = false;
    } else if (ev.type === "job_canceled") {
      job.status = ev.status || "canceled";
      job.cancelPending = false;
    }
    sortJobs();
    draw();
    return true;
  }

  function formatTime(value) {
    if (!value) return "";
    try { return new Date(value).toLocaleString(); } catch (_) { return value; }
  }

  function formatDuration(value) {
    const milliseconds = Number(value || 0);
    if (!milliseconds) return "—";
    if (milliseconds < 1000) return `${milliseconds} ms`;
    if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)} 秒`;
    return `${(milliseconds / 60000).toFixed(1)} 分钟`;
  }

  function formatCost(cost) {
    if (!cost || cost.amount == null) return "费用未知";
    return `${Number(cost.amount).toFixed(6)} ${cost.currency || "USD"}${cost.estimated ? "（估算）" : ""}`;
  }

  function auditKindLabel(kind) {
    return {
      job: "任务", run: "分析", model: "模型", tool: "工具", retry: "重试",
      error: "错误", approval: "人工裁决", artifact: "交付物", claim: "结论",
      evidence: "证据", cost: "成本",
    }[kind] || kind || "事件";
  }

  async function applyAuditFilters(next = null) {
    if (next) state.auditFilters = { ...state.auditFilters, ...next };
    if (!callbacks.onAuditFilters) return;
    await callbacks.onAuditFilters({ ...state.auditFilters });
  }

  function renderAuditMetric(label, value, tone = "") {
    return h("div", { class: `job-audit-metric${tone ? ` is-${tone}` : ""}` }, [
      h("span", null, label),
      h("strong", null, String(value ?? "—")),
    ]);
  }

  function renderAuditFilters() {
    const filters = state.auditFilters;
    const select = (label, key, options) => h("label", { class: "job-audit-filter" }, [
      h("span", null, label),
      h("select", {
        value: filters[key],
        onChange: event => { filters[key] = event.target.value; },
      }, options.map(([value, name]) => h("option", { value, key: value }, name))),
    ]);
    return h("form", {
      class: "job-audit-filters",
      onSubmit: event => { event.preventDefault(); applyAuditFilters(); },
    }, [
      select("对象", "type", [
        ["all", "全部对象"], ["job", "任务"], ["run", "分析运行"], ["model", "模型调用"],
        ["tool", "工具调用"], ["retry", "重试"], ["error", "错误"], ["approval", "人工裁决"],
        ["artifact", "交付物"], ["claim", "结论"], ["evidence", "证据"], ["cost", "成本"],
      ]),
      select("状态", "status", [
        ["all", "全部状态"], ["created", "已创建"], ["queued", "排队中"],
        ["running", "运行中"], ["succeeded", "已完成"], ["failed", "失败"],
        ["canceled", "已取消"], ["pending", "待处理"], ["conflict", "有冲突"],
        ["unsupported", "无证据"],
      ]),
      h("label", { class: "job-audit-filter job-audit-filter-query" }, [
        h("span", null, "关键词"),
        h("input", {
          type: "search", value: filters.query, placeholder: "任务、结论、证据或编号",
          onInput: event => { filters.query = event.target.value; },
        }),
      ]),
      h("label", { class: "job-audit-filter" }, [
        h("span", null, "开始时间"),
        h("input", { type: "datetime-local", value: filters.from,
          onInput: event => { filters.from = event.target.value; } }),
      ]),
      h("label", { class: "job-audit-filter" }, [
        h("span", null, "结束时间"),
        h("input", { type: "datetime-local", value: filters.to,
          onInput: event => { filters.to = event.target.value; } }),
      ]),
      h("div", { class: "job-audit-filter-actions" }, [
        h("button", { class: "btn-sm btn-sm-ghost", type: "button", onClick: () => {
          state.auditFilters = { type: "all", status: "all", query: "", from: "", to: "" };
          applyAuditFilters();
        } }, "重置筛选"),
        h("button", { class: "btn-sm btn-sm-primary", type: "submit" }, "应用筛选"),
      ]),
    ]);
  }

  function renderAuditTimelineItem(item) {
    const metadata = item.metadata || {};
    const details = [
      item.job_id ? `任务 ${item.job_id}` : "",
      item.run_id && item.run_id !== item.job_id ? `运行 ${item.run_id}` : "",
      item.claim_id ? `Claim ${item.claim_id}` : "",
      item.evidence_id ? `Evidence ${item.evidence_id}` : "",
      item.artifact_id ? `Artifact ${item.artifact_id}` : "",
      item.duration_ms != null ? `耗时 ${formatDuration(item.duration_ms)}` : "",
      metadata.model ? `${metadata.provider || "模型"} / ${metadata.model}` : "",
      metadata.input_tokens != null ? `输入 ${Number(metadata.input_tokens || 0)} Token` : "",
      metadata.output_tokens != null ? `输出 ${Number(metadata.output_tokens || 0)} Token` : "",
      metadata.attempt != null ? `第 ${metadata.attempt}/${metadata.max_retries || "?"} 次重试` : "",
      metadata.wait_seconds != null ? `等待 ${Number(metadata.wait_seconds).toFixed(1)} 秒` : "",
      metadata.reason ? `原因 ${metadata.reason}` : "",
      metadata.message || "",
    ].filter(Boolean);
    return h("li", { class: `job-audit-event is-${item.kind} is-${item.status}`, key: item.id }, [
      h("span", { class: "job-audit-event-rail", "aria-hidden": "true" }),
      h("div", { class: "job-audit-event-content" }, [
        h("div", { class: "job-audit-event-head" }, [
          h("span", { class: `job-audit-kind is-${item.kind}` }, auditKindLabel(item.kind)),
          h("strong", null, item.title || item.type),
          h("time", null, formatTime(item.created_at) || "时间未记录"),
        ]),
        details.length ? h("div", { class: "job-audit-event-meta" }, details.join(" · ")) : null,
        item.error ? h("div", { class: "job-audit-event-error" }, item.error) : null,
      ]),
    ]);
  }

  function renderAuditClaim(claim, mode) {
    const links = Array.isArray(claim.evidence_links) ? claim.evidence_links : [];
    const evidenceById = new Map((claim.evidence || []).map(item => [item.evidence_id, item]));
    return h("article", { class: `job-audit-claim is-${mode}`, key: claim.claim_id }, [
      h("div", { class: "job-audit-claim-head" }, [
        h("span", { class: `job-audit-claim-state is-${mode}` }, mode === "conflict" ? "冲突" : "无证据"),
        h("code", null, claim.claim_id),
      ]),
      h("strong", null, claim.text || "未命名结论"),
      claim.verification_reason ? h("p", null, claim.verification_reason) : null,
      claim.human_decision ? h("div", { class: "job-audit-decision" }, `人工决定：${claim.human_decision}`) : null,
      links.length ? h("div", { class: "job-audit-relations" }, links.map(link => {
        const evidence = evidenceById.get(link.evidence_id) || {};
        return h("div", { class: `job-audit-relation is-${link.relation}`, key: link.evidence_id }, [
          h("span", null, link.relation === "refutes" ? "反驳" : "支持"),
          h("div", null, [
            h("code", null, link.evidence_id),
            h("p", null, evidence.snippet || evidence.title || "证据详情暂不可用"),
            evidence.file_name || evidence.worksheet
              ? h("small", null, [evidence.file_name, evidence.worksheet, evidence.locator].filter(Boolean).join(" · "))
              : null,
          ]),
        ]);
      })) : h("p", { class: "job-audit-claim-empty" }, "这条结论还没有关联 Evidence，暂不能核验。"),
    ]);
  }

  function renderAuditCenter() {
    if (state.auditLoading && !state.audit) {
      return h("section", { class: "job-audit-center", "aria-busy": "true" }, [
        h("div", { class: "job-audit-skeleton" }, "正在汇总任务、证据与成本链路…"),
      ]);
    }
    if (state.auditError && !state.audit) {
      return h("section", { class: "job-audit-center" }, [
        h("div", { class: "job-audit-error" }, [
          h("strong", null, "审计信息暂时无法读取"),
          h("span", null, `${state.auditError}。任务历史仍可继续使用。`),
        ]),
      ]);
    }
    const audit = state.audit;
    if (!audit) return null;
    const summary = audit.summary || {};
    const timeline = Array.isArray(audit.timeline) ? audit.timeline : [];
    const conflicts = Array.isArray(audit.conflicts) ? audit.conflicts : [];
    const uncovered = Array.isArray(audit.uncovered_claims) ? audit.uncovered_claims : [];
    const warnings = Array.isArray(audit.warnings) ? audit.warnings : [];
    return h("section", { class: "job-audit-center" }, [
      h("div", { class: "job-audit-title-row" }, [
        h("div", null, [
          h("h2", null, "本会话审计中心"),
          h("p", null, "从任务运行回到工具、模型、交付物、结论与证据。"),
        ]),
        state.auditLoading ? h("span", { class: "job-audit-refreshing" }, "正在更新…") : null,
      ]),
      h("div", { class: "job-audit-metrics" }, [
        renderAuditMetric("任务", summary.jobs || 0),
        renderAuditMetric("成功", summary.succeeded_jobs || 0, "success"),
        renderAuditMetric("失败", summary.failed_jobs || 0, summary.failed_jobs ? "danger" : ""),
        renderAuditMetric("交付物", summary.artifacts || 0),
        renderAuditMetric("Claim", summary.claims || 0),
        renderAuditMetric("无证据", summary.uncovered_claims || 0, summary.uncovered_claims ? "warning" : ""),
        renderAuditMetric("冲突", summary.conflicts || 0, summary.conflicts ? "danger" : ""),
        renderAuditMetric("模型调用", summary.model_calls || 0),
        renderAuditMetric("Token", Number(summary.input_tokens || 0) + Number(summary.output_tokens || 0)),
        renderAuditMetric("累计耗时", formatDuration(summary.total_duration_ms)),
        renderAuditMetric("费用", formatCost(summary.cost)),
      ]),
      renderAuditFilters(),
      state.auditError ? h("div", { class: "job-audit-inline-error" }, `筛选更新失败：${state.auditError}`) : null,
      warnings.length ? h("div", { class: "job-audit-warnings" }, warnings.map((warning, index) => h("p", { key: index }, warning))) : null,
      h("div", { class: "job-audit-layout" }, [
        h("section", { class: "job-audit-timeline-panel" }, [
          h("div", { class: "job-audit-section-head" }, [
            h("h3", null, "执行时间线"),
            h("span", null, `${timeline.length} 条匹配记录`),
          ]),
          timeline.length
            ? h("ol", { class: "job-audit-timeline" }, timeline.map(renderAuditTimelineItem))
            : h("div", { class: "job-audit-empty" }, [
              h("strong", null, "当前筛选没有匹配记录"),
              h("span", null, "重置筛选，或先运行一次数据分析任务。"),
            ]),
        ]),
        h("aside", { class: "job-audit-governance" }, [
          h("div", { class: "job-audit-section-head" }, [
            h("h3", null, "核验与裁决"),
            h("span", null, `${conflicts.length + uncovered.length} 条待处理`),
          ]),
          conflicts.length ? h("div", { class: "job-audit-claim-group" }, [
            h("h4", null, "证据冲突"),
            ...conflicts.slice(0, 20).map(claim => renderAuditClaim(claim, "conflict")),
          ]) : null,
          uncovered.length ? h("div", { class: "job-audit-claim-group" }, [
            h("h4", null, "没有证据的结论"),
            ...uncovered.slice(0, 20).map(claim => renderAuditClaim(claim, "unsupported")),
          ]) : null,
          !conflicts.length && !uncovered.length ? h("div", { class: "job-audit-empty is-compact" }, [
            h("strong", null, "没有待裁决项"),
            h("span", null, "当前已登记 Claim 均未发现冲突或证据缺口。"),
          ]) : null,
        ]),
      ]),
    ]);
  }

  function renderArtifact(artifact, index) {
    const typeName = {
      chart: "分析图表", file: "生成文件", export: "导出文件",
      tool_result: "完整工具结果", schema: "数据结构", report: "分析报告",
      tool_result_summary: "工具结果",
      ppt: "演示文稿", dashboard: "仪表盘", checkpoint: "工作目录检查点",
    }[String(artifact.type || "").toLowerCase()] || "任务结果";
    const name = artifact.filename || artifact.name || artifact.label || `${typeName} ${index + 1}`;
    const href = artifact.url || artifact.download_url || "";
    return href
      ? h("a", { class: "job-history-artifact", href, target: "_blank", rel: "noopener", download: true }, [iconVNode(h, "external", { size: 13 }), h("span", null, name)])
      : h("span", { class: "job-history-artifact" }, [iconVNode(h, "check", { size: 13 }), h("span", null, name)]);
  }

  function renderRegisteredArtifact(artifact, index) {
    const typeName = { xlsx: "Excel", docx: "Word", pptx: "PPT", dashboard: "Dashboard" };
    const type = String(artifact.type || "").toLowerCase();
    const label = typeName[type] || type || "交付物";
    const workspace = artifact.workspace && typeof artifact.workspace === "object" ? artifact.workspace : null;
    const workspaceLabel = workspace?.name || (artifact.workspace_id ? artifact.workspace_id.slice(0, 8) : "");
    const lineage = [
      artifact.run_id ? `运行 ${artifact.run_id}` : "",
      workspaceLabel ? `工作区 ${workspaceLabel}` : "",
      artifact.worksheet ? `工作表 ${artifact.worksheet}` : "",
      artifact.included_rows != null ? `覆盖 ${artifact.included_rows} 行` : "",
      artifact.source_sha256 ? `快照 ${(artifact.source_sha256 || "").slice(0, 12)}…` : "",
      Array.isArray(artifact.claim_ids) ? `结论 ${artifact.claim_ids.length}` : "",
      Array.isArray(artifact.evidence_ids) ? `证据 ${artifact.evidence_ids.length}` : "",
      artifact.download_count != null ? `下载 ${artifact.download_count}` : "",
    ].filter(Boolean).join(" · ");
    const lineageDetails = artifact.lineage || {};
    const claims = Array.isArray(lineageDetails.claims) ? lineageDetails.claims : [];
    const evidence = Array.isArray(lineageDetails.evidence) ? lineageDetails.evidence : [];
    const governanceAudit = Array.isArray(lineageDetails.governance_audit) ? lineageDetails.governance_audit : [];
    const cost = artifact.cost && typeof artifact.cost === "object" ? artifact.cost : null;
    const expanded = Boolean(artifact.lineageExpanded);
    const detailLoading = Boolean(artifact.detailLoading);
    const detailError = artifact.detailError || "";
    const details = expanded ? h("div", { class: "job-history-artifact-details" }, [
      claims.length ? h("div", { class: "job-history-artifact-detail-group" }, [
        h("strong", null, "关键结论"),
        ...claims.map(claim => h("div", { class: "job-history-artifact-claim", key: claim.claim_id }, [
          h("span", { class: "job-history-artifact-claim-status" }, claim.status || "未核验"),
          h("span", null, claim.text || claim.claim_id),
          claim.human_decision ? h("small", null, "人工决定：" + claim.human_decision) : null,
        ])),
      ]) : null,
      evidence.length ? h("div", { class: "job-history-artifact-detail-group" }, [
        h("strong", null, "关联证据"),
        ...evidence.map(item => h("div", { class: "job-history-artifact-evidence", key: item.evidence_id }, [
          h("code", null, item.evidence_id || "evidence"),
          h("span", null, [
            item.file_name ? "文件 " + item.file_name : "",
            item.worksheet ? "工作表 " + item.worksheet : "",
            item.included_rows != null ? "纳入 " + item.included_rows + " 行" : "",
            item.snippet || item.source_url || "",
            item.content_sha256 ? "SHA-256 " + item.content_sha256.slice(0, 16) + "…" : "",
          ].filter(Boolean).join(" · ")),
        ])),
      ]) : null,
      artifact.analysis_parameters ? h("div", { class: "job-history-artifact-detail-group" }, [
        h("strong", null, "分析参数"),
        h("pre", null, JSON.stringify(artifact.analysis_parameters, null, 2)),
      ]) : null,
      workspaceLabel ? h("div", { class: "job-history-artifact-detail-group" }, [
        h("strong", null, "所属工作区"),
        h("div", null, `${workspaceLabel} · ${artifact.workspace_id?.slice(0, 8) || ""}${workspace?.available === false ? " · 当前不可用" : ""}`),
      ]) : null,
      cost ? h("div", { class: "job-history-artifact-detail-group" }, [
        h("strong", null, "分析成本"),
        h("div", { class: "job-history-artifact-cost" },
          cost.source === "deterministic_no_model"
            ? `确定性计算 · 未调用模型 · ${Number(cost.amount || 0).toFixed(2)} ${cost.currency || "USD"}`
            : cost.amount == null
              ? cost.source === "provider_usage_price_unknown"
                ? "模型用量已记录 · 单价未配置，费用未知"
                : "模型费用与用量暂不可得"
              : `基于配置单价估算 · ${Number(cost.amount).toFixed(6)} ${cost.currency || "USD"}`,
        ),
        h("small", null, `模型调用 ${Number(cost.model_calls || 0)} 次 · 输入 ${Number(cost.input_tokens || 0)} Token · 输出 ${Number(cost.output_tokens || 0)} Token`),
        cost.billing_verified === false ? h("small", null, "未与模型供应商账单对账") : null,
      ]) : null,
      artifact.sql ? h("div", { class: "job-history-artifact-detail-group" }, [
        h("strong", null, "生成 SQL"), h("code", { class: "job-history-artifact-sql" }, artifact.sql),
      ]) : null,
      Array.isArray(artifact.chart_specs) && artifact.chart_specs.length ? h("div", { class: "job-history-artifact-detail-group" }, [
        h("strong", null, "图表规格"),
        h("pre", null, JSON.stringify(artifact.chart_specs, null, 2)),
      ]) : null,
      Array.isArray(artifact.final_claims) && artifact.final_claims.length ? h("div", { class: "job-history-artifact-detail-group" }, [
        h("strong", null, "最终结论"),
        ...artifact.final_claims.map((claim, claimIndex) => h("div", { class: "job-history-artifact-final-claim", key: claimIndex }, String(claim))),
      ]) : null,
      Array.isArray(artifact.warnings) && artifact.warnings.length ? h("div", { class: "job-history-artifact-detail-group job-history-artifact-warnings" }, [
        h("strong", null, "运行提示"),
        ...artifact.warnings.map((warning, warningIndex) => h("div", { key: warningIndex }, String(warning))),
      ]) : null,
      governanceAudit.length ? h("div", { class: "job-history-artifact-detail-group" }, [
        h("strong", null, "人工裁决记录"),
        ...governanceAudit.map((event, eventIndex) => h("div", { class: "job-history-artifact-audit", key: eventIndex },
          `${event.at || ""} · ${event.decision || ""}${event.reason ? `：${event.reason}` : ""}`,
        )),
      ]) : null,
      !claims.length && !evidence.length && !governanceAudit.length && !cost && !workspaceLabel && !artifact.analysis_parameters && !artifact.sql && !artifact.chart_specs?.length && !artifact.final_claims?.length && !artifact.warnings?.length
        ? h("small", { class: "job-history-artifact-detail-empty" }, "关联详情暂不可用") : null,
      detailError ? h("small", { class: "job-history-artifact-detail-error" }, detailError) : null,
    ]) : null;
    return h("article", { class: "job-history-registered-artifact", key: artifact.id || (type + "-" + index) }, [
      h("div", { class: "job-history-registered-artifact-head" }, [
        h("strong", null, label),
        h("code", null, artifact.id || "artifact"),
      ]),
      lineage ? h("small", { class: "job-history-registered-artifact-lineage" }, lineage) : null,
      artifact.download_url ? h("a", { class: "job-history-artifact-download", href: artifact.download_url, download: true }, "下载交付物") : null,
      h("button", { class: "job-history-artifact-toggle", type: "button", disabled: detailLoading, onClick: async () => {
        if (!expanded && !artifact.detailLoaded && callbacks.onArtifactDetail) {
          artifact.detailLoading = true; artifact.detailError = ""; draw();
          try { await callbacks.onArtifactDetail(artifact.id); artifact.detailLoaded = true; }
          catch (error) { artifact.detailError = error?.message || "读取交付物详情失败"; }
          finally { artifact.detailLoading = false; }
        }
        artifact.lineageExpanded = !artifact.lineageExpanded;
        draw();
      } }, detailLoading ? "正在读取详情…" : expanded ? "收起详情" : "读取完整详情"),
      details,
    ]);
  }

  function renderStep(step) {
    const duration = step.elapsed === null ? "" : `${step.elapsed.toFixed(2)}s`;
    return h("li", { class: `job-history-step job-history-step-${step.status}` }, [
      h("span", { class: "job-history-step-state", "aria-hidden": "true" }, [iconVNode(h, step.status === "running" ? "refresh" : step.status === "succeeded" ? "check" : "circleHelp", { size: 15 })]),
      h("span", { class: "job-history-step-name" }, step.display || step.tool),
      h("code", { class: "job-history-step-tool" }, step.tool),
      duration ? h("span", { class: "job-history-step-duration" }, duration) : null,
      step.error ? h("div", { class: "job-history-step-error" }, step.error) : null,
    ]);
  }

  function renderJob(job) {
    const terminal = ["succeeded", "failed", "canceled"].includes(job.status);
    const canCancel = !terminal && job.status !== "canceling"
      && job.type !== "filehistory_rewind" && job.type !== "memory_extraction";
    const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
    const title = job.label || job.type || text("job.default_label", "Background job");
    const isConversation = job.type === "conversation_analysis";
    const answer = typeof job.result === "object" ? (job.result?.answer || "") : "";
    const detailCount = job.steps.length || Number(job.result?.step_count) || 0;
    const activation = job.activation || job.result?.activation;
    const children = [
      h("div", { class: "job-history-card-head" }, [
        h("div", { class: "job-history-card-title" }, title),
        h("span", { class: `job-status job-status-${job.status}` },
          text(`job.status.${job.status}`, job.status)),
      ]),
      h("div", { class: "job-history-time" }, formatTime(job.createdAt)),
      job.workspaceId ? h("div", {
        class: "job-history-workspace",
        title: job.workspaceId,
      }, [iconVNode(h, "folder", { size: 14 }), h("span", null, `${text("job.workspace", "工作目录")}：${job.workspace?.name || job.workspaceId.slice(0, 8)}`)]) : null,
      h("div", { class: "job-progress", role: "progressbar", "aria-valuenow": String(progress) }, [
        h("span", { class: "job-progress-fill", style: { width: `${progress}%` } }),
      ]),
      h("div", { class: "job-card-meta" }, [
        h("span", { class: "job-progress-value" },
          isConversation ? `已执行 ${detailCount} 个步骤` : `${progress}%`),
        job.message && !isConversation ? h("span", { class: "job-message" }, job.message) : null,
      ]),
    ];
    if (activation?.kind && activation.kind !== "none") {
      const prefix = activation.kind === "skill" ? "分析技能" : activation.kind === "command" ? "命令" : "内部操作";
      children.splice(2, 0, h("div", {
        class: `job-activation job-activation-${activation.kind}`,
      }, `${prefix}: ${activation.name || ""}`));
    }
    if (job.artifacts.length) {
      children.push(h("div", { class: "job-history-artifacts" }, [
        h("div", { class: "job-history-artifacts-title" }, "任务结果"),
        ...job.artifacts.map(renderArtifact),
      ]));
    }
    if (job.error) children.push(h("div", { class: "job-error" }, job.error));
    if (isConversation && (job.steps.length || answer)) {
      children.push(h("button", {
        class: "job-history-expand", type: "button",
        "aria-expanded": String(job.expanded),
        onClick: () => { job.expanded = !job.expanded; draw(); },
      }, `${job.expanded ? "收起" : "展开"}执行详情 (${detailCount})`));
      if (job.expanded) {
        children.push(h("div", { class: "job-history-detail" }, [
          job.steps.length
            ? h("ol", { class: "job-history-steps" }, job.steps.map(renderStep))
            : null,
          answer ? h("div", { class: "job-history-answer" }, [
            h("div", { class: "job-history-answer-title" }, "最终答案"),
            h("div", { class: "job-history-answer-body" }, answer),
          ]) : null,
        ]));
      }
    }
    if (canCancel) {
      children.push(h("button", {
        class: "job-cancel-btn",
        type: "button",
        disabled: job.cancelPending,
        onClick: async () => {
          if (!callbacks.onCancel || job.cancelPending) return;
          job.cancelPending = true;
          job.status = "canceling";
          draw();
          try { await callbacks.onCancel(job.id); }
          catch (error) {
            job.cancelPending = false;
            job.error = error?.message || text("job.cancel_failed", "Could not cancel job");
            draw();
          }
        },
      }, text("job.cancel", "Cancel")));
    }
    return h("article", {
      class: `job-history-card job-history-card-${job.status}${state.focusJobId === job.id ? " focused" : ""}`,
      key: job.id,
      "data-job-id": job.id,
    }, children);
  }

  function draw() {
    if (!state.open) {
      render(null, root);
      return;
    }
    const historyBody = state.loading && !state.jobs.length
      ? h("div", { class: "job-history-empty" }, text("job.history.loading", "Loading…"))
      : state.error
        ? h("div", { class: "job-history-error" }, state.error)
        : state.jobs.length || state.registeredArtifacts.length
          ? h("div", null, [
            state.jobs.length ? h("div", { class: "job-history-list" }, state.jobs.map(renderJob)) : null,
            state.registeredArtifacts.length ? h("section", { class: "job-history-registered-artifacts" }, [
              h("h3", null, "本会话交付物"),
              ...state.registeredArtifacts.map(renderRegisteredArtifact),
            ]) : null,
          ])
          : h("div", { class: "job-history-empty" }, text("job.history.empty", "No background jobs yet"));
    const body = h("div", { class: "job-history-body" }, [
      renderAuditCenter(),
      h("section", { class: "job-history-ledger" }, [
        h("div", { class: "job-audit-section-head" }, [
          h("h2", null, "任务与交付物"),
          h("span", null, "保留原始运行记录与可下载产物"),
        ]),
        historyBody,
      ]),
    ]);
    render(h("div", {
      class: "overlay open",
      role: "dialog",
      "aria-modal": "true",
      onClick: event => { if (event.target === event.currentTarget) setOpen(false); },
      onKeydown: event => {
        if (event.key !== "Escape" || event.defaultPrevented) return;
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
      },
    }, [h("section", { class: "modal job-history-modal" }, [
      h("header", { class: "job-history-head" }, [
        h("div", null, [
          h("div", { class: "modal-title" }, text("job.history.title", "Job history")),
          h("div", { class: "job-history-summary" },
            text("job.history.summary", `${state.jobs.length} jobs`, { count: state.jobs.length })),
        ]),
        h("div", { class: "job-history-actions" }, [
          h("button", { class: "btn-sm btn-sm-danger", type: "button", disabled: state.loading,
            onClick: () => callbacks.onClearCompleted?.() },
          text("job.history.clear_completed", "清除已完成")),
          h("button", { class: "btn-sm btn-sm-ghost", type: "button", disabled: state.loading,
            onClick: () => callbacks.onRefresh?.() }, text("job.history.refresh", "Refresh")),
          h("button", { class: "job-history-close", type: "button", onClick: () => setOpen(false),
            "aria-label": text("modal.close", "Close"), title: text("modal.close", "Close") },
          iconVNode(h, "close", { size: 16 })),
        ]),
      ]),
      body,
    ])]), root);
  }

  function focus(jobId) {
    state.focusJobId = String(jobId || "");
    const job = findJob(state.focusJobId);
    if (job) job.expanded = true;
    state.open = true;
    draw();
    requestAnimationFrame(() => {
      const escaped = globalThis.CSS?.escape
        ? globalThis.CSS.escape(state.focusJobId)
        : state.focusJobId.replace(/["\\]/g, "\\$&");
      root.querySelector(`[data-job-id="${escaped}"]`)?.scrollIntoView({
        block: "center",
        behavior: "smooth",
      });
    });
  }

  function setOpen(open) {
    const wasOpen = state.open;
    state.open = Boolean(open);
    if (!state.open) state.focusJobId = "";
    draw();
    requestAnimationFrame(() => {
      if (state.open && !wasOpen) root.querySelector(".job-history-close")?.focus();
      if (!state.open && wasOpen) document.getElementById("btn-job-history")?.focus({ preventScroll: true });
    });
  }
  function setLoading(loading) { state.loading = Boolean(loading); draw(); }
  function setError(error) { state.error = error || ""; draw(); }
  function setAudit(audit) {
    state.audit = audit && typeof audit === "object" ? audit : null;
    draw();
  }
  function setAuditLoading(loading) { state.auditLoading = Boolean(loading); draw(); }
  function setAuditError(error) { state.auditError = error || ""; draw(); }
  function setRegisteredArtifacts(artifacts) { state.registeredArtifacts = Array.isArray(artifacts) ? artifacts : []; draw(); }
  function updateRegisteredArtifact(detail) {
    const id = String(detail?.id || "");
    const index = state.registeredArtifacts.findIndex(item => String(item.id || "") === id);
    if (index < 0) return;
    const expanded = state.registeredArtifacts[index].lineageExpanded;
    state.registeredArtifacts[index] = { ...state.registeredArtifacts[index], ...detail, lineageExpanded: expanded, detailLoaded: true };
    draw();
  }
  function reset() {
    state.jobs = []; state.registeredArtifacts = []; state.error = ""; state.loading = false;
    state.audit = null; state.auditError = ""; state.auditLoading = false;
    state.auditFilters = { type: "all", status: "all", query: "", from: "", to: "" };
    draw();
  }

  registerUiIsland("jobHistory", {
    setOpen, setLoading, setError, setAudit, setAuditLoading, setAuditError,
    setJobs, setRegisteredArtifacts, updateRegisteredArtifact, applyEvent, reset, focus,
    isOpen: () => state.open,
  });
}
