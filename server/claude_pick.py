"""
Pick: 从 data/scored/<date>.json 的 top 60 中，让 Claude 精选出最有价值的 10 条。
输出 data/picked/<date>.json (含 topic tags + zh_title + reason，但不含正文摘要)。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from claude_call import call_claude_json

ROOT = Path(__file__).resolve().parent
SCORED_DIR = ROOT / "data" / "scored"
PICKED_DIR = ROOT / "data" / "picked"
LOG_DIR = ROOT / "logs"
PICKED_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "pick.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("pick")

PICK_PROMPT = """你是资深 AI/LLM 资讯编辑，给中文 AI 工程师做每日精选。

任务：从下面算法初筛的 {n} 篇候选文章中，挑出今天最值得读的 10 篇。

选择标准（按重要性降序）：
1. 实质性新内容：新模型/新研究/新工具/重大事件 > 综述/复盘 > 营销稿
2. 技术深度：值得花 5+ 分钟读完，跳过 30 秒标题党
3. 对一线 AI 工程师的实用价值
4. 多样性：同主题最多选 3 条，避免 10 篇都是模型发布
5. 来源权威性：算法分已考虑，但你可基于内容质量调整
6. 排除：重复主题、纯八卦、低质量博客、明显标题党

为每篇选中的文章打主题标签（从下面列表 1-3 个），并写一个 10-25 字的中文标题（要传神，不要直译）。

主题列表：agent_architecture, llm_engineering, model_release, benchmark_eval, rag, multimodal, fine_tuning, reasoning, safety_alignment, inference_hardware, open_source, developer_tools, business_news, policy_regulation, other

严格只输出 JSON 数组，不要任何其他文字、说明、markdown 围栏：
[
  {{"id": "<原文 id>", "rank": 1, "topics": ["..."], "zh_title": "...", "reason": "<15-30 字: 为什么选>"}},
  ...共 10 条...
]

候选文章 JSON：
{candidates}
"""


def simplify_for_prompt(article: dict) -> dict:
    """缩减字段，控制 prompt 体积。"""
    return {
        "id": article["id"],
        "source": article["source"],
        "title": article["title"],
        "summary": (article.get("summary") or "")[:400],
        "url": article["url"],
        "score": article.get("score"),
        "topic_hits": article.get("topic_tags_heuristic", []),
        "published": article.get("published", "")[:10],
    }


def main(date: str | None = None):
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scored_file = SCORED_DIR / f"{date}.json"
    if not scored_file.exists():
        log.error("scored file missing: %s", scored_file)
        return None

    scored = json.loads(scored_file.read_text(encoding="utf-8"))
    candidates = [simplify_for_prompt(a) for a in scored["articles"]]
    log.info("sending %d candidates to Claude for picking", len(candidates))

    prompt = PICK_PROMPT.format(
        n=len(candidates),
        candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
    )
    picks = call_claude_json(prompt, timeout=600)

    if not isinstance(picks, list):
        raise RuntimeError(f"expected list, got {type(picks).__name__}")

    # 用 id 关联回完整文章
    by_id = {a["id"]: a for a in scored["articles"]}
    picked_full = []
    for p in picks:
        full = by_id.get(p.get("id"))
        if not full:
            log.warning("pick id not found in scored: %s", p.get("id"))
            continue
        picked_full.append({
            **full,
            "rank": p.get("rank"),
            "topics": p.get("topics", []),
            "zh_title": p.get("zh_title", ""),
            "pick_reason": p.get("reason", ""),
        })

    picked_full.sort(key=lambda x: x.get("rank") or 99)

    out_file = PICKED_DIR / f"{date}.json"
    out_file.write_text(json.dumps({
        "date": date,
        "picked_at": datetime.now(timezone.utc).isoformat(),
        "count": len(picked_full),
        "articles": picked_full,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("picked %d → %s", len(picked_full), out_file)
    for a in picked_full:
        log.info("  #%s %s | %s", a.get("rank"), a.get("zh_title"), a["title"][:60])
    return out_file


if __name__ == "__main__":
    main()
