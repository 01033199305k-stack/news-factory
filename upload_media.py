# -*- coding: utf-8 -*-
"""
게시 대기열 항목의 카드(JPEG)와 숏폼(mp4)을 Cloudflare 감시기의 미디어 보관소에 올린다.
인스타·스레드는 이 주소에서 이미지·영상을 가져간다. 보관은 3일 (worker/src/index.js).

    MEDIA_BASE=https://news-factory-watch.alsgur3319.workers.dev/media python upload_media.py

인증은 GitHub Actions OIDC 토큰 (워크플로에 permissions: id-token: write 필요) — 따로 넣을 비밀값이 없다.
이미 올린 파일은 item["uploaded"] 에 적어 두고 다시 올리지 않는다.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import make_cards

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "state" / "queue.json"
SPECDIR = ROOT / "specs" / "auto"
MEDIA_BASE = os.environ.get("MEDIA_BASE", "").rstrip("/")
AUDIENCE = "news-factory-media"
TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4"}
SETTLE_SEC = 20   # KV 는 다른 지역에 퍼지는 데 잠깐 걸린다 — 가져가기 전에 조금 기다린다


def oidc_token():
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=" + urllib.parse.quote(AUDIENCE)
    req = urllib.request.Request(url, headers={
        "Authorization": "bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["value"]


def put(key, path, token):
    data = Path(path).read_bytes()
    req = urllib.request.Request("%s/%s" % (MEDIA_BASE, key), data=data, method="PUT", headers={
        "Authorization": "Bearer " + token, "Content-Type": TYPES[Path(path).suffix]})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status
    except urllib.error.HTTPError as ex:
        raise RuntimeError("PUT %s → %d %s" % (key, ex.code, ex.read()[:200]))


def wanted(item):
    """올려야 할 파일 이름들 — 카드는 JPEG, 영상이 있으면 mp4."""
    names = [n[:-4] + ".jpg" for n in item["images"]]
    if item.get("video"):
        names.append(item["video"])
    return names


def ensure_files(item):
    """클라우드 다음 실행 등으로 카드 파일이 없으면 spec 으로 다시 그린다."""
    outdir = ROOT / "output" / item["slug"]
    if all((outdir / n).exists() for n in wanted(item) if n.endswith(".jpg")):
        return
    spec = json.loads((SPECDIR / (item["slug"] + ".json")).read_text(encoding="utf-8"))
    make_cards.render(spec)


def main():
    q = json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else []
    todo = [(it, n) for it in q for n in wanted(it) if n not in it.get("uploaded", [])]
    if not todo:
        print("올릴 미디어 없음")
        return 0
    if not MEDIA_BASE:
        print("MEDIA_BASE 없음")
        return 1
    token = oidc_token()
    for item in {id(it): it for it, _ in todo}.values():
        try:
            ensure_files(item)
        except Exception as ex:
            print("RENDER FAIL", item["slug"], ex)
    for item, name in todo:
        path = ROOT / "output" / item["slug"] / name
        if not path.exists():
            print("MISSING", item["slug"], name)
            if name == item.get("video"):
                item["video"] = None  # 영상이 없으면 인스타는 카드 캐러셀로 올린다
            continue
        try:
            put("%s/%s" % (item["slug"], name), path, token)
            item.setdefault("uploaded", []).append(name)
            print("UPLOADED", item["slug"], name, "%.1fMB" % (path.stat().st_size / 1e6))
        except Exception as ex:
            print("UPLOAD FAIL", item["slug"], name, ex)
    QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
    time.sleep(SETTLE_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
