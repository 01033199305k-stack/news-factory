# -*- coding: utf-8 -*-
"""
숏폼 나레이션 원고. 카드에 이미 들어간 '확인된 문장'만 이어 붙인다 — AI 로 새로 쓰지 않는다.
그래서 영상에 카드에 없는 사실이 끼어들 틈이 없다.

원고 = [{"text": 읽을 문장, "card": 보여줄 카드 종류(cover·map·fact·points·check·outro)}, ...]
"""
import re

INTRO = {
    "breaking": "지금 세계, 해외 속보입니다.",
    "brief": "지금 세계, 해외 뉴스 정리입니다.",
    "update": "지금 세계, 해외 속보 업데이트입니다.",
}
CHECK_INTRO = "지금까지 확인된 것과 아직 확인되지 않은 것을 나눠 보겠습니다."
OUTRO = "세계의 사건 사고, 확인된 것만 가장 빠르게. 지금 세계였습니다."

TARGET_MAX_SEC = 52   # 이보다 길면 핵심 정리부터 줄인다 (목표 45초 안팎)

# 나레이션에서 읽을 매체 이름 (카드에는 원래 이름이 그대로 나간다)
OUTLET_KO = {
    "Reuters": "로이터", "BBC": "BBC", "The Guardian": "가디언", "Al Jazeera": "알자지라",
    "CNN": "CNN", "AP News": "AP", "Associated Press": "AP", "The New York Times": "뉴욕타임스",
    "nytimes.com": "뉴욕타임스", "The Washington Post": "워싱턴포스트", "Financial Times": "파이낸셜타임스",
    "The Independent": "인디펜던트", "Sky News": "스카이뉴스", "CBS News": "CBS", "NBC News": "NBC",
    "ABC News": "ABC", "Bloomberg": "블룸버그", "Bloomberg.com": "블룸버그", "DW": "도이체벨레",
    "France 24": "프랑스24", "NPR": "NPR", "The Times of India": "타임스오브인디아",
    "Hindustan Times": "힌두스탄타임스", "NDTV": "NDTV", "The Telegraph": "텔레그래프",
    "Fox News": "폭스뉴스", "USA Today": "USA투데이", "Axios": "악시오스", "Politico": "폴리티코",
    "The Wall Street Journal": "월스트리트저널", "WSJ": "월스트리트저널", "The Times": "더타임스",
    "The Hindu": "더힌두", "India Today": "인디아투데이", "Euronews": "유로뉴스",
    "The Economist": "이코노미스트", "Newsweek": "뉴스위크", "TIME": "타임", "Yahoo News": "야후뉴스",
}


_JUNK = re.compile(r"(news from|^latest|^breaking|live updates|msn|google|^videos?$)", re.I)
_DOMAINS = {"foxnews.com": "Fox News", "nytimes.com": "The New York Times", "cnn.com": "CNN",
            "bbc.com": "BBC", "bbc.co.uk": "BBC", "reuters.com": "Reuters", "apnews.com": "AP News",
            "theguardian.com": "The Guardian", "aljazeera.com": "Al Jazeera", "nbcnews.com": "NBC News",
            "cbsnews.com": "CBS News", "abcnews.go.com": "ABC News", "washingtonpost.com": "The Washington Post",
            "yahoo.com": "Yahoo News", "news.yahoo.com": "Yahoo News", "Yahoo": "Yahoo News"}


def norm_outlet(s):
    """"ABC News - Breaking News, Latest News and Videos" → "ABC News", "foxnews.com" → "Fox News"."""
    s = re.split(r"\s+[-|–—:]\s+", (s or "").strip())[0].strip()
    s = _DOMAINS.get(s.lower().removeprefix("www."), _DOMAINS.get(s, s))
    return s


def clean_outlets(srcs):
    """보도 매체 이름을 다듬고 매체 이름이 아닌 것(예: "Latest news from Azerbaijan")은 뺀다.
    잘 알려진 매체를 앞에 둔다."""
    good = []
    for x in srcs:
        n = norm_outlet(x)
        if n and len(n) <= 26 and not _JUNK.search(n) and n not in good:
            good.append(n)
    known = [n for n in good if n in OUTLET_KO]
    return known + [n for n in good if n not in OUTLET_KO]


def outlets(srcs, n=3):
    names = []
    for s in clean_outlets(srcs):
        k = OUTLET_KO.get(s, s)
        if k not in names:
            names.append(k)
    return ", ".join(names[:n])


def strip_src(s):
    """문장 속 괄호 출처 "(BBC·로이터)" 는 읽지 않는다 — 화면 카드에 이미 있다."""
    return re.sub(r"\s*\([^)]*\)", "", s or "").strip()


def sentence(s):
    s = s.strip().rstrip(".。 ")
    return s + "." if s else ""


def _unconfirmed(x):
    # "범행 동기 미확인" 을 "아직 확인되지 않은 것은 ~입니다" 로 읽을 때 '미확인'이 겹치지 않게
    return re.sub(r"\s*(미확인|아직 확인 안 됨|확인되지 않음|확인 안 됨)\s*$", "", strip_src(x))


def est_seconds(segs):
    """대략의 길이. 한국어 여성 아나운서(SunHi, +0%) 기준 초당 약 6.5음절 + 문장 사이 쉼."""
    total = 0.0
    for s in segs:
        hangul = len(re.findall(r"[가-힣]", s["text"]))
        other = len(re.findall(r"[A-Za-z0-9]", s["text"]))
        total += hangul / 6.5 + other / 9 + 0.35
    return total


def trim(segs):
    """너무 길면 핵심 정리 문장부터 뒤에서 하나씩 뺀다 (최소 1개는 남긴다)."""
    while est_seconds(segs) > TARGET_MAX_SEC:
        pts = [i for i, s in enumerate(segs) if s["card"] == "points"]
        if len(pts) <= 1:
            break
        segs.pop(pts[-1])
    return segs


def for_news(ev, srcs, badge="breaking", has_map=False):
    segs = []

    def add(text, card):
        if text:
            segs.append({"text": text, "card": card})

    add(INTRO.get(badge, INTRO["breaking"]), "cover")
    add(sentence(strip_src(ev["sub_ko"])), "cover")
    add("%s 등 %d개 매체가 이 소식을 전했습니다." % (outlets(srcs), len(srcs)),
        "map" if has_map else "cover")
    for p in ev["points"][:3]:
        add(sentence(strip_src(p["d"])), "points")
    # "확인된 것은, A, B입니다" 틀에 넣어 자연스러운 짧은 항목만 읽는다. 긴 문장은 화면 카드로만
    conf = [strip_src(x) for x in ev["confirmed"][:3] if 0 < len(strip_src(x)) <= 30]
    unc = [_unconfirmed(x) for x in ev["unconfirmed"][:2] if 0 < len(_unconfirmed(x)) <= 24]
    if conf:
        add("확인된 것은, %s입니다." % ", ".join(conf), "check")
    if unc:
        add("아직 확인되지 않은 것은, %s입니다." % ", ".join(unc), "check")
    add(OUTRO, "outro")
    return trim(segs)


def refresh(segs, srcs):
    """예전에 저장된 원고 손질: 확인 소개 문장을 빼고, 매체 문장을 다듬은 이름으로 다시 쓴다."""
    out = []
    for s in segs:
        if s["text"] == CHECK_INTRO:
            continue
        if re.search(r"등 \d+개 매체가 이 소식을 전했습니다\.$", s["text"]) and srcs:
            s = dict(s, text="%s 등 %d개 매체가 이 소식을 전했습니다." % (outlets(srcs), len(srcs)))
        out.append(s)
    return out


def from_cards(spec):
    """나레이션이 저장되지 않은 예전 spec 용. 카드 문구로 원고를 만든다."""
    cards = {c["type"]: c for c in spec["cards"]}
    cover = cards.get("cover", {})
    ev = {
        "sub_ko": cover.get("sub", ""),
        "points": (cards.get("points") or {}).get("items", []),
        "confirmed": (cards.get("check") or {}).get("confirmed", []),
        "unconfirmed": (cards.get("check") or {}).get("unconfirmed", []),
    }
    srcs = [s for s in (cards.get("outro") or {}).get("sources", []) if not s.endswith("기준")]
    return for_news(ev, srcs, cover.get("badge", "breaking"), "map" in cards)
