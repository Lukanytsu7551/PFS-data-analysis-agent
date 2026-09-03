import unittest

from infrastructure.startup_requirements import (
    CORE_STARTUP_DEPENDENCIES,
    OPTIONAL_FEATURE_DEPENDENCIES,
    DependencySpec,
    MissingCoreDependencies,
    dependency_install_targets,
    inspect_startup_dependencies,
    require_core_dependencies,
)


class StartupRequirementsTests(unittest.TestCase):
    def test_optional_import_and_load_failures_are_categorized_and_do_not_block(self):
        optional = {
            "external_data": (DependencySpec("lark-oapi", "lark_oapi", "external_data"),),
            "database": (DependencySpec("pyodbc", "pyodbc", "database"),),
        }

        def failing_import(name):
            if name == "pyodbc":
                raise OSError("unixODBC host library is unavailable")
            raise ModuleNotFoundError(f"No module named {name}")

        report = inspect_startup_dependencies(core=(), optional=optional, importer=failing_import)

        require_core_dependencies(report)
        self.assertEqual((), dependency_install_targets(report, {}))
        self.assertEqual(
            [
                ("lark-oapi", "external_data", "import_error"),
                ("pyodbc", "database", "load_error"),
            ],
            [
                (issue.package_name, issue.feature_group, issue.failure_kind)
                for issue in report.missing_optional
            ],
        )
        self.assertIn("unixODBC", report.missing_optional[1].error)

    def test_missing_core_dependency_fails_closed(self):
        core = (DependencySpec("flask", "flask", "core"),)

        def missing_import(name):
            raise ImportError(f"cannot import {name}")

        report = inspect_startup_dependencies(core=core, optional={}, importer=missing_import)

        with self.assertRaises(MissingCoreDependencies) as raised:
            dependency_install_targets(report, {})
        self.assertEqual(
            ("flask",),
            tuple(item.package_name for item in raised.exception.dependencies),
        )
        self.assertEqual("import_error", raised.exception.dependencies[0].failure_kind)

    def test_core_load_error_stays_fail_closed_even_with_install_opt_in(self):
        core = (DependencySpec("flask", "flask", "core"),)

        def unloadable_import(_name):
            raise OSError("linked host library cannot be loaded")

        report = inspect_startup_dependencies(core=core, optional={}, importer=unloadable_import)

        with self.assertRaises(MissingCoreDependencies) as raised:
            dependency_install_targets(report, {"PFS_AUTO_INSTALL_DEPENDENCIES": "1"})
        self.assertEqual("load_error", raised.exception.dependencies[0].failure_kind)

    def test_explicit_opt_in_returns_only_failed_manifest_imports(self):
        core = (DependencySpec("flask", "flask", "core"),)
        optional = {
            "database": (
                DependencySpec("pyodbc", "pyodbc", "database"),
                DependencySpec("sqlglot", "sqlglot", "database"),
            )
        }

        def failing_import(name):
            if name == "pyodbc":
                raise OSError("host library missing")
            raise ImportError(f"cannot import {name}")

        report = inspect_startup_dependencies(core=core, optional=optional, importer=failing_import)

        with self.assertRaises(MissingCoreDependencies):
            dependency_install_targets(report, {})
        self.assertEqual(
            ("flask", "sqlglot"),
            dependency_install_targets(report, {"PFS_AUTO_INSTALL_DEPENDENCIES": "true"}),
        )

    def test_default_complete_preflight_has_no_install_action(self):
        imported = []

        def successful_import(name):
            imported.append(name)
            return object()

        report = inspect_startup_dependencies(
            core=CORE_STARTUP_DEPENDENCIES,
            optional={},
            importer=successful_import,
        )

        self.assertEqual((), dependency_install_targets(report, {}))
        self.assertEqual(["flask", "flask_cors"], imported)

    def test_feature_packages_are_not_promoted_to_core_startup(self):
        core_packages = {spec.package_name.lower() for spec in CORE_STARTUP_DEPENDENCIES}
        feature_packages = {
            spec.package_name.lower() for specs in OPTIONAL_FEATURE_DEPENDENCIES.values() for spec in specs
        }

        self.assertEqual({"flask", "flask-cors"}, core_packages)
        self.assertTrue({"python-pptx", "pyodbc", "selenium"}.issubset(feature_packages))
        self.assertTrue(core_packages.isdisjoint(feature_packages))
        self.assertTrue({"export", "database", "browser"}.issubset(OPTIONAL_FEATURE_DEPENDENCIES))

    def test_unexpected_importer_errors_are_not_swallowed(self):
        def broken_import(_name):
            raise RuntimeError("import side effect failed")

        with self.assertRaisesRegex(RuntimeError, "import side effect failed"):
            inspect_startup_dependencies(
                core=CORE_STARTUP_DEPENDENCIES,
                optional={},
                importer=broken_import,
            )


if __name__ == "__main__":
    unittest.main()
