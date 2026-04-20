"""
Score: 读 data/raw/<date>.json + weights.yaml，按 4 维加权打分。

输出 data/scored/<date>.json，按 score 降序排列。
默认保留 top 60 给 pick 阶段。

4 维：
  source_authority * 35%
  topic_match      * 30%   (关键词启发式 + topic_weights 加权)
  engagement       * 20%
  recency          * 15%
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dateutil import parser as dateparser

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
SCORED_DIR = ROOT / "data" / "scored"
WEIGHTS_FILE = ROOT / "weights.yaml"
LOG_DIR = ROOT / "logs"
SCORED_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "score.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("score")

# 主题关键词（用于启发式匹配，Claude 精选时再校正）
TOPIC_KEYWORDS = {
    "agent_architecture": ["agent", "agentic", "tool use", "tool-use", "multi-step", "autonomy", "autonomous", "react", "swarm"],
    "llm_engineering": ["prompt", "prompting", "context window", "context engineering", "system prompt", "few-shot"],
    "model_release": ["release", "launch", "introducing", "announce", "announcing", "now available", "we are excited", "general availability", "ga"],
    "benchmark_eval": ["benchmark", "evaluation", "evals", "swe-bench", "mmlu", "humaneval", "leaderboard", "scoring"],
    "rag": ["rag", "retrieval", "retrieval-augmented", "vector db", "vector search", "embedding"],
    "multimodal": ["image", "video", "audio", "vision", "multimodal", "vlm", "speech", "tts"],
    "fine_tuning": ["fine-tun", "finetun", "fine tun", "lora", "peft", "sft", "rlhf", "dpo", "post-training"],
    "reasoning": ["reasoning", "chain of thought", "chain-of-thought", "cot", "thinking", "extended thinking", "o1", "o3", "deepseek-r1"],
    "safety_alignment": ["safety", "alignment", "harm", "bias", "jailbreak", "red team", "red-team", "adversarial"],
    "inference_hardware": ["gpu", "tpu", "inference", "latency", "throughput", "vllm", "tensorrt", "kv cache"],
    "open_source": ["open source", "open-source", "open weight", "open-weight", "apache 2.0", "mit license"],
    "developer_tools": ["sdk", "cli", "ide", "vscode", "extension", "mcp", "framework", "library"],
    "business_news": ["funding", "raised", "valuation", "ipo", "acquired", "billion", "investment", "lawsuit"],
    "policy_regulation": ["regulation", "policy", "executive order", "eu ai act", "compliance", "governance"],
}

DEFAULT_SOURCE_WEIGHT = 0.5


# ============== Scoring ==============

def topic_match_score(article: dict, topic_weights: dict) -> tuple[float, list[str]]:
    """启发式主题匹配。返回 (得分, 命中标签列表)。"""
    text = (article["title"] + " " + article.get("summary", "")).lower()
    hits = []
    best = 0.0
    for topic, keywords in TOPIC_KEYWORDS.items():
        count = sum(text.count(k) for k in keywords)
        if count == 0:
            continue
        local = min(count, 3) / 3.0  # 0.33 / 0.67 / 1.0
        weighted = topic_weights.get(topic, 0.5) * local
        hits.append(topic)
        if weighted > best:
            best = weighted
    if not hits:
        # 全不命中，按 other 兜底
        best = topic_weights.get("other", 0.5) * 0.3
        hits = ["other"]
    return best, hits


def recency_score(published_iso: str, cfg: dict) -> float:
    try:
        pub = dateparser.parse(published_iso)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
    except Exception:
        return 0.3
    age_h = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
    if age_h < 0:
        return 1.0
    if age_h >= cfg["cutoff_hours"]:
        return 0.0
    if age_h <= cfg["full_score_hours"]:
        return 1.0
    excess = age_h - cfg["full_score_hours"]
    return 0.5 ** (excess / cfg["half_life_hours"])


def engagement_score(article: dict, norm_cfg: dict) -> float:
    metric = article["engagement"]["metric"]
    value = article["engagement"]["value"]
    if metric is None or value is None:
        return 0.5  # 无信号，中性兜底
    src_cfg = norm_cfg.get(article["source"]) or norm_cfg["default"]
    if src_cfg["metric"] != metric:
        return 0.5
    if metric == "trending_rank":
        # rank=1 满分，rank>=full_score_at*25 接近 0
        return max(0.0, 1.0 - (value - 1) / 25.0)
    full = src_cfg["full_score_at"]
    if full <= 0:
        return 0.5
    return min(1.0, value / full)


def score_article(article: dict, weights: dict) -> dict:
    sig_w = weights["signal_weights"]
    src_auth = weights["source_weights"].get(article["source"], DEFAULT_SOURCE_WEIGHT)
    topic_val, hit_topics = topic_match_score(article, weights["topic_weights"])
    engage_val = engagement_score(article, weights["engagement_normalization"])
    rec_val = recency_score(article["published"], weights["recency"])

    total = (
        sig_w["source_authority"] * src_auth
        + sig_w["topic_match"] * topic_val
        + sig_w["engagement"] * engage_val
        + sig_w["recency"] * rec_val
    )
    return {
        **article,
        "score": round(total, 4),
        "score_breakdown": {
            "source_authority": round(src_auth, 3),
            "topic_match": round(topic_val, 3),
            "engagement": round(engage_val, 3),
            "recency": round(rec_val, 3),
        },
        "topic_tags_heuristic": hit_topics,
    }


# ============== Main ==============

def main(date: str | None = None, top_n: int = 60):
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw_file = RAW_DIR / f"{date}.json"
    if not raw_file.exists():
        log.error("raw file not found: %s", raw_file)
        return None

    raw = json.loads(raw_file.read_text(encoding="utf-8"))
    weights = yaml.safe_load(WEIGHTS_FILE.read_text(encoding="utf-8"))

    scored = [score_article(a, weights) for a in raw["articles"]]
    scored.sort(key=lambda x: x["score"], reverse=True)

    out_file = SCORED_DIR / f"{date}.json"
    out_file.write_text(json.dumps({
        "date": date,
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "weights_version": weights.get("version"),
        "total_input": len(scored),
        "top_n": top_n,
        "articles": scored[:top_n],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("scored %d → top %d → %s", len(scored), top_n, out_file)
    log.info("Top 5 preview:")
    for i, a in enumerate(scored[:5], 1):
        log.info("  %d. [%.3f] %s | %s", i, a["score"], a["source"], a["title"][:80])
    return out_file


if __name__ == "__main__":
    main()
