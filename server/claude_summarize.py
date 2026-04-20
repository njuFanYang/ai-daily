"""
Summarize: 给 picked 里的每篇文章生成中文摘要。
直接修改 data/picked/<date>.json 加上 zh_summary 字段。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from claude_call import call_claude

ROOT = Path(__file__).resolve().parent
PICKED_DIR = ROOT / "data" / "picked"
LOG_DIR = ROOT / "logs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "summarize.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("summarize")

SUMMARY_PROMPT = """用地道流畅的中文为下面这篇 AI/LLM 文章写一段 150-250 字的摘要，给中文 AI 工程师快速判断是否值得点原文。

写作要求（务必遵守）：
1. 直接陈述事实，不要"本文介绍了""作者认为""文章讨论了"等空洞开头
2. 突出：核心结论、关键数据/数字、技术细节、对工程师的实际意义
3. 不要复述标题，要给标题之外的增量信息
4. AI 领域专业术语没有公认中文翻译时保留英文原词，例如：
   RAG、agent、prompt、context window、SFT、RLHF、MoE、tool use、embedding、benchmark、SOTA、fine-tuning
5. 中文要顺畅自然，不要翻译腔、不要诘屈聱牙
6. 如果原文摘要信息不足无法判断细节，写"原文未透露具体细节"也比胡编强

只输出摘要文本本身，没有标题、引号、markdown 标记或任何前后缀。

文章信息：
标题：{title}
来源：{source}
发布：{published}
链接：{url}

原文摘要/正文片段：
{raw_summary}
"""


def summarize_one(article: dict, fast: bool = True) -> str:
    raw = (article.get("summary") or "").strip()
    if not raw:
        raw = "(原文 list 页未提供摘要，仅有标题和链接)"
    prompt = SUMMARY_PROMPT.format(
        title=article["title"],
        source=article["source"],
        published=article.get("published", "")[:10],
        url=article["url"],
        raw_summary=raw[:2000],
    )
    return call_claude(prompt, timeout=300, fast=fast).strip()


def main(date: str | None = None, fast: bool = True):
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    picked_file = PICKED_DIR / f"{date}.json"
    if not picked_file.exists():
        log.error("picked file missing: %s", picked_file)
        return None

    data = json.loads(picked_file.read_text(encoding="utf-8"))
    for i, art in enumerate(data["articles"], 1):
        if art.get("zh_summary"):
            log.info("  [%d/%d] cached, skip", i, len(data["articles"]))
            continue
        log.info("  [%d/%d] summarizing %s", i, len(data["articles"]), art["title"][:60])
        try:
            art["zh_summary"] = summarize_one(art, fast=fast)
        except Exception as e:
            log.warning("summarize failed: %s", e)
            art["zh_summary"] = f"(摘要生成失败：{e})"

    data["summarized_at"] = datetime.now(timezone.utc).isoformat()
    picked_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("summarized %d articles → %s", len(data["articles"]), picked_file)
    return picked_file


if __name__ == "__main__":
    main()
