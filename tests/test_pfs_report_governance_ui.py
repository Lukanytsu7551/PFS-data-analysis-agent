import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "frontend" / "legacy" / "pfs-report-preview.js"
STYLES = ROOT / "static" / "css" / "parts" / "modals.css"


class PfsReportGovernanceUiTests(unittest.TestCase):
    def test_report_renders_governance_summary_before_claims(self):
        source = PREVIEW.read_text(encoding="utf-8")
        self.assertIn("function renderGovernanceSummary(result)", source)
        self.assertIn("renderGovernanceSummary(result),", source)
        self.assertLess(source.index("renderGovernanceSummary(result),"), source.index("renderClaims(result),"))
        for token in ("supported", "refutes", "supports", "conflicted", "pending", "verification_reason", "human_decision", "renderConflictQueue"):
            self.assertIn(token, source)
        self.assertIn("async function decideClaim", source)
        self.assertIn("/api/pfs/ledger/claims/", source)
        self.assertIn("确认裁决", source)
        self.assertIn("dataset.claimDecision", source)

    def test_governance_states_have_distinct_visual_treatment(self):
        styles = STYLES.read_text(encoding="utf-8")
        for token in ("pfs-report-governance", "data-state=\"conflict\"", "data-state=\"pending\"", "pfs-report-claim-details", "pfs-report-conflict-item", "pfs-report-conflict-actions", "pfs-report-evidence-details"):
            self.assertIn(token, styles)

    def test_switching_report_source_clears_stale_results(self):
        source = PREVIEW.read_text(encoding="utf-8")
        self.assertIn("function renderIdle()", source)
        self.assertIn('translate("pfs_report.ready_to_run"', source)
        change_handler = source[source.index('source.addEventListener("change"'):]
        self.assertIn("state.result = null;", change_handler)
        self.assertIn("renderIdle();", change_handler)
        self.assertLess(change_handler.index("state.result = null;"), change_handler.index("renderIdle();"))

    def test_report_delivery_area_exposes_structured_formats(self):
        source = PREVIEW.read_text(encoding="utf-8")
        template = (ROOT / "templates" / "agent_chat.html").read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        for output_format in ("xlsx", "docx", "pptx", "dashboard"):
            self.assertIn(f'data-pfs-delivery-format="{output_format}"', template)
        self.assertIn("async function generateDelivery(format)", source)
        self.assertIn("/pfs/deliver", source)
        self.assertIn("renderDeliveryArtifacts();", source)
        self.assertIn("pfs-report-delivery-lineage", source)
        self.assertIn("artifact.source_sha256", source)
        delivery_head = styles[styles.index(".pfs-report-delivery-head {") :]
        delivery_status = styles[styles.index(".pfs-report-delivery-status {") :]
        self.assertIn("grid-template-columns: minmax(0, 1fr);", delivery_head)
        self.assertIn("overflow-wrap: anywhere;", delivery_status)

    def test_multi_sheet_selector_and_actionable_errors_are_wired(self):
        source = PREVIEW.read_text(encoding="utf-8")
        template = (ROOT / "templates" / "agent_chat.html").read_text(encoding="utf-8")
        self.assertIn('id="pfs-report-worksheet"', template)
        self.assertIn("function updateWorksheetControl()", source)
        self.assertIn("worksheet: selectedWorksheet()", source)
        for code in (
            "worksheet_required",
            "worksheet_not_found",
            "source_header_missing",
            "source_has_no_rows",
            "source_columns_missing",
            "source_date_invalid",
            "date_filter_invalid",
            "date_range_invalid",
            "metric_value_not_numeric",
        ):
            self.assertIn(code, source)
        self.assertIn('translate("pfs_report.needs_repair", "需修复")', source)
        self.assertIn("source.validation_error", source)

        i18n = (ROOT / "frontend" / "legacy" / "i18n.js").read_text(encoding="utf-8")
        for key in (
            "pfs_report.error_columns_missing",
            "pfs_report.error_date_invalid",
            "pfs_report.error_date_filter_invalid",
            "pfs_report.error_delivery_table",
            "pfs_report.error_file_too_large",
            "pfs_report.error_model_not_configured",
            "pfs_report.error_delivery_generation",
            "pfs_report.error_delivery_ambiguous",
        ):
            self.assertGreaterEqual(i18n.count(key), 2)

    def test_report_analysis_has_real_cancel_request_and_canceled_state(self):
        source = PREVIEW.read_text(encoding="utf-8")
        template = (ROOT / "templates" / "agent_chat.html").read_text(encoding="utf-8")
        self.assertIn('id="pfs-report-cancel"', template)
        self.assertIn('class="btn-sm btn-sm-danger hidden" id="pfs-report-cancel"', template)
        self.assertIn("async function cancelCurrentRun()", source)
        self.assertIn("async function requestServerCancel(run)", source)
        self.assertIn("const retryDelays = [0, 50, 100, 200, 400, 800]", source)
        self.assertIn("response.status !== 404", source)
        self.assertIn('button.classList.toggle("hidden", !available)', source)
        self.assertIn("new AbortController()", source)
        self.assertIn("/pfs/runs/${encodeURIComponent(run.runId)}/cancel", source)
        self.assertIn('error?.code === "pfs_analysis_canceled"', source)
        self.assertIn("renderCanceled();", source)

    def test_job_history_loads_session_artifacts_separately_from_jobs(self):
        history = (ROOT / "frontend/legacy/job_history.js").read_text(encoding="utf-8")
        ui = (ROOT / "frontend/features/ui/job-history-ui.js").read_text(encoding="utf-8")
        self.assertIn("/api/session/${encodeURIComponent(targetSid)}/lifecycle/artifacts?limit=", history)
        self.assertIn("setRegisteredArtifacts", history)
        self.assertIn("本会话交付物", ui)
        self.assertIn("job-history-registered-artifact-lineage", ui)
        self.assertIn("读取完整详情", ui)
        self.assertIn("governance_audit", ui)
        self.assertIn("分析成本", ui)
        self.assertIn("deterministic_no_model", ui)
        self.assertIn("所属工作区", ui)
        self.assertIn("workspace?.name", ui)
        self.assertNotIn("workspace?.path", ui)
        self.assertIn("下载交付物", ui)

    def test_job_history_dialog_closes_on_escape_from_internal_controls(self):
        ui = (ROOT / "frontend/features/ui/job-history-ui.js").read_text(encoding="utf-8")

        self.assertIn("onKeydown: event => {", ui)
        self.assertIn('event.key !== "Escape" || event.defaultPrevented', ui)
        self.assertIn("event.stopPropagation();", ui)
        self.assertIn("setOpen(false);", ui)

    def test_chat_model_error_has_actionable_configuration_guidance(self):
        source = (ROOT / "frontend/features/chat-stream.js").read_text(encoding="utf-8")
        self.assertIn("model_not_configured", source)
        self.assertIn("配置 DeepSeek", source)

    def test_artifact_history_contract_exposes_lineage_details(self):
        lifecycle = (ROOT / "api/lifecycle.py").read_text(encoding="utf-8")
        self.assertIn('artifact["lineage"]', lifecycle)
        self.assertIn('"claims": claims', lifecycle)
        self.assertIn('"evidence": list(evidence_by_id.values())', lifecycle)
        self.assertIn("for evidence_id in (artifact.get(\"evidence_ids\") or [])", lifecycle)
        self.assertIn("_artifact_governance_audit", lifecycle)
        self.assertIn("detail_url", lifecycle)
        self.assertIn("/download", lifecycle)


if __name__ == "__main__":
    unittest.main()
