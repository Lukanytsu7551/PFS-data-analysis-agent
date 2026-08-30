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
        for token in ("supported", "conflicted", "pending", "verification_reason", "human_decision"):
            self.assertIn(token, source)

    def test_governance_states_have_distinct_visual_treatment(self):
        styles = STYLES.read_text(encoding="utf-8")
        for token in ("pfs-report-governance", "data-state=\"conflict\"", "data-state=\"pending\"", "pfs-report-claim-details"):
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
            "metric_value_not_numeric",
        ):
            self.assertIn(code, source)


if __name__ == "__main__":
    unittest.main()
