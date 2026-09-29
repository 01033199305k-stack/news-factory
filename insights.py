# -*- coding: utf-8 -*-
"""
올린 게시물의 반응(조회·좋아요·댓글·공유)을 플랫폼별로 모아 표로 출력한다. 아무것도 게시하지 않는다.

    python insights.py            # 표 출력 + state/insights.json 저장

- 스레드: /{id}/insights (threads_manage_insights 권한이 없으면 오류만 적고 넘어간다)
- 인스타: /{id}/insights (릴스는 조회·도달·평균 시청 시간까지)
- 유튜브: Data API 통계 + (권한이 있으면) Analytics API 평균 시청 비율
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from post import Instagram, Threads, YouTube, read_json, write_json, LOG, ROOT

OUT = os.path.join(ROOT, "state", "insights.json")
KST = timezone(timedelta(hours=9))


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as ex:  # 한 건이 실패해도 나머지는 모은다
        return {"error": str(ex)[:160]}


def metrics(rows):
    """insights 응답의 data 배열 → {이름: 값}"""
    out = {}
    for m in rows.get("data", []):
        vals = m.get("values") or [{}]
        out[m["name"]] = m.get("total_value", {}).get("value", vals[-1].get("value"))
    return out


def threads_stats(th, ids):
    res = {}
    for slug, mid in ids:
        r = safe(th.api, "GET", "%s/insights" % mid, metric="views,likes,replies,reposts,quotes,shares")
        res[slug] = r if "error" in r else metrics(r)
    acct = safe(th.api, "GET", "%s/threads_insights" % th.uid(),
                metric="views,likes,replies,reposts,quotes,followers_count")
    return res, (acct if "error" in acct else metrics(acct))


def instagram_stats(ig, ids):
    res = {}
    for slug, mid in ids:
        base = safe(ig.api, "GET", mid, fields="media_product_type,like_count,comments_count")
        reel = base.get("media_product_type") == "REELS"
        names = ("views,reach,likes,comments,shares,saved,total_interactions,ig_reels_avg_watch_time"
                 if reel else "views,reach,likes,comments,shares,saved,total_interactions")
        r = safe(ig.api, "GET", "%s/insights" % mid, metric=names)
        if "error" in r:  # 지원 안 하는 지표가 섞였으면 기본만 다시
            r = safe(ig.api, "GET", "%s/insights" % mid, metric="reach,likes,comments,shares,saved")
        row = dict(base) if "error" not in base else {}
        row.update(r if "error" in r else metrics(r))
        res[slug] = row
    acct = safe(ig.api, "GET", "me", fields="username,followers_count,media_count")
    return res, acct


def youtube_stats(yt, ids):
    tok = yt.access()
    res = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        q = urllib.parse.urlencode({"part": "statistics,status", "id": ",".join(m for _, m in chunk)})
        req = urllib.request.Request("https://www.googleapis.com/youtube/v3/videos?" + q,
                                     headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=30) as r:
            items = {v["id"]: v for v in json.loads(r.read())["items"]}
        for slug, mid in chunk:
            v = items.get(mid, {})
            st = v.get("statistics", {})
            res[slug] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                         "comments": int(st.get("commentCount", 0)),
                         "privacy": v.get("status", {}).get("privacyStatus")}
    # 평균 시청 비율 — yt-analytics.readonly 권한이 없으면 오류만 남긴다
    today = datetime.now(KST).date()
    q = urllib.parse.urlencode({
        "ids": "channel==MINE", "startDate": str(today - timedelta(days=7)), "endDate": str(today),
        "metrics": "views,averageViewDuration,averageViewPercentage", "dimensions": "video",
        "sort": "-views", "maxResults": 50})
    req = urllib.request.Request("https://youtubeanalytics.googleapis.com/v2/reports?" + q,
                                 headers={"Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read()).get("rows", [])
        by_id = {row[0]: row for row in rows}
        for slug, mid in ids:
            if mid in by_id:
                res[slug]["avg_sec"] = by_id[mid][2]
                res[slug]["avg_pct"] = round(by_id[mid][3], 1)
        analytics = "ok"
    except urllib.error.HTTPError as ex:
        analytics = "analytics %d (권한 없음이면 정상)" % ex.code
    return res, analytics


def main():
    log = read_json(LOG, [])
    ids = {p: [(it["slug"], it["done"][p]["id"]) for it in log if "id" in it.get("done", {}).get(p, {})]
           for p in ("threads", "instagram", "youtube")}
    out = {"at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"), "account": {}}
    th, ig, yt = Threads(), Instagram(), YouTube()
    if th.token:
        out["threads"], out["account"]["threads"] = threads_stats(th, ids["threads"])
    if ig.token:
        out["instagram"], out["account"]["instagram"] = instagram_stats(ig, ids["instagram"])
    if yt.token:
        out["youtube"], out["account"]["youtube_analytics"] = safe(youtube_stats, yt, ids["youtube"])

    print("계정:", json.dumps(out["account"], ensure_ascii=False))
    for it in log:
        slug = it["slug"]
        at = datetime.fromtimestamp(it.get("at", 0), KST).strftime("%m-%d %H:%M")
        cells = []
        for p in ("threads", "instagram", "youtube"):
            s = (out.get(p) or {}).get(slug)
            if not s:
                cells.append("%s -" % p[:2])
            elif "error" in s:
                cells.append("%s ERR(%s)" % (p[:2], s["error"][:60]))
            else:
                cells.append("%s v%s l%s c%s%s" % (
                    p[:2], s.get("views", s.get("reach", "?")), s.get("likes", s.get("like_count", "?")),
                    s.get("replies", s.get("comments", s.get("comments_count", "?"))),
                    (" %s%%" % s["avg_pct"]) if "avg_pct" in s else
                    (" %sms" % s["ig_reels_avg_watch_time"]) if "ig_reels_avg_watch_time" in s else ""))
        print(at, slug[11:55].ljust(44), " | ".join(cells))
    write_json(OUT, out)
    print("INSIGHTS_JSON " + json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
