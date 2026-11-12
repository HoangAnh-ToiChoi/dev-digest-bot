"""Webhook Telegram cho Dev Digest Bot (chạy trên Vercel).

Nhận câu hỏi từ Telegram, tìm repo "claude skill" trên GitHub theo chủ đề,
rồi nhờ Gemini viết câu trả lời tiếng Việt dựa đúng trên kết quả tìm được.

Biến môi trường:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID  bắt buộc. Chỉ trả lời đúng chat này.
  WEBHOOK_SECRET                    bắt buộc. Chuỗi bí mật khai báo lúc setWebhook.
  GEMINI_API_KEY                    bắt buộc.
  GEMINI_MODEL                      tuỳ chọn, mặc định gemini-flash-latest.
  GITHUB_TOKEN                      tuỳ chọn, tăng giới hạn GitHub Search API.
"""

import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus

UA = {"User-Agent": "dev-digest-bot"}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
MAX_REPOS = 6


def http_json(url, data=None, headers=None, timeout=15):
    body = json.dumps(data).encode() if data is not None else None
    h = {**UA, **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def gemini(prompt, as_json=False):
    """Gọi Gemini, thử model dự phòng một lần nếu lỗi. Không chờ lâu vì webhook có giới hạn thời gian."""
    models = [os.environ.get("GEMINI_MODEL") or "gemini-flash-latest", "gemini-flash-lite-latest"]
    config = {"responseMimeType": "application/json"} if as_json else {}
    last = None
    for model in models:
        try:
            data = http_json(
                GEMINI_URL.format(model),
                {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config},
                {"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                timeout=20,
            )
            cand = data["candidates"][0]
            if cand.get("finishReason") != "STOP":
                last = f"{model}: finishReason={cand.get('finishReason')}"
                continue
            return "".join(p.get("text", "") for p in cand["content"]["parts"])
        except (OSError, KeyError, IndexError, ValueError) as e:
            last = f"{model}: {e.__class__.__name__}: {e}"
    raise RuntimeError(f"Gemini lỗi ({last})")


def extract_keywords(question):
    """Đổi câu hỏi tiếng Việt thành 1-2 từ khoá tiếng Anh để tìm trên GitHub."""
    prompt = (
        "Người dùng hỏi về các Claude skill trên GitHub. Từ câu hỏi, rút ra tối đa 2 từ khoá "
        "tiếng Anh, mỗi từ đúng một từ, mô tả CHỦ ĐỀ (ví dụ: legal, law, finance). "
        'Không gồm các từ "claude", "skill", "github". '
        'Trả về JSON dạng {"keywords": ["..."]}.\n\nCâu hỏi: ' + question
    )
    data = json.loads(gemini(prompt, as_json=True))
    words = [w.strip().lower() for w in data.get("keywords", []) if isinstance(w, str) and w.strip()]
    return [w for w in words if w.replace("-", "").isalnum()][:2]


def search_github(keywords):
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    found = {}
    for kw in keywords:
        q = f"claude skill {kw} in:name,description,topics"
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {"q": q, "sort": "stars", "order": "desc", "per_page": 8})
        for r in http_json(url, headers=headers, timeout=15).get("items", []):
            found.setdefault(r["full_name"], {
                "name": r["full_name"],
                "url": r["html_url"],
                "description": (r.get("description") or "")[:200],
                "stars": r["stargazers_count"],
                "updated": r["pushed_at"][:10],
            })
    repos = sorted(found.values(), key=lambda r: r["stars"], reverse=True)
    return repos[:MAX_REPOS]


def answer(question):
    keywords = extract_keywords(question)
    if not keywords:
        return "Mình chưa hiểu bạn muốn tìm skill về chủ đề gì. Bạn nói rõ hơn giúp mình nhé."
    repos = search_github(keywords)
    if not repos:
        return f"Mình tìm trên GitHub với từ khoá {', '.join(keywords)} mà chưa thấy repo claude skill nào phù hợp."
    prompt = (
        "Bạn là trợ lý tìm Claude skill. Trả lời bằng tiếng Việt, ngắn gọn, tự nhiên. "
        "CHỈ được nhắc tới các repo trong danh sách dưới đây, không thêm repo nào khác và không bịa chức năng. "
        "Mỗi repo một mục: tên repo, một câu nói nó làm gì (dựa vào mô tả, mô tả trống thì ghi rõ chưa có mô tả), "
        "số sao, ngày cập nhật cuối và link. Cuối cùng nhắc người dùng tự đọc README trước khi cài. "
        "Không dùng markdown, chỉ văn bản thường.\n\n"
        f"Câu hỏi: {question}\n\nDanh sách repo (JSON):\n{json.dumps(repos, ensure_ascii=False)}"
    )
    return gemini(prompt).strip()


def send(chat_id, text):
    http_json(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage",
        {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
    )


def handle_update(update, secret_header):
    """Trả về mã HTTP. Luôn 200 khi đã nhận (kể cả lỗi xử lý) để Telegram không gửi lại."""
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret or not hmac.compare_digest(secret_header or "", secret):
        return 403
    msg = update.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    allowed = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not text:
        return 200
    if chat_id != allowed:
        state = "chưa được đặt" if not allowed else "khác số này"
        print(f"Bỏ qua tin từ chat {chat_id}: TELEGRAM_CHAT_ID trên Vercel {state}", file=sys.stderr)
        return 200
    try:
        if text.startswith("/start"):
            reply = "Chào bạn! Hỏi mình kiểu: 'tìm claude skill về luật' hoặc 'skill claude cho tài chính'."
        else:
            reply = answer(text)
    except Exception as e:  # noqa: BLE001 - báo lỗi cho người dùng thay vì im lặng
        print(f"Lỗi xử lý: {e.__class__.__name__}: {e}", file=sys.stderr)
        reply = "Mình gặp lỗi khi tìm, bạn thử lại sau ít phút nhé."
    try:
        send(chat_id, reply)
    except OSError as e:
        print(f"Gửi Telegram lỗi: {e}", file=sys.stderr)
    return 200


def app(environ, start_response):
    """Điểm vào WSGI cho Vercel (khai báo trong pyproject.toml)."""
    code = 200
    if environ["REQUEST_METHOD"] == "POST":
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            update = json.loads(environ["wsgi.input"].read(length) or b"{}")
            code = handle_update(update, environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"))
        except (ValueError, OSError):
            code = 400
    start_response(f"{code} {HTTPStatus(code).phrase}", [("Content-Type", "text/plain")])
    return [b"ok"]
