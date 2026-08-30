#!/usr/bin/env python3
"""Restricted PFS remote-runner entry point.

Only the fixed preflight JSON protocol is exposed. Arbitrary shell commands
and training execution are intentionally outside this entry point.
"""
from __future__ import annotations

import argparse
import json
import platform


def preflight() -> int:
    try:
        import torch
    except ImportError:
        print(json.dumps({"status": "not_ready", "message": "PyTorch 未安装"}, ensure_ascii=False))
        return 2
    if not torch.cuda.is_available():
        print(json.dumps({"status": "not_ready", "message": "未检测到可用 CUDA"}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "ready",
        "version": "0.1",
        "python": platform.python_version(),
        "cuda": True,
        "gpu_name": torch.cuda.get_device_name(0),
    }, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="pfs_remote_runner")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.preflight and args.json:
        return preflight()
    parser.error("only --preflight --json is supported")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
