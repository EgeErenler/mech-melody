"""Download a recording from a direct link (Google Drive and Dropbox share links are rewritten)."""
import re
import sys
import urllib.request

url, dst = sys.argv[1], sys.argv[2]
m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([\w-]+)", url)
if m:
    url = f"https://drive.usercontent.google.com/download?id={m.group(1)}&export=download&confirm=t"
if "dropbox.com" in url:
    url = re.sub(r"[?&]dl=0", "", url) + ("&" if "?" in url else "?") + "dl=1"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=300) as r, open(dst, "wb") as f:
    data = r.read()
    if data[:15].lower().startswith(b"<!doctype html") or data[:6].lower() == b"<html>":
        sys.exit("the link gave a web page, not audio: share the file as 'anyone with the link' and try again")
    f.write(data)
print(f"fetched {len(data) / 1e6:.1f} MB")
