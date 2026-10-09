# -*- coding: utf-8 -*-
"""
세로 숏폼(1080×1920) 모션 그래픽 렌더러.

카드 사진을 확대하던 예전 방식 대신, 장면을 HTML 로 그리고 시간 t 마다 render(t) 로 위치·투명도를
정한 뒤 헤드리스 크롬으로 한 프레임씩 찍어 ffmpeg 로 묶는다. 애니메이션이 전부 t 의 함수라
몇 번을 돌려도 같은 영상이 나온다.

- 장면: 표지(자료 사진·화면 위에 궁금증 제목) → 지구본(서울에서 사건 장소로 날아간다) → 핵심(숫자·발표·사실
  세 가지 모양을 번갈아) → 확인된 것/아직 모르는 것 → 한국 관련 → 시청자 질문
- 나레이션 문장이 시작하는 순간에 그 문장의 화면 요소가 들어온다 (말과 화면이 맞는다)
- 자막도 HTML 로 그린다: 한 번에 한 구절, 지금 읽는 단어 강조, 숫자는 빨강 + 화면이 살짝 다가간다(펀치)
- 화면 배치는 쇼츠 안전 구역 기준: 위 150px·아래 460px·오른쪽 버튼 줄은 비운다
- 2026-10-09: 첫 화면이 제목 글자 카드뿐이라 1초 만에 다 읽히고 넘겨졌다(쇼츠 70편 2천 벽) → 표지에 자료 사진·
  지구본을 깔고 제목은 결론을 감춘 hook 으로, '핵심 정리 01·02·03' 반복은 장면 모양을 바꿔 가며

필요한 것: playwright(파이썬), 크롬, ffmpeg
"""
import html as _html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import make_cards
import narration

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
W, H, FPS = 1080, 1920, 30
ENTER = 0.45          # 장면이 들어오는 시간
TAIL = 0.6            # 마지막 나레이션 뒤 여유
LEAD = 0.28           # 화면이 목소리보다 먼저 바뀐다 (J컷). 목소리가 먼저 나오고 화면이 따라오면 늦게 느껴진다
PUSH = 0.03           # 장면마다 아주 천천히 다가간다 — 긴 문장에서도 화면이 멈춰 보이지 않게

# 소리. 목소리만 있고 문장 사이가 완전히 비면 뚝뚝 끊겨 들린다 → 잔잔한 배경음 + 장면 전환 효과음.
# 음원 파일 없이 ffmpeg 수식으로 만든다 (저작권 걱정 없음, 공개 저장소에 음원을 둘 필요도 없음)
# 배경음: 열린 5도(라·미) 패드 + 100BPM 낮은 맥박 + 엇박 초침
BED = ("0.030*sin(2*PI*110*t)*(0.7+0.3*sin(2*PI*0.20*t))"
       "+0.022*sin(2*PI*164.81*t)*(0.7+0.3*sin(2*PI*0.17*t+1.3))"
       "+0.018*sin(2*PI*220*t)*(0.7+0.3*sin(2*PI*0.23*t+2.1))"
       "+0.010*sin(2*PI*329.63*t)*(0.7+0.3*sin(2*PI*0.13*t+0.7))"
       "+0.16*sin(2*PI*52*t)*(1-exp(-300*mod(t,0.6)))*exp(-14*mod(t,0.6))"
       "+0.018*(2*random(1)-1)*exp(-150*mod(t+0.3,0.6))")
BOOM = "0.55*sin(2*PI*46*t)*exp(-4.5*t)*(1-exp(-400*t))"   # 첫 화면의 낮은 울림
# 크기는 목소리(edge-tts 약 -17 LUFS) 기준: 배경음은 목소리보다 약 13dB 아래(말할 땐 더 내려간다),
# 효과음은 들릴 듯 말 듯. 2026-09-28 측정: 배경음 -36 LUFS(-4dB 일 때)는 거의 안 들려서 올렸다
BED_DB, WHOOSH_DB, BOOM_DB = 1.0, -8.0, -9.0
# 숫자를 말하는 순간의 낮은 '톡' — 화면 펀치와 같이 들어간다
TOCK = "0.6*sin(2*PI*(95+260*exp(-30*t))*t)*exp(-22*t)*(1-exp(-600*t))"
TOCK_DB = -13.0
SEOUL = (37.5665, 126.978)


def esc(s):
    t = _html.escape(str(s or "")).replace("\n", "<br>")
    return re.sub(r"\[\[(.+?)\]\]", r"<em>\1</em>", t)


def plain(s):
    return re.sub(r"\[\[|\]\]", "", str(s or "")).replace("\n", " ").strip()


# ────────────────────────────────────────────── 장면 나누기
_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(만|억|조)?\s*(명|척|%|퍼센트|달러|원|유로|개|곳|건|채|대|km|킬로미터|"
                  r"미터|도|배|년|시간|일|분|차례|회|발|기|톤|가구|마리|편|국|개국)?")
# "미국 재무부는 ~라고 밝혔습니다" — 말한 주체가 문장 맨 앞(세 낱말 이내)에 있을 때만 '발표' 모양으로
_SAID = re.compile(r"^((?:\S+\s){0,2}?\S+?)(?:\s측)?(?:은|는)\s.+?(?:라고|이라고|다고|며)\s"
                   r"(밝혔|말했|발표했|주장했|경고했|강조했|설명했|전했|요구했|촉구했)")
_SAID_LABEL = {"주장했": "주장", "경고했": "경고", "요구했": "요구", "촉구했": "촉구", "전했": "보도"}


def _stat(text):
    """t 안의 첫 숫자+단위 → {"to": 17, "dec": 0, "unit": "척"}. 연도(2026년)·작은 수(1~3)는 숫자 장면으로 안 쓴다."""
    for m in _NUM.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        unit = (m.group(2) or "") + (m.group(3) or "")
        if not unit or (m.group(3) == "년" and v >= 1900) or v <= 3:
            continue
        return {"to": v, "dec": len(raw.split(".")[1]) if "." in raw else 0, "unit": unit}
    return None


def _said(text):
    m = _SAID.match(narration.strip_src(text or ""))
    if not m or len(m.group(1)) > 16:
        return None
    return {"who": m.group(1).strip(), "label": _SAID_LABEL.get(m.group(2), "발표")}


def _km(a, b):
    import math
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _globe(cards):
    """지도 좌표 → 지구본 설정. 서울에서 날아가 사건 장소로 다가간다 (가까우면 날지 않고 다가가기만)."""
    m = (cards.get("map") or {}).get("map") or (cards.get("cover") or {}).get("map")
    if not m:
        return None
    lat, lon = m["lat"], m["lon"]
    km = _km(SEOUL, (lat, lon))
    city = ((cards.get("cover") or {}).get("map") or {}).get("zoom", 1.0) >= 2
    return {"lat": lat, "lon": lon, "from": [SEOUL[1], SEOUL[0]], "near": km < 600,
            "zoom": 5.0 if city else 3.0, "km": int(round(km, -1)), "fly": True}


def scenes_for(spec, segs, times):
    """segs[i] 를 times[i]=(시작, 끝) 에 읽는다. 같은 화면에 머무는 문장끼리 한 장면으로 묶고,
    장면 안에서 문장이 바뀌는 순간(steps)에 요소를 하나씩 들여보낸다."""
    cards = {c["type"]: c for c in spec["cards"]}
    cover = cards.get("cover", {})
    globe = _globe(cards)
    out = []

    def scene(kind, i, **data):
        if out and out[-1]["kind"] == kind and kind in ("cover", "check", "korea", "ask"):
            out[-1]["steps"].append(times[i][0])
            out[-1]["tags"].append(segs[i]["text"])
            out[-1]["screens"].append(segs[i].get("screen", ""))
            out[-1]["end"] = times[i][1]
            return
        out.append({"kind": kind, "start": times[i][0], "end": times[i][1], "steps": [times[i][0]],
                    "tags": [segs[i]["text"]], "screens": [segs[i].get("screen", "")], **data})

    pts = (cards.get("points") or {}).get("items", [])
    k = 0
    last_layout = None
    for i, s in enumerate(segs):
        card = s["card"]
        if card == "map" and globe:
            scene("map", i, globe=dict(globe))
        elif card in ("points", "fact"):
            if card == "fact" and cards.get("fact"):   # 지진 속보의 여진 카드 (값·단위가 따로 있다)
                f = cards["fact"]
                it = {"t": f.get("title", "").replace("\n", " "), "d": ""}
                st = _stat("%s%s" % (f.get("value", ""), f.get("unit", "")))
            else:
                it = pts[k] if k < len(pts) else {"t": "", "d": s["text"]}
                it = it if isinstance(it, dict) else {"t": it}
                st = _stat(it.get("t", ""))
            # 화면 글도 목소리와 같은 "~습니다" 체로 (예전 카드의 "~보도됐다." 가 반말처럼 보인다)
            d = narration.polite(it.get("d", ""))
            said = None if st else _said(d) or _said(s["text"])
            layout = "stat" if st else "said" if said else "fact"
            if layout == last_layout == "said":   # 같은 모양 두 번 연속이면 하나는 사실 카드로
                layout, said = "fact", None
            last_layout = layout
            scene("point", i, idx=k + 1, total=min(3, len(pts)) or 1, t=it.get("t", ""), d=d,
                  layout=layout, stat=st, said=said)
            k += 1
        elif card == "check":
            scene("check", i)
        elif card in ("korea", "ask"):
            scene(card, i)
        elif card == "outro":
            scene("outro", i)
        else:
            scene("cover", i)
    if globe and out and out[0]["kind"] == "cover":
        # 표지에 자료 사진이 없으면 지구본을 깐다. 뒤에 지구본 장면이 따로 있으면 표지에선 날지 않고 그 자리에서 돈다
        out[0]["globe"] = dict(globe, fly=not any(s["kind"] == "map" for s in out), zoom=min(globe["zoom"], 2.2))
    # J컷: 둘째 장면부터 목소리보다 LEAD 초 먼저 들어온다 (장면 안 글자는 여전히 목소리에 맞춰 나온다)
    for s in out[1:]:
        s["start"] = max(0.0, s["start"] - LEAD)
    # 장면 사이 빈틈 없이: 다음 장면 시작까지 늘린다
    for a, b in zip(out, out[1:]):
        a["end"] = b["start"]
    return out, cover, cards


# ────────────────────────────────────────────── 자막 구절
def phrases(seg_words, times, max_chars=12):
    """문장마다 단어를 짧은 구절로 묶는다. 구절은 문장 경계를 넘지 않는다."""
    out = []
    for words, (s0, s1) in zip(seg_words, times):
        cur = []
        for w in words:
            txt = w.text.strip()
            if not txt:
                continue
            n = sum(len(x.text) for x in cur) + len(txt)
            if cur and (n > max_chars or len(cur) >= 4):
                out.append(cur)
                cur = []
            cur.append(w)
        if cur:
            # 문장 끝에 한 단어("전했습니다", "세계였습니다")만 따로 뜨면 뜻 없는 조각이 된다 → 앞 구절에 붙인다
            if out and len(cur) == 1 and out[-1][0] in words and sum(len(x.text) for x in out[-1] + cur) <= max_chars + 8:
                out[-1].extend(cur)
            else:
                out.append(cur)
    res = []
    for i, ph in enumerate(out):
        nxt = out[i + 1][0].start if i + 1 < len(out) else ph[-1].end + 0.4
        end = min(nxt, ph[-1].end + 0.6)
        res.append({"s": round(ph[0].start, 3), "e": round(end, 3),
                    "w": [[x.text, round(x.start, 3), round(x.end, 3)] for x in ph]})
    return res


# ────────────────────────────────────────────── HTML
CSS = r"""
@font-face{font-family:'Pretendard';font-weight:100 900;src:url('FONT') format('woff2')}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1920px;overflow:hidden;background:#07090E}
body{font-family:'Pretendard',sans-serif;color:#F4F6FA;-webkit-font-smoothing:antialiased;
  word-break:keep-all;position:relative}
em{font-style:normal;color:var(--acc)}
:root{--acc:#FF3B30;--acc2:#FFB020;--ok:#30D158;--mut:rgba(244,246,250,.62);
  --line:rgba(244,246,250,.12);--panel:rgba(22,28,42,.82)}
#bg{position:absolute;inset:-200px;background:
  radial-gradient(600px 520px at 50% 30%,rgba(255,59,48,.22),transparent 70%),
  radial-gradient(700px 700px at 80% 90%,rgba(60,90,200,.16),transparent 70%),
  linear-gradient(180deg,#0C1019 0%,#07090E 100%)}
#grid{position:absolute;inset:0;opacity:.35;background-image:
  linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
  background-size:90px 90px;-webkit-mask-image:linear-gradient(180deg,transparent 5%,#000 30%,#000 60%,transparent 85%)}
/* 위: 진행 막대 + 로고 */
#bars{position:absolute;left:60px;right:60px;top:158px;display:flex;gap:10px}
#bars div{flex:1;height:7px;border-radius:4px;background:rgba(255,255,255,.18);overflow:hidden}
#bars i{display:block;height:100%;width:0;background:#fff;border-radius:4px}
#hd{position:absolute;left:60px;right:60px;top:196px;display:flex;align-items:center;
  justify-content:space-between}
#logo{display:flex;align-items:center;gap:16px;font-size:38px;font-weight:850;letter-spacing:-.5px}
#logo i{width:20px;height:20px;border-radius:50%;background:var(--acc)}
#clock{font-size:30px;font-weight:650;color:var(--mut);font-variant-numeric:tabular-nums}
/* 장면 */
.sc{position:absolute;left:0;top:0;width:1080px;height:1920px;opacity:0;transform-origin:50% 42%}
.pad{position:absolute;left:72px;right:110px;top:300px;height:960px;display:flex;flex-direction:column;justify-content:center}
.pad.fixed{height:auto;display:block}
.pad > .in{transform-origin:0 50%}
.badge{display:inline-flex;align-items:center;gap:14px;background:var(--acc);color:#fff;
  font-weight:900;font-size:46px;padding:14px 30px 16px;border-radius:14px;letter-spacing:1px}
.badge b{width:16px;height:16px;border-radius:50%;background:#fff}
h1{font-size:118px;font-weight:900;line-height:1.1;letter-spacing:-4px;margin-top:44px}
h1.m{font-size:100px;letter-spacing:-3.2px}
h1.s{font-size:84px;letter-spacing:-2.6px;line-height:1.16}
h1 .ln{display:block}
.sub{margin-top:40px;font-size:50px;line-height:1.4;font-weight:600;color:rgba(244,246,250,.86)}
.bar{height:8px;width:180px;background:var(--acc);border-radius:4px;margin-top:44px}
.out{margin-top:56px;display:flex;flex-wrap:wrap;gap:16px;align-items:center}
.out .k{width:100%;font-size:32px;font-weight:700;color:var(--mut);margin-bottom:4px}
.chip{background:var(--panel);border:2px solid var(--line);border-radius:16px;padding:16px 26px;
  font-size:40px;font-weight:800}
.chip.n{background:var(--acc);border-color:var(--acc)}
.chip span{color:var(--mut);font-weight:700;margin-right:14px}
.out .note{width:100%;font-size:32px;font-weight:700;color:var(--mut);margin-top:6px}
.eye{font-size:40px;font-weight:800;color:var(--acc);letter-spacing:1px}
/* 자료 사진·화면: 화면 전체에 깔고 위아래를 어둡게 (글자·자막이 읽히게) */
.media{position:absolute;inset:0;overflow:hidden}
.media img{position:absolute;left:0;top:0;width:1080px;height:1920px;object-fit:cover;transform-origin:50% 45%}
.shade{position:absolute;inset:0;background:linear-gradient(180deg,rgba(7,9,14,.82) 0%,rgba(7,9,14,.25) 22%,
  rgba(7,9,14,.18) 40%,rgba(7,9,14,.78) 58%,rgba(7,9,14,.94) 72%,rgba(7,9,14,.96) 100%)}
.shade.full{background:rgba(7,9,14,.70)}
.credit{position:absolute;left:60px;right:110px;top:262px;font-size:24px;font-weight:600;color:rgba(244,246,250,.62);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* 지구본 */
.globe{position:absolute;left:0;right:0;top:0;height:1920px}
.globe svg{position:absolute;left:0;top:0}
.globe .lbl{font:800 34px 'Pretendard';fill:#fff;paint-order:stroke;stroke:rgba(7,9,14,.9);stroke-width:8px}
.dist{position:absolute;left:72px;top:1130px;background:rgba(7,9,14,.86);border:2px solid rgba(255,176,32,.6);
  border-radius:18px;padding:16px 28px;font-size:44px;font-weight:850}
.dist b{color:var(--acc2)}
/* 표지 */
.top{position:absolute;left:72px;right:110px;top:310px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.where{display:inline-flex;align-items:center;gap:12px;background:rgba(7,9,14,.72);border:2px solid var(--line);
  border-radius:14px;padding:12px 24px 14px;font-size:40px;font-weight:800}
.where i{width:18px;height:18px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:var(--acc)}
.hero{position:absolute;left:72px;right:110px;top:300px;height:950px;display:flex;flex-direction:column;justify-content:flex-end}
.hero h1{margin-top:0;text-shadow:0 6px 30px rgba(0,0,0,.6)}
.hero .out{margin-top:36px}
/* 숫자 장면 */
.big{display:flex;align-items:baseline;gap:14px;margin-top:26px;color:var(--acc)}
.big .cnt{font-size:230px;font-weight:900;letter-spacing:-9px;line-height:1;font-variant-numeric:tabular-nums}
.big small{font-size:96px;font-weight:900;letter-spacing:-3px}
/* 발표 장면 */
.who{display:inline-flex;align-items:center;gap:16px;font-size:46px;font-weight:850;margin-top:24px}
.who i{width:58px;height:58px;border-radius:50%;background:var(--acc2);position:relative}
.who i::after{content:'';position:absolute;left:21px;top:12px;width:16px;height:26px;border-radius:9px;background:#07090E}
.said{margin-top:34px;border-left:12px solid var(--acc2);padding:8px 0 8px 40px;font-size:84px;font-weight:900;
  line-height:1.16;letter-spacing:-2.6px}
/* 한국 관련 */
.kr{width:120px;height:120px;border-radius:50%;background:linear-gradient(180deg,#E8343F 0 50%,#1B4FA0 50% 100%);
  border:6px solid #fff;box-shadow:0 0 0 6px rgba(255,255,255,.12)}
/* 시청자 질문 */
.qmark{width:170px;height:150px;border-radius:40px;background:var(--acc);position:relative;display:flex;
  align-items:center;justify-content:center;font-size:110px;font-weight:900}
.qmark::after{content:'';position:absolute;left:34px;bottom:-34px;border:20px solid transparent;
  border-top:24px solid var(--acc);border-left:24px solid var(--acc)}
.q{font-size:96px;font-weight:900;line-height:1.14;letter-spacing:-3px;margin-top:64px}
.pill{display:inline-block;margin-top:52px;background:#fff;color:#07090E;border-radius:999px;padding:20px 40px;
  font-size:44px;font-weight:850}
.num{font-size:250px;font-weight:900;line-height:.9;letter-spacing:-10px;color:transparent;
  -webkit-text-stroke:4px var(--acc)}
.of{font-size:40px;font-weight:800;color:var(--mut);margin-top:10px}
.pt{font-size:88px;font-weight:900;line-height:1.14;letter-spacing:-3px;margin-top:40px}
.pd{font-size:52px;font-weight:600;line-height:1.42;color:rgba(244,246,250,.86);margin-top:40px}
.box{border-radius:30px;padding:40px 42px;background:var(--panel);border:2px solid var(--line);margin-top:34px}
.box h3{font-size:40px;font-weight:900;margin-bottom:14px;display:flex;align-items:center;gap:14px}
.box.ok{border-color:rgba(48,209,88,.45)} .box.ok h3{color:var(--ok)}
.box.no{border-color:rgba(255,176,32,.45)} .box.no h3{color:var(--acc2)}
.box li{list-style:none;font-size:46px;line-height:1.38;font-weight:700;margin-top:16px;padding-left:34px;position:relative}
.box li::before{content:'';position:absolute;left:2px;top:24px;width:14px;height:14px;border-radius:50%;background:currentColor;opacity:.45}
.box.no li{color:rgba(244,246,250,.9)}
.cta{position:absolute;left:72px;right:110px;top:820px;background:var(--acc);border-radius:34px;
  padding:48px 50px;font-size:64px;font-weight:900;line-height:1.25;letter-spacing:-1.5px}
.cta small{display:block;font-size:40px;font-weight:700;opacity:.9;margin-top:14px;letter-spacing:0}
.srcs{position:absolute;left:72px;right:110px;top:1140px;font-size:34px;line-height:1.5;color:var(--mut);font-weight:600}
.big-logo{font-size:120px;font-weight:900;letter-spacing:-4px;display:flex;align-items:center;gap:28px}
.big-logo i{width:56px;height:56px;border-radius:50%;background:var(--acc)}
/* 자막 */
#cap{position:absolute;left:60px;right:60px;top:1296px;height:190px;display:flex;align-items:flex-start;justify-content:center}
#cap div{background:rgba(7,9,14,.82);border:2px solid rgba(255,255,255,.10);border-radius:26px;
  padding:18px 34px 22px;font-size:68px;font-weight:880;letter-spacing:-1.4px;text-align:center;line-height:1.22;
  max-width:960px}
#cap span{color:#fff;display:inline-block}
#cap span.on{color:var(--acc2)}
#cap span.done{color:rgba(255,255,255,.92)}
#cap span.n{color:#FF5A4F}
"""

JS = r"""
const C = x => x < 0 ? 0 : x > 1 ? 1 : x;
const eo = x => 1 - Math.pow(1 - C(x), 3);
const eb = x => { x = C(x); const a = 1.9, b = a + 1; return 1 + b * Math.pow(x - 1, 3) + a * Math.pow(x - 1, 2); };
const D = window.DATA;
const scenes = [...document.querySelectorAll('.sc')];
const fxEls = [...document.querySelectorAll('[data-in]')];
const bars = [...document.querySelectorAll('#bars i')];
const cap = document.querySelector('#cap');
let lastCap = -2;

function fx(el, t) {
  const t0 = +el.dataset.in, kind = el.dataset.fx || 'up', dur = +(el.dataset.dur || 0.5);
  const p = (t - t0) / dur;
  if (kind === 'up') { el.style.opacity = eo(p); el.style.transform = `translateY(${(1 - eo(p)) * 70}px)`; }
  else if (kind === 'pop') { el.style.opacity = C(p * 3); el.style.transform = `scale(${p <= 0 ? .5 : .5 + .5 * eb(p)})`; }
  else if (kind === 'left') { el.style.opacity = eo(p); el.style.transform = `translateX(${(1 - eo(p)) * -90}px)`; }
  else if (kind === 'wipe') { el.style.clipPath = `inset(0 ${(1 - eo(p)) * 100}% 0 0)`; el.style.opacity = p > 0 ? 1 : 0; }
  else if (kind === 'fade') { el.style.opacity = eo(p); }
}

// 숫자를 말하는 순간 화면이 살짝 다가갔다가 돌아온다
function punch(t) {
  let v = 0;
  for (const p of D.punches) { const k = (t - p) / 0.38; if (k >= 0 && k < 1) v = Math.max(v, Math.pow(1 - k, 2)); }
  return v;
}

function fmt(v, dec) {
  return v.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

window.render = function (t) {
  const waits = [];
  document.querySelector('#bg').style.transform =
    `translate(${Math.sin(t * .25) * 60}px,${Math.cos(t * .2) * 50}px)`;
  document.querySelector('#logo i').style.opacity = .55 + .45 * Math.abs(Math.cos(t * 2.2));
  const pz = 1 + 0.045 * punch(t);
  scenes.forEach((el, i) => {
    const s = D.scenes[i], last = i === scenes.length - 1;
    // 첫 장면은 0초부터 다 보인다 — 피드에서 넘기기 전 첫 프레임이 빈 화면이면 바로 넘어간다
    // (2026-09-29 인스타 릴스 평균 시청 2~5초, 예전 첫 프레임은 제목 없는 검은 화면이었다)
    const pin = i === 0 ? 1 : (t - s.start) / 0.42, pout = (t - s.end) / 0.32;
    let op = eo(pin) * (last ? 1 : 1 - eo(pout));
    if ((i > 0 && t < s.start - 0.01) || (!last && t > s.end + 0.35)) op = 0;
    el.style.opacity = op;
    const prog = C((t - s.start) / Math.max(1, s.end - s.start));
    el.style.transform = `translateX(${(1 - eo(pin)) * 80 + (last ? 0 : eo(pout) * -80)}px) scale(${(1 + D.push * prog) * pz})`;
    el.style.visibility = op > 0.001 ? 'visible' : 'hidden';
    if (op <= 0) return;
    if (s.globe) globeTick(el.querySelector('.globe'), t, s);
    const img = el.querySelector('.media img');
    if (img && s.media) {
      // 사진은 천천히 다가가며 옆으로 흐르고(켄 번스), 영상은 프레임을 한 장씩 바꿔 끼운다
      const sp = C((t - s.start + 0.3) / Math.max(1.5, s.end - s.start + 0.6));
      if (s.media.type === 'clip') {
        const k = Math.floor(Math.max(0, t - s.start + 0.3) * D.fps) % s.media.n + 1;
        if (img.dataset.k != k) {
          img.dataset.k = k; img.src = s.media.base + String(k).padStart(4, '0') + '.jpg';
          waits.push(img.decode().catch(() => {}));
        }
        img.style.transform = `scale(${1.02 + 0.05 * sp})`;
      } else {
        img.style.objectPosition = `${35 + 30 * sp}% 50%`;
        img.style.transform = `scale(${1.06 + 0.10 * sp})`;
      }
    }
    el.querySelectorAll('.cnt').forEach(c => {
      const p = (t - +c.dataset.at) / 0.9;
      c.textContent = fmt(+c.dataset.to * (p >= 1 ? 1 : eo(p)), +c.dataset.dec);
    });
  });
  fxEls.forEach(el => fx(el, t));
  bars.forEach((b, i) => {
    const s = D.scenes[i];
    b.style.width = (C((t - s.start) / Math.max(.1, s.end - s.start)) * 100) + '%';
  });
  // 자막
  let k = -1;
  for (let i = 0; i < D.caps.length; i++) if (t >= D.caps[i].s && t < D.caps[i].e) { k = i; break; }
  if (k !== lastCap) {
    cap.innerHTML = k < 0 ? '' : '<div>' + D.caps[k].w.map(w => `<span${/\d/.test(w[0]) ? ' data-n="1"' : ''}>${w[0]}</span>`).join(' ') + '</div>';
    lastCap = k;
  }
  if (k >= 0) {
    const box = cap.firstChild, p = (t - D.caps[k].s) / 0.18;
    box.style.opacity = eo(p); box.style.transform = `translateY(${(1 - eo(p)) * 24}px) scale(${.94 + .06 * eo(p)})`;
    [...box.children].forEach((sp, j) => {
      const w = D.caps[k].w[j];
      const on = t >= w[1] && t < w[2] + 0.05;
      sp.className = sp.dataset.n ? (t >= w[1] ? 'n' : '') : on ? 'on' : t >= w[2] ? 'done' : '';
      // 숫자 낱말은 읽는 순간 톡 튀어나온다
      const q = (t - w[1]) / 0.3;
      sp.style.transform = sp.dataset.n && q >= 0 && q < 1 ? `scale(${1 + 0.22 * Math.sin(Math.PI * q)})` : '';
    });
  }
  return Promise.all(waits);   // 촬영은 자료 화면 프레임이 다 그려질 때까지 기다린다
};

// 지구본: 서울에서 출발해 사건 장소로 돌아가며(대권 항로가 그려진다) 다가간다
const ss = x => { x = C(x); return x * x * (3 - 2 * x); };
let LAND = null, MESH = null;

function globeInit(box) {
  const s = D.scenes[+box.dataset.scene], G = s.globe, w = 1080, h = 1920;
  const cy = +box.dataset.cy || 760;
  const svg = d3.select(box).append('svg').attr('width', w).attr('height', h);
  const proj = d3.geoOrthographic().translate([w / 2, cy]).clipAngle(90).precision(0.6);
  const path = d3.geoPath(proj);
  const defs = svg.append('defs');
  const gr = defs.append('radialGradient').attr('id', 'oc' + box.dataset.scene).attr('cx', '42%').attr('cy', '38%');
  gr.append('stop').attr('offset', '0%').attr('stop-color', '#1A2C55');
  gr.append('stop').attr('offset', '100%').attr('stop-color', '#0A1124');
  const g = {
    proj, path, cy,
    glow: svg.append('circle').attr('cx', w / 2).attr('cy', cy).attr('fill', 'none')
      .attr('stroke', 'rgba(90,140,255,.18)').attr('stroke-width', 26),
    sphere: svg.append('path').datum({ type: 'Sphere' }).attr('fill', `url(#oc${box.dataset.scene})`),
    grat: svg.append('path').datum(d3.geoGraticule().step([15, 15])()).attr('fill', 'none')
      .attr('stroke', 'rgba(244,246,250,.07)').attr('stroke-width', 1.2),
    land: svg.append('path').datum(LAND).attr('fill', '#2A3A5C'),
    mesh: svg.append('path').datum(MESH).attr('fill', 'none').attr('stroke', '#46587F').attr('stroke-width', 1.3),
    arc: svg.append('path').attr('fill', 'none').attr('stroke', '#FFB020').attr('stroke-width', 6)
      .attr('stroke-linecap', 'round').attr('stroke-dasharray', '2 14'),
    home: svg.append('circle').attr('r', 11).attr('fill', '#fff'),
    homeL: svg.append('text').attr('class', 'lbl').text('서울'),
    rings: [0, 1, 2].map(() => svg.append('circle').attr('fill', 'none').attr('stroke', '#FF3B30').attr('stroke-width', 6)),
    dot: svg.append('circle').attr('r', 17).attr('fill', '#FF3B30').attr('stroke', '#fff').attr('stroke-width', 6),
  };
  box._g = g;
}

function globeTick(box, t, s) {
  if (!box || !box._g) return;
  const g = box._g, G = s.globe, R0 = 400;
  const dur = Math.max(1.4, s.end - s.start);
  const p = (t - s.start) / dur;
  const dst = [G.lon, G.lat];
  let c, f, z;
  if (G.fly && !G.near) {
    f = ss((p - 0.04) / 0.56);            // 서울 → 사건 장소
    z = ss((p - 0.42) / 0.58);            // 다가가기
    c = d3.geoInterpolate(G.from, dst)(f);
  } else {
    f = 1; z = G.fly ? ss(p / 0.9) : 0.35 + 0.35 * ss(p);
    c = [G.lon - (G.fly ? 0 : 8 * (1 - p)), G.lat];   // 날지 않을 땐 천천히 돈다
  }
  const scale = R0 * (1 + (G.zoom - 1) * z);
  g.proj.rotate([-c[0], -c[1]]).scale(scale);
  g.glow.attr('r', scale + 10).attr('opacity', C(1.6 - z * 1.4));
  for (const k of ['sphere', 'grat', 'land', 'mesh']) g[k].attr('d', g.path);
  const vis = q => d3.geoDistance(q, c) < Math.PI / 2 - 0.02;
  if (!G.near) {
    const ln = { type: 'LineString', coordinates: [G.from, d3.geoInterpolate(G.from, dst)(Math.max(0.001, f))] };
    g.arc.attr('d', g.path(ln) || '').attr('opacity', f > 0 ? 1 : 0);
  } else g.arc.attr('d', '');
  // 서울 표시가 위쪽 머리글·제목 글자와 겹치면 숨긴다 (data-top 아래에서만)
  const hp = g.proj(G.from), hv = vis(G.from) && !G.near && hp[1] > (+box.dataset.top || 300);
  g.home.attr('cx', hp[0]).attr('cy', hp[1]).attr('opacity', hv ? 1 : 0);
  g.homeL.attr('x', hp[0] + 20).attr('y', hp[1] - 16).attr('opacity', hv && !box.dataset.nolabel ? 1 : 0);
  const tp = g.proj(dst), tv = vis(dst) && f > 0.9;
  g.dot.attr('cx', tp[0]).attr('cy', tp[1]).attr('opacity', tv ? 1 : 0);
  g.rings.forEach((r, i) => {
    const q = ((t * 0.7) + i / 3) % 1;
    r.attr('cx', tp[0]).attr('cy', tp[1]).attr('r', 18 + q * 150).attr('opacity', tv ? (1 - q) * .85 : 0);
  });
}

// 가운데 정렬 + 넘치면 글자를 줄인다 (자막 자리를 침범하지 않게)
function fit() {
  document.querySelectorAll('.pad:not(.fixed)').forEach(pad => {
    const inn = document.createElement('div'); inn.className = 'in';
    while (pad.firstChild) inn.appendChild(pad.firstChild);
    pad.appendChild(inn);
    let z = 1;
    while (inn.getBoundingClientRect().height > pad.clientHeight && z > 0.55) { z -= 0.03; inn.style.zoom = z; }
  });
}

(async () => {
  await document.fonts.ready;
  if (window.d3 && window.WORLD) {
    LAND = topojson.feature(WORLD, WORLD.objects.countries);
    MESH = topojson.mesh(WORLD, WORLD.objects.countries, (a, b) => a !== b);
    document.querySelectorAll('.globe').forEach(globeInit);
  }
  await Promise.all([...document.querySelectorAll('.media img')].map(i => i.decode().catch(() => {})));
  fit();
  await render(0);
  window.READY = true;
})();
"""


def _title_cls(title):
    n = max(len(plain(l)) for l in str(title).split("\n"))
    return "" if n <= 8 else "m" if n <= 10 else "s"


def _video_lines(title, width=8):
    """영상 표지 제목은 줄을 짧게 끊어 글자를 키운다. 카드용 두 줄 제목(줄마다 14자 안팎)을 그대로 쓰면
    84px 까지 작아져 화면 아래 절반이 비었다. 4줄을 넘으면 원래 줄바꿈을 쓴다. [[강조]]가 줄을 넘으면 줄마다 닫는다."""
    out = []
    for line in str(title).split("\n"):
        cur = ""
        for w in line.split(" "):
            cand = (cur + " " + w).strip()
            if cur and len(plain(cand)) > width:
                out.append(cur)
                cur = w
            else:
                cur = cand
        if cur:
            out.append(cur)
    if len(out) > 4:
        return str(title).split("\n")
    fixed, carry = [], False
    for l in out:
        if carry:
            l = "[[" + l
        opened = l.count("[[") > l.count("]]")
        fixed.append(l + "]]" if opened else l)
        carry = opened
    return fixed


def scene_html(i, s, spec, cover, cards):
    k = s["kind"]
    st = s["steps"]

    def at(j, d=0.0):  # j 번째 문장이 시작할 때 (+d 초)
        return st[min(j, len(st) - 1)] + d

    def vis(d=0.0):    # 장면이 들어오기 시작할 때 (+d 초) — J컷이라 목소리보다 조금 먼저
        return s["start"] + d

    bg = _backdrop(i, s)
    if k == "cover":
        return bg + _cover(s, spec, cover, at)
    if k == "map":
        G = s["globe"]
        where = (cards.get("map") or {}).get("title") or (cover.get("map") or {}).get("label", "")
        dist = ""
        if not G["near"] and G["km"] >= 300:
            # 서울에서 날아가 도착할 즈음 거리가 뜬다 (USGS·지오코딩 좌표로 계산한 값)
            dist = ('<div class="dist" data-in="%.3f" data-fx="pop">서울에서 약 <b>%s km</b></div>'
                    % (vis(0.62 * max(1.4, s["end"] - s["start"])), format(G["km"], ",")))
        return ('<div class="globe" data-scene="%d" data-cy="820" data-top="600"></div>'
                '<div class="pad fixed"><div class="eye" data-in="%.3f" data-fx="left">어디서</div>'
                '<h1 class="s" style="margin-top:20px;text-shadow:0 4px 24px rgba(0,0,0,.7)" data-in="%.3f" data-fx="up">%s</h1></div>%s'
                % (i, vis(0.05), vis(0.18), esc(where), dist))
    if k == "point":
        eye = '<div class="eye" data-in="%.3f" data-fx="left">핵심 %02d</div>' % (vis(0.02), s["idx"])
        if s.get("layout") == "stat":
            sv = s["stat"]
            return bg + ('<div class="pad">%s<div class="big" data-in="%.3f" data-fx="pop">'
                         '<span class="cnt" data-at="%.3f" data-to="%s" data-dec="%d">0</span><small>%s</small></div>'
                         '<div class="pt" style="margin-top:22px" data-in="%.3f" data-fx="up">%s</div>'
                         '<div class="pd" data-in="%.3f" data-fx="up">%s</div></div>'
                         % (eye, vis(0.08), vis(0.12), sv["to"], sv["dec"], esc(sv["unit"]),
                            vis(0.3), esc(s["t"]), at(0, 0.25), esc(s["d"])))
        if s.get("layout") == "said":
            sd = s["said"]
            return bg + ('<div class="pad"><div class="eye" data-in="%.3f" data-fx="left">%s</div>'
                         '<div class="who" data-in="%.3f" data-fx="left"><i></i>%s</div>'
                         '<div class="said" data-in="%.3f" data-fx="up">%s</div>'
                         '<div class="pd" data-in="%.3f" data-fx="up">%s</div></div>'
                         % (vis(0.02), esc(sd["label"]), vis(0.1), esc(sd["who"]), vis(0.22), esc(s["t"]),
                            at(0, 0.3), esc(s["d"])))
        if s.get("media"):
            # 자료 사진·화면 위의 사실 카드 — 큰 번호 대신 사진이 장면을 채운다
            return bg + ('<div class="hero" style="top:360px;height:890px">%s'
                         '<div class="pt" data-in="%.3f" data-fx="up">%s</div>'
                         '<div class="pd" data-in="%.3f" data-fx="up">%s</div></div>'
                         % (eye, vis(0.2), esc(s["t"]), at(0, 0.2), esc(s["d"])))
    if k == "korea":
        txt = next((x for x in s["screens"] if x), "") or s["tags"][0]
        return ('<div class="pad"><div class="kr" data-in="%.3f" data-fx="pop"></div>'
                '<div class="eye" style="margin-top:40px" data-in="%.3f" data-fx="left">한국은?</div>'
                '<div class="pt" data-in="%.3f" data-fx="up">%s</div></div>'
                % (vis(0.02), vis(0.12), vis(0.24), esc(txt)))
    if k == "ask":
        txt = next((x for x in s["screens"] if x), "") or s["tags"][-1]
        return ('<div class="pad"><div class="qmark" data-in="%.3f" data-fx="pop">?</div>'
                '<div class="q" data-in="%.3f" data-fx="up">%s</div>'
                '<div><div class="pill" data-in="%.3f" data-fx="pop">댓글로 의견을 남겨 주세요</div></div></div>'
                % (vis(0.02), vis(0.15), esc(txt), at(len(s["steps"]) - 1, 0.9)))
    return _card(i, s, spec, cover, cards, at, vis)


def _backdrop(i, s):
    """장면 뒤 배경: 자료 사진·화면(+ 출처 표시) 또는 지구본."""
    m = s.get("media")
    if m:
        src = (m["base"] + "0001.jpg") if m["type"] == "clip" else m["src"]
        full = "" if s["kind"] == "cover" else " full"
        return ('<div class="media"><img src="%s"></div><div class="shade%s"></div><div class="credit">%s</div>'
                % (src, full, esc(m.get("credit", ""))))
    if s["kind"] == "cover" and s.get("globe"):
        return '<div class="globe" data-scene="%d" data-cy="560" data-top="300" data-nolabel="1"></div><div class="shade" style="opacity:.55"></div>' % i
    return ""


def _cover(s, spec, cover, at):
    """첫 화면: 배지 + 장소, 아래쪽에 궁금증 제목(hook). 숫자 칩은 첫 문장이 숫자를 말할 즈음 튀어나온다.
    hook 이 없는 예전 spec·지진 속보는 카드 제목을 그대로 쓴다."""
    b = cover.get("badge", "breaking")
    hook = spec.get("hook") or ""
    lines = [l for l in hook.split("\n") if l.strip()] if hook else _video_lines(cover.get("title", spec.get("topic", "")))
    if len(lines) == 1 and len(plain(lines[0])) > 9:
        lines = _video_lines(lines[0], width=9)
    h1 = "".join('<span class="ln" data-in="-1" data-fx="up">%s</span>' % esc(l) for l in lines)
    where = ((cover.get("map") or {}).get("label") or "") if s.get("globe") or s.get("media") else ""
    place = ('<div class="where" data-in="-1" data-fx="fade"><i></i>%s</div>' % esc(where)) if where else ""
    nums = (cover.get("chips") or [])[:2]
    out = ""
    if nums:
        t0 = at(0, 1.0)
        out = '<div class="out">%s</div>' % "".join(
            '<div class="chip" data-in="%.3f" data-fx="pop"><span>%s</span>%s</div>'
            % (t0 + 0.12 * n, esc(x.get("k", "")), esc(x.get("v", ""))) for n, x in enumerate(nums))
    elif not hook and cover.get("sub"):
        out = '<div class="sub" data-in="%.3f" data-fx="up">%s</div>' % (at(0, 0.9), esc(cover.get("sub", "")))
    srcs = narration.clean_outlets(s.get("outlets") or [])
    if srcs and len(srcs) >= 2:
        # 몇 개 매체가 같은 사실을 전했는지는 읽지 않고 화면에만 — 믿을 이유가 된다
        out += ('<div class="out"><div class="note" data-in="%.3f" data-fx="fade">%s 등 %d개 매체 보도</div></div>'
                % (at(0, 1.5), esc("·".join(srcs[:2])), len(s.get("outlets") or [])))
    return ('<div class="top"><div class="badge" data-in="-1" data-fx="pop"><b></b>%s</div>%s</div>'
            '<div class="hero"><h1 class="%s">%s</h1>'
            '<div class="bar" data-in="%.3f" data-fx="wipe"></div>%s</div>'
            % (esc(make_cards.BADGES.get(b, "속보")), place, _title_cls("\n".join(lines)), h1, at(0, 0.5), out))


def _card(i, s, spec, cover, cards, at, vis):
    """자료 없는 장면 — 예전 글자 카드 모양 그대로."""
    k = s["kind"]
    if k == "point":
        return ('<div class="pad"><div class="eye" data-in="%.3f" data-fx="left">핵심 정리</div>'
                '<div class="num" style="margin-top:30px" data-in="%.3f" data-fx="pop">%02d</div>'
                '<div class="of" data-in="%.3f" data-fx="fade">%d / %d</div>'
                '<div class="pt" data-in="%.3f" data-fx="up">%s</div>'
                '<div class="pd" data-in="%.3f" data-fx="up">%s</div></div>'
                % (vis(0.02), vis(0.10), s["idx"], vis(0.2), s["idx"], s["total"],
                   vis(0.25), esc(s["t"]), at(0, 0.15), esc(s["d"])))
    if k == "check":
        ck = cards.get("check", {})
        conf, unc = ck.get("confirmed", [])[:3], ck.get("unconfirmed", [])[:2]
        tags = s.get("tags", [])
        # 예전 원고("아직 확인되지 않은 것은, ~")면 그 문장에서, 아니면 이 장면 첫 문장에서 '아직 모르는 것'을 올린다
        j_no = next((j for j, x in enumerate(tags) if x.startswith("아직 확인되지")), 0)

        def lis(rows, t0):
            return "".join('<li data-in="%.3f" data-fx="up">%s</li>' % (t0 + 0.3 + 0.3 * n, esc(r))
                           for n, r in enumerate(rows))
        # 확인된 것은 장면과 함께 깔고(이미 말한 사실이라 다시 읽지 않는다), 아직 모르는 것은 말하는 순간에
        ok = ('<div class="box ok" data-in="%.3f" data-fx="up"><h3>✓ 확인된 것</h3><ul>%s</ul></div>'
              % (vis(0.2), lis(conf, vis(0.2)))) if conf else ""
        no = ('<div class="box no" data-in="%.3f" data-fx="up"><h3>? 아직 확인 안 됨</h3><ul>%s</ul></div>'
              % (at(j_no, 0.05), lis(unc, at(j_no, 0.05)))) if unc else ""
        return ('<div class="pad"><div class="eye" data-in="%.3f" data-fx="left">팩트 체크</div>'
                '<h1 class="s" style="margin-top:16px;font-size:74px" data-in="%.3f" data-fx="up">'
                '확인된 것과<br><em>아직 모르는 것</em></h1>%s%s</div>'
                % (vis(0.02), vis(0.12), ok, no))
    if k == "outro":
        srcs = narration.clean_outlets(s.get("outlets") or [])
        return ('<div class="pad fixed" style="top:330px"><div class="big-logo" data-in="%.3f" data-fx="pop"><i></i>지금 세계</div></div>'
                '<div class="cta" data-in="%.3f" data-fx="up">세계 사건·사고,<br>확인된 것만 가장 빠르게'
                '<small>팔로우하고 먼저 받아보세요 · @jigeum.segye</small></div>'
                '<div class="srcs" data-in="%.3f" data-fx="fade">출처 · %s</div>'
                % (vis(0.05), vis(0.3), vis(0.55), esc(" · ".join(srcs[:5]))))
    return ""


def plan(spec, segs, times):
    """장면 목록. make_video 가 여기에 자료 사진·화면(broll.attach)을 붙인 뒤 build_html 에 넘긴다."""
    scenes, _, cards = scenes_for(spec, segs, times)
    outlets = [s for s in (cards.get("outro") or {}).get("sources", []) if not s.endswith("기준")]
    for s in scenes:
        s["outlets"] = outlets
    return scenes


def build_html(spec, segs, times, seg_words, total, scenes=None):
    """→ (html, 장면 전환 시각, 숫자 펀치 시각)"""
    scenes = scenes if scenes is not None else plan(spec, segs, times)
    cards = {c["type"]: c for c in spec["cards"]}
    cover = cards.get("cover", {})
    for s in scenes:
        m = s.get("media")
        if m and m["type"] == "clip":
            m["base"] = make_cards.file_url(str(Path(m["dir"]) / "f_"))
        elif m:
            m["src"] = make_cards.file_url(str(m["src"]))
    bodies = []
    for i, s in enumerate(scenes):
        bodies.append('<div class="sc" id="s%d">%s</div>' % (i, scene_html(i, s, spec, cover, cards)))
    caps = phrases(seg_words, times)
    # 숫자 낱말을 읽기 시작하는 순간 (한 구절에 숫자가 여럿이면 첫 번째만)
    punches = []
    for c in caps:
        nw = [w for w in c["w"] if re.search(r"\d", w[0])]
        if nw and (not punches or nw[0][1] - punches[-1] > 0.8):
            punches.append(nw[0][1])
    data = {"scenes": [{k: s.get(k) for k in ("kind", "start", "end", "globe", "media")} for s in scenes],
            "caps": caps, "total": total, "push": PUSH, "punches": punches, "fps": FPS}
    libs = ""
    if any(s.get("globe") and (s["kind"] == "map" or not s.get("media")) for s in scenes):
        libs = "".join('<script src="%s"></script>' % make_cards.file_url(str(VENDOR / f))
                       for f in ("d3.min.js", "topojson-client.min.js", "world.js"))
    css = CSS.replace("FONT", make_cards.file_url(str(VENDOR / "PretendardVariable.woff2")))
    clock = esc(spec.get("time", ""))
    bars = "".join("<div><i></i></div>" for _ in scenes)
    html = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>%s</style></head><body>'
            '<div id="bg"></div><div id="grid"></div>%s'
            '<div id="bars">%s</div><div id="hd"><div id="logo"><i></i>지금 세계</div><div id="clock">%s</div></div>'
            '<div id="cap"></div>%s<script>window.DATA=%s;</script><script>%s</script></body></html>'
            % (css, "".join(bodies), bars, clock, libs, json.dumps(data, ensure_ascii=False), JS))
    return html, [s["start"] for s in scenes[1:]], punches   # 장면 전환·숫자 펀치 시각 — 효과음 자리


# ────────────────────────────────────────────── 촬영
def capture(html_path, out_mp4, total):
    from playwright.sync_api import sync_playwright

    work = Path(html_path).parent
    if os.name == "nt":  # 크롬 프로필 폴더가 한글 경로면 조용히 실패한다 (make_cards.work_dir 참고)
        os.environ["TMP"] = os.environ["TEMP"] = str(work)
    frames = int(round(total * FPS))
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS),
         "-c:v", "mjpeg", "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
         "-pix_fmt", "yuv420p", "-r", str(FPS), str(out_mp4)], stdin=subprocess.PIPE)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=make_cards.find_chrome(),
                              args=["--allow-file-access-from-files", "--disable-gpu"])
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        pg.goto(make_cards.file_url(str(html_path)))
        pg.wait_for_function("window.READY === true", timeout=30000)
        for f in range(frames):
            pg.evaluate("t => render(t)", f / FPS)
            ff.stdin.write(pg.screenshot(type="jpeg", quality=93))
        b.close()
    ff.stdin.close()
    if ff.wait() != 0:
        raise RuntimeError("ffmpeg encode failed")
    return out_mp4


def mux(video, voice, out, total, cuts=(), punches=()):
    """목소리 + 잔잔한 배경음(목소리가 나오면 자동으로 작아진다) + 장면 전환 효과음 + 첫 화면 울림
    + 숫자를 말하는 순간의 낮은 '톡'."""
    T = "%.3f" % total
    steps = ["[1:a]aresample=48000,apad,atrim=0:%s,asplit=2[vo][key]" % T,
             "aevalsrc=exprs='%s':s=48000:d=%s,highpass=f=35,lowpass=f=4500,volume=%.1fdB[bed0]"
             % (BED, T, BED_DB),
             # 목소리가 나오면 배경음이 물러났다가, 문장 사이 틈에 다시 차오른다
             "[bed0][key]sidechaincompress=threshold=0.015:ratio=8:attack=25:release=250[bed]",
             "aevalsrc=exprs='%s':s=48000:d=1.4,volume=%.1fdB[boom]" % (BOOM, BOOM_DB)]
    mix = ["[vo]", "[bed]", "[boom]"]
    cuts = [c for c in cuts if 0.3 < c < total - 0.3]
    if cuts:
        # 쉭 소리가 가장 커지는 순간(0.22초)이 장면이 미끄러져 들어오는 한가운데에 오게
        steps.append("anoisesrc=c=pink:a=0.5:d=0.5:r=48000,highpass=f=400,lowpass=f=6000,"
                     "afade=t=in:st=0:d=0.22:curve=qsin,afade=t=out:st=0.22:d=0.28:curve=qsin,"
                     "volume=%.1fdB,asplit=%d%s" % (WHOOSH_DB, len(cuts),
                                                   "".join("[w%d]" % n for n in range(len(cuts)))))
        for n, c in enumerate(cuts):
            steps.append("[w%d]adelay=%d:all=1[x%d]" % (n, max(0, int((c - 0.12) * 1000)), n))
            mix.append("[x%d]" % n)
    punches = [p for p in punches if 0.2 < p < total - 0.3]
    if punches:
        steps.append("aevalsrc=exprs='%s':s=48000:d=0.35,volume=%.1fdB,asplit=%d%s"
                     % (TOCK, TOCK_DB, len(punches), "".join("[p%d]" % n for n in range(len(punches)))))
        for n, c in enumerate(punches):
            steps.append("[p%d]adelay=%d:all=1[y%d]" % (n, int(c * 1000), n))
            mix.append("[y%d]" % n)
    steps.append("%samix=inputs=%d:duration=first:normalize=0,"
                 "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]" % ("".join(mix), len(mix)))
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(voice),
         "-filter_complex", ";".join(steps),
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-t", T, str(out)], check=True)
    return out
