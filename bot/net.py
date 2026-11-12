"""Gọi HTTP trả JSON bằng thư viện chuẩn, không cần cài thêm gói nào."""

import json
import urllib.request

UA = {"User-Agent": "dev-digest-bot"}


def http_json(url, data=None, headers=None, timeout=15):
    body = json.dumps(data).encode() if data is not None else None
    h = {**UA, **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())
