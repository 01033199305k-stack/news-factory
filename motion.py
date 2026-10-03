# -*- coding: utf-8 -*-
"""
세로 숏폼(1080×1920) 모션 그래픽 렌더러.

카드 사진을 확대하던 예전 방식 대신, 장면을 HTML 로 그리고 시간 t 마다 render(t) 로 위치·투명도를
정한 뒤 헤드리스 크롬으로 한 프레임씩 찍어 ffmpeg 로 묶는다. 애니메이션이 전부 t 의 함수라
몇 번을 돌려도 같은 영상이 나온다.

- 장면: 표지(배지·제목·요약·보도 매체) → 지도 → 핵심 1·2·3 → 확인된 것/아직 모르는 것 → 마무리
- 나레이션 문장이 시작하는 순간에 그 문장의 화면 요소가 들어온다 (말과 화면이 맞는다)
- 자막도 HTML 로 그린다: 한 번에 한 구절, 지금 읽는 단어만 강조
- 화면 배치는 쇼츠 안전 구역 기준: 위 150px·아래 460px·오른쪽 버튼 줄은 비운다

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


def esc(s):
    t = _html.escape(str(s or "")).replace("\n", "<br>")
    return re.sub(r"\[\[(.+?)\]\]", r"<em>\1</em>", t)


def plain(s):
    return re.sub(r"\[\[|\]\]", "", str(s or "")).replace("\n", " ").strip()


# ────────────────────────────────────────────── 장면 나누기
def scenes_for(spec, segs, times):
    """segs[i] 를 times[i]=(시작, 끝) 에 읽는다. 같은 화면에 머무는 문장끼리 한 장면으로 묶고,
    장면 안에서 문장이 바뀌는 순간(steps)에 요소를 하나씩 들여보낸다."""
    cards = {c["type"]: c for c in spec["cards"]}
    cover = cards.get("cover", {})
    has_map = "map" in cards
    out = []

    def scene(kind, i, **data):
        if out and out[-1]["kind"] == kind and kind in ("cover", "check"):
            out[-1]["steps"].append(times[i][0])
            out[-1]["tags"].append(segs[i]["text"])
            out[-1]["end"] = times[i][1]
            return
        out.append({"kind": kind, "start": times[i][0], "end": times[i][1],
                    "steps": [times[i][0]], "tags": [segs[i]["text"]], **data})

    pts = (cards.get("points") or {}).get("items", [])
    k = 0
    for i, s in enumerate(segs):
        card = s["card"]
        if card == "map" and has_map:
            scene("map", i)
        elif card == "points":
            it = pts[k] if k < len(pts) else {"t": "", "d": s["text"]}
            it = it if isinstance(it, dict) else {"t": it}
            # 화면 글도 목소리와 같은 "~습니다" 체로 (예전 카드의 "~보도됐다." 가 반말처럼 보인다)
            scene("point", i, idx=k + 1, total=min(3, len(pts)) or 1, t=it.get("t", ""),
                  d=narration.polite(it.get("d", "")))
            k += 1
        elif card == "check":
            scene("check", i)
        elif card == "outro":
            scene("outro", i)
        else:
            scene("cover", i)
    # J컷: 둘째 장면부터 목소리보다 LEAD 초 먼저 들어온다 (장면 안 글자는 여전히 목소리에 맞춰 나온다)
    for s in out[1:]:
        s["start"] = max(0.0, s["start"] - LEAD)
    # 장면 사이 빈틈 없이: 다음 장면 시작까지 늘린다
    for a, b in zip(out, out[1:]):
        a["end"] = b["start"]
    return out, cover, cards


# ────────────────────────────────────────────── 자막 구절
def phrases(seg_words, times, max_chars=14):
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
.mapbox{position:absolute;left:60px;right:60px;top:560px;height:700px;border-radius:36px;overflow:hidden;
  background:#0E1422;border:2px solid var(--line)}
.mapbox svg{position:absolute;left:0;top:0}
.mapbox .tag{position:absolute;left:28px;bottom:28px;background:rgba(7,9,14,.85);border:2px solid var(--line);
  border-radius:16px;padding:14px 24px;font-size:38px;font-weight:800}
.mapbox .tag span{color:var(--acc);margin-right:10px}
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
#cap{position:absolute;left:60px;right:60px;top:1300px;height:170px;display:flex;align-items:center;justify-content:center}
#cap div{background:rgba(7,9,14,.78);border:2px solid rgba(255,255,255,.10);border-radius:26px;
  padding:18px 34px 22px;font-size:62px;font-weight:850;letter-spacing:-1.2px;text-align:center;line-height:1.25;
  max-width:960px}
#cap span{color:#fff}
#cap span.on{color:var(--acc2)}
#cap span.done{color:rgba(255,255,255,.92)}
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

window.render = function (t) {
  document.querySelector('#bg').style.transform =
    `translate(${Math.sin(t * .25) * 60}px,${Math.cos(t * .2) * 50}px)`;
  document.querySelector('#logo i').style.opacity = .55 + .45 * Math.abs(Math.cos(t * 2.2));
  scenes.forEach((el, i) => {
    const s = D.scenes[i], last = i === scenes.length - 1;
    // 첫 장면은 0초부터 다 보인다 — 피드에서 넘기기 전 첫 프레임이 빈 화면이면 바로 넘어간다
    // (2026-09-29 인스타 릴스 평균 시청 2~5초, 예전 첫 프레임은 제목 없는 검은 화면이었다)
    const pin = i === 0 ? 1 : (t - s.start) / 0.42, pout = (t - s.end) / 0.32;
    let op = eo(pin) * (last ? 1 : 1 - eo(pout));
    if ((i > 0 && t < s.start - 0.01) || (!last && t > s.end + 0.35)) op = 0;
    el.style.opacity = op;
    const prog = C((t - s.start) / Math.max(1, s.end - s.start));
    el.style.transform = `translateX(${(1 - eo(pin)) * 80 + (last ? 0 : eo(pout) * -80)}px) scale(${1 + D.push * prog})`;
    el.style.visibility = op > 0.001 ? 'visible' : 'hidden';
    if (s.kind === 'map' && op > 0) mapTick(el, t, s);
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
    cap.innerHTML = k < 0 ? '' : '<div>' + D.caps[k].w.map(w => `<span>${w[0]}</span>`).join(' ') + '</div>';
    lastCap = k;
  }
  if (k >= 0) {
    const box = cap.firstChild, p = (t - D.caps[k].s) / 0.18;
    box.style.opacity = eo(p); box.style.transform = `translateY(${(1 - eo(p)) * 24}px) scale(${.94 + .06 * eo(p)})`;
    [...box.children].forEach((sp, j) => {
      const w = D.caps[k].w[j];
      sp.className = t >= w[1] && t < w[2] + 0.05 ? 'on' : t >= w[2] ? 'done' : '';
    });
  }
};

// 지도: 천천히 다가가고, 사건 지점에서 레이더처럼 퍼지는 원
function mapTick(el, t, s) {
  const g = el.querySelector('g.world'), rings = el.querySelectorAll('circle.ring');
  if (!g) return;
  const p = C((t - s.start) / Math.max(1, s.end - s.start));
  const z = 1 + 0.35 * (p * p * (3 - 2 * p));
  const [x, y] = s.pt;
  g.setAttribute('transform', `translate(${x} ${y}) scale(${z}) translate(${-x} ${-y})`);
  rings.forEach((r, i) => {
    const f = ((t * 0.7) + i / 3) % 1;
    r.setAttribute('r', 14 + f * 150); r.setAttribute('opacity', (1 - f) * .85);
  });
}

function drawMaps() {
  document.querySelectorAll('.mapbox').forEach((box, i) => {
    const s = D.scenes[+box.dataset.scene], w = box.clientWidth, h = box.clientHeight;
    const land = topojson.feature(WORLD, WORLD.objects.countries);
    const mesh = topojson.mesh(WORLD, WORLD.objects.countries, (a, b) => a !== b);
    const proj = d3.geoMercator().rotate([-s.map.lon, 0]).center([0, s.map.lat])
      .scale(w * 0.55 * Math.pow(2, s.map.zoom)).translate([w / 2, h / 2]);
    const path = d3.geoPath(proj);
    const svg = d3.select(box).insert('svg', ':first-child').attr('width', w).attr('height', h);
    const g = svg.append('g').attr('class', 'world');
    g.append('path').datum(d3.geoGraticule().step([10, 10])()).attr('d', path)
      .attr('fill', 'none').attr('stroke', 'rgba(244,246,250,.08)');
    g.append('path').datum(land).attr('d', path).attr('fill', '#1F2A40');
    g.append('path').datum(mesh).attr('d', path).attr('fill', 'none').attr('stroke', '#34425F').attr('stroke-width', 1.4);
    const pt = proj([s.map.lon, s.map.lat]);
    s.pt = pt;
    for (let k = 0; k < 3; k++) svg.append('circle').attr('class', 'ring').attr('cx', pt[0]).attr('cy', pt[1])
      .attr('fill', 'none').attr('stroke', '#FF3B30').attr('stroke-width', 5);
    svg.append('circle').attr('cx', pt[0]).attr('cy', pt[1]).attr('r', 16)
      .attr('fill', '#FF3B30').attr('stroke', '#fff').attr('stroke-width', 5);
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
  if (window.d3) drawMaps();
  await document.fonts.ready;
  fit();
  render(0);
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

    if k == "cover":
        b = cover.get("badge", "breaking")
        lines = _video_lines(cover.get("title", spec.get("topic", "")))
        # 배지·제목은 첫 프레임부터 떠 있다 (data-in 이 음수 = 이미 들어온 상태)
        h1 = "".join('<span class="ln" data-in="-1" data-fx="up">%s</span>' % esc(l) for l in lines)
        srcs = narration.clean_outlets(s.get("outlets") or [])
        more = len(s.get("outlets") or [])
        nums = (cover.get("chips") or [])[:3]
        if nums:
            # 숫자 칩(사망 6명 등)이 있으면 매체 이름보다 먼저 — 멈춰서 볼 이유가 된다. 매체는 한 줄로 작게
            t0 = at(0, 1.1)
            chips = "".join('<div class="chip" data-in="%.3f" data-fx="pop"><span>%s</span>%s</div>'
                            % (t0 + 0.12 * n, esc(x.get("k", "")), esc(x.get("v", "")))
                            for n, x in enumerate(nums))
            note = ('<div class="note" data-in="%.3f" data-fx="fade">%s 등 %d개 매체 보도</div>'
                    % (t0 + 0.5, esc("·".join(srcs[:2])), more)) if srcs else ""
            out = '<div class="out">%s%s</div>' % (chips, note)
        else:
            # 보도 매체는 읽지 않고 화면에만 — 제목이 자리 잡은 뒤 조용히 들어온다
            t0 = at(1, 0.4) if len(st) > 1 else at(0, 1.6)
            chips = "".join('<div class="chip" data-in="%.3f" data-fx="pop">%s</div>'
                            % (t0 + 0.1 * n, esc(x)) for n, x in enumerate(srcs[:3]))
            if more:
                chips += ('<div class="chip n" data-in="%.3f" data-fx="pop">%d개 매체 보도</div>'
                          % (t0 + 0.1 * min(3, len(srcs)), more))
            out = ('<div class="out"><div class="k" data-in="%.3f" data-fx="fade">보도</div>%s</div>'
                   % (t0, chips)) if chips else ""
        return ('<div class="pad"><div class="badge" data-in="-1" data-fx="pop"><b></b>%s</div>'
                '<h1 class="%s">%s</h1><div class="bar" data-in="%.3f" data-fx="wipe"></div>'
                '<div class="sub" data-in="%.3f" data-fx="up">%s</div>%s</div>'
                % (esc(make_cards.BADGES.get(b, "속보")), _title_cls("\n".join(lines)), h1,
                   at(0, 0.5), at(1) if len(st) > 1 else at(0, 0.9), esc(cover.get("sub", "")), out))
    if k == "map":
        m = cards["map"]
        s["map"] = {"lat": m["map"]["lat"], "lon": m["map"]["lon"], "zoom": m["map"].get("zoom", 0.6)}
        return ('<div class="pad fixed"><div class="eye" data-in="%.3f" data-fx="left">어디서</div>'
                '<h1 class="s" style="margin-top:20px" data-in="%.3f" data-fx="up">%s</h1></div>'
                '<div class="mapbox" data-scene="%d"><div class="tag"><span>●</span>%s</div></div>'
                % (vis(0.05), vis(0.18), esc(m.get("title", "")), i, esc(m["map"].get("label", ""))))
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


def build_html(spec, segs, times, seg_words, total):
    scenes, cover, cards = scenes_for(spec, segs, times)
    outlets = [s for s in (cards.get("outro") or {}).get("sources", []) if not s.endswith("기준")]
    for s in scenes:
        s["outlets"] = outlets
    bodies = []
    for i, s in enumerate(scenes):
        bodies.append('<div class="sc" id="s%d">%s</div>' % (i, scene_html(i, s, spec, cover, cards)))
    data = {"scenes": [{k: s.get(k) for k in ("kind", "start", "end", "map")} for s in scenes],
            "caps": phrases(seg_words, times), "total": total, "push": PUSH}
    uses_map = any(s["kind"] == "map" for s in scenes)
    libs = ""
    if uses_map:
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
    return html, [s["start"] for s in scenes[1:]]   # 장면이 바뀌는 시각 — 전환 효과음 자리


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


def mux(video, voice, out, total, cuts=()):
    """목소리 + 잔잔한 배경음(목소리가 나오면 자동으로 작아진다) + 장면 전환 효과음 + 첫 화면 울림."""
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
    steps.append("%samix=inputs=%d:duration=first:normalize=0,"
                 "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]" % ("".join(mix), len(mix)))
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(voice),
         "-filter_complex", ";".join(steps),
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-t", T, str(out)], check=True)
    return out
