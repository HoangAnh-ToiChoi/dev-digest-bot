"""Dev Digest: mỗi sáng gửi tin hot cho dev về Telegram.

Biến môi trường:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID  bắt buộc (trừ khi DRY_RUN=1)
  GITHUB_TOKEN                      nên có: tăng giới hạn GitHub API (Actions tự cấp)
  GEMINI_API_KEY                    tuỳ chọn (miễn phí): tóm tắt "Là gì / Vì sao hot" bằng Gemini
  GEMINI_MODEL                      tuỳ chọn: mặc định gemini-flash-latest
  ANTHROPIC_API_KEY                 tuỳ chọn (trả phí): tóm tắt bằng Claude nếu không có GEMINI_API_KEY
  CLAUDE_MODEL                      tuỳ chọn: model Claude dùng để tóm tắt
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


def item(title, url, source, note="", context="", ref=None):
    # context: thông tin thêm cho Claude tóm tắt; ref: ("repo"|"hf", tên) để tải README khi cần
    return {"title": title.strip(), "url": clean_url(url), "source": source, "note": note,
            "context": context, "ref": ref}


def plain_text(text, limit=1500):
    """Bỏ thẻ HTML/markdown rườm rà (ảnh, badge) để gửi cho Claude gọn hơn."""
    text = re.sub(r"<[^>]+>|!\[[^\]]*\]\([^)]*\)|\[!\[.*?\]\(.*?\)\]\(.*?\)", " ", text or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text[:limit]


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
        owner = "tổ chức" if r["owner"].get("type") == "Organization" else "cá nhân"
        context = (f"Repo GitHub của {owner} {r['owner']['login']}, tạo ngày {r['created_at'][:10]}, "
                   f"{r['stargazers_count']} sao, {r['forks_count']} fork. Mô tả: {desc}")
        out.append(item(f"{r['full_name']} – {desc[:100]}{'…' if len(desc) > 100 else ''}" if desc else r["full_name"],
                        r["html_url"], source, note, context, ("repo", r["full_name"])))
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
                out.append(item(e.get("title", e.link), e.link, source,
                                context=plain_text(e.get("summary", ""))))
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
        context = (f"Model trên Hugging Face, tạo ngày {created[:10]}, {m.get('likes', 0)} likes, "
                   f"{m.get('downloads', 0)} lượt tải, loại: {m.get('pipeline_tag') or 'không rõ'}")
        out.append(item(m["id"], f"https://huggingface.co/{m['id']}", "Hugging Face", note,
                        context, ("hf", m["id"])))
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
            out.append(item(f"{repo} {rel['tag_name']}", rel["html_url"], "GitHub Releases",
                            context="Ghi chú phát hành: " + plain_text(rel.get("body"))))
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
        context = f"{h['points']} điểm, {h.get('num_comments', 0)} bình luận trên Hacker News. " + plain_text(h.get("story_text"))
        out.append(item(h["title"], url, "Hacker News", f"▲ {h['points']}", context))
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
    spare = 2 if summarizer_enabled() else 0
    for key, _title, limit in SECTIONS:
        limit += spare  # lấy dư để bù tin Claude loại vì không liên quan
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


def add_readmes(items):
    """Tải README của repo / model card để Claude hiểu repo đó thật sự làm gì."""
    for it in items:
        if not it.get("ref"):
            continue
        kind, name = it["ref"]
        try:
            if kind == "repo":
                headers = {"Accept": "application/vnd.github.raw", **UA}
                if os.environ.get("GITHUB_TOKEN"):
                    headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
                r = requests.get(f"https://api.github.com/repos/{name}/readme", headers=headers, timeout=30)
            else:
                r = requests.get(f"https://huggingface.co/{name}/raw/main/README.md", headers=UA, timeout=30)
            if r.ok:
                it["context"] += " README: " + plain_text(r.text, 2000)
        except requests.RequestException:
            pass


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                    "keep": {"type": "boolean"},
                },
                "required": ["id", "what", "why", "keep"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SUMMARY_PROMPT = """Bạn viết bản tin sáng cho một lập trình viên Việt Nam (stack: React, Node/Express, Supabase, có dùng AI).
Với mỗi tin bên dưới, trả về:
- what: "Là gì" – 1 câu tiếng Việt dễ hiểu (tối đa 30 từ), giải thích cho người chưa từng nghe tên nó.
- why: "Vì sao đáng chú ý" – 1 câu (tối đa 30 từ): vì sao nó hot hoặc dev nên quan tâm, dựa vào dữ kiện có sẵn (ai làm ra, tăng bao nhiêu sao trong mấy ngày, tính năng mới, ứng dụng thực tế...).
- keep: false nếu tin rõ ràng không liên quan tới phần mềm/IT/AI (ví dụ chuyện nông nghiệp, đời sống), hoặc chỉ là bản đóng gói lại/không kiểm duyệt của model có sẵn mà không có gì mới; ngược lại true.

Quy tắc: chỉ dùng thông tin trong tiêu đề và phần "Thông tin"; không bịa số liệu. Nếu thiếu dữ kiện để nói vì sao hot, viết "Chưa rõ, xem link để biết thêm". Giữ nguyên tên riêng, tên model, tên repo. Viết tự nhiên, không sáo rỗng."""


GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "INTEGER"},
                    "what": {"type": "STRING"},
                    "why": {"type": "STRING"},
                    "keep": {"type": "BOOLEAN"},
                },
                "required": ["id", "what", "why", "keep"],
            },
        }
    },
    "required": ["items"],
}


def summarizer_enabled():
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


GEMINI_FALLBACK_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.1-flash-lite"]


def ask_gemini(prompt):
    """Gemini (gói miễn phí của Google AI Studio). Trả về (chuỗi JSON, None) hoặc (None, lý do lỗi).

    Gói free hay bị quá tải (503) hoặc giới hạn (429), nên thử lại vài lần rồi chuyển model khác.
    """
    models = [os.environ.get("GEMINI_MODEL")] if os.environ.get("GEMINI_MODEL") else []
    models += [m for m in GEMINI_FALLBACK_MODELS if m not in models]
    reason = "không rõ"
    for model in models:
        for wait in (0, 10, 30):
            time.sleep(wait)
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], **UA},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json",
                                             "responseSchema": GEMINI_SCHEMA},
                    },
                    timeout=180,
                )
            except requests.RequestException as e:
                reason = f"không kết nối được ({e.__class__.__name__})"
                continue
            if r.status_code in (429, 500, 503):
                reason = f"Gemini quá tải/giới hạn ({r.status_code})"
                print(f"{model}: lỗi {r.status_code}, thử lại", file=sys.stderr)
                continue
            if not r.ok:
                reason = f"Gemini lỗi {r.status_code}"
                print(f"{model}: lỗi {r.status_code}: {r.text[:300]}", file=sys.stderr)
                break  # lỗi khác (key sai, model không tồn tại...): thử model tiếp theo
            candidates = r.json().get("candidates") or []
            if not candidates or candidates[0].get("finishReason") != "STOP":
                reason = "Gemini trả kết quả không đầy đủ"
                print(f"{model}: {candidates[:1]}", file=sys.stderr)
                break
            print(f"Tóm tắt bằng {model}", file=sys.stderr)
            return "".join(part.get("text", "") for part in candidates[0]["content"]["parts"]), None
    return None, reason


def ask_claude(prompt):
    """Claude API (trả phí). Trả về (chuỗi JSON, None) hoặc (None, lý do lỗi)."""
    import anthropic

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=os.environ.get("CLAUDE_MODEL") or "claude-opus-5-5",
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIStatusError as e:
        return None, f"Claude API lỗi {e.status_code}"
    except anthropic.APIConnectionError:
        return None, "không kết nối được Claude API"
    if response.stop_reason != "end_turn":
        return None, f"Claude dừng giữa chừng ({response.stop_reason})"
    return next(b.text for b in response.content if b.type == "text"), None


def summarize(picked):
    """Thêm "Là gì" / "Vì sao hot" cho mỗi tin. Ưu tiên Gemini (miễn phí), sau đó Claude.
    Lỗi thì trả về lý do (hiện ở cuối bản tin), digest vẫn gửi."""
    if not summarizer_enabled():
        return
    flat = [it for items in picked.values() for it in items]
    if not flat:
        return
    add_readmes(flat)
    listing = "\n\n".join(
        f"[{i}] ({it['source']}) {it['title']}\nLink: {it['url']}\nThông tin: {it['context'] or 'không có'}"
        for i, it in enumerate(flat)
    )
    prompt = SUMMARY_PROMPT + "\n\n" + listing
    text, reason = ask_gemini(prompt) if os.environ.get("GEMINI_API_KEY") else ask_claude(prompt)
    if not text:
        return f"tóm tắt AI: {reason}"
    try:
        results = json.loads(text)["items"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return "tóm tắt AI: kết quả không phải JSON hợp lệ"
    for s in results:
        if 0 <= s["id"] < len(flat):
            flat[s["id"]].update(what=s["what"].strip(), why=s["why"].strip(), keep=s["keep"])
    return None


def render(picked, errors):
    today = datetime.now(VN_TZ)
    lines = [f"🤖 <b>Dev Digest – {today:%d/%m}</b>"]
    total = 0
    for key, title, limit in SECTIONS:
        items = [it for it in picked.get(key) or [] if it.get("keep", True)][:limit]
        lines.append(f"\n<b>{title}</b>")
        if not items:
            lines.append("<i>Không có tin mới.</i>")
        for it in items:
            total += 1
            it["shown"] = True
            meta = " · ".join(x for x in (it["note"], it["source"]) if x)
            lines.append(f'• <a href="{html.escape(it["url"])}">{html.escape(it["title"])}</a> <i>({html.escape(meta)})</i>')
            if it.get("what"):
                lines.append(f"   📝 <b>Là gì:</b> {html.escape(it['what'])}")
            if it.get("why"):
                lines.append(f"   🔥 <b>Vì sao hot:</b> {html.escape(it['why'])}")
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


LAST_DIGEST_KEY = "__last_digest__"


def already_sent_today(state):
    last = state.get(LAST_DIGEST_KEY)
    return bool(last) and datetime.fromtimestamp(last, VN_TZ).date() == datetime.now(VN_TZ).date()


def main():
    state = load_state()
    # Lịch chạy có nhiều mốc dự phòng (GitHub hay bỏ lỡ lịch); mốc nào chạy trước thì gửi,
    # các mốc sau trong ngày bỏ qua. Bấm "Run workflow" bằng tay thì luôn gửi.
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and already_sent_today(state):
        print("Hôm nay đã gửi bản tin, bỏ qua lần chạy dự phòng.")
        return
    picked, errors = collect(state)
    summary_error = summarize(picked)
    if summary_error:
        errors.append(summary_error)
    for c in chunk(render(picked, errors)):
        print(c) if DRY_RUN else send(c)
    if not DRY_RUN:
        for items in picked.values():
            for it in items:
                # tin dư chưa hiển thị thì để dành cho hôm sau
                if it.get("shown") or it.get("keep") is False:
                    state[it["url"]] = int(NOW)
        state[LAST_DIGEST_KEY] = int(NOW)
        save_state(state)


if __name__ == "__main__":
    main()
