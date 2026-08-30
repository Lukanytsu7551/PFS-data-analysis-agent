#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PFS Data Analysis Agent — 自适应本地与托管环境
"""

import logging

log = logging.getLogger(__name__)

import multiprocessing
import os
from pathlib import Path
import sys

from infrastructure.compat import env

if getattr(sys, "frozen", False):
    multiprocessing.freeze_support()


# -------------------------------
# 依赖检查（默认不在启动阶段产生 pip 副作用）
# -------------------------------
def ensure_requirements():
    import subprocess

    from infrastructure.startup_requirements import (
        MissingCoreDependencies,
        dependency_install_targets,
        inspect_startup_dependencies,
    )

    if getattr(sys, "frozen", False):
        return
    report = inspect_startup_dependencies()

    if report.missing_optional:
        for dependency in report.missing_optional:
            if dependency.failure_kind == "load_error":
                log.warning(
                    "[app] Optional feature %s cannot load %s (package %s): %s",
                    dependency.feature_group,
                    dependency.import_name,
                    dependency.package_name,
                    dependency.error,
                )
            else:
                log.warning(
                    "[app] Optional feature %s import unavailable: %s (package %s): %s",
                    dependency.feature_group,
                    dependency.import_name,
                    dependency.package_name,
                    dependency.error,
                )

    try:
        install_targets = dependency_install_targets(report, os.environ)
    except MissingCoreDependencies:
        for dependency in report.missing_core:
            problem = "load error" if dependency.failure_kind == "load_error" else "import unavailable"
            log.error(
                "[app] Core startup dependency %s (%s %s): %s",
                dependency.package_name,
                dependency.import_name,
                problem,
                dependency.error,
            )
        log.error(
            "[app] Install the reported core package(s), or explicitly set "
            "PFS_AUTO_INSTALL_DEPENDENCIES=1 for the legacy auto-install flow.",
        )
        raise SystemExit(1)

    # Legacy compatibility only: pip is never invoked unless the user explicitly
    # opts in. Targets are restricted to import failures in explicit manifests.
    if install_targets:
        packages = " ".join(install_targets)
        log.warning("[app] Explicit legacy auto-install enabled; installing: %s", packages)
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", *install_targets])
        except subprocess.CalledProcessError:
            log.exception("[app] Some packages failed to install")
            raise SystemExit(1)
        log.info("[app] Installation complete. Restarting...")
        # os.execv 在 Windows 上行为不稳定，改用 subprocess 启动新进程后退出。
        try:
            subprocess.Popen([sys.executable] + sys.argv)
        except Exception as exc:
            log.warning("[app] Auto-restart failed: %s", exc)
        sys.exit(0)

    log.info("[app] Core startup requirements satisfied.")


# 托管平台会在构建阶段安装依赖；运行时重启会被平台视为进程崩溃。
# 本地环境仍保留自动依赖检查，兼容现有桌面启动方式。
is_managed_runtime = (
    os.environ.get("VERCEL") == "1"
    or bool(os.environ.get("RAILWAY_PROJECT_ID"))
    or os.environ.get("PFS_SKIP_DEPENDENCY_CHECK") == "1"
)
if not is_managed_runtime:
    ensure_requirements()

# -------------------------------
# 应用本地兼容性补丁
# -------------------------------
try:
    from infrastructure import local_patches

    local_patches.apply()
except ImportError as e:
    log.debug("[app] local_patches not available: %s", e)

# -------------------------------
# 自动判断运行环境
# -------------------------------
is_vercel = os.environ.get("VERCEL") == "1"

# 日志目录
from config.product_identity import PRODUCT_NAME
from infrastructure.paths import data_path

log_dir = data_path("outputs", "Log")
os.environ.setdefault("LOG_DIR", str(log_dir))

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).parent))

# -------------------------------
# 初始化日志
# -------------------------------
from infrastructure.logging_setup import setup_logging

setup_logging(level=20)  # logging.INFO

# -------------------------------
# 启动后台清理（仅本地；Vercel 短生命周期不需要）
# -------------------------------
if not is_vercel:
    from infrastructure.cleanup import setup_cleanup

    setup_cleanup()

# -------------------------------
# 导入 Flask app
# -------------------------------
from api import create_app

app = create_app()


# -------------------------------
# 启动配置（统一 waitress，开发/生产同一入口）
# -------------------------------
def _serve(app, host: str, port: int):
    """统一 WSGI 入口：本地用 waitress，Linux 私有化可切 gunicorn。

    waitress ≥ 3.0 支持 chunked transfer-encoding，SSE 流式正常。
    保留 gunicorn 作为 Linux 生产备选（通过环境变量 PFS_WSGI=gunicorn 切换）。
    """
    wsgi = env("PFS_WSGI", "waitress").lower()
    if wsgi == "gunicorn":
        # 仅 Linux 可用；本地 Windows 不走这条路径
        from gunicorn.app.base import BaseApplication

        class _GunicornApp(BaseApplication):
            def __init__(self, app, options=None):
                self.app = app
                self.options = options or {}
                super().__init__()

            def load_config(self):
                for k, v in self.options.items():
                    self.cfg.set(k.lower(), v)

            def load(self):
                return self.app

        _GunicornApp(
            app,
            {
                "bind": f"{host}:{port}",
                "workers": int(env("PFS_WORKERS", "1")),
                "worker_class": "sync",
                "timeout": 300,  # SSE 长连接
            },
        ).run()
    else:
        try:
            from waitress import serve as waitress_serve
        except ImportError as e:
            log.error("[app] waitress 未安装，请运行 pip install waitress>=3.0: %s", e)
            log.error("[app] 或临时回退：PFS_WSGI=flask python app.py")
            sys.exit(1)
        waitress_serve(
            app,
            host=host,
            port=port,
            # SSE 长连接需要足够大的 send_buffer 和无 send_bytes 限制
            send_bytes=1,  # 每次发送 1 字节边界，让 chunked 立即 flush
            inbuf_overflow=1024 * 1024,
            connection_limit=100,
            channel_timeout=300,  # SSE 长连接超时
        )


if __name__ == "__main__":
    port = int(os.environ.get("PFS_PORT") or os.environ.get("PORT") or os.environ.get("AGENT_PORT", 5001))
    host = env("PFS_HOST", "0.0.0.0")
    log.info("\n  %s → http://localhost:%s\n", PRODUCT_NAME, port)
    log.info("  [WSGI] %s  (PFS_WSGI=gunicorn 可切换)\n", env("PFS_WSGI", "waitress"))
    _serve(app, host, port)
