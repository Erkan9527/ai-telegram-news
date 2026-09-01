#!/usr/bin/env python3
"""AI 资讯 → Telegram。适合本地试跑 + GitHub Actions 定时。"""

from __future__ import annotations

import html
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent
SEEN_PATH = ROOT / "data" / "seen.json"
FEEDS_PATH = ROOT / "feeds.yaml"

AI_KEYWORDS = (
    "ai",
    "artificial intelligence",
    "llm",
    "gpt",
    "openai",
    "anthropic",
    "claude",
    "gemini",
    "deepmind",
    "machine learning",
    "neural",
    "transformer",
    "hugging face",
    "diffusion",
    "agent",
    "大模型",
    "人工智能",
    "生成式",
    "智能体",
    "具身智能",
    "多模态",
    "chatgpt",
    "AIGC",
    "算力",
    "英伟达",
    "nvidia",
)

# 这些源默认视为 AI/科技相关，不做关键词过滤
ALWAYS_AI_FEED_MARKERS = (
    "openai",
    "google ai",
    "techcrunch ai",
    "venturebeat",
    "verge ai",
    "nvidia",
    "mit tech",
    "量子位",
    "雷峰网",
    "infoq",
    "github ai ranking",
)


def load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"缺少环境变量: {name}")
    return value


def load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        return set(data.get("urls", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen(urls: set[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 只保留最近 3000 条，避免文件无限长大
    trimmed = list(urls)[-3000:]
    SEEN_PATH.write_text(
        json.dumps({"urls": trimmed, "updated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    return None


def strip_html(text: str) -> str:
    # 极简去标签，够用在 RSS summary 上
    import re

    text = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(text.split())


def is_ai_related(title: str, summary: str, feed_name: str) -> bool:
    # 明确 AI 源默认放行；Trending/36氪/HN 等综合源做关键词过滤
    name_l = feed_name.lower()
    if any(x in name_l for x in ALWAYS_AI_FEED_MARKERS):
        return True
    text = f"{title} {strip_html(summary)}".lower()
    return any(k.lower() in text for k in AI_KEYWORDS)


def fetch_entries(lookback_hours: int) -> list[dict]:
    with FEEDS_PATH.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    feeds = config.get("feeds") or []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    items: list[dict] = []
    headers = {"User-Agent": "ai-telegram-news/1.0 (+https://github.com; RSS reader)"}

    for feed in feeds:
        name = feed.get("name", "unknown")
        url = feed.get("url")
        if not url:
            continue
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        except Exception as exc:  # noqa: BLE001
            print(f"[skip] {name}: {exc}")
            continue
        if getattr(parsed, "bozo", False) and not parsed.entries:
            print(f"[skip] {name}: bad feed ({parsed.get('bozo_exception')})")
            continue

        for entry in parsed.entries:
            link = (entry.get("link") or "").strip()
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue
            when = entry_time(entry)
            if when and when < cutoff:
                continue
            summary = entry.get("summary") or entry.get("description") or ""
            if not is_ai_related(title, summary, name):
                continue
            items.append(
                {
                    "title": title,
                    "link": link,
                    "source": name,
                    "summary": summary[:500],
                    "when": when.isoformat() if when else "",
                }
            )

    # 新到旧；同链接去重
    items.sort(key=lambda x: x.get("when") or "", reverse=True)
    deduped: list[dict] = []
    seen_links: set[str] = set()
    for item in items:
        if item["link"] in seen_links:
            continue
        seen_links.add(item["link"])
        deduped.append(item)
    return deduped


def summarize_zh(title: str, summary: str, groq_key: str) -> str | None:
    if not groq_key:
        return None
    prompt = (
        "用一两句中文概括这条 AI 资讯，客观简短，不要标题党，不要 emoji。\n"
        f"标题: {title}\n"
        f"摘要: {summary[:800]}"
    )
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 120,
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text[:300] if text else None
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Groq 摘要失败: {exc}")
        return None


def format_message(item: dict, zh_summary: str | None) -> str:
    title = html.escape(item["title"])
    source = html.escape(item["source"])
    link = item["link"]
    is_github = "github.com/" in link.lower() or "github" in item["source"].lower()
    body = html.escape(zh_summary) if zh_summary else ""
    if not body and is_github:
        raw = strip_html(item.get("summary") or "")[:180]
        if raw:
            body = html.escape(raw)

    if is_github:
        parts = [f"开源推荐 · <b>{title}</b>", f"来源: {source}"]
        if body:
            parts.append(body)
        parts.append(f'<a href="{html.escape(link, quote=True)}">GitHub 仓库</a>')
    else:
        parts = [f"<b>{title}</b>", f"来源: {source}"]
        if body:
            parts.append(body)
        parts.append(f'<a href="{html.escape(link, quote=True)}">阅读原文</a>')
    return "\n".join(parts)


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Telegram 发送失败: {resp.status_code} {resp.text}")


def main() -> None:
    load_env_file()
    token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    max_posts = int(os.environ.get("MAX_POSTS_PER_RUN", "5"))
    lookback = int(os.environ.get("LOOKBACK_HOURS", "24"))

    seen = load_seen()
    entries = fetch_entries(lookback)
    fresh = [e for e in entries if e["link"] not in seen]
    print(f"抓到 {len(entries)} 条，其中新内容 {len(fresh)} 条")

    posted = 0
    for item in fresh[:max_posts]:
        zh = summarize_zh(item["title"], item["summary"], groq_key)
        msg = format_message(item, zh)
        send_telegram(token, chat_id, msg)
        seen.add(item["link"])
        posted += 1
        print(f"[ok] {item['title'][:80]}")
        time.sleep(1.2)  # 防 Telegram 限流

    save_seen(seen)
    print(f"本次推送 {posted} 条")


if __name__ == "__main__":
    main()
