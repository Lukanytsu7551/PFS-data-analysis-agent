"""Explicit, side-effect-free dependency manifests for startup preflight.

The base application has a deliberately small fail-closed manifest. Feature
integrations are recorded separately so their absence can be diagnosed without
preventing Flask from starting. This module never parses requirements files and
never installs anything by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from types import MappingProxyType
from typing import Callable, Iterable, Literal, Mapping


AUTO_INSTALL_ENV = "PFS_AUTO_INSTALL_DEPENDENCIES"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class DependencySpec:
    """One explicit distribution-to-import contract."""

    package_name: str
    import_name: str
    feature_group: str


# Only imports required to construct the base Flask application belong here.
# Servers, data engines, connectors, export libraries, and analysis packages
# are feature dependencies and must not make the base preflight fail closed.
CORE_STARTUP_DEPENDENCIES = (
    DependencySpec("flask", "flask", "core"),
    DependencySpec("flask-cors", "flask_cors", "core"),
)


OPTIONAL_FEATURE_DEPENDENCIES = MappingProxyType(
    {
        "serving": (
            DependencySpec("waitress", "waitress", "serving"),
            DependencySpec("gunicorn", "gunicorn", "serving"),
        ),
        "data_analysis": (
            DependencySpec("duckdb", "duckdb", "data_analysis"),
            DependencySpec("pandas", "pandas", "data_analysis"),
            DependencySpec("numpy", "numpy", "data_analysis"),
            DependencySpec("scipy", "scipy", "data_analysis"),
            DependencySpec("PyYAML", "yaml", "data_analysis"),
            DependencySpec("openpyxl", "openpyxl", "data_analysis"),
            DependencySpec("xlrd", "xlrd", "data_analysis"),
            DependencySpec("python-calamine", "python_calamine", "data_analysis"),
        ),
        "visualization": (
            DependencySpec("pyecharts", "pyecharts", "visualization"),
            DependencySpec("plotly", "plotly", "visualization"),
            DependencySpec("matplotlib", "matplotlib", "visualization"),
        ),
        "browser": (
            DependencySpec("selenium", "selenium", "browser"),
            DependencySpec("snapshot-selenium", "snapshot_selenium", "browser"),
        ),
        "llm_http": (
            DependencySpec("requests", "requests", "llm_http"),
            DependencySpec("charset-normalizer", "charset_normalizer", "llm_http"),
            DependencySpec("openai", "openai", "llm_http"),
            DependencySpec("python-dotenv", "dotenv", "llm_http"),
            DependencySpec("httpx", "httpx", "llm_http"),
        ),
        "remote_compute": (
            DependencySpec("paramiko", "paramiko", "remote_compute"),
            DependencySpec("keyring", "keyring", "remote_compute"),
        ),
        "export": (
            DependencySpec("python-docx", "docx", "export"),
            DependencySpec("python-pptx", "pptx", "export"),
            DependencySpec("lxml", "lxml", "export"),
        ),
        "time_series": (
            DependencySpec("statsmodels", "statsmodels", "time_series"),
            DependencySpec("pmdarima", "pmdarima", "time_series"),
        ),
        "database": (
            DependencySpec("SQLAlchemy", "sqlalchemy", "database"),
            DependencySpec("PyMySQL", "pymysql", "database"),
            DependencySpec("psycopg2-binary", "psycopg2", "database"),
            DependencySpec("pyodbc", "pyodbc", "database"),
            DependencySpec("sqlglot", "sqlglot", "database"),
        ),
        "external_data": (
            DependencySpec("lark-oapi", "lark_oapi", "external_data"),
        ),
        "knowledge": (
            DependencySpec("jieba", "jieba", "knowledge"),
            DependencySpec("onnxruntime", "onnxruntime", "knowledge"),
            DependencySpec("tokenizers", "tokenizers", "knowledge"),
        ),
    }
)


FailureKind = Literal["import_error", "load_error"]


@dataclass(frozen=True)
class DependencyIssue:
    package_name: str
    import_name: str
    feature_group: str
    failure_kind: FailureKind
    error: str


@dataclass(frozen=True)
class DependencyReport:
    missing_core: tuple[DependencyIssue, ...]
    missing_optional: tuple[DependencyIssue, ...]

    @property
    def all_issues(self) -> tuple[DependencyIssue, ...]:
        return self.missing_core + self.missing_optional

    @property
    def installable_missing_package_names(self) -> tuple[str, ...]:
        """Return import failures only; pip cannot repair host load errors."""

        return tuple(item.package_name for item in self.all_issues if item.failure_kind == "import_error")


class MissingCoreDependencies(RuntimeError):
    def __init__(self, dependencies: Iterable[DependencyIssue]) -> None:
        self.dependencies = tuple(dependencies)
        packages = ", ".join(item.package_name for item in self.dependencies)
        super().__init__(f"Missing core startup dependencies: {packages}")


def _inspect_specs(
    specs: Iterable[DependencySpec],
    importer: Callable[[str], object],
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    for spec in specs:
        try:
            importer(spec.import_name)
        except ImportError as exc:
            issues.append(
                DependencyIssue(
                    package_name=spec.package_name,
                    import_name=spec.import_name,
                    feature_group=spec.feature_group,
                    failure_kind="import_error",
                    error=str(exc),
                )
            )
        except OSError as exc:
            issues.append(
                DependencyIssue(
                    package_name=spec.package_name,
                    import_name=spec.import_name,
                    feature_group=spec.feature_group,
                    failure_kind="load_error",
                    error=str(exc),
                )
            )
    return issues


def inspect_startup_dependencies(
    *,
    core: Iterable[DependencySpec] = CORE_STARTUP_DEPENDENCIES,
    optional: Mapping[str, Iterable[DependencySpec]] = OPTIONAL_FEATURE_DEPENDENCIES,
    importer: Callable[[str], object] = importlib.import_module,
) -> DependencyReport:
    """Inspect explicit manifests without installing or reading requirement files."""

    missing_core = _inspect_specs(core, importer)
    missing_optional: list[DependencyIssue] = []
    for specs in optional.values():
        missing_optional.extend(_inspect_specs(specs, importer))
    return DependencyReport(tuple(missing_core), tuple(missing_optional))


def require_core_dependencies(report: DependencyReport) -> None:
    """Fail closed only when a dependency needed by the base app is unavailable."""

    if report.missing_core:
        raise MissingCoreDependencies(report.missing_core)


def legacy_auto_install_enabled(environ: Mapping[str, str]) -> bool:
    """Return whether the user explicitly opted into startup-time pip install."""

    return environ.get(AUTO_INSTALL_ENV, "").strip().lower() in _TRUE_VALUES


def dependency_install_targets(
    report: DependencyReport,
    environ: Mapping[str, str],
) -> tuple[str, ...]:
    """Return explicit-manifest install targets or enforce the core gate.

    The default path is side-effect free. Targets can only originate from the
    explicit manifests, and load errors are excluded because reinstalling a
    distribution cannot reliably repair host libraries or permissions.
    """

    core_load_errors = tuple(item for item in report.missing_core if item.failure_kind == "load_error")
    if core_load_errors:
        raise MissingCoreDependencies(core_load_errors)
    if legacy_auto_install_enabled(environ):
        return tuple(dict.fromkeys(report.installable_missing_package_names))
    require_core_dependencies(report)
    return ()
