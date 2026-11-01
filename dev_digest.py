"""Dev Digest: mỗi sáng gửi tin hot cho dev về Telegram.

Biến môi trường:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID  bắt buộc (trừ khi DRY_RUN=1)
  GITHUB_TOKEN                      nên có: tăng giới hạn GitHub API (Actions tự cấp)
  ANTHROPIC_API_KEY                 tuỳ chọn: tóm tắt từng tin bằng tiếng Việt
  CLAUDE_MODEL                      tuỳ chọn: model dùng để tóm tắt
  DRY_RUN=1                         in tin ra màn hình, không gửi, không lưu state
"""

import calendar
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests

DRY_RUN = os.environ.get("DRY_RUN") == "1"
STATE_FILE = Path(__file__).parent / "state" / "sent.json"
STATE_KEEP_DAYS = 30
VN_TZ = timezone(timedelta(hours=7))
NOW = time.time()
UA = {"User-Agent": "dev-digest-bot/1.0"}

# Framework/thư viện theo dõi bản phát hành (major/minor, bỏ qua patch).
# Mặc định theo stack của SchoolarsGo, sửa tuỳ ý.
WATCH_REPOS = [
    "facebook/react",
    "vitejs/vite",
    "tailwindlabs/tailwindcss",
    "expressjs/express",
    "supabase/supabase-js",
    "nodejs/node",
    "microsoft/playwright",
    "colinhacks/zod",
    "TanStack/query",
    "vercel/next.js",
    "oven-sh/bun",
    "anthropics/claude-code",
]

# Chỉ lấy từ nguồn chính thức để tránh tin sai tên/ngày như các trang tổng hợp.
MODEL_FEEDS = {
    "OpenAI": "https://openai.com/news/rss.xml",
    "Google": "https://blog.google/technology/ai/rss/",
    "Google DeepMind": "https://deepmind.google/blog/rss.xml",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
}
MODEL_WORDS = re.compile(
    r"\b(model|models|release[sd]?|launch\w*|introduc\w*|weights|open[- ]source|"
    r"gpt|gemini|gemma|claude|llama|qwen|deepseek|mistral|kimi|grok)\b",
    re.IGNORECASE,
)

WORKFLOW_FEEDS = {
    "Simon Willison": "https://simonwillison.net/atom/everything/",
    "Latent Space": "https://www.latent.space/feed",
}
WORKFLOW_WORDS = re.compile(
    r"\b(prompt\w*|agent\w*|workflow|how to|guide|tutorial|tips?|claude code|codex|"
    r"cursor|mcp|skills?|coding|vibe)\b",
    re.IGNORECASE,
)

# (khoá, tiêu đề, số bài tối đa)
SECTIONS = [
    ("repos", "🔥 REPO HOT (GitHub, tạo trong 7 ngày)", 5),
    ("skills", "🧩 CLAUDE SKILL MỚI", 3),
    ("models", "🧠 MODEL / RELEASE AI (nguồn chính thức)", 4),
    ("workflow", "🛠️ CÁCH DÙNG AI / WORKFLOW", 3),
    ("frameworks", "📦 FRAMEWORK / THƯ VIỆN MỚI", 3),
    ("industry", "🌐 XU HƯỚNG NGÀNH IT (Hacker News)", 3),
]


def clean_url(url):
    """Bỏ tham số theo dõi (utm_...) để link gọn và dedupe chính xác."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query), fragment=""))


def item(title, url, source, note=""):
    return {"title": title.strip(), "url": clean_url(url), "source": source, "note": note}


def gh_get(path, **params):
    headers = {"Accept": "application/vnd.github+json", **UA}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    r = requests.get(f"https://api.github.com{path}", headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def looks_star_farmed(repo):
    # Repo thật hiếm khi có hàng nghìn sao mà gần như không ai fork.
    return repo["stargazers_count"] >= 1000 and repo["forks_count"] < repo["stargazers_count"] / 200


def search_repos(query, source):
    since = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    data = gh_get("/search/repositories", q=f"{query} created:>{since}",
                  sort="stars", order="desc", per_page=20)
    out = []
    for r in data["items"]:
        if looks_star_farmed(r):
            continue
        desc = (r.get("description") or "").strip()
        note = f"⭐ {r['stargazers_count']:,}" + (f" · {r['language']}" if r.get("language") else "")
        out.append(item(f"{r['full_name']} – {desc[:100]}{'…' if len(desc) > 100 else ''}" if desc else r["full_name"],
                        r["html_url"], source, note))
    return out


def fetch_repos(_hours):
    return search_repos("stars:>100", "GitHub")


def fetch_skills(_hours):
    return search_repos("claude skill in:name,description,topics", "GitHub")


def entry_time(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return calendar.timegm(t) if t else None


def fetch_feeds(feeds, words, hours):
    out, errors = [], []
    for source, url in feeds.items():
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            errors.append(f"{source} ({e.__class__.__name__})")
            continue
        for e in feedparser.parse(r.content).entries:
            ts = entry_time(e)
            if not ts or ts < NOW - hours * 3600 or not e.get("link"):
                continue
            if words.search(f"{e.get('title', '')} {e.get('summary', '')}"):
                out.append(item(e.get("title", e.link), e.link, source))
    if errors and not out:
        raise RuntimeError(", ".join(errors))
    return out, errors


def fetch_hf_models(hours):
    """Model mở đang trending trên Hugging Face, chỉ lấy model mới tạo."""
    r = requests.get("https://huggingface.co/api/models", headers=UA, timeout=30,
                     params={"sort": "trendingScore", "limit": 30})
    r.raise_for_status()
    out = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(hours, 7 * 24))
    for m in r.json():
        created = m.get("createdAt")
        if not created or datetime.fromisoformat(created.replace("Z", "+00:00")) < cutoff:
            continue
        note = f"❤️ {m.get('likes', 0):,}" if m.get("likes") else ""
        out.append(item(m["id"], f"https://huggingface.co/{m['id']}", "Hugging Face", note))
    return out


def fetch_models(hours):
    out, errors = [], []
    try:
        feed_items, errors = fetch_feeds(MODEL_FEEDS, MODEL_WORDS, hours)
        out += feed_items
    except RuntimeError as e:
        errors = [str(e)]
    try:
        out += fetch_hf_models(hours)
    except requests.RequestException as e:
        errors.append(f"Hugging Face API ({e.__class__.__name__})")
    if errors and not out:
        raise RuntimeError(", ".join(errors))
    return out, errors


def fetch_workflow(hours):
    return fetch_feeds(WORKFLOW_FEEDS, WORKFLOW_WORDS, hours)


def is_minor_or_major(tag):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", tag)
    return bool(m) and (m.group(3) in (None, "0"))


def fetch_frameworks(hours):
    out, errors = [], []
    for repo in WATCH_REPOS:
        try:
            releases = gh_get(f"/repos/{repo}/releases", per_page=5)
        except requests.RequestException as e:
            errors.append(f"{repo} ({e.__class__.__name__})")
            continue
        for rel in releases:
            if rel.get("draft") or rel.get("prerelease") or not rel.get("published_at"):
                continue
            ts = datetime.fromisoformat(rel["published_at"].replace("Z", "+00:00")).timestamp()
            if ts < NOW - hours * 3600 or not is_minor_or_major(rel["tag_name"]):
                continue
            out.append(item(f"{repo} {rel['tag_name']}", rel["html_url"], "GitHub Releases"))
    if errors and not out and len(errors) == len(WATCH_REPOS):
        raise RuntimeError("GitHub Releases: " + ", ".join(errors[:3]))
    return out, errors


def fetch_industry(hours):
    r = requests.get("https://hn.algolia.com/api/v1/search", headers=UA, timeout=30, params={
        "tags": "story",
        "numericFilters": f"created_at_i>{int(NOW - hours * 3600)},points>200",
        "hitsPerPage": 30,
    })
    r.raise_for_status()
    hits = sorted(r.json()["hits"], key=lambda h: h.get("points", 0), reverse=True)
    out = []
    for h in hits:
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append(item(h["title"], url, "Hacker News", f"▲ {h['points']}"))
    return out


FETCHERS = {
    "repos": fetch_repos,
    "skills": fetch_skills,
    "models": fetch_models,
    "workflow": fetch_workflow,
    "frameworks": fetch_frameworks,
    "industry": fetch_industry,
}


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    cutoff = NOW - STATE_KEEP_DAYS * 86400
    state = {u: ts for u, ts in state.items() if ts >= cutoff}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=0, sort_keys=True) + "\n")


def collect(sent):
    """Lấy tin từng mục; mục nào thiếu bài thì mở rộng từ 24h lên 48h."""
    picked, errors, seen = {}, [], set(sent)
    for key, _title, limit in SECTIONS:
        chosen = []
        for hours in (24, 48):
            try:
                result = FETCHERS[key](hours)
            except requests.RequestException as e:
                errors.append(f"{key}: {e.__class__.__name__}")
                break
            except (RuntimeError, KeyError, ValueError) as e:
                errors.append(f"{key}: {e}")
                break
            items, soft_errors = result if isinstance(result, tuple) else (result, [])
            for it in items:
                if it["url"] not in seen and len(chosen) < limit:
                    if hours == 48:
                        it["note"] = (it["note"] + " · " if it["note"] else "") + "tin 2 ngày"
                    chosen.append(it)
                    seen.add(it["url"])
            if len(chosen) >= limit or key in ("repos", "skills"):
                errors += [f"{key}: {s}" for s in soft_errors]
                break
        picked[key] = chosen
    return picked, errors


def summarize(picked):
    """Tóm tắt 1 câu tiếng Việt cho mỗi tin. Lỗi thì bỏ qua, digest vẫn gửi."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return
    import anthropic

    flat = [it for items in picked.values() for it in items]
    if not flat:
        return
    listing = "\n".join(f"{i}. [{it['source']}] {it['title']} ({it['url']})" for i, it in enumerate(flat))
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=os.environ.get("CLAUDE_MODEL") or "claude-opus-5-5",
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "low",
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "summary": {"type": "string"},
                                    },
                                    "required": ["id", "summary"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["items"],
                        "additionalProperties": False,
                    },
                },
            },
            messages=[{
                "role": "user",
                "content": (
                    "Bạn viết bản tin sáng cho một lập trình viên Việt Nam. Với mỗi tin dưới đây, "
                    "viết đúng 1 câu tiếng Việt (tối đa 25 từ) nói nó là gì và vì sao dev nên quan tâm. "
                    "Chỉ dựa vào tiêu đề và nguồn; nếu không chắc thì mô tả đúng những gì tiêu đề nói, "
                    "không bịa thêm số liệu. Giữ nguyên tên riêng, tên model, tên repo.\n\n" + listing
                ),
            }],
        )
    except anthropic.APIStatusError as e:
        print(f"Bỏ qua tóm tắt: Claude API lỗi {e.status_code}", file=sys.stderr)
        return
    except anthropic.APIConnectionError:
        print("Bỏ qua tóm tắt: không kết nối được Claude API", file=sys.stderr)
        return
    if response.stop_reason != "end_turn":
        print(f"Bỏ qua tóm tắt: stop_reason={response.stop_reason}", file=sys.stderr)
        return
    text = next(b.text for b in response.content if b.type == "text")
    for s in json.loads(text)["items"]:
        if 0 <= s["id"] < len(flat):
            flat[s["id"]]["summary"] = s["summary"].strip()


def render(picked, errors):
    today = datetime.now(VN_TZ)
    lines = [f"🤖 <b>Dev Digest – {today:%d/%m}</b>"]
    total = 0
    for key, title, _limit in SECTIONS:
        items = picked.get(key) or []
        lines.append(f"\n<b>{title}</b>")
        if not items:
            lines.append("<i>Không có tin mới.</i>")
        for it in items:
            total += 1
            meta = " · ".join(x for x in (it["note"], it["source"]) if x)
            lines.append(f'• <a href="{html.escape(it["url"])}">{html.escape(it["title"])}</a> <i>({html.escape(meta)})</i>')
            if it.get("summary"):
                lines.append(f"   ↳ {html.escape(it['summary'])}")
    if today.weekday() == 0:
        lines.append('\n📊 <b>Thứ Hai: xem bảng xếp hạng model</b>\n• <a href="https://arena.ai/leaderboard">arena.ai/leaderboard</a> (bảng chính thức, thay đổi hằng tuần)')
    if errors:
        lines.append("\n⚠️ <i>Nguồn lỗi hôm nay (có thể sót tin): " + html.escape("; ".join(errors))[:600] + "</i>")
    lines.append(f"\n<i>{total} tin</i>")
    return lines


def chunk(lines, limit=3900):
    """Telegram giới hạn 4096 ký tự/tin; cắt theo dòng để không vỡ thẻ HTML."""
    chunks, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    return chunks


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage",
        data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text,
              "parse_mode": "HTML", "disable_web_page_preview": "true"},
        timeout=30,
    )
    if not r.ok:
        sys.exit(f"Telegram lỗi {r.status_code}: {r.text}")


def main():
    state = load_state()
    picked, errors = collect(state)
    summarize(picked)
    for c in chunk(render(picked, errors)):
        print(c) if DRY_RUN else send(c)
    if not DRY_RUN:
        for items in picked.values():
            for it in items:
                state[it["url"]] = int(NOW)
        save_state(state)


if __name__ == "__main__":
    main()
