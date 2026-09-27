# -*- coding: utf-8 -*-
"""
게시 대기열(state/queue.json)을 스레드·인스타그램·유튜브에 올린다.

    MEDIA_BASE=https://news-factory-watch.alsgur3319.workers.dev/media python post.py [--only threads|instagram|youtube]

- 스레드: 카드 캐러셀 (JPEG)
- 인스타그램: 숏폼 영상이 있으면 릴스, 없으면 카드 캐러셀 (JPEG)
- 유튜브: 숏폼 영상이 있을 때만 쇼츠 (없거나 하루 한도를 다 쓰면 건너뜀)
- 이미지·영상은 upload_media.py 가 먼저 미디어 보관소에 올려 둔다 (두 API 모두 공개 URL 로만 받는다)
- --only: 그 플랫폼만 올린다. 스레드는 카드가 나오자마자, 인스타는 영상이 나온 뒤에 올리려고 나눴다
- 토큰이 있는 플랫폼에만 올리고, 올린 결과를 item["done"] 에 적어서 다시 올리지 않는다
- 발생 후 POST_MAX_AGE_H 시간이 지난 건은 '속보'로 늦었으니 올리지 않고 expired 로 뺀다
- 한 플랫폼에서 3번 연속 실패하면 그 플랫폼은 포기하고 exit 1 → GitHub 가 실패 메일을 보낸다
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(ROOT, "state", "queue.json")
LOG = os.path.join(ROOT, "state", "posted.json")

POST_MAX_AGE_H = 6
MAX_ATTEMPTS = 3
MEDIA_BASE = os.environ.get("MEDIA_BASE", "").rstrip("/")
DEFAULT_TAGS = ["지금세계", "속보", "해외뉴스", "세계뉴스"]


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def media_url(item, name):
    return "%s/%s/%s" % (MEDIA_BASE, item["slug"], urllib.parse.quote(name))


def card_urls(item):
    return [media_url(item, n[:-4] + ".jpg") for n in item["images"]]


class Platform:
    name = ""
    host = ""
    token_env = ""

    def __init__(self):
        self.token = os.environ.get(self.token_env, "")
        self._uid = None

    def api(self, method, path, **params):
        params["access_token"] = self.token
        url = "%s/%s" % (self.host, path)
        data = None
        if method == "GET":
            url += "?" + urllib.parse.urlencode(params)
        else:
            data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as ex:
            body = ex.read().decode(errors="replace")
            try:  # 에러 메시지에 토큰이 섞여 나오지 않도록 message 만 뽑는다
                body = json.loads(body)["error"]["message"]
            except (ValueError, KeyError):
                body = body[:300]
            raise RuntimeError("%s %s → %s: %s" % (method, path, ex.code, body))


class Threads(Platform):
    name, host, token_env = "threads", "https://graph.threads.net/v1.0", "THREADS_ACCESS_TOKEN"

    def uid(self):
        if not self._uid:  # 사용자 ID 는 토큰으로 조회한다
            self._uid = os.environ.get("THREADS_USER_ID") or self.api("GET", "me", fields="id")["id"]
        return self._uid

    def wait(self, cid, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            st = self.api("GET", cid, fields="status,error_message")
            if st.get("status") == "FINISHED":
                return
            if st.get("status") in ("ERROR", "EXPIRED"):
                raise RuntimeError("container %s: %s" % (cid, st.get("error_message", st)))
            time.sleep(4)
        raise RuntimeError("container %s not ready in %ds" % (cid, timeout))

    def post(self, item):
        uid = self.uid()
        kids = [self.api("POST", "%s/threads" % uid, media_type="IMAGE", image_url=u,
                         is_carousel_item="true")["id"]
                for u in card_urls(item)[:20]]
        for k in kids:
            self.wait(k)
        text = item["text"] if len(item["text"]) <= 500 else item["text"][:499] + "…"
        params = {"media_type": "CAROUSEL", "children": ",".join(kids), "text": text}
        if item.get("topic_tag"):
            params["topic_tag"] = item["topic_tag"]
        parent = self.api("POST", "%s/threads" % uid, **params)["id"]
        self.wait(parent)
        mid = self.api("POST", "%s/threads_publish" % uid, creation_id=parent)["id"]
        return mid, self.api("GET", mid, fields="permalink").get("permalink", "")


class Instagram(Platform):
    """Instagram API (인스타그램 로그인). 프로페셔널 계정 필요. 이미지는 JPEG, 캐러셀 10장까지."""
    name, host, token_env = "instagram", "https://graph.instagram.com", "INSTAGRAM_ACCESS_TOKEN"

    def uid(self):
        if not self._uid:
            me = self.api("GET", "me", fields="user_id,username")
            self._uid = me.get("user_id") or me["id"]
        return self._uid

    def wait(self, cid, timeout=300):
        end = time.time() + timeout
        while time.time() < end:
            st = self.api("GET", cid, fields="status_code,status").get("status_code")
            if st in ("FINISHED", "PUBLISHED"):
                return
            if st in ("ERROR", "EXPIRED"):
                detail = self.api("GET", cid, fields="status").get("status", "")
                raise RuntimeError("container %s: %s %s" % (cid, st, detail))
            time.sleep(6)
        raise RuntimeError("container %s not ready in %ds" % (cid, timeout))

    def caption(self, item):
        tags = " ".join("#" + t.replace(" ", "").lstrip("#")
                        for t in dict.fromkeys(DEFAULT_TAGS + item.get("hashtags", [])))
        return (item["text"] + "\n\n" + tags)[:2200]

    def post(self, item):
        uid = self.uid()
        if item.get("video"):
            # 숏폼 영상 → 릴스 (피드에도 보이게)
            parent = self.api("POST", "%s/media" % uid, media_type="REELS",
                              video_url=media_url(item, item["video"]), caption=self.caption(item),
                              share_to_feed="true", thumb_offset="1500")["id"]
        else:
            kids = [self.api("POST", "%s/media" % uid, image_url=u, is_carousel_item="true")["id"]
                    for u in card_urls(item)[:10]]
            for k in kids:
                self.wait(k)
            parent = self.api("POST", "%s/media" % uid, media_type="CAROUSEL",
                              children=",".join(kids), caption=self.caption(item))["id"]
        self.wait(parent)
        mid = self.api("POST", "%s/media_publish" % uid, creation_id=parent)["id"]
        return mid, self.api("GET", mid, fields="permalink").get("permalink", "")


class Skip(Exception):
    """이 플랫폼에는 올리지 않고 넘어간다 (실패로 세지 않음)."""


class YouTube(Platform):
    """YouTube Data API v3 — 숏폼 영상만 쇼츠로 올린다. 영상이 없으면 건너뛴다.
    인증은 refresh token (YOUTUBE_CLIENT_ID·YOUTUBE_CLIENT_SECRET·YOUTUBE_REFRESH_TOKEN).
    무료 한도는 하루 약 6건 — 다 쓰면 그 건은 건너뛴다. 심사 전 앱의 업로드는 유튜브가 비공개로 잠근다."""
    name, token_env = "youtube", "YOUTUBE_REFRESH_TOKEN"
    PRIVACY = os.environ.get("YOUTUBE_PRIVACY", "public")

    def access(self):
        if not self._uid:  # _uid 자리에 액세스 토큰을 캐시한다
            data = urllib.parse.urlencode({
                "client_id": os.environ["YOUTUBE_CLIENT_ID"],
                "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
                "refresh_token": self.token, "grant_type": "refresh_token"}).encode()
            try:
                with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data,
                                            timeout=30) as r:
                    self._uid = json.loads(r.read())["access_token"]
            except urllib.error.HTTPError as ex:
                raise RuntimeError("token refresh → %d %s" % (ex.code, ex.read()[:200]))
        return self._uid

    def title(self, item):
        spec = read_json(os.path.join(ROOT, "specs", "auto", item["slug"] + ".json"), {})
        head = item["text"].split("]")[0] + "] " if item["text"].startswith("[") else ""
        topic = spec.get("topic") or item["text"][len(head):].split(".")[0]
        t = (head + topic).replace("<", "").replace(">", "")
        return t[:90] + " #Shorts"

    def video_bytes(self, item):
        local = os.path.join(ROOT, "output", item["slug"], item["video"])
        if os.path.exists(local):
            with open(local, "rb") as fp:
                return fp.read()
        req = urllib.request.Request(media_url(item, item["video"]),
                                     headers={"User-Agent": "news-factory-uploader/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()

    def post(self, item):
        if not item.get("video"):
            raise Skip("영상 없음")
        tags = list(dict.fromkeys(DEFAULT_TAGS + item.get("hashtags", [])))
        meta = {
            "snippet": {"title": self.title(item),
                        "description": (item["text"] + "\n\n" +
                                        " ".join("#" + t.replace(" ", "") for t in tags))[:4900],
                        "tags": tags, "categoryId": "25",   # 뉴스·정치
                        "defaultLanguage": "ko", "defaultAudioLanguage": "ko"},
            "status": {"privacyStatus": self.PRIVACY, "selfDeclaredMadeForKids": False},
        }
        video = self.video_bytes(item)
        auth = {"Authorization": "Bearer " + self.access()}
        try:
            req = urllib.request.Request(
                "https://www.googleapis.com/upload/youtube/v3/videos"
                "?uploadType=resumable&part=snippet,status",
                data=json.dumps(meta).encode(), method="POST", headers=dict(auth, **{
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(len(video))}))
            with urllib.request.urlopen(req, timeout=60) as r:
                loc = r.headers["Location"]
            req = urllib.request.Request(loc, data=video, method="PUT",
                                         headers=dict(auth, **{"Content-Type": "video/mp4"}))
            with urllib.request.urlopen(req, timeout=600) as r:
                vid = json.loads(r.read())["id"]
        except urllib.error.HTTPError as ex:
            body = ex.read().decode(errors="replace")
            if "quotaExceeded" in body or "uploadLimitExceeded" in body:
                raise Skip("하루 업로드 한도 초과")
            try:
                body = json.loads(body)["error"]["message"]
            except (ValueError, KeyError):
                body = body[:300]
            raise RuntimeError("upload → %d: %s" % (ex.code, body))
        return vid, "https://youtube.com/shorts/" + vid


def main():
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None
    queue = read_json(QUEUE, [])
    if not queue:
        print("대기열 비어 있음")
        return 0
    active = [p for p in (Threads(), Instagram(), YouTube()) if p.token]
    if not active:
        print("게시 토큰 없음 — 건너뜀 (대기 %d건)" % len(queue))
        return 0
    if not MEDIA_BASE:
        print("MEDIA_BASE 없음 — 이미지 주소를 만들 수 없어 게시 건너뜀")
        return 1
    targets = [p for p in active if only in (None, p.name)]
    print("게시 대상:", ", ".join(p.name for p in targets) or "(없음)")

    log = read_json(LOG, [])
    failed = False
    remaining = []
    for item in queue:
        done = item.setdefault("done", {})
        tries = item["attempts"] = (item["attempts"] if isinstance(item.get("attempts"), dict)
                                    else {})
        todo = [p for p in targets if p.name not in done]
        age_h = (time.time() * 1000 - item["event_ms"]) / 3.6e6
        if todo and age_h > POST_MAX_AGE_H:
            print("EXPIRED", item["slug"], [p.name for p in todo], "(%.1f시간 지남)" % age_h)
            log.append(dict(item, status="expired", at=int(time.time())))
            continue
        for p in todo:
            try:
                mid, link = p.post(item)
                kind = ("shorts" if p.name == "youtube" else
                        "reel" if (p.name == "instagram" and item.get("video")) else "carousel")
                done[p.name] = {"id": mid, "permalink": link, "kind": kind, "at": int(time.time())}
                print("POSTED", p.name, kind, item["slug"], link)
                time.sleep(10)
            except Skip as ex:
                done[p.name] = {"skipped": str(ex), "at": int(time.time())}
                print("SKIP", p.name, item["slug"], ex)
            except Exception as ex:
                tries[p.name] = tries.get(p.name, 0) + 1
                print("FAIL", p.name, item["slug"], "attempt", tries[p.name], ex)
                if tries[p.name] >= MAX_ATTEMPTS:
                    done[p.name] = {"failed": str(ex)[:300], "at": int(time.time())}
                    failed = True
        if all(p.name in done for p in active):
            log.append(dict(item, status="done", at=int(time.time())))
        else:
            remaining.append(item)
    write_json(QUEUE, remaining)
    write_json(LOG, log)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
