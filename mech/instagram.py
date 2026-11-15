"""Instagram Graph API: publish a Reel, read comments, reply.

Two ways in, picked by IG_API:
  instagram (default)  Instagram API with Instagram Login: graph.instagram.com, an Instagram
                       user access token. The video and cover are fetched by Instagram from public
                       URLs (we host them as assets of a GitHub release on this repo).
  facebook             Instagram API with Facebook Login for Business: graph.facebook.com and a
                       Page/user token; the video is uploaded straight from disk (resumable).

Env: IG_USER_ID, IG_ACCESS_TOKEN, IG_API, IG_API_VERSION (default v25.0).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = os.environ.get("IG_API_VERSION", "v25.0")


def _host() -> str:
    return "graph.facebook.com" if os.environ.get("IG_API", "instagram") == "facebook" else "graph.instagram.com"


def _token() -> str:
    t = os.environ.get("IG_ACCESS_TOKEN")
    if not t:
        raise RuntimeError("IG_ACCESS_TOKEN is not set")
    return t


def user_id() -> str:
    u = os.environ.get("IG_USER_ID")
    if not u:
        raise RuntimeError("IG_USER_ID is not set")
    return u


def call(method: str, path: str, params: dict | None = None, host: str | None = None, data: bytes | None = None, headers: dict | None = None) -> dict:
    params = dict(params or {})
    url = f"https://{host or _host()}/{VERSION}/{path.lstrip('/')}"
    hdr = {"Authorization": f"Bearer {_token()}", **(headers or {})}
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
        body = None
    elif data is not None:
        body = data
    else:
        body = urllib.parse.urlencode(params).encode()
        hdr["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Instagram {method} {path}: {e.code} {e.read().decode(errors='replace')}") from None


def _wait(container: str, minutes: float = 15) -> None:
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        st = call("GET", container, {"fields": "status_code,status"})
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the video: {st}")
        time.sleep(10)
    raise RuntimeError("Instagram took too long to process the video")


def publish_reel(video: Path, caption: str, video_url: str | None = None, cover_url: str | None = None, thumb_offset_ms: int | None = None) -> dict:
    """Create the container, get the video to Instagram, wait, publish. Returns {id, permalink}."""
    params = {"media_type": "REELS", "caption": caption, "share_to_feed": "true"}
    if cover_url:
        params["cover_url"] = cover_url
    elif thumb_offset_ms is not None:
        params["thumb_offset"] = str(thumb_offset_ms)
    if video_url:
        params["video_url"] = video_url
        c = call("POST", f"{user_id()}/media", params)
    else:
        if _host() != "graph.facebook.com":
            raise RuntimeError("uploading from disk needs IG_API=facebook; with Instagram Login give a public video_url")
        params["upload_type"] = "resumable"
        c = call("POST", f"{user_id()}/media", params)
        size = video.stat().st_size
        call("POST", f"ig-api-upload/{c['id']}", host="rupload.facebook.com", data=video.read_bytes(),
             headers={"Authorization": f"OAuth {_token()}", "offset": "0", "file_size": str(size)})
    _wait(c["id"])
    pub = call("POST", f"{user_id()}/media_publish", {"creation_id": c["id"]})
    info = call("GET", pub["id"], {"fields": "id,permalink,timestamp"})
    return info


def recent_media(limit: int = 12) -> list[dict]:
    return call("GET", f"{user_id()}/media", {"fields": "id,caption,permalink,timestamp,comments_count", "limit": str(limit)}).get("data", [])


def comments(media_id: str) -> list[dict]:
    out, params = [], {"fields": "id,text,username,timestamp,from,replies{id,username}", "limit": "50"}
    res = call("GET", f"{media_id}/comments", params)
    out.extend(res.get("data", []))
    while res.get("paging", {}).get("next") and len(out) < 300:
        nxt = res["paging"]["next"]
        req = urllib.request.Request(nxt, headers={"Authorization": f"Bearer {_token()}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.load(r)
        out.extend(res.get("data", []))
    return out


def reply(comment_id: str, message: str) -> dict:
    return call("POST", f"{comment_id}/replies", {"message": message[:2000]})


def me() -> dict:
    """Our own account (works on both hosts: the IG user node, not /me, which is the Facebook user there)."""
    return call("GET", user_id(), {"fields": "id,username"})


def refresh_token() -> str:
    """A fresh 60-day Instagram Login token from the current one (do it before it expires)."""
    url = "https://graph.instagram.com/refresh_access_token?" + urllib.parse.urlencode({"grant_type": "ig_refresh_token", "access_token": _token()})
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)["access_token"]
