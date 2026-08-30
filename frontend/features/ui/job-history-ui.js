import { registerUiIsland } from "../../core/ui-registry.js";

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
      ? h("a", { class: "job-history-artifact", href, target: "_blank", rel: "noopener", download: true }, `↗ ${name}`)
      : h("span", { class: "job-history-artifact" }, `✓ ${name}`);
  }

  function renderRegisteredArtifact(artifact, index) {
    const typeName = { xlsx: "Excel", docx: "Word", pptx: "PPT", dashboard: "Dashboard" };
    const type = String(artifact.type || "").toLowerCase();
    const label = typeName[type] || type || "交付物";
    const lineage = [
      artifact.run_id ? `运行 ${artifact.run_id}` : "",
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
          h("span", null, item.snippet || item.source_url || ""),
        ])),
      ]) : null,
      artifact.analysis_parameters ? h("div", { class: "job-history-artifact-detail-group" }, [
        h("strong", null, "分析参数"),
        h("pre", null, JSON.stringify(artifact.analysis_parameters, null, 2)),
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
      !claims.length && !evidence.length && !governanceAudit.length && !artifact.analysis_parameters && !artifact.sql && !artifact.chart_specs?.length && !artifact.final_claims?.length && !artifact.warnings?.length
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
      h("span", { class: "job-history-step-state", "aria-hidden": "true" },
        step.status === "running" ? "⟳" : step.status === "succeeded" ? "✓" : "!"),
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
        title: job.workspace?.path || job.workspaceId,
      }, `📁 ${text("job.workspace", "工作目录")}：${job.workspace?.name || job.workspaceId.slice(0, 8)}`) : null,
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
    const body = state.loading && !state.jobs.length
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
    render(h("div", {
      class: "overlay open",
      role: "dialog",
      "aria-modal": "true",
      onClick: event => { if (event.target === event.currentTarget) setOpen(false); },
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
            "aria-label": text("modal.close", "Close") }, "×"),
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
    state.open = Boolean(open);
    if (!state.open) state.focusJobId = "";
    draw();
  }
  function setLoading(loading) { state.loading = Boolean(loading); draw(); }
  function setError(error) { state.error = error || ""; draw(); }
  function setRegisteredArtifacts(artifacts) { state.registeredArtifacts = Array.isArray(artifacts) ? artifacts : []; draw(); }
  function updateRegisteredArtifact(detail) {
    const id = String(detail?.id || "");
    const index = state.registeredArtifacts.findIndex(item => String(item.id || "") === id);
    if (index < 0) return;
    const expanded = state.registeredArtifacts[index].lineageExpanded;
    state.registeredArtifacts[index] = { ...state.registeredArtifacts[index], ...detail, lineageExpanded: expanded, detailLoaded: true };
    draw();
  }
  function reset() { state.jobs = []; state.registeredArtifacts = []; state.error = ""; state.loading = false; draw(); }

  registerUiIsland("jobHistory", {
    setOpen, setLoading, setError, setJobs, setRegisteredArtifacts, updateRegisteredArtifact, applyEvent, reset, focus,
    isOpen: () => state.open,
  });
}
