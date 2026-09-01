#!/usr/bin/env python3
"""资讯 → Telegram（带图、无「阅读原文」）。适合本地试跑 + GitHub Actions。"""

from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent
SEEN_PATH = ROOT / "data" / "seen.json"
FEEDS_PATH = ROOT / "feeds.yaml"
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ai-telegram-news/1.1; +https://github.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

CATEGORY_LABEL = {
    "ai": "科技",
    "game": "游戏",
    "finance": "金融",
    "github": "开源",
}

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
    "aigc",
    "算力",
    "英伟达",
    "nvidia",
)

ALWAYS_PASS_NAME = (
    "openai",
    "google ai",
    "techcrunch ai",
    "venturebeat",
    "verge ai",
    "nvidia",
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
    trimmed = list(urls)[-3000:]
    SEEN_PATH.write_text(
        json.dumps(
            {"urls": trimmed, "updated_at": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
            indent=2,
        ),
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
    text = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(text.split())


def should_keep(title: str, summary: str, feed_name: str, category: str) -> bool:
    if category in ("game", "finance", "github"):
        if category == "github":
            # trending 全站仍用 AI 关键词；AI Ranking 放行
            if "ai ranking" in feed_name.lower():
                return True
            text = f"{title} {strip_html(summary)}".lower()
            return any(k.lower() in text for k in AI_KEYWORDS)
        return True
    name_l = feed_name.lower()
    if any(x in name_l for x in ALWAYS_PASS_NAME):
        return True
    text = f"{title} {strip_html(summary)}".lower()
    return any(k.lower() in text for k in AI_KEYWORDS)


def _looks_like_image_url(url: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    path = urlparse(url).path.lower()
    if any(x in path for x in (".svg", "sprite", "logo", "icon", "avatar", "1x1", "pixel")):
        return False
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return True
    # CDN 常无扩展名
    return "image" in url.lower() or "img" in url.lower() or "photo" in url.lower() or "media" in url.lower()


def extract_image_from_entry(entry, page_url: str) -> str | None:
    candidates: list[str] = []

    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key) or []:
            url = (media.get("url") or "").strip()
            if url:
                candidates.append(url)

    for enc in entry.get("enclosures") or []:
        href = (enc.get("href") or enc.get("url") or "").strip()
        typ = (enc.get("type") or "").lower()
        if href and (typ.startswith("image") or _looks_like_image_url(href)):
            candidates.append(href)

    html_blob = " ".join(
        str(entry.get(k) or "")
        for k in ("summary", "description", "content")
    )
    if isinstance(entry.get("content"), list):
        html_blob += " " + " ".join(c.get("value", "") for c in entry["content"])

    for match in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html_blob, flags=re.I):
        candidates.append(urljoin(page_url, match.group(1)))

    for url in candidates:
        abs_url = urljoin(page_url, url)
        if _looks_like_image_url(abs_url):
            return abs_url
    return None


def fetch_og_image(page_url: str) -> str | None:
    try:
        resp = requests.get(page_url, headers=HTTP_HEADERS, timeout=12, allow_redirects=True)
        if resp.status_code >= 400 or not resp.text:
            return None
        text = resp.text[:200_000]
        patterns = [
            r'<meta[^>]+property=["\']og:image:secure_url["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.I)
            if m:
                url = urljoin(page_url, html.unescape(m.group(1).strip()))
                if _looks_like_image_url(url) or url.startswith("http"):
                    return url
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] og:image 失败: {exc}")
    return None


def resolve_image(entry, page_url: str) -> str | None:
    img = extract_image_from_entry(entry, page_url)
    if img:
        return img
    return fetch_og_image(page_url)


def fetch_entries(lookback_hours: int) -> list[dict]:
    with FEEDS_PATH.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    feeds = config.get("feeds") or []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    items: list[dict] = []

    for feed in feeds:
        name = feed.get("name", "unknown")
        url = feed.get("url")
        category = (feed.get("category") or "ai").lower()
        if not url:
            continue
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=15)
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
            if not should_keep(title, summary, name, category):
                continue
            image = extract_image_from_entry(entry, link)
            items.append(
                {
                    "title": title,
                    "link": link,
                    "source": name,
                    "category": category,
                    "summary": summary[:800],
                    "image": image or "",
                    "when": when.isoformat() if when else "",
                    "_entry": entry,
                }
            )

    items.sort(key=lambda x: x.get("when") or "", reverse=True)
    deduped: list[dict] = []
    seen_links: set[str] = set()
    for item in items:
        if item["link"] in seen_links:
            continue
        seen_links.add(item["link"])
        deduped.append(item)
    return deduped


def summarize_zh(title: str, summary: str, category: str) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip() or os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        return None
    base = (os.environ.get("OPENAI_API_BASE") or "https://api.siliconflow.cn/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL") or "Qwen/Qwen3.5-4B"
    label = CATEGORY_LABEL.get(category, "资讯")
    prompt = (
        f"用一两句中文概括这条{label}资讯，客观简短，不要标题党，不要 emoji。\n"
        f"标题: {title}\n"
        f"摘要: {strip_html(summary)[:800]}"
    )
    try:
        resp = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": float(os.environ.get("OPENAI_TEMPERATURE", "0") or "0"),
                "max_tokens": 120,
                "enable_thinking": False,
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = (resp.json()["choices"][0]["message"].get("content") or "").strip()
        return text[:300] if text else None
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 摘要失败 ({model}): {exc}")
        return None


def format_caption(item: dict, zh_summary: str | None) -> str:
    """标题可点进原文，但不显示「阅读原文」字样。"""
    title = html.escape(item["title"])
    source = html.escape(item["source"])
    link = item["link"]
    cat = CATEGORY_LABEL.get(item.get("category") or "", "资讯")
    body = html.escape(zh_summary) if zh_summary else ""
    if not body and item.get("category") == "github":
        raw = strip_html(item.get("summary") or "")[:160]
        if raw:
            body = html.escape(raw)

    title_html = f'<a href="{html.escape(link, quote=True)}"><b>{title}</b></a>'
    parts = [f"{cat} · {title_html}", f"来源: {source}"]
    if body:
        parts.append(body)
    caption = "\n".join(parts)
    # Telegram caption 上限 1024
    return caption[:1020]


def send_telegram_photo(token: str, chat_id: str, photo_url: str, caption: str) -> bool:
    api = f"https://api.telegram.org/bot{token}/sendPhoto"
    resp = requests.post(
        api,
        data={
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML",
        },
        timeout=45,
    )
    if resp.status_code == 200:
        return True
    print(f"[warn] sendPhoto 失败: {resp.status_code} {resp.text[:240]}")
    return False


def send_telegram_text(token: str, chat_id: str, text: str) -> None:
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        api,
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Telegram 发送失败: {resp.status_code} {resp.text}")


def post_item(token: str, chat_id: str, item: dict, zh: str | None) -> None:
    caption = format_caption(item, zh)
    image = item.get("image") or ""
    if not image and item.get("_entry") is not None:
        image = resolve_image(item["_entry"], item["link"]) or ""
        item["image"] = image

    if image:
        ok = send_telegram_photo(token, chat_id, image, caption)
        if ok:
            return
    send_telegram_text(token, chat_id, caption)


def main() -> None:
    load_env_file()
    token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    max_posts = int(os.environ.get("MAX_POSTS_PER_RUN", "5"))
    lookback = int(os.environ.get("LOOKBACK_HOURS", "24"))
    dry_run = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "yes")

    seen = load_seen()
    entries = fetch_entries(lookback)
    fresh = [e for e in entries if e["link"] not in seen]
    print(f"抓到 {len(entries)} 条，其中新内容 {len(fresh)} 条")
    print(f"摘要模型: {os.environ.get('OPENAI_MODEL') or 'Qwen/Qwen3.5-4B'}")

    # 尽量让科技/游戏/金融轮流出几条
    buckets = {"ai": [], "game": [], "finance": [], "github": []}
    for e in fresh:
        buckets.setdefault(e.get("category") or "ai", []).append(e)
    ordered: list[dict] = []
    while len(ordered) < max_posts and any(buckets.values()):
        for key in ("ai", "game", "finance", "github"):
            if buckets.get(key):
                ordered.append(buckets[key].pop(0))
            if len(ordered) >= max_posts:
                break

    posted = 0
    for item in ordered:
        # 发送前补 og:image（只对要发的几条做，省时间）
        if not item.get("image"):
            item["image"] = resolve_image(item.get("_entry"), item["link"]) or ""
        zh = summarize_zh(item["title"], item["summary"], item.get("category") or "ai")
        caption = format_caption(item, zh)
        print(f"[prep] cat={item.get('category')} img={'Y' if item.get('image') else 'N'} {item['title'][:60]}")
        print(f"       caption: {strip_html(caption)[:100]}")
        if item.get("image"):
            print(f"       image: {item['image'][:100]}")
        if not dry_run:
            post_item(token, chat_id, item, zh)
            seen.add(item["link"])
            posted += 1
            print(f"[ok] posted")
            time.sleep(1.2)
        else:
            posted += 1
            print("[dry-run] skip send")

    if not dry_run:
        # 不要把 feedparser entry 写进 seen；seen 只存 url
        save_seen(seen)
    print(f"本次处理 {posted} 条")


if __name__ == "__main__":
    main()
