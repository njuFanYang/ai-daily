"""
Daily pipeline: crawl → score → pick → summarize → render.
顺序执行，任一步失败立即终止，记录到 logs/pipeline.log。

可选：python pipeline.py --date 2026-04-20 重跑指定日期。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import crawler
import score
import claude_pick
import claude_summarize
import render

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s pipeline | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("pipeline")


def run(date: str | None = None, skip_crawl: bool = False, skip_pick: bool = False):
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("===== pipeline start: %s =====", date)
    started = datetime.now()

    try:
        if not skip_crawl:
            log.info("[1/5] crawling sources...")
            crawler.main()
        else:
            log.info("[1/5] skip crawl")

        log.info("[2/5] scoring articles...")
        score.main(date=date)

        if not skip_pick:
            log.info("[3/5] Claude picking top 10...")
            claude_pick.main(date=date)
        else:
            log.info("[3/5] skip pick")

        log.info("[4/5] Claude summarizing in Chinese...")
        claude_summarize.main(date=date)

        log.info("[5/5] rendering HTML...")
        render.main(date=date)

        elapsed = (datetime.now() - started).total_seconds()
        log.info("===== pipeline done in %.1fs =====", elapsed)
        return True
    except Exception as e:
        log.exception("pipeline failed: %s", e)
        return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD; default = today UTC")
    ap.add_argument("--skip-crawl", action="store_true")
    ap.add_argument("--skip-pick", action="store_true")
    args = ap.parse_args()
    ok = run(date=args.date, skip_crawl=args.skip_crawl, skip_pick=args.skip_pick)
    sys.exit(0 if ok else 1)
