"""
Daily pipeline: crawl → score → pick → summarize → render.
顺序执行，任一步失败立即终止，记录到 logs/pipeline.log。

可选：python pipeline.py --date 2026-04-20 重跑指定日期。
"""
from __future__ import annotations

import argparse
import logging
import subprocess
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

PROJECT_ROOT = ROOT.parent


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run git in the project root."""
    return subprocess.run(
        ["git", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def deploy_to_github(date: str) -> bool:
    """Commit + push web/ and picked data so GitHub Pages picks it up."""
    if not (PROJECT_ROOT / ".git").exists():
        log.info("no .git directory; skipping deploy")
        return False
    # Stage the artifacts we want public + history we keep
    for path in ["web", "server/data/picked", "server/data/reflections", "server/data/weights_history"]:
        full = PROJECT_ROOT / path
        if full.exists():
            git("add", path, check=False)
    # Anything staged?
    status = git("status", "--porcelain", check=False).stdout.strip()
    if not status:
        log.info("no changes to deploy")
        return True
    log.info("staged changes:\n%s", status[:800])
    msg = f"daily: {date} update"
    git("commit", "-m", msg)
    log.info("committed: %s", msg)
    try:
        push = git("push", "origin", "HEAD", check=True)
        log.info("pushed: %s", (push.stdout + push.stderr).strip()[:400])
        return True
    except subprocess.CalledProcessError as e:
        log.error("git push failed: %s", (e.stdout or "") + (e.stderr or ""))
        return False


def run(date: str | None = None, skip_crawl: bool = False, skip_pick: bool = False, skip_deploy: bool = False):
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

        log.info("[5/6] rendering HTML...")
        render.main(date=date)

        if not skip_deploy:
            log.info("[6/6] deploying to GitHub Pages...")
            deploy_to_github(date)
        else:
            log.info("[6/6] skip deploy")

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
    ap.add_argument("--skip-deploy", action="store_true")
    args = ap.parse_args()
    ok = run(
        date=args.date,
        skip_crawl=args.skip_crawl,
        skip_pick=args.skip_pick,
        skip_deploy=args.skip_deploy,
    )
    sys.exit(0 if ok else 1)
