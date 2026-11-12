"""Gửi tin nhắn Telegram."""

import os

from .net import http_json


def send(chat_id, text):
    http_json(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage",
        {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
    )
