# -*- coding: utf-8 -*-
"""
게시 대기열(state/queue.json)의 카드뉴스를 스레드에 캐러셀로 올린다.

    IMAGE_BASE=https://raw.githubusercontent.com/<owner>/<repo>/<sha> \\
    THREADS_USER_ID=... THREADS_ACCESS_TOKEN=... python post_threads.py

스레드 API는 이미지를 URL로만 받는다. 그래서 GitHub Actions 가 카드를 먼저 커밋·푸시하고,
그 커밋의 raw 주소를 IMAGE_BASE 로 넘긴다.

- 토큰이 없으면 아무것도 올리지 않고 대기열을 그대로 둔다
- 발생 후 POST_MAX_AGE_H 시간이 지난 건은 '속보'로 늦었으니 올리지 않고 expired 로 뺀다
- 3번 연속 실패한 건은 failed 로 빼고 exit 1 → GitHub 가 실패 메일을 보낸다
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

API = "https://graph.threads.net/v1.0"
POST_MAX_AGE_H = 6
MAX_ATTEMPTS = 3
MAX_CHARS = 500

USER_ID = os.environ.get("THREADS_USER_ID", "")
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
IMAGE_BASE = os.environ.get("IMAGE_BASE", "").rstrip("/")


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


def api(method, path, **params):
    params["access_token"] = TOKEN
    url = "%s/%s" % (API, path)
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


def wait_ready(cid, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = api("GET", cid, fields="status,error_message")
        if st.get("status") == "FINISHED":
            return
        if st.get("status") in ("ERROR", "EXPIRED"):
            raise RuntimeError("container %s: %s" % (cid, st.get("error_message", st)))
        time.sleep(4)
    raise RuntimeError("container %s not ready in %ds" % (cid, timeout))


def post_carousel(item):
    urls = ["%s/output/%s/%s" % (IMAGE_BASE, item["slug"], name) for name in item["images"]]
    children = []
    for u in urls[:20]:  # 캐러셀은 2~20장
        c = api("POST", "%s/threads" % USER_ID, media_type="IMAGE",
                image_url=u, is_carousel_item="true")
        children.append(c["id"])
    for cid in children:
        wait_ready(cid)
    params = {"media_type": "CAROUSEL", "children": ",".join(children), "text": item["text"]}
    if item.get("topic_tag"):
        params["topic_tag"] = item["topic_tag"]
    parent = api("POST", "%s/threads" % USER_ID, **params)["id"]
    wait_ready(parent)
    mid = api("POST", "%s/threads_publish" % USER_ID, creation_id=parent)["id"]
    link = api("GET", mid, fields="permalink").get("permalink", "")
    return mid, link


def main():
    queue = read_json(QUEUE, [])
    if not queue:
        print("대기열 비어 있음")
        return 0
    if not (USER_ID and TOKEN):
        print("THREADS_USER_ID / THREADS_ACCESS_TOKEN 없음 — 게시 건너뜀 (대기 %d건)" % len(queue))
        return 0
    if not IMAGE_BASE:
        print("IMAGE_BASE 없음 — 이미지 주소를 만들 수 없어 게시 건너뜀")
        return 1

    log = read_json(LOG, [])
    failed = False
    remaining = []
    for item in queue:
        age_h = (time.time() * 1000 - item["event_ms"]) / 3.6e6
        if age_h > POST_MAX_AGE_H:
            print("EXPIRED", item["slug"], "(%.1f시간 지남)" % age_h)
            log.append(dict(item, status="expired", at=int(time.time())))
            continue
        if len(item["text"]) > MAX_CHARS:
            item["text"] = item["text"][:MAX_CHARS - 1] + "…"
        try:
            mid, link = post_carousel(item)
            print("POSTED", item["slug"], link)
            log.append(dict(item, status="posted", id=mid, permalink=link, at=int(time.time())))
            time.sleep(20)
        except Exception as ex:
            item["attempts"] = item.get("attempts", 0) + 1
            print("FAIL", item["slug"], "attempt", item["attempts"], ex)
            if item["attempts"] >= MAX_ATTEMPTS:
                log.append(dict(item, status="failed", error=str(ex), at=int(time.time())))
                failed = True
            else:
                remaining.append(item)
    write_json(QUEUE, remaining)
    write_json(LOG, log)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
