"""Local smoke cards for every built-in analysis module.

These tests prove that each analyzer accepts a deterministic fixture and
returns its documented result shape. They are not statistical accuracy or
production-scale acceptance tests. Optional deep-learning dependencies are
reported as skipped rather than hiding the reason for non-execution.
"""

from __future__ import annotations

import importlib.util
import warnings
from pathlib import Path
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ANALYZE_ROOT = ROOT / "Function" / "Analyze"


def _fixture() -> pd.DataFrame:
    n = 48
    t = np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "x1": 10 + t * 0.25 + np.sin(t / 3),
            "x2": 5 + np.cos(t / 4) + np.sin(t / 7),
            "y": 20 + t * 0.8 + np.sin(t / 2),
            "group": ["control"] * (n // 2) + ["treatment"] * (n // 2),
            "label": [0, 1] * (n // 2),
            "cat": ["a", "b", "c"] * (n // 3),
        }
    )


CASES = {
    "AB_Test_Analysis": {"target_column": "y", "groupby_column": "group"},
    "Data_Decile_Analysis": {"target_column": "y", "groupby_column": "group", "n_deciles": 4},
    "Decision_Tree": {"target_column": "label"},
    "K-Means": {"target_column": "x1", "groupby_column": "cat", "n_deciles": 3},
    "Logistic_Regression": {"target_column": "label"},
    "Regression": {"target_column": "y"},
    "Sklearn_Model": {"target_column": "y", "groupby_column": "lm"},
    "Torch_MLP": {"target_column": "label", "groupby_column": "mlp_cls", "n_deciles": 10},
    "Univariate_Screening": {"target_column": "y"},
    "Time_Series_ARIMA": {"target_column": "y", "groupby_column": "date", "n_deciles": 3},
    "Time_Series_SARIMA": {"target_column": "y", "groupby_column": "date", "n_deciles": 3},
    "Time_Series_VAR": {"target_column": "y", "groupby_column": "date,y,x2", "n_deciles": 3},
    "Time_Series_Prophet": {"target_column": "y", "groupby_column": "date", "n_deciles": 3},
    "Time_Series_GRU": {"target_column": "y", "groupby_column": "date", "n_deciles": 3},
}


def _load(name: str):
    path = ANALYZE_ROOT / name / "analyze.py"
    spec = importlib.util.spec_from_file_location(f"pfs_analysis_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载分析模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AnalysisModuleSmokeTests(unittest.TestCase):
    def test_all_builtin_analyzers_return_documented_shapes(self):
        passed = []
        skipped = []
        for name, kwargs in CASES.items():
            with self.subTest(analyzer=name):
                module = _load(name)
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        output = module.run(_fixture(), **kwargs)
                except ImportError as exc:
                    if name == "Torch_MLP":
                        skipped.append(f"{name}: {exc}")
                        continue
                    raise
                self.assertIsInstance(output, (tuple, dict))
                frames = list(output.values()) if isinstance(output, dict) else list(output[:-1])
                self.assertGreaterEqual(len(frames), 2)
                self.assertTrue(all(isinstance(frame, pd.DataFrame) for frame in frames))
                self.assertTrue(any(not frame.empty for frame in frames), name)
                if isinstance(output, tuple):
                    self.assertIsInstance(output[-1], str)
                passed.append(name)

        self.assertEqual(len(passed) + len(skipped), len(CASES))
        if skipped:
            self.skipTest("可选分析依赖未安装：" + "；".join(skipped))

    def test_invalid_required_column_fails_clearly(self):
        module = _load("Regression")
        with self.assertRaises(ValueError):
            module.run(_fixture(), target_column="missing_column")


if __name__ == "__main__":
    unittest.main()
