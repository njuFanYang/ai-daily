"""
Crawler: 按 sources.yaml 拉取所有源，归一化后写 data/raw/YYYY-MM-DD.json

每条文章 schema:
{
  "id": str,                # url 的 sha256[:16]
  "source": str,
  "category": str,
  "title": str,
  "url": str,
  "summary": str,
  "published": str,         # ISO 8601
  "engagement": {"metric": str|None, "value": int|None},
  "raw_meta": dict
}
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.yaml"
DATA_DIR = ROOT / "data" / "raw"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 20.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "crawler.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("crawler")


def article_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def clean_text(text: str | None, max_len: int = 1200) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def to_iso(value) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    try:
        if isinstance(value, time.struct_time):
            return datetime.fromtimestamp(time.mktime(value), tz=timezone.utc).isoformat()
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        if isinstance(value, str):
            return dateparser.parse(value).astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    return datetime.now(timezone.utc).isoformat()


# ============== Fetchers ==============

def fetch_rss(source: dict, client: httpx.Client) -> list[dict]:
    url = source["url"]
    try:
        r = client.get(url, headers={"User-Agent": UA})
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except Exception as e:
        log.warning("rss fetch failed %s: %s", source["name"], e)
        return []
    out = []
    for e in feed.entries[:30]:
        link = e.get("link") or ""
        if not link:
            continue
        out.append({
            "id": article_id(link),
            "source": source["name"],
            "category": source.get("category", "other"),
            "title": clean_text(e.get("title", ""), 300),
            "url": link,
            "summary": clean_text(e.get("summary") or e.get("description", "")),
            "published": to_iso(e.get("published") or e.get("updated") or e.get("published_parsed")),
            "engagement": {"metric": None, "value": None},
            "raw_meta": {"author": e.get("author", "")},
        })
    return out


def fetch_arxiv_rss(source: dict, client: httpx.Client) -> list[dict]:
    items = fetch_rss(source, client)
    for it in items:
        # arxiv 摘要里常带 "Title: ... Authors: ..." 噪音，简单清理
        s = it["summary"]
        s = re.sub(r"^.*?Abstract:\s*", "", s, flags=re.I)
        it["summary"] = s[:1000]
    return items


def fetch_hn_algolia(source: dict, client: httpx.Client) -> list[dict]:
    try:
        r = client.get(source["url"], headers={"User-Agent": UA})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("hn fetch failed: %s", e)
        return []
    out = []
    for hit in data.get("hits", [])[:30]:
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        out.append({
            "id": article_id(link),
            "source": source["name"],
            "category": source.get("category", "forum"),
            "title": clean_text(hit.get("title") or hit.get("story_title") or "", 300),
            "url": link,
            "summary": clean_text(hit.get("story_text") or ""),
            "published": to_iso(hit.get("created_at")),
            "engagement": {"metric": "points", "value": int(hit.get("points") or 0)},
            "raw_meta": {
                "comments": hit.get("num_comments"),
                "author": hit.get("author"),
                "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            },
        })
    return out


def fetch_reddit_json(source: dict, client: httpx.Client) -> list[dict]:
    try:
        r = client.get(source["url"], headers={"User-Agent": UA})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("reddit fetch failed %s: %s", source["name"], e)
        return []
    out = []
    for child in data.get("data", {}).get("children", [])[:25]:
        d = child.get("data", {})
        link = d.get("url_overridden_by_dest") or f"https://www.reddit.com{d.get('permalink', '')}"
        title = d.get("title") or ""
        if d.get("stickied"):
            continue
        out.append({
            "id": article_id(link),
            "source": source["name"],
            "category": source.get("category", "forum"),
            "title": clean_text(title, 300),
            "url": link,
            "summary": clean_text(d.get("selftext") or "", 800),
            "published": to_iso(d.get("created_utc")),
            "engagement": {"metric": "ups", "value": int(d.get("ups") or 0)},
            "raw_meta": {
                "subreddit": d.get("subreddit"),
                "comments": d.get("num_comments"),
                "permalink": f"https://www.reddit.com{d.get('permalink', '')}",
            },
        })
    return out


def fetch_hf_papers(source: dict, client: httpx.Client) -> list[dict]:
    """HF Papers 没有官方 RSS，抓 trending HTML。"""
    try:
        r = client.get(source["url"], headers={"User-Agent": UA})
        r.raise_for_status()
    except Exception as e:
        log.warning("hf papers fetch failed: %s", e)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    # HF papers 页面的论文卡片，每个 article 标签是一篇
    cards = soup.select("article")
    for rank, card in enumerate(cards[:25], start=1):
        a = card.find("a", href=re.compile(r"^/papers/"))
        if not a:
            continue
        href = "https://huggingface.co" + a.get("href", "")
        title_el = a.find("h3") or a
        title = clean_text(title_el.get_text(" ", strip=True), 300)
        # 摘要可能在卡片下方 p 标签
        summary_el = card.find("p")
        summary = clean_text(summary_el.get_text(" ", strip=True)) if summary_el else ""
        out.append({
            "id": article_id(href),
            "source": source["name"],
            "category": source.get("category", "papers"),
            "title": title,
            "url": href,
            "summary": summary,
            "published": datetime.now(timezone.utc).isoformat(),
            "engagement": {"metric": "trending_rank", "value": rank},
            "raw_meta": {"trending_rank": rank},
        })
    return out


def fetch_github_releases(source: dict, client: httpx.Client) -> list[dict]:
    """用于 dair-ai/ML-Papers-of-the-Week 这种以 commit/release 滚动更新的仓库。"""
    try:
        r = client.get(source["url"], headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("github fetch failed %s: %s", source["name"], e)
        return []
    out = []
    for c in data[:10]:
        commit = c.get("commit", {})
        msg = commit.get("message", "")
        first_line = msg.split("\n", 1)[0]
        link = c.get("html_url") or ""
        if not link:
            continue
        out.append({
            "id": article_id(link),
            "source": source["name"],
            "category": source.get("category", "papers"),
            "title": clean_text(first_line, 300),
            "url": link,
            "summary": clean_text(msg, 800),
            "published": to_iso(commit.get("author", {}).get("date")),
            "engagement": {"metric": None, "value": None},
            "raw_meta": {"sha": c.get("sha", "")[:8]},
        })
    return out


def fetch_anthropic_html(source: dict, client: httpx.Client) -> list[dict]:
    """Anthropic news/engineering 没有 RSS，从 list 页面抓链接。"""
    base_url = source["url"]
    path_prefix = "/" + base_url.rstrip("/").rsplit("/", 1)[-1] + "/"  # /news/ or /engineering/
    try:
        r = client.get(base_url, headers={"User-Agent": UA})
        r.raise_for_status()
    except Exception as e:
        log.warning("anthropic fetch failed %s: %s", source["name"], e)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    seen = set()
    out = []
    for a in soup.find_all("a", href=re.compile(rf"^{re.escape(path_prefix)}[^#?]+$")):
        href = a.get("href", "")
        if href in seen or href.rstrip("/") == path_prefix.rstrip("/"):
            continue
        seen.add(href)
        title = clean_text(a.get_text(" ", strip=True), 300)
        if not title:
            # 从 slug 派生
            slug = href.rstrip("/").rsplit("/", 1)[-1]
            title = slug.replace("-", " ").title()
        url = "https://www.anthropic.com" + href
        out.append({
            "id": article_id(url),
            "source": source["name"],
            "category": source.get("category", "lab_official"),
            "title": title,
            "url": url,
            "summary": "",  # list 页无摘要，pick 阶段需要时再抓详情
            "published": datetime.now(timezone.utc).isoformat(),
            "engagement": {"metric": None, "value": None},
            "raw_meta": {"slug": href},
        })
        if len(out) >= 25:
            break
    return out


FETCHERS = {
    "rss": fetch_rss,
    "arxiv_rss": fetch_arxiv_rss,
    "hn_algolia": fetch_hn_algolia,
    "reddit_json": fetch_reddit_json,
    "hf_papers": fetch_hf_papers,
    "github_releases": fetch_github_releases,
    "anthropic_html": fetch_anthropic_html,
}


# ============== Main ==============

def main():
    sources = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))["sources"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_file = DATA_DIR / f"{today}.json"

    all_articles: list[dict] = []
    seen_ids: set[str] = set()
    stats: dict[str, int] = {}

    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        for src in sources:
            if src.get("enabled", True) is False:
                log.info("skip disabled source %s", src["name"])
                continue
            fetcher = FETCHERS.get(src["type"])
            if not fetcher:
                log.warning("unknown type %s for %s", src["type"], src["name"])
                continue
            log.info("fetching %s (%s)", src["name"], src["type"])
            items = fetcher(src, client)
            kept = 0
            for it in items:
                if it["id"] in seen_ids:
                    continue
                seen_ids.add(it["id"])
                all_articles.append(it)
                kept += 1
            stats[src["name"]] = kept
            log.info("  -> %d items (kept %d after dedup)", len(items), kept)

    out_file.write_text(json.dumps({
        "date": today,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "total": len(all_articles),
        "articles": all_articles,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("wrote %d articles to %s", len(all_articles), out_file)
    return out_file


if __name__ == "__main__":
    main()
