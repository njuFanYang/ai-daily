"""
Claude CLI 调用封装。统一通过 stdin 传 prompt（避免命令行长度限制），
返回字符串结果或解析后的 JSON。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("claude_call")

CLAUDE_BIN = "claude"  # 已在 PATH


def call_claude(prompt: str, timeout: int = 600, fast: bool = False) -> str:
    """调用 claude -p，返回模型纯文本输出。fast 参数预留。"""
    cmd = [CLAUDE_BIN, "-p", "--output-format", "json"]
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude returned non-JSON: {proc.stdout[:500]}")
    if data.get("is_error"):
        raise RuntimeError(f"claude is_error: {data.get('result')[:500]}")
    return data.get("result", "")


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$", re.MULTILINE)


def call_claude_json(prompt: str, timeout: int = 600, fast: bool = False):
    """调用 claude 并把输出解析为 JSON。自动剥 markdown 代码围栏。"""
    raw = call_claude(prompt, timeout=timeout, fast=fast).strip()
    cleaned = _FENCE_RE.sub("", raw).strip()
    # 尝试找第一个 [ 或 { 作为 JSON 起点（容错前置废话）
    for start_idx in (cleaned.find("["), cleaned.find("{")):
        if start_idx >= 0:
            try:
                return json.loads(cleaned[start_idx:])
            except json.JSONDecodeError:
                continue
    return json.loads(cleaned)
