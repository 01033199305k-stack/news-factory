# -*- coding: utf-8 -*-
"""
USGS 실시간 지진 피드를 감시하다가 기준을 넘는 지진이 나오면
카드뉴스 초안(spec + PNG + caption.md)을 자동으로 만든다.

    python watch_usgs.py            # 한 번 확인 (작업 스케줄러용)
    python watch_usgs.py --loop     # 3분마다 계속 확인
    python watch_usgs.py --event us6000txpi   # 특정 지진으로 강제 생성 (테스트)

만드는 건 '초안'이다. 게시 전에 사람이 caption.md 와 카드를 한 번 본다.
수치는 전부 USGS 응답에서 그대로 가져오고, 문장은 정해진 틀로만 만든다 (지어내지 않음).
"""
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import make_cards

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state", "usgs_seen.json")
QUEUE = os.path.join(ROOT, "state", "queue.json")     # 게시 대기
REVIEW = os.path.join(ROOT, "state", "review.json")   # 사람 확인 필요 (자동 게시 안 함)
SPECDIR = os.path.join(ROOT, "specs", "auto")

FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
QUERY = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# ── 기준 ─────────────────────────────────────────
MIN_MAG_WORLD = 6.0     # 전 세계: 규모 6.0 이상
MIN_MAG_NEAR = 5.0      # 서울 반경 NEAR_KM 안: 규모 5.0 이상 (일본·중국 동부·대만 등)
NEAR_KM = 1500
MAX_AGE_H = 6           # 이보다 오래된 지진은 속보로 안 만든다
LOOP_SEC = 180

SEOUL = (37.5665, 126.9780)
KST = timezone(timedelta(hours=9))

# ── 한국어 표기 ──────────────────────────────────
DIRS = {
    "N": "북", "NNE": "북북동", "NE": "북동", "ENE": "동북동", "E": "동",
    "ESE": "동남동", "SE": "남동", "SSE": "남남동", "S": "남", "SSW": "남남서",
    "SW": "남서", "WSW": "서남서", "W": "서", "WNW": "서북서", "NW": "북서", "NNW": "북북서",
}

REGIONS = {
    "Japan": "일본", "Indonesia": "인도네시아", "Philippines": "필리핀",
    "Papua New Guinea": "파푸아뉴기니", "New Caledonia": "뉴칼레도니아",
    "Vanuatu": "바누아투", "Fiji": "피지", "Tonga": "통가", "Samoa": "사모아",
    "American Samoa": "미국령 사모아", "Solomon Islands": "솔로몬 제도",
    "New Zealand": "뉴질랜드", "Kermadec Islands": "케르마데크 제도",
    "Chile": "칠레", "Peru": "페루", "Ecuador": "에콰도르", "Colombia": "콜롬비아",
    "Bolivia": "볼리비아", "Argentina": "아르헨티나", "Venezuela": "베네수엘라",
    "Mexico": "멕시코", "Guatemala": "과테말라", "El Salvador": "엘살바도르",
    "Nicaragua": "니카라과", "Costa Rica": "코스타리카", "Panama": "파나마",
    "Honduras": "온두라스", "Haiti": "아이티", "Dominican Republic": "도미니카 공화국",
    "Puerto Rico": "푸에르토리코", "Jamaica": "자메이카", "Cuba": "쿠바",
    "Alaska": "미국 알래스카", "California": "미국 캘리포니아", "Hawaii": "미국 하와이",
    "Oregon": "미국 오리건", "Washington": "미국 워싱턴주", "Nevada": "미국 네바다",
    "Aleutian Islands, Alaska": "미국 알류샨 열도", "Canada": "캐나다",
    "Taiwan": "대만", "China": "중국", "Mongolia": "몽골", "Russia": "러시아",
    "South Korea": "한국", "North Korea": "북한", "Myanmar": "미얀마", "Burma": "미얀마",
    "India": "인도", "Nepal": "네팔", "Bhutan": "부탄", "Bangladesh": "방글라데시",
    "Pakistan": "파키스탄", "Afghanistan": "아프가니스탄", "Tajikistan": "타지키스탄",
    "Kyrgyzstan": "키르기스스탄", "Kazakhstan": "카자흐스탄", "Uzbekistan": "우즈베키스탄",
    "Iran": "이란", "Iraq": "이라크", "Turkey": "튀르키예", "Türkiye": "튀르키예",
    "Syria": "시리아", "Greece": "그리스", "Italy": "이탈리아", "Albania": "알바니아",
    "Croatia": "크로아티아", "Romania": "루마니아", "Portugal": "포르투갈", "Spain": "스페인",
    "Iceland": "아이슬란드", "Morocco": "모로코", "Algeria": "알제리",
    "Timor Leste": "동티모르", "East Timor": "동티모르", "Guam": "괌",
    "Northern Mariana Islands": "북마리아나 제도", "Micronesia": "미크로네시아",
    "Wallis and Futuna": "왈리스 푸투나", "Kuril Islands": "쿠릴 열도",
}

PAGER = {  # USGS PAGER 경보 등급 → (표시, 설명)
    "green": ("초록", "인명·경제 피해 가능성 낮음"),
    "yellow": ("노랑", "일부 인명·경제 피해 가능"),
    "orange": ("주황", "상당한 인명·경제 피해 가능"),
    "red": ("빨강", "대규모 인명·경제 피해 가능"),
}


def get_json(url, params=None):
    if params:
        url += "?" + "&".join("%s=%s" % kv for kv in params.items())
    req = urllib.request.Request(url, headers={"User-Agent": "news-factory/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def km_between(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def region_ko(place):
    """'80 km ENE of Tadine, New Caledonia' → ('뉴칼레도니아', '뉴칼레도니아 Tadine에서 동북동쪽 80km', True)
    사전에 없으면 원문을 그대로 두고 needs_review=True."""
    tail = place.split(", ")[-1] if ", " in place else ""
    for key in sorted(REGIONS, key=len, reverse=True):  # 긴 이름 먼저 (Aleutian Islands, Alaska)
        if place.endswith(key):
            country = REGIONS[key]
            head = place[: -len(key)].rstrip(", ")
            parts = head.split(" of ", 1)
            if len(parts) == 2 and parts[0].endswith(tuple(DIRS)):
                dist, d = parts[0].rsplit(" ", 1)
                km = dist.replace(" km", "").strip()
                where = "%s %s에서 %s쪽 %skm" % (country, parts[1], DIRS.get(d, d), km)
            else:
                where = "%s (%s)" % (country, head) if head else country
            return country, where, False
    return tail or place, place, True


def fmt_kst(ms, pattern="%Y.%m.%d %H:%M KST"):
    return datetime.fromtimestamp(ms / 1000, KST).strftime(pattern)


def qualifies(f):
    p, g = f["properties"], f["geometry"]["coordinates"]
    mag = p.get("mag") or 0
    age_h = (time.time() * 1000 - p["time"]) / 3.6e6
    if age_h > MAX_AGE_H or p.get("type") != "earthquake":
        return False
    if mag >= MIN_MAG_WORLD:
        return True
    return mag >= MIN_MAG_NEAR and km_between(SEOUL, (g[1], g[0])) <= NEAR_KM


def aftershocks(ev):
    """본진 이후 반경 150km, 규모 4 이상 (본진 제외)."""
    p, g = ev["properties"], ev["geometry"]["coordinates"]
    start = datetime.fromtimestamp(p["time"] / 1000 + 1, timezone.utc)
    d = get_json(QUERY, {
        "format": "geojson", "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "latitude": g[1], "longitude": g[0], "maxradiuskm": 150, "minmagnitude": 4,
    })
    fs = [f for f in d["features"] if f["id"] != ev["id"]]
    return sorted(fs, key=lambda f: f["properties"]["time"])


def build_spec(ev):
    p, g = ev["properties"], ev["geometry"]["coordinates"]
    lon, lat, depth = g[0], g[1], g[2]
    mag = p["mag"]
    mag_s = "%.1f" % mag
    country, where, review = region_ko(p.get("place") or "")
    seoul_km = km_between(SEOUL, (lat, lon))
    t_kst = fmt_kst(p["time"])
    now_kst = datetime.now(KST).strftime("%Y.%m.%d %H:%M KST")
    alert = PAGER.get(p.get("alert") or "")
    after = aftershocks(ev)
    depth_s = "%dkm" % round(depth)

    chips = [{"k": "규모", "v": mag_s}, {"k": "깊이", "v": depth_s}]
    chips.append({"k": "서울에서", "v": "%skm" % format(int(round(seoul_km, -1)), ",")})

    mt = (p.get("magType") or "").lower()
    mt_s = " (모멘트 규모)" if mt.startswith("mw") else ""
    confirmed = ["규모 %s%s, 깊이 %s" % (mag_s, mt_s, depth_s)]
    unconfirmed = ["현지 당국의 피해 집계"]
    if alert:
        confirmed.append("USGS 피해 추정(PAGER) '%s' = %s" % alert)
    else:
        unconfirmed.append("USGS 피해 추정(PAGER) 아직 산정 전")
    if p.get("tsunami") == 1:
        # USGS tsunami=1 은 '쓰나미 정보를 확인할 해역 지진'이라는 표시일 뿐 경보가 아니다
        unconfirmed.append("쓰나미 경보 여부 (각국 경보센터 발표 확인 필요)")
    else:
        confirmed.append("USGS 이벤트에 쓰나미 표시 없음")

    ll = "%s %.2f° %s %.2f°" % ("남위" if lat < 0 else "북위", abs(lat),
                                 "서경" if lon < 0 else "동경", abs(lon))
    cards = [
        {
            "type": "cover", "badge": "breaking",
            "title": "%s 인근\n규모 [[%s]] 지진" % (country, mag_s),
            "sub": where, "chips": chips,
            "map": {"lat": lat, "lon": lon, "zoom": 0.7, "h": 430, "label": "진앙"},
            "alt": "%s 인근 규모 %s 지진 속보 표지와 진앙 지도" % (country, mag_s),
        },
        {
            "type": "map", "eyebrow": "어디서",
            "title": "%s 인근\n서울에서 약 %skm" % (country, format(int(round(seoul_km, -1)), ",")),
            "map": {"lat": lat, "lon": lon, "zoom": 0.3, "h": 700, "ring": 18, "label": ll},
            "alt": "진앙 위치를 표시한 지도",
        },
    ]
    if after:
        big = max(after, key=lambda f: f["properties"]["mag"])
        gap = (big["properties"]["time"] - p["time"]) / 60000
        cards.append({
            "type": "fact", "eyebrow": "여진",
            "title": "본진 이후\n규모 4 이상 여진", "value": str(len(after)), "unit": "회",
            "sub": "가장 큰 여진은 본진 %d시간 %d분 뒤 규모 %.1f\n%s 기준"
                   % (gap // 60, gap % 60, big["properties"]["mag"], now_kst),
            "alt": "규모 4 이상 여진 %d회" % len(after),
        })
    cards += [
        {"type": "check", "confirmed": confirmed, "unconfirmed": unconfirmed,
         "alt": "공식 확인된 사실과 아직 확인되지 않은 사항"},
        {"type": "outro",
         "sources": ["USGS 이벤트 페이지 %s" % ev["id"],
                     "USGS 지진 목록 (반경 150km, 규모 4 이상)",
                     "%s 기준 조회" % now_kst],
         "cta": "세계 사건·사고,\n확인된 것만 가장 빠르게", "cta_sub": "팔로우하고 먼저 받아보세요",
         "alt": "출처와 팔로우 안내"},
    ]

    lines = ["[속보] %s 인근 규모 %s 지진" % (country, mag_s), "",
             "한국 시각 %s, %s. 깊이 %s." % (fmt_kst(p["time"], "%m월 %d일 %H시 %M분"), where, depth_s),
             ""]
    if alert:
        lines.append("• USGS 피해 추정 등급 '%s' (%s)" % alert)
    if after:
        lines.append("• 이후 규모 4 이상 여진 %d회" % len(after))
    lines += ["• 현지 피해 집계는 아직 없음", "", "출처: USGS %s" % ev["id"]]

    slug = "%s-usgs-%s" % (fmt_kst(p["time"], "%Y-%m-%d"), ev["id"])
    return {
        "slug": slug,
        "topic": "%s 인근 규모 %s 지진" % (country, mag_s),
        "brand": "지금 세계", "theme": "night", "time": t_kst,
        "source": "USGS (미국 지질조사국) %s" % ev["id"],
        "needs_review": review,
        "usgs": {"id": ev["id"], "place": p.get("place"), "url": p.get("url")},
        "cards": cards,
        "caption": {"threads": "\n".join(lines),
                    "hashtags": ["지진", country.replace(" ", ""), "세계뉴스", "속보", "해외뉴스"]},
    }


def notify(title, body):
    """윈도우 알림 (실패해도 조용히 넘어간다)."""
    if os.name != "nt":
        return
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
        "$t=$x.GetElementsByTagName('text');$t.Item(0).AppendChild($x.CreateTextNode($env:NF_T))|Out-Null;"
        "$t.Item(1).AppendChild($x.CreateTextNode($env:NF_B))|Out-Null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('news-factory').Show("
        "[Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    import subprocess
    env = dict(os.environ, NF_T=title, NF_B=body)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_seen():
    try:
        with open(STATE, encoding="utf-8") as fp:
            return set(json.load(fp))
    except (OSError, ValueError):
        return set()


def save_seen(seen):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as fp:
        json.dump(sorted(seen)[-500:], fp)


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


def make_draft(ev, queue=False):
    """카드를 만들고, queue=True 면 게시 대기열(state/queue.json)에 올린다.
    지명 번역이 안 된 건 자동 게시하지 않고 검토 목록(state/review.json)으로 보낸다."""
    spec = build_spec(ev)
    write_json(os.path.join(SPECDIR, spec["slug"] + ".json"), spec)
    outdir, made = make_cards.render(json.loads(json.dumps(spec)))
    flag = " (지명 번역 확인 필요)" if spec["needs_review"] else ""
    print("DRAFT", spec["topic"], "->", outdir, flag)
    if not os.environ.get("GITHUB_ACTIONS"):
        notify("속보 초안: " + spec["topic"], "카드 %d장 생성됨%s" % (len(made), flag))
    if queue:
        item = {"slug": spec["slug"], "event_ms": ev["properties"]["time"],
                "images": [name for name, _ in made],
                "text": spec["caption"]["threads"], "topic_tag": "지진",
                "hashtags": spec["caption"].get("hashtags", [])}
        target = REVIEW if spec["needs_review"] else QUEUE
        q = read_json(target, [])
        q.append(item)
        write_json(target, q)
    return outdir


def check_once(queue=False):
    seen = load_seen()
    feed = get_json(FEED)
    new = [f for f in feed["features"] if f["id"] not in seen and qualifies(f)]
    for f in sorted(new, key=lambda f: f["properties"]["time"]):
        try:
            make_draft(f, queue=queue)
        except Exception as ex:  # 한 건 실패해도 다음 건은 계속
            print("FAIL", f["id"], ex)
            continue
        seen.add(f["id"])
        save_seen(seen)
    if not new:
        print(datetime.now(KST).strftime("%H:%M:%S"), "새 속보 없음")


def seed():
    """지금 피드에 있는 지진을 전부 '이미 본 것'으로 표시한다. 다음 속보부터 올리기 위해."""
    seen = load_seen()
    ids = [f["id"] for f in get_json(FEED)["features"]]
    seen.update(ids)
    save_seen(seen)
    print("seeded %d events" % len(ids))


def main():
    args = sys.argv[1:]
    queue = "--queue" in args  # 클라우드: 만든 초안을 게시 대기열에 올린다
    if "--seed" in args:
        seed()
        return
    if "--event" in args:
        eid = args[args.index("--event") + 1]
        make_draft(get_json(QUERY, {"format": "geojson", "eventid": eid}))
        return
    if "--loop" in args:
        while True:
            try:
                check_once(queue)
            except Exception as ex:
                print("ERR", ex)
            time.sleep(LOOP_SEC)
    check_once(queue)


if __name__ == "__main__":
    main()
