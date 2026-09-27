# -*- coding: utf-8 -*-
"""
news-factory - 한국어 속보 카드뉴스 렌더러. 1080x1350 (4:5, 인스타·스레드 공용).

Usage:
    python make_cards.py specs/<name>.json

output/<slug>/NN_<type>.png 와 게시용 문구(caption.md)를 만든다.

렌더링은 pin-factory 와 같은 방식:
HTML -> 헤드리스 크롬 2배 캡처 -> LANCZOS 로 정확한 픽셀에 축소.
지도(d3 + Natural Earth)와 폰트(Pretendard)는 vendor/ 에 있어서 오프라인으로 돈다.
"""
import html as _html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

SCALE = 2
W, H = 1080, 1350
ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_chrome():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    for name in ("chrome", "google-chrome", "google-chrome-stable",
                 "chromium", "chromium-browser", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    raise RuntimeError("Chrome not found.")


def file_url(p):
    return "file:///" + os.path.abspath(p).replace("\\", "/")


def e(s):
    """escape + 줄바꿈 + [[강조]] 를 accent 색으로."""
    t = _html.escape(str(s)).replace("\n", "<br>")
    return re.sub(r"\[\[(.+?)\]\]", r"<em>\1</em>", t)


# ================================================================ themes
# 한 계정은 한 테마로 고정한다. 피드에서 하나의 매체로 읽혀야 한다.
THEMES = {
    "night": {  # 기본. 어두운 뉴스룸 + 속보 레드
        "bg": "#0B0E14", "panel": "#131826", "fg": "#F4F6FA", "accent": "#FF3B30",
        "accent2": "#FFB020", "muted": "rgba(244,246,250,.60)",
        "line": "rgba(244,246,250,.13)", "land": "#1E2638", "border": "#2E3850",
        "sea": "#0E1320", "glow": "rgba(255,59,48,.18)",
    },
    "day": {  # 밝은 신문 톤
        "bg": "#F5F3EE", "panel": "#FFFFFF", "fg": "#111418", "accent": "#D7261E",
        "accent2": "#B7791F", "muted": "rgba(17,20,24,.58)",
        "line": "rgba(17,20,24,.12)", "land": "#DCD8CE", "border": "#BDB7A9",
        "sea": "#EAE7E0", "glow": "rgba(215,38,30,.10)",
    },
}

BADGES = {  # badge 필드 → 표시 문구
    "breaking": "속보", "update": "업데이트", "brief": "정리", "explain": "해설",
}

CSS = """
@font-face{font-family:'Pretendard';font-weight:100 900;
  src:url('FONTURL') format('woff2')}
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1350px;overflow:hidden;position:relative;
  background:var(--bg);color:var(--fg);
  font-family:'Pretendard','Malgun Gothic',sans-serif;
  -webkit-font-smoothing:antialiased;word-break:keep-all}
body::before{content:'';position:absolute;inset:0;z-index:0;
  background:radial-gradient(90% 55% at 50% -8%,var(--glow),transparent 62%)}
.wrap{position:absolute;inset:0;z-index:2;padding:64px 72px 56px;
  display:flex;flex-direction:column}
em{font-style:normal;color:var(--accent)}

/* ── 헤더: 로고 · 배지 · 시각 ── */
.hd{display:flex;align-items:center;justify-content:space-between;gap:20px}
.logo{display:flex;align-items:center;gap:14px;font-size:27px;font-weight:800;
  letter-spacing:-.3px}
.logo i{display:block;width:16px;height:16px;border-radius:50%;
  background:var(--accent);box-shadow:0 0 0 6px var(--glow)}
.time{font-size:23px;font-weight:600;color:var(--muted);
  font-variant-numeric:tabular-nums}
.badge{display:inline-flex;align-items:center;gap:10px;align-self:flex-start;
  background:var(--accent);color:#fff;font-weight:800;font-size:30px;
  padding:10px 22px 11px;border-radius:10px;letter-spacing:.5px}
.badge.soft{background:transparent;color:var(--accent);
  border:3px solid var(--accent);padding:7px 19px 8px}
.eyebrow{font-size:26px;font-weight:700;color:var(--accent);letter-spacing:.5px}

/* ── 제목 ── */
h1{font-weight:850;letter-spacing:-3px;line-height:1.14;font-size:104px}
h1.lg{font-size:120px;letter-spacing:-4px;line-height:1.1}
h1.sm{font-size:82px;letter-spacing:-2.4px;line-height:1.18}
h1.xs{font-size:64px;letter-spacing:-1.6px;line-height:1.24}
h2{font-weight:800;font-size:62px;letter-spacing:-1.8px;line-height:1.22}
.sub{font-size:36px;line-height:1.5;color:var(--muted);font-weight:500}

.body{flex:1;min-height:0;display:flex;flex-direction:column;
  justify-content:center;gap:34px}
.top{justify-content:center;padding-bottom:40px}

/* ── 칩 ── */
.chips{display:flex;flex-wrap:wrap;gap:14px}
.chip{background:var(--panel);border:2px solid var(--line);border-radius:14px;
  padding:14px 22px;font-size:29px;font-weight:700}
.chip span{color:var(--muted);font-weight:600;margin-right:10px}

/* ── 지도 ── */
.map{position:relative;border-radius:28px;overflow:hidden;background:var(--sea);
  border:2px solid var(--line);flex:0 0 auto}
.map svg{display:block}
.map .inset{position:absolute;right:20px;top:20px;width:170px;height:170px;
  border-radius:50%;background:var(--bg);border:2px solid var(--line)}
.map .tag{position:absolute;left:24px;bottom:22px;background:var(--bg);
  border:2px solid var(--line);border-radius:12px;padding:10px 18px;
  font-size:24px;font-weight:700}
.map .tag span{color:var(--accent)}

/* ── 큰 숫자 ── */
.big{font-weight:900;color:var(--accent);font-size:230px;line-height:.95;
  letter-spacing:-8px;font-variant-numeric:tabular-nums}
.big.sm{font-size:160px;letter-spacing:-5px}
.big small{font-size:.36em;letter-spacing:-1px;margin-left:10px;color:var(--fg)}

/* ── 목록 ── */
.items{display:flex;flex-direction:column;gap:30px}
.it{display:flex;gap:26px;align-items:flex-start}
.it .n{flex:0 0 64px;height:64px;border-radius:16px;background:var(--accent);
  color:#fff;font-weight:800;font-size:30px;display:flex;align-items:center;
  justify-content:center}
.it b{display:block;font-size:40px;line-height:1.3;font-weight:750}
.it p{font-size:30px;line-height:1.45;color:var(--muted);margin-top:8px}

/* ── 타임라인 ── */
.tl{display:flex;flex-direction:column;position:relative;padding-left:44px}
.tl::before{content:'';position:absolute;left:11px;top:14px;bottom:14px;width:3px;
  background:var(--line)}
.ev{position:relative;padding:0 0 50px}
.ev:last-child{padding-bottom:0}
.ev::before{content:'';position:absolute;left:-44px;top:12px;width:25px;height:25px;
  border-radius:50%;background:var(--bg);border:4px solid var(--accent)}
.ev.now::before{background:var(--accent)}
.ev .t{font-size:29px;font-weight:700;color:var(--accent);
  font-variant-numeric:tabular-nums}
.ev .x{font-size:44px;line-height:1.35;font-weight:700;margin-top:8px}

/* ── 확인됨 / 미확인 ── */
.ck{display:flex;flex-direction:column;gap:22px}
.box{border-radius:24px;padding:38px 40px;background:var(--panel);
  border:2px solid var(--line)}
.box h3{font-size:27px;font-weight:800;letter-spacing:.4px;margin-bottom:16px;
  display:flex;align-items:center;gap:12px}
.box.ok h3{color:#34C759}
.box.no h3{color:var(--accent2)}
.box li{list-style:none;font-size:37px;line-height:1.42;font-weight:600;
  padding-left:30px;position:relative;margin-top:10px}
.box li::before{content:'';position:absolute;left:4px;top:19px;width:10px;height:10px;
  border-radius:50%;background:currentColor;opacity:.5}

/* ── 아웃트로 ── */
.srcs{display:flex;flex-direction:column;gap:14px}
.srcs div{font-size:31px;line-height:1.4;color:var(--muted);padding-left:48px;
  position:relative}
.srcs div::before{content:'—';position:absolute;left:0;color:var(--accent)}
.cta{background:var(--accent);color:#fff;border-radius:24px;padding:36px 40px;
  font-size:40px;font-weight:800;line-height:1.35;letter-spacing:-.8px}
.cta small{display:block;font-size:27px;font-weight:600;opacity:.85;
  margin-top:8px;letter-spacing:0}

/* ── 푸터 ── */
.ft{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;
  border-top:2px solid var(--line);padding-top:22px;margin-top:30px}
.ft .src{font-size:21px;line-height:1.4;color:var(--muted)}
.ft .pg{font-size:22px;font-weight:700;color:var(--muted);white-space:nowrap;
  font-variant-numeric:tabular-nums}
"""

MAP_JS = """
(function(){
  var cfg = MAPCFG;
  var W = cfg.w, H = cfg.h;
  var land = topojson.feature(WORLD, WORLD.objects.countries);
  var mesh = topojson.mesh(WORLD, WORLD.objects.countries, function(a,b){return a!==b;});
  var css = getComputedStyle(document.documentElement);
  var c = function(k){return css.getPropertyValue('--'+k).trim();};

  // 메인: 사건 지점을 가운데 둔 메르카토르. zoom 1 = 약 반경 3000km
  // rotate 로 사건 경도를 가운데 둔다 — center 만 쓰면 경도 180° 에서 지도가 잘린다
  var proj = d3.geoMercator().rotate([-cfg.lon, 0]).center([0, cfg.lat])
      .scale(W * 0.55 * Math.pow(2, cfg.zoom))
      .translate([W/2, H/2]);
  var path = d3.geoPath(proj);
  var svg = d3.select('#' + cfg.id).append('svg').attr('width', W).attr('height', H);
  svg.append('path').datum(d3.geoGraticule().step([10,10])())
     .attr('d', path).attr('fill','none').attr('stroke', c('line')).attr('stroke-width',1);
  svg.append('path').datum(land).attr('d', path).attr('fill', c('land'));
  svg.append('path').datum(mesh).attr('d', path).attr('fill','none')
     .attr('stroke', c('border')).attr('stroke-width', 1.4);

  var p = proj([cfg.lon, cfg.lat]);
  [cfg.ring*3, cfg.ring*2, cfg.ring].forEach(function(r, i){
    svg.append('circle').attr('cx',p[0]).attr('cy',p[1]).attr('r',r)
       .attr('fill', c('accent')).attr('fill-opacity', [.10,.18,.30][i])
       .attr('stroke', c('accent')).attr('stroke-opacity', .6).attr('stroke-width', 2);
  });
  svg.append('circle').attr('cx',p[0]).attr('cy',p[1]).attr('r',11)
     .attr('fill', c('accent')).attr('stroke','#fff').attr('stroke-width',4);

  // 인셋: 지구본 위 위치
  if (cfg.inset) {
    var S = 170, g = d3.geoOrthographic().rotate([-cfg.lon, -cfg.lat*0.6])
        .scale(S/2 - 4).translate([S/2, S/2]);
    var gp = d3.geoPath(g);
    var ins = d3.select('#' + cfg.id + ' .inset').append('svg').attr('width',S).attr('height',S);
    ins.append('path').datum({type:'Sphere'}).attr('d', gp).attr('fill', c('sea'));
    ins.append('path').datum(land).attr('d', gp).attr('fill', c('land'));
    var q = g([cfg.lon, cfg.lat]);
    ins.append('circle').attr('cx',q[0]).attr('cy',q[1]).attr('r',7)
       .attr('fill', c('accent')).attr('stroke','#fff').attr('stroke-width',2.5);
  }
})();
"""


# ================================================================ parts
def header(c, spec):
    brand = e(spec.get("brand", ""))
    t = e(c.get("time", spec.get("time", "")))
    return ('<div class="hd"><div class="logo"><i></i>%s</div>'
            '<div class="time">%s</div></div>' % (brand, t))


def badge(c):
    b = c.get("badge")
    if not b:
        return ""
    soft = "" if b == "breaking" else " soft"
    return '<div class="badge%s">%s</div>' % (soft, e(BADGES.get(b, b)))


def eyebrow(c):
    return '<div class="eyebrow">%s</div>' % e(c["eyebrow"]) if c.get("eyebrow") else ""


_LADDER = [("lg", 14), ("", 22), ("sm", 34), ("xs", 10 ** 6)]


def title(c, start=""):
    """글자 수로 크기를 고른다. 캔버스는 넘치면 잘리니 렌더 후 눈으로 확인할 것."""
    if not c.get("title"):
        return ""
    cls = c.get("size")
    if cls is None:
        # 제일 긴 줄 기준 — 한 줄이 길면 줄바꿈이 늘어나 세로로 넘친다
        n = max(len(re.sub(r"\[\[|\]\]", "", l)) for l in str(c["title"]).split("\n"))
        names = [r for r, _ in _LADDER]
        ladder = _LADDER[names.index(start):]
        cls = next(r for r, lim in ladder if n <= lim)
    return '<h1 class="%s">%s</h1>' % (cls, e(c["title"]))


def sub(c):
    return '<p class="sub">%s</p>' % e(c["sub"]) if c.get("sub") else ""


def chips(c):
    if not c.get("chips"):
        return ""
    out = ""
    for ch in c["chips"]:
        if isinstance(ch, dict):
            out += '<div class="chip"><span>%s</span>%s</div>' % (e(ch["k"]), e(ch["v"]))
        else:
            out += '<div class="chip">%s</div>' % e(ch)
    return '<div class="chips">%s</div>' % out


def footer(c, i, n):
    src = c.get("source", "")
    s = '<div class="src">출처 · %s</div>' % e(src) if src else "<div></div>"
    return '<div class="ft">%s<div class="pg">%d / %d</div></div>' % (s, i, n)


_map_seq = [0]


def map_block(m, w, h):
    """m = {lat, lon, zoom?, label?, inset?}. 스크립트는 page() 가 끝에 붙인다."""
    _map_seq[0] += 1
    mid = "map%d" % _map_seq[0]
    cfg = {
        "id": mid, "w": w, "h": h, "lat": m["lat"], "lon": m["lon"],
        "zoom": m.get("zoom", 1), "ring": m.get("ring", 26),
        "inset": m.get("inset", True),
    }
    tag = ""
    if m.get("label"):
        tag = '<div class="tag"><span>●</span> %s</div>' % e(m["label"])
    inset = '<div class="inset"></div>' if cfg["inset"] else ""
    html = ('<div class="map" id="%s" style="width:%dpx;height:%dpx">%s%s</div>'
            % (mid, w, h, inset, tag))
    return html, MAP_JS.replace("MAPCFG", json.dumps(cfg))


# ================================================================ cards
# 각 빌더는 (본문 html, [지도 스크립트]) 를 돌려준다.
INNER_W = W - 72 * 2


def build_cover(c):
    scripts = []
    parts = [badge(c), title(c, "lg"), sub(c), chips(c)]
    if c.get("map"):
        html, js = map_block(c["map"], INNER_W, c["map"].get("h", 470))
        parts.append(html)
        scripts.append(js)
    return '<div class="body">%s</div>' % "".join(parts), scripts


def build_map(c):
    html, js = map_block(c["map"], INNER_W, c["map"].get("h", 760))
    return ('<div class="body">%s%s%s%s</div>'
            % (eyebrow(c), title(c, "sm"), html, sub(c))), [js]


def build_fact(c):
    v = str(c["value"])
    unit = '<small>%s</small>' % e(c["unit"]) if c.get("unit") else ""
    cls = " sm" if len(v) > 4 else ""
    return ('<div class="body">%s%s<div class="big%s">%s%s</div>%s%s</div>'
            % (eyebrow(c), title(c, "sm"), cls, e(v), unit, sub(c), chips(c))), []


def build_points(c):
    rows = ""
    for i, it in enumerate(c["items"], 1):
        if isinstance(it, str):
            it = {"t": it}
        d = "<p>%s</p>" % e(it["d"]) if it.get("d") else ""
        rows += ('<div class="it"><div class="n">%d</div><div><b>%s</b>%s</div></div>'
                 % (i, e(it["t"]), d))
    return ('<div class="body top">%s<h2>%s</h2><div class="items">%s</div></div>'
            % (eyebrow(c), e(c.get("title", "핵심 정리")), rows)), []


def build_timeline(c):
    rows = ""
    for ev in c["events"]:
        now = " now" if ev.get("now") else ""
        rows += ('<div class="ev%s"><div class="t">%s</div><div class="x">%s</div></div>'
                 % (now, e(ev["t"]), e(ev["x"])))
    return ('<div class="body top">%s<h2>%s</h2><div class="tl">%s</div></div>'
            % (eyebrow(c), e(c.get("title", "시간순 정리")), rows)), []


def build_check(c):
    """확인된 것과 아직 확인 안 된 것을 나눠 보여준다. 이 계정의 신뢰 장치."""
    def box(cls, head, rows):
        if not rows:
            return ""
        li = "".join("<li>%s</li>" % e(r) for r in rows)
        return '<div class="box %s"><h3>%s</h3><ul>%s</ul></div>' % (cls, head, li)
    return ('<div class="body top">%s<h2>%s</h2><div class="ck">%s%s</div></div>'
            % (eyebrow(c), e(c.get("title", "지금까지 확인된 것")),
               box("ok", "✓ 공식 확인", c.get("confirmed", [])),
               box("no", "? 아직 확인 안 됨", c.get("unconfirmed", [])))), []


def build_outro(c):
    srcs = "".join("<div>%s</div>" % e(s) for s in c.get("sources", []))
    cta = c.get("cta", "팔로우하면 세계 소식을\n가장 먼저 받아봅니다")
    cta_sub = c.get("cta_sub", "")
    cs = "<small>%s</small>" % e(cta_sub) if cta_sub else ""
    return ('<div class="body" style="justify-content:flex-start;padding-top:70px">'
            '%s<h2>%s</h2><div class="srcs">%s</div>'
            '<div class="spacer" style="flex:1"></div><div class="cta">%s%s</div></div>'
            % (eyebrow(c), e(c.get("title", "출처")), srcs, e(cta), cs)), []


BUILDERS = {
    "cover": build_cover, "map": build_map, "fact": build_fact,
    "points": build_points, "timeline": build_timeline,
    "check": build_check, "outro": build_outro,
}


def page(inner, scripts, theme):
    t = THEMES.get(theme, THEMES["night"])
    tvars = ";".join("--%s:%s" % (k, v) for k, v in t.items())
    css = CSS.replace("FONTURL", file_url(os.path.join(VENDOR, "PretendardVariable.woff2")))
    libs = ""
    if scripts:
        libs = "".join('<script src="%s"></script>' % file_url(os.path.join(VENDOR, f))
                       for f in ("d3.min.js", "topojson-client.min.js", "world.js"))
        libs += "<script>%s</script>" % "".join(scripts)
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<style>:root{%s}%s</style></head><body>%s%s</body></html>'
            % (tvars, css, inner, libs))


def work_dir():
    """크롬 --user-data-dir / --screenshot 은 한글 경로(C:\\Users\\강민혁)에서 조용히 실패하고,
    이 사용자 폴더엔 8.3 짧은 이름도 없다. 영문 경로(공용 폴더)에 임시 폴더를 만든다."""
    if os.name == "nt":
        base = os.environ.get("PUBLIC", r"C:\Users\Public")
    else:  # GitHub Actions 등 리눅스
        base = tempfile.gettempdir()
    root = os.path.join(base, "news-factory-tmp")
    # tempfile.mkdtemp 는 쓰지 않는다: 3.12.4+ 윈도우에선 소유자 전용 ACL 을 걸어서
    # 크롬 자식 프로세스가 그 안에 캡처를 못 쓴다
    path = os.path.join(root, "run-%d-%d" % (os.getpid(), int(time.time() * 1000)))
    os.makedirs(path)
    return path


def shoot(chrome, html_path, png_path, work, idx, timeout=30):
    # 카드마다 새 프로필: 같은 프로필을 이어 쓰면 앞 프로세스가 잠가서 exit 21 로 실패한다
    prof = os.path.join(work, "p%02d" % idx)
    shot = os.path.join(work, "s%02d.png" % idx)
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--hide-scrollbars", "--allow-file-access-from-files",
           "--user-data-dir=%s" % prof,
           "--force-device-scale-factor=%d" % SCALE,
           "--virtual-time-budget=4000",  # 폰트·지도 스크립트가 끝날 때까지
           "--screenshot=%s" % shot, "--window-size=%d,%d" % (W, H),
           file_url(html_path)]
    # 이 PC의 chrome.exe 는 곧바로 반환하고 캡처는 뒤에서 끝낸다 → 파일이 생길 때까지 기다린다
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline, last = time.time() + timeout, -1
    while time.time() < deadline:
        if os.path.exists(shot):
            size = os.path.getsize(shot)
            if size > 0 and size == last:  # 다 써졌는지 크기로 확인
                break
            last = size
        time.sleep(0.4)
    else:
        raise RuntimeError("capture failed (timeout %ds): %s" % (timeout, png_path))
    shutil.move(shot, png_path)
    try:
        from PIL import Image
        im = Image.open(png_path)
        if im.size != (W, H):
            im.resize((W, H), Image.LANCZOS).save(png_path)
    except ImportError:
        pass


def write_caption(outdir, spec, made):
    """caption.md - 스레드(500자 제한) / 인스타 본문 + 카드별 대체 텍스트."""
    cap = spec.get("caption", {})
    threads = cap.get("threads", "")
    insta = cap.get("instagram", threads)
    tags = " ".join("#" + t.lstrip("#") for t in cap.get("hashtags", []))
    md = ["# %s" % spec.get("topic", spec["slug"]), ""]
    md += ["## 스레드 (%d/500자)" % len(threads), "```", threads, "```", ""]
    md += ["## 인스타그램", "```", insta + ("\n\n" + tags if tags else ""), "```", ""]
    md += ["## 이미지 순서 / 대체 텍스트", ""]
    for name, card in made:
        md.append("- `%s` — %s" % (name, card.get("alt", "")))
    if len(threads) > 500:
        md.insert(2, "> ⚠ 스레드 본문이 500자를 넘습니다. 줄이세요.\n")
    with open(os.path.join(outdir, "caption.md"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))


def render(spec):
    theme = spec.get("theme", "night")
    outdir = os.path.join(ROOT, "output", spec["slug"])
    htmldir = os.path.join(outdir, "_html")
    if os.path.isdir(outdir):
        for f in os.listdir(outdir):
            if f.endswith(".png"):
                os.remove(os.path.join(outdir, f))
    os.makedirs(htmldir, exist_ok=True)

    cards = spec["cards"]
    n = len(cards)
    chrome = find_chrome()
    work = work_dir()
    made = []
    try:
        for i, c in enumerate(cards, 1):
            kind = c["type"]
            if kind not in BUILDERS:
                raise ValueError("unknown card type: %s (available: %s)"
                                 % (kind, ", ".join(BUILDERS)))
            if not c.get("source") and spec.get("source") and kind != "outro":
                c["source"] = spec["source"]
            body, scripts = BUILDERS[kind](c)
            inner = ('<div class="wrap">%s%s%s</div>'
                     % (header(c, spec), body, footer(c, i, n)))
            name = "%02d_%s.png" % (i, kind)
            hp = os.path.join(htmldir, name.replace(".png", ".html"))
            with open(hp, "w", encoding="utf-8") as fp:
                fp.write(page(inner, scripts, c.get("theme", theme)))
            shoot(chrome, hp, os.path.join(outdir, name), work, i)
            made.append((name, c))
            print("OK", name)
    finally:
        time.sleep(1)  # 백그라운드 크롬이 프로필을 놓을 때까지
        shutil.rmtree(work, ignore_errors=True)

    write_caption(outdir, spec, made)
    return outdir, made


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("themes:", ", ".join(THEMES))
        print("types :", ", ".join(BUILDERS))
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as fp:
        spec = json.load(fp)
    outdir, made = render(spec)
    print("\n%d cards -> %s" % (len(made), outdir))
    print("게시용 문구 -> caption.md")


if __name__ == "__main__":
    main()
