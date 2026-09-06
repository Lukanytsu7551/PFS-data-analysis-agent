import re
import subprocess
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRACKED_PNG = "static/Images/pfs-mark.png"
EXISTING_SVG = "static/Images/pfs-mark.svg"

TRACKED_OPAQUE_PATHS = (
    "Function/Charts_generation/charts/AlibabaPuHuiTi-3-55-Regular.ttf",
    "Function/Output/PPT/PPT_template/mckinsey.pptx",
    "deploy/samples/Sample-data.xlsx",
    "installer/icon.ico",
    "packaging/pfs-mark.ico",
)

WORKTREE_TEXT_PATHS = (
    "start.command",
    "start.bat",
    "installer/launch.bat",
    "install.sh",
    "install.ps1",
    "Dockerfile",
    "installer/setup.iss",
    "packaging/build_macos.sh",
    "packaging/build_windows.ps1",
)


class GitAttributesStaticTests(unittest.TestCase):
    def _git_attributes(self, relative_path):
        completed = subprocess.run(
            ["git", "check-attr", "text", "eol", "binary", "--", relative_path],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"git check-attr failed for {relative_path}: {completed.stderr}",
        )

        attributes = {}
        prefix = f"{relative_path}: "
        for line in completed.stdout.splitlines():
            self.assertTrue(line.startswith(prefix), msg=line)
            name, value = line[len(prefix) :].split(": ", 1)
            attributes[name] = value
        return attributes

    def test_repository_text_file_policy_is_explicit_and_scoped(self):
        path = PROJECT_ROOT / ".gitattributes"
        self.assertTrue(path.is_file())
        raw = path.read_bytes()
        text = raw.decode("utf-8")

        for rule in (
            "*.bat text eol=crlf",
            "*.cmd text eol=crlf",
            "*.ps1 text eol=crlf",
            "*.command text eol=lf",
            "*.sh text eol=lf",
            "Dockerfile text eol=lf",
            "*.iss text eol=lf",
            "*.spec text eol=lf",
            "*.svg text eol=lf",
            ".gitattributes text eol=lf",
            "*.py text eol=lf",
            "*.js text eol=lf",
            "*.md text eol=lf",
            "/.dockerignore text eol=lf",
            "/.env.example text eol=lf",
            "/.gitignore text eol=lf",
            "/.prettierignore text eol=lf",
            "/.prettierrc.json text eol=lf",
            "/MCP/AtlasCloud/.gitignore text eol=lf",
            "/MCP/AtlasCloud/.npmignore text eol=lf",
            "data/fixtures/** text eol=lf",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

        for rule in (
            "data/datasource_config.json binary -eol",
            "LLM/llm_config.json binary -eol",
            "memory/** binary -eol",
            "uploads/** binary -eol",
            "outputs/** binary -eol",
            "*.db binary -eol",
            "*.sqlite binary -eol",
            "*.sqlite3 binary -eol",
            "*.duckdb binary -eol",
            "*.parquet binary -eol",
            "*.png binary -eol",
            "*.pdf binary -eol",
            "*.xlsx binary -eol",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

        forbidden_scopes = ("database/", "databases/")
        for scope in forbidden_scopes:
            with self.subTest(scope=scope):
                self.assertNotIn(scope, text)

        self.assertNotRegex(text, r"(?m)^\s*\*\.csv\s+")
        self.assertNotIn(b"\r", raw)

    def test_git_check_attr_matches_release_paths(self):
        expected = {
            "start.command": {"text": "set", "eol": "lf", "binary": "unspecified"},
            "start.bat": {"text": "set", "eol": "crlf", "binary": "unspecified"},
            "install.sh": {"text": "set", "eol": "lf", "binary": "unspecified"},
            "install.ps1": {"text": "set", "eol": "crlf", "binary": "unspecified"},
            "Dockerfile": {"text": "set", "eol": "lf", "binary": "unspecified"},
            "installer/setup.iss": {"text": "set", "eol": "lf", "binary": "unspecified"},
            "packaging/build_macos.sh": {
                "text": "set",
                "eol": "lf",
                "binary": "unspecified",
            },
            "packaging/build_windows.ps1": {
                "text": "set",
                "eol": "crlf",
                "binary": "unspecified",
            },
            TRACKED_PNG: {"text": "unset", "eol": "unset", "binary": "set"},
            EXISTING_SVG: {"text": "set", "eol": "lf", "binary": "unspecified"},
            ".dockerignore": {"text": "set", "eol": "lf", "binary": "unspecified"},
            ".env.example": {"text": "set", "eol": "lf", "binary": "unspecified"},
            ".gitignore": {"text": "set", "eol": "lf", "binary": "unspecified"},
            ".prettierignore": {"text": "set", "eol": "lf", "binary": "unspecified"},
            ".prettierrc.json": {"text": "set", "eol": "lf", "binary": "unspecified"},
            "MCP/AtlasCloud/.gitignore": {
                "text": "set",
                "eol": "lf",
                "binary": "unspecified",
            },
            "MCP/AtlasCloud/.npmignore": {
                "text": "set",
                "eol": "lf",
                "binary": "unspecified",
            },
            "data/fixtures/pfs_sales.csv": {
                "text": "set",
                "eol": "lf",
                "binary": "unspecified",
            },
            "data/fixtures/pfs_city_monthly_pnl.csv": {
                "text": "set",
                "eol": "lf",
                "binary": "unspecified",
            },
            "data/fixtures/pfs_monthly_demand.csv": {
                "text": "set",
                "eol": "lf",
                "binary": "unspecified",
            },
        }

        for relative_path in TRACKED_OPAQUE_PATHS:
            expected[relative_path] = {
                "text": "unset",
                "eol": "unset",
                "binary": "set",
            }

        for relative_path, expected_attributes in expected.items():
            with self.subTest(path=relative_path):
                self.assertTrue((PROJECT_ROOT / relative_path).is_file())
                self.assertEqual(self._git_attributes(relative_path), expected_attributes)

    def _git_eol_state(self, relative_path):
        completed = subprocess.run(
            ["git", "ls-files", "--eol", "--", relative_path],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"git ls-files --eol failed for {relative_path}: {completed.stderr}",
        )
        fields = completed.stdout.split()
        self.assertTrue(fields, msg=f"git ls-files --eol returned no data: {relative_path}")
        return {field[0]: field[2:] for field in fields if len(field) >= 3 and field[:2] in {"i/", "w/"}}

    @staticmethod
    def _line_ending_style(line_endings):
        unique = set(line_endings)
        if len(unique) != 1:
            return "mixed"
        if line_endings[0] == b"\n":
            return "lf"
        if line_endings[0] == b"\r\n":
            return "crlf"
        return "cr"

    def _is_stale_checkout(self, relative_path, actual_style):
        """Allow a uniform pre-attribute checkout until Git re-checks it out."""

        state = self._git_eol_state(relative_path)
        return state.get("i") == actual_style == state.get("w")

    def test_worktree_text_files_use_declared_line_endings(self):

        for relative_path in WORKTREE_TEXT_PATHS:
            with self.subTest(path=relative_path):
                path = PROJECT_ROOT / relative_path
                self.assertTrue(path.is_file())
                raw = path.read_bytes()
                line_endings = re.findall(rb"\r\n|\r|\n", raw)
                self.assertTrue(line_endings, msg=f"no line ending found: {relative_path}")
                actual_style = self._line_ending_style(line_endings)
                attributes = self._git_attributes(relative_path)
                declared_style = attributes["eol"]
                self.assertIn(
                    declared_style,
                    {"lf", "crlf"},
                    msg=f"unsupported declared eol for {relative_path}: {attributes}",
                )
                if actual_style != declared_style:
                    self.assertTrue(
                        actual_style != "mixed" and self._is_stale_checkout(relative_path, actual_style),
                        msg=(
                            f"{relative_path} uses {actual_style}, but .gitattributes "
                            f"declares {declared_style} and Git has no stale-checkout evidence"
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
