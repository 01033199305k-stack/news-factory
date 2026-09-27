# -*- coding: utf-8 -*-
"""
인스타 릴스로 나간 숏폼(video.mp4)을 유튜브 쇼츠로도 올린다 (YouTube Data API v3).

    python post_youtube.py                 # 게시 기록에서 아직 유튜브에 안 올린 영상 → 쇼츠
    python post_youtube.py --check         # 토큰 확인 (연결된 채널 이름만 출력, 아무것도 안 올림)
    python post_youtube.py --dry           # 올릴 제목·설명만 출력 (네트워크 안 씀)
    python post_youtube.py --slug <slug>   # 그 항목 하나만 (시험용. 켜짐 여부·시간 제한 무시)

필요한 값
- 시크릿 YOUTUBE_CLIENT_ID · YOUTUBE_CLIENT_SECRET · YOUTUBE_REFRESH_TOKEN (yt_auth.py 로 발급)
- 변수 YOUTUBE_UPLOAD=on 일 때만 실제로 올린다. 구글 API 심사(audit) 전에는 API 로 올린 영상이
  '비공개(잠김)'로 고정되기 때문에, 심사 통과 메일을 받은 뒤 켠다
- 변수 YOUTUBE_PRIVACY (기본 public)

동작
- 대상은 state/posted.json 에서 영상이 있고 done["youtube"] 가 없는 항목. 발생 후 POST_MAX_AGE_H 시간이
  지난 건 속보로 늦었으니 올리지 않고 skipped 로 적는다
- 영상은 이번 실행의 output/<slug>/video.mp4, 없으면 미디어 보관소(3일 보관)에서 받는다
- 결과는 done["youtube"] 에 적는다. 3번 실패하면 포기하고 exit 1 → GitHub 가 실패 메일을 보낸다
- 쿼터(업로드 하루 한도 등)에 걸리면 실패로 치지 않고 다음 실행으로 미룬다
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "state" / "posted.json"
MEDIA_BASE = os.environ.get("MEDIA_BASE", "").rstrip("/")

POST_MAX_AGE_H = 6
MAX_ATTEMPTS = 3
MAX_PER_RUN = 3
CATEGORY_NEWS = "25"   # 뉴스/정치
DEFAULT_TAGS = ["지금세계", "속보", "해외뉴스", "세계뉴스"]
FOOTER = "지금 세계 · 세계 사건·사고, 확인된 것만 가장 빠르게\n스레드·인스타그램 @jigeum.segye"
UA = "news-factory-uploader/1.0 (+https://github.com/01033199305k-stack/news-factory)"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")
NEEDED = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")


class QuotaError(RuntimeError):
    """하루 쿼터·업로드 한도 — 실패로 치지 않고 다음 실행에서 다시 한다."""


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def request(req, timeout=120):
    """(headers, body). 에러 메시지에 토큰이 섞이지 않도록 reason·message 만 남긴다."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.headers, r.read()
    except urllib.error.HTTPError as ex:
        raw = ex.read().decode(errors="replace")
        try:
            data = json.loads(raw)
            err = data["error"]
            if isinstance(err, dict):   # YouTube API
                reasons = ",".join(e.get("reason", "") for e in err.get("errors", []))
                msg = "%s %s" % (reasons, err.get("message", ""))
            else:                       # 토큰 엔드포인트: {"error": "invalid_grant", ...}
                msg = "%s %s" % (err, data.get("error_description", ""))
        except (ValueError, KeyError, TypeError):
            msg = raw[:300]
        text = "%s %d: %s" % (req.get_method(), ex.code, msg.strip()[:300])
        if ex.code in (403, 429) and re.search(r"quota|uploadLimit|rateLimit", msg, re.I):
            raise QuotaError(text)
        raise RuntimeError(text)


def access_token():
    data = urllib.parse.urlencode({
        "client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    _, body = request(urllib.request.Request(TOKEN_URL, data=data, method="POST"), timeout=30)
    return json.loads(body)["access_token"]


def api_get(token, path, **params):
    url = "https://www.googleapis.com/youtube/v3/%s?%s" % (path, urllib.parse.urlencode(params))
    _, body = request(urllib.request.Request(url, headers={"Authorization": "Bearer " + token}),
                      timeout=30)
    return json.loads(body)


def clean(s):
    return s.replace("<", "(").replace(">", ")")   # 제목·설명에는 < > 를 못 쓴다


def title_of(item):
    """본문 첫 문장 ("[속보] …했습니다") — 유튜브 제목은 100자까지."""
    first = item["text"].strip().split("\n", 1)[0].strip()
    m = re.match(r"(.+?[다요]\.)(?=\s|$)", first)
    if m:
        first = m.group(1)
    first = clean(first).rstrip(".").strip()
    return first if len(first) <= 100 else first[:99].rstrip() + "…"


def tags_of(item):
    tags = DEFAULT_TAGS + [t.replace(" ", "").lstrip("#") for t in item.get("hashtags", [])]
    return [t for t in dict.fromkeys(tags) if t]


def description_of(item):
    tags = " ".join("#" + t for t in tags_of(item) + ["Shorts"])
    desc = clean("%s\n\n%s\n\n%s" % (item["text"].strip(), tags, FOOTER))
    while len(desc.encode("utf-8")) > 4900:   # 설명은 5000바이트까지
        desc = desc[:-50]
    return desc


def video_bytes(item):
    local = ROOT / "output" / item["slug"] / item["video"]
    if local.exists():
        return local.read_bytes()
    if not MEDIA_BASE:
        raise RuntimeError("영상 파일이 없고 MEDIA_BASE 도 없음")
    url = "%s/%s/%s" % (MEDIA_BASE, item["slug"], urllib.parse.quote(item["video"]))
    _, body = request(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120)
    return body


def upload(token, item, data):
    privacy = (os.environ.get("YOUTUBE_PRIVACY") or "public").strip()
    meta = {
        "snippet": {
            "title": title_of(item), "description": description_of(item), "tags": tags_of(item),
            "categoryId": CATEGORY_NEWS, "defaultLanguage": "ko", "defaultAudioLanguage": "ko",
        },
        "status": {
            "privacyStatus": privacy, "selfDeclaredMadeForKids": False,
            "embeddable": True, "license": "youtube",
        },
    }
    headers, _ = request(urllib.request.Request(
        UPLOAD_URL, data=json.dumps(meta).encode("utf-8"), method="POST", headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(len(data)),
        }), timeout=60)
    session = headers.get("Location")
    if not session:
        raise RuntimeError("업로드 세션 주소를 못 받음")
    _, body = request(urllib.request.Request(session, data=data, method="PUT", headers={
        "Authorization": "Bearer " + token, "Content-Type": "video/mp4"}), timeout=600)
    video = json.loads(body)
    return video["id"], video.get("status", {}).get("privacyStatus", privacy)


def main():
    args = sys.argv[1:]
    dry = "--dry" in args
    slug = args[args.index("--slug") + 1] if "--slug" in args else None
    if not dry and not all(os.environ.get(k) for k in NEEDED):
        print("유튜브 토큰 없음 — 건너뜀")
        return 0
    if "--check" in args:
        token = access_token()
        items = api_get(token, "channels", part="snippet", mine="true").get("items", [])
        names = ["%s (%s)" % (c["snippet"]["title"], c["snippet"].get("customUrl", c["id"]))
                 for c in items]
        print("유튜브 연결 채널:", ", ".join(names) or "(없음)")
        return 0 if items else 1
    if not (dry or slug) and (os.environ.get("YOUTUBE_UPLOAD") or "").strip().lower() != "on":
        print("YOUTUBE_UPLOAD 가 on 이 아님 — 유튜브 쇼츠 업로드 건너뜀")
        return 0

    log = read_json(LOG, [])
    now = time.time()
    changed, todo = False, []
    for item in log:
        if slug and item.get("slug") != slug:
            continue
        if "youtube" in (item.get("done") or {}) or not item.get("video") or item.get("status") != "done":
            continue
        if not slug and (now * 1000 - item.get("event_ms", 0)) / 3.6e6 > POST_MAX_AGE_H:
            if not dry:   # 속보로 늦었다 — 다시 보지 않게 표시만
                item.setdefault("done", {})["youtube"] = {"skipped": "expired", "at": int(now)}
                changed = True
            continue
        todo.append(item)
    if not todo:
        print("올릴 쇼츠 없음" + (" (%s)" % slug if slug else ""))
        if changed:
            write_json(LOG, log)
        return 0

    if dry:
        for item in todo[:MAX_PER_RUN]:
            print("DRY", item["slug"], "|", title_of(item))
            print(description_of(item))
            print("tags:", ", ".join(tags_of(item)), "\n---")
        return 0

    try:
        token = access_token()
    except Exception as ex:
        print("유튜브 토큰 갱신 실패 — yt_auth.py 로 다시 발급해 YOUTUBE_REFRESH_TOKEN 을 바꾼다:", ex)
        return 1
    failed = False
    for item in todo[:MAX_PER_RUN]:
        tries = item["attempts"] = (item["attempts"] if isinstance(item.get("attempts"), dict)
                                    else {})
        try:
            vid, privacy = upload(token, item, video_bytes(item))
            link = "https://youtube.com/shorts/" + vid
            item.setdefault("done", {})["youtube"] = {
                "id": vid, "permalink": link, "kind": "short", "privacy": privacy,
                "at": int(time.time())}
            print("POSTED youtube short", item["slug"], link, privacy)
            changed = True
            time.sleep(5)
        except QuotaError as ex:
            print("QUOTA youtube — 다음 실행에서 다시:", ex)
            break
        except Exception as ex:
            tries["youtube"] = tries.get("youtube", 0) + 1
            print("FAIL youtube", item["slug"], "attempt", tries["youtube"], ex)
            if tries["youtube"] >= MAX_ATTEMPTS:
                item.setdefault("done", {})["youtube"] = {"failed": str(ex)[:300],
                                                          "at": int(time.time())}
                failed = True
            changed = True
    if changed:
        write_json(LOG, log)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
