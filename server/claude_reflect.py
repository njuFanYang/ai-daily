"""
Reflection: 每 14 天回看过去选文，让 Claude 自我修正 weights.yaml。
- 输入：过去 N 天的 picked + 当前 weights.yaml
- Claude 输出：新 weights + reflection 报告
- 旧 weights 归档到 data/weights_history/

运行：python claude_reflect.py [--days 14] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from claude_call import call_claude

ROOT = Path(__file__).resolve().parent
PICKED_DIR = ROOT / "data" / "picked"
WEIGHTS_FILE = ROOT / "weights.yaml"
HISTORY_DIR = ROOT / "data" / "weights_history"
REFLECTIONS_DIR = ROOT / "data" / "reflections"
LOG_DIR = ROOT / "logs"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "reflect.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("reflect")


REFLECT_PROMPT = """你是 AI 资讯系统的"自我反思"模块，任务是回顾过去 {days} 天的精选文章，校正打分权重，让系统更贴近一个 AI 工程师真正想读的内容。

当前权重配置 (YAML):
```yaml
{current_weights}
```

过去 {days} 天每日 Top 10 精选 (id, source, zh_title, topics, pick_reason, score 截选):
```json
{recent_picks}
```

请回答以下 5 个问题，每个 80-150 字，最后给出新的 weights.yaml：

1. **主题分布观察**：哪些主题反复出现且事后被证明重要？哪些主题虽然权重高但选出的内容质量平庸？
2. **来源观察**：哪些源持续高质（值得提权）？哪些源被频繁选中却内容平庸（值得降权）？
3. **遗漏检查**：从你对 AI 行业近 14 天的认知，是否有"大家都在讨论"但系统没选到的事件？说明可能漏掉的源或主题。
4. **维度配比**：4 个 signal_weights (source_authority/topic_match/engagement/recency) 当前 35/30/20/15，是否需要重新平衡？比如近期模型迭代极快应该给 recency 更多权重？
5. **新主题建议**：是否需要新增 / 删除 / 改名 topic 类别？

完成回答后，输出新版 weights.yaml 完整内容（即可直接覆盖当前文件）。
要求：
- 调整幅度温和：单个 source/topic 权重一次最多 ±0.15
- version 加 1，last_updated 改为 {today}，next_reflection 改为 {next_reflection}
- 保留所有现有 source/topic 键，可新增不可删除（除非明确建议）
- 配置之外的所有结构（engagement_normalization, recency 等）原样保留

输出格式（务必严格遵守）：

# Reflection Report

## 1. 主题分布观察
...

## 2. 来源观察
...

## 3. 遗漏检查
...

## 4. 维度配比
...

## 5. 新主题建议
...

## 调整摘要
- 提权: source X 0.7→0.85; topic Y 0.6→0.75
- 降权: ...
- 新增: ...

# New weights.yaml

```yaml
<完整 YAML 内容>
```
"""


def collect_recent_picks(days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    rows = []
    for f in sorted(PICKED_DIR.glob("*.json")):
        date = f.stem
        try:
            d = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        for a in data.get("articles", []):
            rows.append({
                "date": date,
                "rank": a.get("rank"),
                "source": a.get("source"),
                "zh_title": a.get("zh_title"),
                "topics": a.get("topics"),
                "pick_reason": a.get("pick_reason"),
                "score": a.get("score"),
            })
    return rows


def extract_yaml_block(report: str) -> str | None:
    """从 reflection 报告中提取 ```yaml ... ``` 代码块。"""
    import re
    matches = re.findall(r"```ya?ml\s*\n(.*?)```", report, re.DOTALL)
    return matches[-1].strip() if matches else None


def main(days: int = 14, dry_run: bool = False):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    next_reflection = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")

    picks = collect_recent_picks(days)
    if len(picks) < 10:
        log.warning("only %d picks in last %d days; reflection postponed", len(picks), days)
        return None
    log.info("collected %d picks across %d days", len(picks), days)

    current_weights = WEIGHTS_FILE.read_text(encoding="utf-8")
    prompt = REFLECT_PROMPT.format(
        days=days,
        current_weights=current_weights,
        recent_picks=json.dumps(picks, ensure_ascii=False, indent=2),
        today=today,
        next_reflection=next_reflection,
    )

    log.info("calling Claude for reflection (this may take 30-60s)...")
    report = call_claude(prompt, timeout=600)

    # 写报告
    report_file = REFLECTIONS_DIR / f"reflection_{today}.md"
    report_file.write_text(report, encoding="utf-8")
    log.info("report → %s", report_file)

    # 提取新 weights
    new_yaml = extract_yaml_block(report)
    if not new_yaml:
        log.error("no yaml block found in reflection; weights unchanged")
        return report_file

    # 验证 yaml 可解析
    try:
        parsed = yaml.safe_load(new_yaml)
        assert "signal_weights" in parsed and "source_weights" in parsed
    except Exception as e:
        log.error("new weights invalid (%s); not applying", e)
        return report_file

    if dry_run:
        log.info("[dry-run] would write new weights; preview top:")
        log.info("\n%s", new_yaml[:600])
        return report_file

    # 归档旧 weights，写新 weights
    history_file = HISTORY_DIR / f"weights_v{parsed.get('version', '?') - 1}_{today}.yaml"
    shutil.copy2(WEIGHTS_FILE, history_file)
    WEIGHTS_FILE.write_text(new_yaml, encoding="utf-8")
    log.info("archived old → %s", history_file)
    log.info("applied new weights v%s", parsed.get("version"))
    return report_file


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    main(days=args.days, dry_run=args.dry_run)
