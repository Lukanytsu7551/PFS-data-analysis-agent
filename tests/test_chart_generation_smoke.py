"""Registry-wide chart generation smoke coverage."""

from __future__ import annotations

import sys
import unittest
import warnings

import numpy as np
import pandas as pd

from LLM.chart_selector import _CHARTS

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "Function" / "Charts_generation"))
from chart_generate import generate_chart  # noqa: E402


def _fixture() -> pd.DataFrame:
    n = 60
    t = np.arange(n)
    return pd.DataFrame(
        {
            "x": [f"cat{i % 6}" for i in range(n)],
            "x_num": (t + 1).astype(float),
            "y": (t % 10 + 1).astype(float),
            "series": [f"s{i % 3}" for i in range(n)],
            "group": [f"g{i % 5}" for i in range(n)],
            "category": [f"c{i % 6}" for i in range(n)],
            "start": (t % 7 + 1).astype(float),
            "end": (t % 7 + 2).astype(float),
            "label": [f"label{i % 8}" for i in range(n)],
            "value": ((t % 9 + 1) * np.where(t % 2, -1, 1)).astype(float),
            "positive_value": (t % 9 + 1).astype(float),
            "actual": (t % 8 + 2).astype(float),
            "target_node": [f"dst{i % 5}" for i in range(n)],
            "target": [f"dst{i % 5}" for i in range(n)],
            "target_value": (t % 12 + 3).astype(float),
            "source": [f"src{i % 4}" for i in range(n)],
            "z": (t % 5 + 1).astype(float),
            "time": t % 12,
            "left_value": (t % 8 + 1).astype(float),
            "right_value": (t % 8 + 2).astype(float),
            "male": (t % 8 + 1).astype(float),
            "female": (t % 8 + 2).astype(float),
            "geo": ["北京", "上海", "广州", "深圳", "杭州"] * 12,
            "dim_a": (t % 5).astype(float),
            "dim_b": (t % 7).astype(float),
            "dim_c": (t % 3).astype(float),
        }
    )


ROLE_COLUMNS = {
    "x": "x", "y": "y", "series": "series",
    "group": "group", "category": "category",
    "start": "start", "end": "end", "label": "label",
    "value": "value", "actual": "actual",
    "target": "target", "source": "source",
    "z": "z", "time": "time",
    "left_value": "left_value", "right_value": "right_value",
    "dimensions": ["dim_a", "dim_b", "dim_c"],
    "labels": "label", "values": "value", "names": "label",
}


class ChartGenerationSmokeTests(unittest.TestCase):
    def test_every_registered_chart_returns_html_or_explicit_constraint_error(self):
        frame = _fixture()
        outcomes = {}
        for chart in _CHARTS:
            chart_id = chart["chart_id"]
            mapping = {role: ROLE_COLUMNS[role] for role in chart["required_roles"]}
            if chart_id in {"Scatter_Plot", "Connected_Scatter"}:
                mapping.update({"x": "x_num", "y": "y"})
            if chart_id == "Bullet_Chart":
                mapping.update({"actual": "actual", "target": "target_value"})
            if chart_id == "Stacked_Area_Chart":
                mapping.update({"x": "time", "y": ["y", "value"]})
            if chart_id == "Horizon_Chart":
                mapping.update({"x": "time", "y": ["y", "actual"]})
            if chart_id == "Dot_Density_Map":
                mapping["label"] = "geo"
            if chart_id == "Beeswarm_Plot":
                mapping.update({"x": "group", "y": "y"})
            if chart_id == "Diverging_Bar_Chart":
                mapping.update({"label": "label", "value": "value"})
            if chart_id == "Waffle":
                mapping["value"] = "positive_value"
            if chart_id == "Network_Diagram":
                mapping.update({"source": "source", "target": "target", "value": "y"})
            if chart_id == "Chord_Diagram":
                mapping["target"] = "target_node"
            with self.subTest(chart=chart_id):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    result = generate_chart(
                        df=frame, chart_type=chart_id, mapping=mapping,
                        options={"title": "PFS chart smoke"},
                    )
                if result.get("success"):
                    self.assertGreater(len(result.get("html", "")), 500)
                    outcomes[chart_id] = "pass"
                else:
                    outcomes[chart_id] = result.get("error", "unknown error")

        self.assertEqual(len(outcomes), len(_CHARTS))
        failures = {key: value for key, value in outcomes.items() if value != "pass"}
        self.assertFalse(failures, failures)

    def test_missing_column_is_rejected_without_rendering(self):
        result = generate_chart(
            df=_fixture(), chart_type="Bar_Chart",
            mapping={"x": "missing", "y": "y"},
        )
        self.assertIn("error", result)
        self.assertIn("missing", result["error"])


if __name__ == "__main__":
    unittest.main()
