"""Điểm vào của Vercel: nhận request WSGI từ Telegram rồi giao cho bot.handler.

Biến môi trường (đặt trên Vercel):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID  bắt buộc. Chỉ trả lời đúng chat này.
  WEBHOOK_SECRET                    bắt buộc. Chuỗi bí mật khai báo lúc setWebhook.
  GEMINI_API_KEY                    bắt buộc.
  GEMINI_MODEL                      tuỳ chọn, mặc định gemini-flash-latest.
  GITHUB_TOKEN                      tuỳ chọn, tăng giới hạn GitHub Search API.
"""

import json
import sys
from http import HTTPStatus
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # để import được thư mục bot/

from bot.handler import handle_update  # noqa: E402


def app(environ, start_response):
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
