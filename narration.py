# -*- coding: utf-8 -*-
"""
숏폼 나레이션 원고.

- 새 소식: AI 가 '말하듯' 쓴 원고(narration_ko)를 읽는다. 카드·본문과 똑같이 헤드라인 팩트체크(verify)와
  숫자 검증(numbers_ok)을 통과한 문장만 들어온다
- 예전 spec·원고가 없을 때: 카드 문장으로 만든다
- 어느 쪽이든 읽기 전에 다듬는다: 말투는 "~습니다"로 통일(한다체 "~했다"가 섞이면 반말처럼 들린다),
  문장마다 붙은 "~라고 BBC 등이 보도했습니다" 는 떼고(출처는 화면에 있다), 이미 말한 사실은 다시 읽지 않는다

원고 = [{"text": 읽을 문장, "card": 보여줄 화면(cover·map·points·check·outro)}, ...]
"""
import re

INTRO = {  # 예전 원고 손질용 — 새 원고는 인사말 없이 핵심부터 시작한다
    "breaking": "지금 세계, 해외 속보입니다.",
    "brief": "지금 세계, 해외 뉴스 정리입니다.",
    "update": "지금 세계, 해외 속보 업데이트입니다.",
}
CHECK_INTRO = "지금까지 확인된 것과 아직 확인되지 않은 것을 나눠 보겠습니다."
OLD_OUTRO = "세계의 사건 사고, 확인된 것만 가장 빠르게. 지금 세계였습니다."
# 끝은 짧게 — 쇼츠는 마무리가 길면 이탈하고, 짧으면 다시 보기(반복 재생)로 이어진다
OUTRO = "확인된 것만 빠르게, 지금 세계였습니다."

TARGET_MAX_SEC = 38   # 이보다 길면 핵심 정리부터 줄인다 (목표 30초 안팎)

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


_JUNK = re.compile(r"(news from|^latest|^breaking|live updates|msn|google|^videos?$)", re.I)
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


# ── 한국어 손질 ──────────────────────────────────
def _batchim(ch):
    """한글 음절의 받침 번호 (0 = 없음, 4 = ㄴ, 17 = ㅂ, 18 = ㅄ, 20 = ㅆ). 한글이 아니면 -1."""
    return (ord(ch) - 0xAC00) % 28 if ch and "가" <= ch <= "힣" else -1


def _set_batchim(ch, b):
    code = ord(ch) - 0xAC00
    return chr(0xAC00 + code - code % 28 + b)


# '다'로 끝나는 이름 — 동사 끝맺음으로 보고 바꾸면 안 된다
_DA_NOUNS = ("바다", "캐나다", "우간다", "르완다", "그레나다", "플로리다", "네바다", "앞바다")


def polite(s):
    """한다체 끝맺음을 합쇼체로: "접근 중이다." → "접근 중입니다.", "보도됐다." → "보도됐습니다.",
    "위협이 있다." → "위협이 있습니다.", "피해가 늘어난다." → "피해가 늘어납니다".
    카드용 글("~했다")을 그대로 읽으면 "속보입니다" 뒤에서 반말처럼 들려 말투가 섞인다."""
    t = (s or "").strip().rstrip(".。 ")
    if not t.endswith("다") or t.endswith("니다") or t.endswith(_DA_NOUNS):
        return s
    if t.endswith("는다") and len(t) > 2:           # 먹는다 → 먹습니다
        return t[:-2] + "습니다."
    x = t[-2:-1]
    b = _batchim(x)
    if b < 0:
        return s
    if b in (0, 4):                                  # 이다 → 입니다, 한다 → 합니다, 된다 → 됩니다
        return t[:-2] + _set_batchim(x, 17) + "니다."
    return t[:-1] + "습니다."                         # 했다 → 했습니다, 있다 → 있습니다


# 매체가 전한 말만 뗀다. "~측은 ~했다고 주장했습니다" 처럼 당사자의 주장은 절대 건드리지 않는다
# (떼면 주장이 사실처럼 들린다)
_MEDIA = r"(외신들?|해외 매체들?|언론들?|\S+ ?통신|\S+ 등|%s)" % "|".join(
    re.escape(n) for n in sorted(set(OUTLET_KO.values()), key=len, reverse=True))
_TAIL = re.compile(r"(다고|이라고|라고)\s+%s(이|가|은|는|에서)?\s+(보도|전)했습니다\.?$" % _MEDIA)


def unattribute(s):
    """"~숨졌다고 폭스뉴스 등이 전했습니다." → "~숨졌습니다."
    말로 들을 때 문장마다 매체 이름이 붙으면 어색하다. 출처는 화면(카드 괄호·보도 매체 칩)에 남아 있다.
    확실히 바꿀 수 있는 꼴(과거형 ㅆ·없다, 명사+이라고)만 바꾸고 나머지는 그대로 둔다."""
    m = _TAIL.search(s)
    if not m:
        return s
    head = s[:m.start()]
    if m.group(1) == "다고":
        return head + "습니다." if _batchim(head[-1:]) in (18, 20) else s
    return head + "입니다." if head else s


def josa(word, a, b):
    """받침 있으면 a, 없으면 b ("원인" + 은/는)."""
    return a if _batchim(word.strip()[-1:]) > 0 else b


def spoken(s):
    """본문·원고 한 줄을 읽을 문장으로: [속보] 표시·국기·괄호 출처를 떼고, 말투를 맞추고, 마침표로 끝낸다."""
    s = re.sub(r"^\s*\[(속보|정리|업데이트|마감)\]\s*", "", s or "")
    s = re.sub(r"^[·•\-\s]+", "", s.replace("🇰🇷", ""))
    return polite(sentence(strip_src(s)))


def _unconfirmed(x):
    # "범행 동기 미확인" → "범행 동기", "상륙 시점은" → "상륙 시점" (문장 틀과 겹치지 않게)
    x = re.sub(r"\s*(아직\s*)?(미확인|미상|불명|확인 안 됨|확인되지 않음|확인되지 않았습니다|조사 중)\.?\s*$",
               "", strip_src(x)).strip()
    return re.sub(r"(은|는)$", "", x).strip()


def est_seconds(segs):
    """대략의 길이. 한국어 여성 아나운서(SunHi, +8%) 기준 초당 약 7음절 + 문장 사이 쉼."""
    total = 0.0
    for s in segs:
        hangul = len(re.findall(r"[가-힣]", s["text"]))
        other = len(re.findall(r"[A-Za-z0-9]", s["text"]))
        total += hangul / 7.0 + other / 9 + 0.45
    return total


def trim(segs):
    """너무 길면 핵심 정리 문장부터 뒤에서 하나씩 뺀다 (최소 1개는 남긴다)."""
    while est_seconds(segs) > TARGET_MAX_SEC:
        pts = [i for i, s in enumerate(segs) if s["card"] == "points"]
        if len(pts) <= 1:
            break
        segs.pop(pts[-1])
    return segs


def _same(a, b):
    """같은 사실을 되풀이하는 문장인지. 조사가 달라도 잡도록 낱말 앞 두 글자로 비교한다
    ("태평양 연안으로 허리케인 폴로가 접근 중" ≈ "태평양 연안과 ~ 지역으로 허리케인 폴로가 접근하고")."""
    na = re.sub(r"[^가-힣A-Za-z0-9]", "", a)
    nb = re.sub(r"[^가-힣A-Za-z0-9]", "", b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ta = {w[:2] for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", a)}
    tb = {w[:2] for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", b)}
    return min(len(ta), len(tb)) >= 4 and len(ta & tb) / min(len(ta), len(tb)) >= 0.6


def _add(segs, seg):
    """같은 사실을 되풀이하는 문장은 넣지 않는다. 둘 중엔 제목형 조각("~공동 요격.")보다
    끝까지 말하는 문장("~공동 요격했습니다.")을 남긴다 (자리는 먼저 나온 쪽)."""
    if not seg.get("text"):
        return
    for o in segs:
        if _same(seg["text"], o["text"]):
            if not o["text"].endswith("다.") and seg["text"].endswith("다."):
                o["text"] = seg["text"]
            return
    segs.append(seg)


def _from_cards(ev, has_map):
    """원고가 없을 때: 첫 줄(훅) → 새 사실(핵심 정리) → 아직 모르는 것 하나."""
    segs = []

    def add(text, card):
        _add(segs, {"text": text, "card": card})

    add(spoken((ev.get("caption_ko") or "").split("\n")[0]) or spoken(ev.get("sub_ko")), "cover")
    for p in ev.get("points", [])[:3]:
        d = p.get("d") if isinstance(p, dict) else p
        add(unattribute(spoken(d)), "points")
    unc = [u for u in (_unconfirmed(x) for x in ev.get("unconfirmed", [])[:2]) if 0 < len(u) <= 24]
    if unc:
        add("%s%s 아직 확인되지 않았습니다." % (unc[0], josa(unc[0], "은", "는")), "check")
    return segs


def for_news(ev, srcs=(), badge="breaking", has_map=False):
    segs = []
    for x in ev.get("narration_ko") or []:
        card = x.get("card") or "cover"
        if card == "map" and not has_map:
            card = "cover"
        _add(segs, {"text": spoken(x.get("text")), "card": card})
    if len(segs) < 2:   # 원고가 없거나 팩트체크에서 거의 다 지워졌으면 카드 문장으로
        segs = _from_cards(ev, has_map)
    # 끝인사(OUTRO)는 읽지 않는다 — 2026-10-03 쇼츠 20편 확인: 마지막 3~4초 채널 홍보 카드에서 이탈.
    # '아직 모르는 것'으로 끝나야 다시 보기(반복 재생)로 이어진다. 채널 이름은 화면 상단에 늘 떠 있다
    return trim(segs)


def refresh(segs, srcs=()):
    """예전에 저장된 원고 손질: 인사말·확인 소개·매체 나열 문장과 이미 말한 사실을 다시 읽는 줄을 빼고,
    말투를 "~습니다"로 맞추고, 매체 꼬리를 떼고, 끝인사를 줄인다."""
    out = []
    for s in segs:
        t = s["text"]
        if t in INTRO.values() or t == CHECK_INTRO:
            continue
        if re.search(r"등 \d+개 매체가 이 소식을 전했습니다\.$", t) or t.startswith("확인된 것은,"):
            continue
        if t in (OLD_OUTRO, OUTRO):
            continue
        m = re.match(r"아직 확인되지 않은 것은, (.+)입니다\.$", t)
        if m:   # 예전 틀 "아직 확인되지 않은 것은, 상륙 시점은입니다." 같은 깨진 문장 고치기
            u = _unconfirmed(m.group(1))
            t = "%s%s 아직 확인되지 않았습니다." % (u, josa(u, "은", "는"))
        _add(out, dict(s, text=unattribute(polite(t))))
    return out


def for_video(spec):
    """make_video 가 읽을 최종 원고. 예전 spec 도 새 규칙으로 다듬는다."""
    segs = refresh(spec.get("narration") or from_cards(spec))
    if segs and segs[0]["card"] == "cover" and not segs[0]["text"].endswith("다."):
        # 예전 원고의 첫 문장이 제목형("~서 총격 발생해 11명 부상.")이면 본문 첫 문장으로 바꾼다
        cap = re.sub(r"^\s*\[[^\]]+\]\s*", "", (spec.get("caption") or {}).get("threads", ""))
        m = re.match(r"(.+?다\.)", cap.strip(), re.S)
        if m:
            rest = segs[1:]
            segs = [dict(segs[0], text=spoken(m.group(1).split("\n")[-1]))]
            for s in rest:
                _add(segs, s)
    return segs


def from_cards(spec):
    """나레이션이 저장되지 않은 예전 spec 용. 카드 문구로 원고를 만든다."""
    cards = {c["type"]: c for c in spec["cards"]}
    cover = cards.get("cover", {})
    ev = {
        "sub_ko": cover.get("sub", ""),
        "caption_ko": (spec.get("caption") or {}).get("threads", ""),
        "points": (cards.get("points") or {}).get("items", []),
        "confirmed": (cards.get("check") or {}).get("confirmed", []),
        "unconfirmed": (cards.get("check") or {}).get("unconfirmed", []),
    }
    return for_news(ev, [], cover.get("badge", "breaking"), "map" in cards)
