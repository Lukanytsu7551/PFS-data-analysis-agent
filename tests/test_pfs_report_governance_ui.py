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
        for output_format in ("xlsx", "docx", "pptx", "dashboard"):
            self.assertIn(f'data-pfs-delivery-format="{output_format}"', template)
        self.assertIn("async function generateDelivery(format)", source)
        self.assertIn("/pfs/deliver", source)
        self.assertIn("renderDeliveryArtifacts();", source)
        self.assertIn("pfs-report-delivery-lineage", source)
        self.assertIn("artifact.source_sha256", source)

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
            "metric_value_not_numeric",
        ):
            self.assertIn(code, source)

    def test_job_history_loads_session_artifacts_separately_from_jobs(self):
        history = (ROOT / "frontend/legacy/job_history.js").read_text(encoding="utf-8")
        ui = (ROOT / "frontend/features/ui/job-history-ui.js").read_text(encoding="utf-8")
        self.assertIn("/api/session/${encodeURIComponent(targetSid)}/lifecycle/artifacts?limit=", history)
        self.assertIn("setRegisteredArtifacts", history)
        self.assertIn("本会话交付物", ui)
        self.assertIn("job-history-registered-artifact-lineage", ui)
        self.assertIn("读取完整详情", ui)
        self.assertIn("governance_audit", ui)
        self.assertIn("下载交付物", ui)

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
