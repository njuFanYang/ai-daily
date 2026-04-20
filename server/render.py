"""
Render: 把 data/picked/<date>.json 渲染成 web/archive/<date>.html。
同时刷新 web/index.html (= 最新一天) 和 web/archive/index.html (归档目录)。

可选：调用 Claude 给当日生成一段 intro headline + 50 字开篇。
默认使用 fallback 标题（前 3 篇关键词拼接），不烧额外 token。
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
PICKED_DIR = ROOT / "data" / "picked"
WEB_DIR = PROJECT_ROOT / "web"
ARCHIVE_DIR = WEB_DIR / "archive"
TEMPLATE_DIR = WEB_DIR / "templates"
LOG_DIR = ROOT / "logs"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "render.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("render")

env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def fmt_date(date_str: str) -> str:
    """2026-04-20 → 2026 年 4 月 20 日（周一）"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return f"{dt.year} 年 {dt.month} 月 {dt.day} 日（{weekdays[dt.weekday()]}）"


def derive_headline(articles: list[dict]) -> str:
    """从前 3 篇 zh_title 派生一个综合标题。"""
    titles = [a.get("zh_title", "").strip() for a in articles[:3] if a.get("zh_title")]
    if not titles:
        return "今日 AI 精选"
    # 取最具信号的第一篇做主标题
    return titles[0]


def render_daily(date: str) -> Path:
    picked_file = PICKED_DIR / f"{date}.json"
    if not picked_file.exists():
        raise FileNotFoundError(f"picked file not found: {picked_file}")

    data = json.loads(picked_file.read_text(encoding="utf-8"))
    articles = data["articles"]

    # 算来源数量、查上一/下一日
    source_count = len({a["source"] for a in articles})
    all_dates = sorted([p.stem for p in PICKED_DIR.glob("*.json")])
    idx = all_dates.index(date) if date in all_dates else -1
    prev_date = all_dates[idx - 1] if idx > 0 else None
    next_date = all_dates[idx + 1] if 0 <= idx < len(all_dates) - 1 else None

    # 估算 total_input：从 scored 文件读
    scored_file = ROOT / "data" / "scored" / f"{date}.json"
    total_input = 0
    if scored_file.exists():
        total_input = json.loads(scored_file.read_text(encoding="utf-8")).get("total_input", 0)

    ctx = {
        "date": date,
        "date_display": fmt_date(date),
        "headline": derive_headline(articles),
        "intro": data.get("intro"),
        "articles": articles,
        "source_count": source_count,
        "total_input": total_input,
        "prev_date": prev_date,
        "next_date": next_date,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "assets_prefix": "../",
        "root_prefix": "../",
    }
    html = env.get_template("daily.html").render(**ctx)
    out = ARCHIVE_DIR / f"{date}.html"
    out.write_text(html, encoding="utf-8")
    log.info("rendered daily → %s", out)
    return out


def render_index(latest_date: str) -> Path:
    """index.html = 最新一天的副本（链接修正为同级 assets）"""
    latest_picked = PICKED_DIR / f"{latest_date}.json"
    data = json.loads(latest_picked.read_text(encoding="utf-8"))
    articles = data["articles"]
    source_count = len({a["source"] for a in articles})
    scored_file = ROOT / "data" / "scored" / f"{latest_date}.json"
    total_input = 0
    if scored_file.exists():
        total_input = json.loads(scored_file.read_text(encoding="utf-8")).get("total_input", 0)

    ctx = {
        "date": latest_date,
        "date_display": fmt_date(latest_date),
        "headline": derive_headline(articles),
        "intro": data.get("intro"),
        "articles": articles,
        "source_count": source_count,
        "total_input": total_input,
        "prev_date": None,
        "next_date": None,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "assets_prefix": "",
        "root_prefix": "",
    }
    html = env.get_template("daily.html").render(**ctx)
    out = WEB_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    log.info("rendered index → %s", out)
    return out


def render_archive_index() -> Path:
    """归档目录页"""
    entries = []
    for picked_file in sorted(PICKED_DIR.glob("*.json"), reverse=True):
        date = picked_file.stem
        try:
            d = json.loads(picked_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        entries.append({
            "date": date,
            "date_display": fmt_date(date),
            "file": f"{date}.html",
            "headline": derive_headline(d.get("articles", [])),
            "count": len(d.get("articles", [])),
        })

    ctx = {
        "entries": entries,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "assets_prefix": "../",
        "root_prefix": "../",
    }
    html = env.get_template("archive_index.html").render(**ctx)
    out = ARCHIVE_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    log.info("rendered archive index (%d entries) → %s", len(entries), out)
    return out


def main(date: str | None = None):
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    render_daily(date)
    # latest = 最新一份 picked
    all_dates = sorted([p.stem for p in PICKED_DIR.glob("*.json")])
    if all_dates:
        render_index(all_dates[-1])
    render_archive_index()
    log.info("render done. open %s", WEB_DIR / "index.html")


if __name__ == "__main__":
    main()
