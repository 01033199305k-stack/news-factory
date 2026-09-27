# -*- coding: utf-8 -*-
"""
게시 대기열(state/queue.json)의 카드뉴스를 스레드·인스타그램에 캐러셀로 올린다.

    IMAGE_BASE=https://raw.githubusercontent.com/<owner>/<repo>/<sha> python post.py

두 API 모두 이미지를 공개 URL 로만 받는다. 그래서 GitHub Actions 가 카드를 먼저 커밋·푸시하고,
그 커밋의 raw 주소를 IMAGE_BASE 로 넘긴다.

- 토큰이 있는 플랫폼에만 올린다 (THREADS_ACCESS_TOKEN / INSTAGRAM_ACCESS_TOKEN)
- 플랫폼별로 올린 결과를 item["done"] 에 적어서, 한쪽이 실패해도 다른 쪽을 다시 올리지 않는다
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
IMAGE_BASE = os.environ.get("IMAGE_BASE", "").rstrip("/")
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

    def image_urls(self, item, ext):
        return ["%s/output/%s/%s" % (IMAGE_BASE, item["slug"], n[:-4] + ext)
                for n in item["images"]]


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
                for u in self.image_urls(item, ".png")[:20]]
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
    """Instagram API (인스타그램 로그인). 프로페셔널 계정 필요, 이미지는 JPEG 만, 캐러셀 10장까지."""
    name, host, token_env = "instagram", "https://graph.instagram.com", "INSTAGRAM_ACCESS_TOKEN"

    def uid(self):
        if not self._uid:
            me = self.api("GET", "me", fields="user_id,username")
            self._uid = me.get("user_id") or me["id"]
        return self._uid

    def wait(self, cid, timeout=180):
        end = time.time() + timeout
        while time.time() < end:
            st = self.api("GET", cid, fields="status_code").get("status_code")
            if st in ("FINISHED", "PUBLISHED"):
                return
            if st in ("ERROR", "EXPIRED"):
                raise RuntimeError("container %s: %s" % (cid, st))
            time.sleep(5)
        raise RuntimeError("container %s not ready in %ds" % (cid, timeout))

    def post(self, item):
        uid = self.uid()
        kids = [self.api("POST", "%s/media" % uid, image_url=u, is_carousel_item="true")["id"]
                for u in self.image_urls(item, ".jpg")[:10]]
        for k in kids:
            self.wait(k)
        tags = " ".join("#" + t.replace(" ", "").lstrip("#")
                        for t in dict.fromkeys(DEFAULT_TAGS + item.get("hashtags", [])))
        caption = (item["text"] + "\n\n" + tags)[:2200]
        parent = self.api("POST", "%s/media" % uid, media_type="CAROUSEL",
                          children=",".join(kids), caption=caption)["id"]
        self.wait(parent)
        mid = self.api("POST", "%s/media_publish" % uid, creation_id=parent)["id"]
        return mid, self.api("GET", mid, fields="permalink").get("permalink", "")


def main():
    queue = read_json(QUEUE, [])
    if not queue:
        print("대기열 비어 있음")
        return 0
    active = [p for p in (Threads(), Instagram()) if p.token]
    if not active:
        print("게시 토큰 없음 — 건너뜀 (대기 %d건)" % len(queue))
        return 0
    if not IMAGE_BASE:
        print("IMAGE_BASE 없음 — 이미지 주소를 만들 수 없어 게시 건너뜀")
        return 1
    print("게시 대상:", ", ".join(p.name for p in active))

    log = read_json(LOG, [])
    failed = False
    remaining = []
    for item in queue:
        done = item.setdefault("done", {})
        tries = item["attempts"] = (item["attempts"] if isinstance(item.get("attempts"), dict)
                                    else {})
        todo = [p for p in active if p.name not in done]
        age_h = (time.time() * 1000 - item["event_ms"]) / 3.6e6
        if todo and age_h > POST_MAX_AGE_H:
            print("EXPIRED", item["slug"], [p.name for p in todo], "(%.1f시간 지남)" % age_h)
            log.append(dict(item, status="expired", at=int(time.time())))
            continue
        for p in todo:
            try:
                mid, link = p.post(item)
                done[p.name] = {"id": mid, "permalink": link, "at": int(time.time())}
                print("POSTED", p.name, item["slug"], link)
                time.sleep(15)
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
