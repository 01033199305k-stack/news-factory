# -*- coding: utf-8 -*-
"""
하루 동안 올린 숏폼을 모아 가로(1920×1080) 롱폼 「세계 뉴스 하루 정리」를 만들고 유튜브에 올린다.

    python daily_longform.py                       # 어제(KST) 영상 만들기만 → output/daily/<날짜>/
    python daily_longform.py --date 2026-10-23     # 날짜 지정
    python daily_longform.py --upload              # 만들고 올리기 (YOUTUBE_* 비밀값, DAILY_PRIVACY=private|public)

- 영상 재료: 미디어 보관소(MEDIA_BASE)의 <slug>/video.mp4 — 보관소는 3일만 보관하므로 그 안에 돌려야 한다
- 세로 쇼츠를 그대로 이어 붙이면 좌우가 검게 비고 '같은 영상 재업로드'로 보인다 → 왼쪽에 쇼츠, 오른쪽에 순번·
  제목·확인된 것/아직 모르는 것·진행 막대·다음 소식을 그린 가로 화면으로 다시 짠다. 앞에 오늘의 목차(나레이션),
  끝에 마무리 인사, 설명란에 챕터
- 총격·테러는 넣지 않는다 (쇼츠 때도 조회 0~4회, 수익화 심사에도 불리). USGS 지진은 같은 규모 지진 기사가
  있으면 기사 쪽만 남긴다 (10/22 '중국 인근 규모 7.0' = 10/23 '티베트 규모 7.0 강진')
- 순서: 유튜브에서 조회가 많았던 쇼츠부터 (첫 소식에서 이탈이 가장 크다), 유튜브에 안 올라간 소식은 시간순으로 뒤에
"""
import datetime
import html as _html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import make_cards

ROOT = Path(__file__).resolve().parent
POSTED = ROOT / "state" / "posted.json"
DAILY = ROOT / "state" / "daily.json"
SPECDIR = ROOT / "specs" / "auto"
MEDIA_BASE = os.environ.get("MEDIA_BASE", "https://news-factory-watch.alsgur3319.workers.dev/media").rstrip("/")
KST = datetime.timezone(datetime.timedelta(hours=9))
UA = "news-factory-daily/1.0 (+https://github.com/01033199305k-stack/news-factory)"

W, H, FPS = 1920, 1080, 30
VX, VY, VW, VH = 150, 60, 540, 960          # 쇼츠가 들어가는 자리
VOICE, RATE = "ko-KR-SunHiNeural", "+8%"    # 쇼츠와 같은 목소리
FADE = 0.25
SKIP_CATEGORIES = ("총격·테러",)
GROUP = {"국제": 0, "경제·과학": 0, "재난": 1, "사고": 1, "폭발·화재": 1, "사건사고": 1}
TAGS = ["지금세계", "세계뉴스", "오늘의뉴스", "해외뉴스", "뉴스정리"]


def plain(s):
    return re.sub(r"\[\[|\]\]", "", str(s or "")).replace("\n", " ").strip()


def esc(s):
    t = _html.escape(str(s or "")).replace("\n", "<br>")
    return re.sub(r"\[\[(.+?)\]\]", r"<em>\1</em>", t)


def ts(sec):
    sec = int(sec)
    return "%d:%02d:%02d" % (sec // 3600, sec // 60 % 60, sec % 60) if sec >= 3600 else "%d:%02d" % (sec // 60, sec % 60)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError("%s\n%s" % (" ".join(map(str, cmd[:6])), "\n".join(p.stderr.strip().splitlines()[-8:])))
    return p.stdout


def duration(p):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]))


# ────────────────────────────────────────────── 고르기
def pick(day):
    posted = json.loads(POSTED.read_text(encoding="utf-8"))
    items = []
    for it in posted:
        at = it.get("at")
        if not at or datetime.datetime.fromtimestamp(at, KST).strftime("%Y-%m-%d") != day:
            continue
        if it.get("category") in SKIP_CATEGORIES or it.get("status") != "done":
            continue
        sp = SPECDIR / (it["slug"] + ".json")
        if not sp.exists():
            continue
        spec = json.loads(sp.read_text(encoding="utf-8"))
        yt = (it.get("done") or {}).get("youtube") or {}
        items.append({"slug": it["slug"], "spec": spec, "category": it.get("category") or
                      ("지진" if spec.get("usgs") else ""), "yt": yt.get("id"), "at": at})
    # 같은 지진을 USGS 속보와 기사로 두 번 올렸으면 기사만
    mags = {m for x in items if not x["spec"].get("usgs")
            for m in re.findall(r"규모 (\d\.\d)", plain(x["spec"]["cards"][0].get("title")))
            if re.search(r"지진|강진", x["spec"].get("topic", ""))}
    items = [x for x in items if not (x["spec"].get("usgs") and
                                      re.search(r"규모 (\d\.\d)", x["spec"]["topic"]) and
                                      re.search(r"규모 (\d\.\d)", x["spec"]["topic"]).group(1) in mags)]
    return items


def views(ids):
    """유튜브 조회수. 비밀값이 있으면 Data API(1건당 1 할당), 없으면 yt-dlp, 둘 다 안 되면 0."""
    ids = [i for i in ids if i]
    out = {}
    if not ids:
        return out
    if os.environ.get("YOUTUBE_REFRESH_TOKEN"):
        try:
            tok = yt_access()
            q = urllib.parse.urlencode({"part": "statistics", "id": ",".join(ids[:50])})
            req = urllib.request.Request("https://www.googleapis.com/youtube/v3/videos?" + q,
                                         headers={"Authorization": "Bearer " + tok})
            with urllib.request.urlopen(req, timeout=30) as r:
                for v in json.loads(r.read())["items"]:
                    out[v["id"]] = int(v["statistics"].get("viewCount", 0))
            return out
        except Exception as ex:
            print("조회수 API 실패", ex)
    for i in ids:
        try:
            out[i] = int(run(["yt-dlp", "--js-runtimes", "node", "--skip-download", "--print", "view_count",
                              "https://www.youtube.com/shorts/" + i]).strip().splitlines()[-1])
        except Exception:
            out[i] = 0
    return out


def order(items):
    v = views([x["yt"] for x in items])
    for x in items:
        x["views"] = v.get(x["yt"], 0) if x["yt"] else -1
    return sorted(items, key=lambda x: (x["views"] < 0, -x["views"] if x["views"] >= 0 else 0,
                                        GROUP.get(x["category"], 2), x["at"]))


# ────────────────────────────────────────────── 화면 (HTML → 크롬 캡처)
CSS = """
@font-face{font-family:P;src:url('FONTURL') format('woff2');font-weight:100 900}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:%dpx;height:%dpx;overflow:hidden}
body{font-family:P,sans-serif;color:#F4F5F7;letter-spacing:-0.02em;
 background:radial-gradient(1200px 800px at 85%% 10%%,#1B2233 0%%,#0B0E14 60%%) #0B0E14}
em{font-style:normal;color:#FF3B30}
.brand{position:absolute;left:%dpx;top:28px;font-weight:800;font-size:26px;color:#fff}
.brand b{display:inline-block;width:14px;height:14px;border-radius:50%%;background:#FF3B30;margin-right:10px}
""" % (W, H, VX)


def shoot(html, png, work, idx):
    work = Path(work)
    page = work / ("d%03d.html" % idx)
    page.write_text(html, encoding="utf-8")
    prof, shot = work / ("dp%03d" % idx), work / ("ds%03d.png" % idx)
    subprocess.run([make_cards.find_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--hide-scrollbars", "--allow-file-access-from-files", "--user-data-dir=%s" % prof,
                    "--force-device-scale-factor=1", "--virtual-time-budget=3000",
                    "--screenshot=%s" % shot, "--window-size=%d,%d" % (W, H), make_cards.file_url(page)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline, last = time.time() + 40, -1
    while time.time() < deadline:
        if shot.exists():
            size = shot.stat().st_size
            if size > 0 and size == last:
                break
            last = size
        time.sleep(0.4)
    else:
        raise RuntimeError("capture failed: %s" % png)
    os.replace(shot, png)
    return png


def wrap(body, extra_css="", script=""):
    css = CSS.replace("FONTURL", make_cards.file_url(ROOT / "vendor" / "PretendardVariable.woff2"))
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>%s%s</style></head>'
            '<body>%s<script>%s</script></body></html>' % (css, extra_css, body, script))


def kday(day):
    d = datetime.date.fromisoformat(day)
    return "%d월 %d일" % (d.month, d.day)


STORY_CSS = """
.frame{position:absolute;left:%dpx;top:%dpx;width:%dpx;height:%dpx;border-radius:18px;
 box-shadow:0 0 0 3px rgba(255,255,255,.10),0 30px 80px rgba(0,0,0,.6);background:#000}
.side{position:absolute;left:800px;top:70px;width:1000px;height:940px}
.top{display:flex;justify-content:space-between;align-items:baseline;font-size:26px;color:#9AA3B2;font-weight:600}
.num{font-size:30px;color:#fff;font-weight:800}.num span{color:#5C6577}
.chip{display:inline-block;margin-top:46px;padding:8px 18px;border-radius:999px;background:#FF3B30;
 color:#fff;font-size:26px;font-weight:800}
h1{margin-top:22px;font-size:64px;line-height:1.22;font-weight:850;word-break:keep-all;max-height:236px;overflow:hidden}
.meta{margin-top:20px;font-size:24px;color:#9AA3B2;font-weight:500}
.box{margin-top:34px;padding:26px 30px;border-radius:18px;background:rgba(255,255,255,.05)}
.box h3{font-size:24px;font-weight:800;color:#7CDB9A;margin-bottom:12px}
.box h3.no{color:#FFB547;margin-top:18px}
.box li{list-style:none;font-size:28px;line-height:1.42;font-weight:600;color:#E6E8EC;padding-left:26px;
 position:relative;word-break:keep-all}
.box li:before{content:'';position:absolute;left:4px;top:17px;width:9px;height:9px;border-radius:50%%;background:#5C6577}
.bar{position:absolute;left:0;right:0;bottom:62px;display:flex;gap:6px}
.bar i{flex:1;height:8px;border-radius:4px;background:#2A3140}.bar i.d{background:#8A93A3}.bar i.c{background:#FF3B30}
.next{position:absolute;left:0;right:0;bottom:0;font-size:24px;color:#9AA3B2;font-weight:600;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.next b{color:#fff;margin-right:12px}
""" % (VX, VY, VW, VH)


def story_html(day, i, n, item, nxt):
    spec = item["spec"]
    cover = spec["cards"][0]
    check = next((c for c in spec["cards"] if c["type"] == "check"), {})
    conf = [plain(x) for x in check.get("confirmed", [])][:2]
    unc = [plain(x) for x in check.get("unconfirmed", [])][:1]
    cat = item["category"] if item["category"] and item["category"] != "기타 사건" else "세계"
    box = ""
    if conf or unc:
        box = '<div class="box">%s%s</div>' % (
            ("<h3>확인된 것</h3><ul>%s</ul>" % "".join("<li>%s</li>" % esc(x) for x in conf)) if conf else "",
            ('<h3 class="no">아직 모르는 것</h3><ul>%s</ul>' % "".join("<li>%s</li>" % esc(x) for x in unc)) if unc else "")
    bar = "".join('<i class="%s"></i>' % ("c" if k == i else "d" if k < i else "") for k in range(n))
    nx = ('<div class="next"><b>다음</b>%s</div>' % esc(plain(nxt["spec"]["cards"][0].get("title")))) if nxt else \
        '<div class="next"><b>다음</b>오늘 정리 마무리</div>'
    src = re.sub(r"\s*보도 종합$", "", spec.get("source", ""))
    body = ('<div class="brand"><b></b>지금 세계</div><div class="frame"></div><div class="side">'
            '<div class="top"><div>%s 세계 뉴스 정리</div><div class="num">%02d <span>/ %02d</span></div></div>'
            '<div class="chip">%s</div><h1>%s</h1><div class="meta">%s · %s</div>%s'
            '<div class="bar">%s</div>%s</div>'
            % (kday(day), i + 1, n, esc(cat), esc(cover.get("title", spec["topic"])),
               esc(spec.get("time", "")), esc(src), box, bar, nx))
    return wrap(body, STORY_CSS, FIT_H1)


# 제목이 3줄을 넘으면 글자를 줄인다
FIT_H1 = ("document.fonts.ready.then(function(){var h=document.querySelector('h1');if(!h)return;"
          "var s=64;while(h.scrollHeight>h.clientHeight+1&&s>40){s-=4;h.style.fontSize=s+'px';}});")


INTRO_CSS = """
.l{position:absolute;left:150px;top:200px;width:760px}
.date{display:inline-block;padding:10px 22px;border-radius:999px;background:#FF3B30;font-size:34px;font-weight:800}
.l h1{margin-top:34px;font-size:120px;line-height:1.08;font-weight:900}
.l p{margin-top:34px;font-size:36px;color:#9AA3B2;font-weight:600}
.r{position:absolute;left:1000px;top:170px;width:780px}
.r li{list-style:none;display:flex;gap:22px;align-items:baseline;padding:18px 0;border-bottom:1px solid #222938;
 font-size:32px;font-weight:700;white-space:nowrap;overflow:hidden}
.r li b{color:#FF3B30;font-size:28px;min-width:44px}.r li span{overflow:hidden;text-overflow:ellipsis}
.r .more{font-size:28px;color:#9AA3B2;border:0}
"""


def intro_html(day, items, mins):
    rows = "".join("<li><b>%02d</b><span>%s</span></li>" % (k + 1, esc(plain(x["spec"]["cards"][0].get("title"))))
                   for k, x in enumerate(items[:7]))
    if len(items) > 7:
        rows += '<li class="more">외 %d건</li>' % (len(items) - 7)
    body = ('<div class="brand"><b></b>지금 세계</div><div class="l"><div class="date">%s</div>'
            '<h1>오늘의<br>세계 뉴스</h1><p>소식 %d건 · 약 %d분</p></div><ul class="r">%s</ul>'
            % (kday(day), len(items), mins, rows))
    return wrap(body, INTRO_CSS)


OUTRO_CSS = """
.c{position:absolute;left:0;right:0;top:330px;text-align:center}
.c h1{font-size:96px;font-weight:900}.c p{margin-top:30px;font-size:40px;color:#9AA3B2;font-weight:600}
.c .sub{display:inline-block;margin-top:60px;padding:20px 44px;border-radius:999px;background:#FF3B30;font-size:40px;font-weight:800}
"""


def outro_html(day):
    body = ('<div class="brand"><b></b>지금 세계</div><div class="c"><h1>%s 정리는 여기까지</h1>'
            '<p>세계 사건·사고, 확인된 것만 가장 빠르게</p><div class="sub">구독하고 매일 받아보기</div></div>'
            % kday(day))
    return wrap(body, OUTRO_CSS)


THUMB_CSS = """
html,body{width:1280px;height:720px}
.t{position:absolute;left:60px;top:80px;width:660px}
.d{display:inline-block;padding:10px 24px;border-radius:12px;background:#FF3B30;font-size:42px;font-weight:900}
.t h1{margin-top:30px;font-size:82px;line-height:1.14;font-weight:900;word-break:keep-all}
.t p{margin-top:28px;font-size:42px;font-weight:800;color:#FFD84D}
.cards{position:absolute;left:760px;top:40px;width:500px;height:640px}
.cards div{position:absolute;width:300px;height:533px;border-radius:22px;background-size:cover;background-position:center;
 box-shadow:0 0 0 4px rgba(255,255,255,.14),0 24px 60px rgba(0,0,0,.7)}
.cards .a{left:180px;top:70px;transform:rotate(5deg)}
.cards .b{left:0;top:20px;transform:rotate(-4deg)}
"""


def thumb_html(day, frames, line1, n):
    cards = "".join('<div class="%s" style="background-image:url(&quot;%s&quot;)"></div>' % (k, u)
                    for k, u in zip("ab", reversed(frames[:2])))
    body = ('<div class="cards">%s</div><div class="t"><div class="d">%s 세계 뉴스</div><h1>%s</h1>'
            '<p>외 %d건 한 번에 정리</p></div>' % (cards, kday(day), esc(line1), n - 1))
    return wrap(body, THUMB_CSS)


# ────────────────────────────────────────────── 영상
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def story_clip(short, panel, out):
    d = duration(short)
    vf = ("[0:v]scale=%d:%d,setsar=1[s];[1:v][s]overlay=%d:%d,format=yuv420p,"
          "fade=t=in:st=0:d=%.2f,fade=t=out:st=%.2f:d=%.2f[v]" % (VW, VH, VX, VY, FADE, d - FADE, FADE))
    af = "[0:a]aresample=48000,afade=t=in:st=0:d=0.15,afade=t=out:st=%.2f:d=%.2f[a]" % (d - FADE, FADE)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(short), "-loop", "1", "-i", str(panel),
         "-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]", "-t", "%.3f" % d] + ENC + [str(out)])
    return d


def card_clip(png, text, out, work, name, hold=1.0):
    """정지 화면 + 나레이션 (쇼츠와 같은 선희 목소리)."""
    sys.path.insert(0, str(ROOT))
    from video import tts
    wav = Path(work) / (name + ".wav")
    res = tts.synthesize_tight(text, VOICE, wav, rate=RATE)
    d = res.duration + 0.5 + hold
    run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(png), "-i", str(wav),
         "-filter_complex", "[0:v]format=yuv420p,fade=t=in:st=0:d=%.2f,fade=t=out:st=%.2f:d=%.2f[v];"
         "[1:a]aresample=48000,adelay=500|500,apad,loudnorm=I=-16:TP=-1.5:LRA=11[a]" % (FADE, d - FADE, FADE),
         "-map", "[v]", "-map", "[a]", "-t", "%.3f" % d] + ENC + [str(out)])
    return d


def fetch(url, dst):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        Path(dst).write_bytes(r.read())
    return dst


def chapter_title(item):
    t = plain(item["spec"]["cards"][0].get("title")) or item["spec"]["topic"]
    return t if len(t) <= 40 else t[:39] + "…"


def build(day):
    items = pick(day)
    work = Path(make_cards.work_dir())
    got = []
    for x in items:
        dst = work / (x["slug"] + ".mp4")
        try:
            fetch("%s/%s/video.mp4" % (MEDIA_BASE, x["slug"]), dst)
            x["short"] = dst
            got.append(x)
        except Exception as ex:
            print("영상 없음 — 건너뜀", x["slug"], ex)
    if len(got) < 3:
        raise SystemExit("영상이 %d편뿐이라 롱폼을 만들지 않습니다" % len(got))
    items = order(got)
    n = len(items)
    est = sum(duration(x["short"]) for x in items) + 20
    outdir = ROOT / "output" / "daily" / day
    outdir.mkdir(parents=True, exist_ok=True)

    clips, chapters, t = [], [], 0.0
    lead = "·".join(plain(x["spec"]["topic"]) for x in items[:2])
    intro_png = shoot(intro_html(day, items, max(1, round(est / 60))), work / "intro.png", work, 0)
    say = "%s, 지금 세계 하루 정리입니다. %s 등, 오늘 소식 %d건을 이어서 전해 드립니다." % (kday(day), lead, n)
    d = card_clip(intro_png, say, work / "c000.mp4", work, "intro")
    clips.append(work / "c000.mp4")
    chapters.append((0, "오늘의 소식 %d건" % n))
    t += d
    for i, x in enumerate(items):
        png = shoot(story_html(day, i, n, x, items[i + 1] if i + 1 < n else None), work / ("p%03d.png" % (i + 1)),
                    work, i + 1)
        out = work / ("c%03d.mp4" % (i + 1))
        d = story_clip(x["short"], png, out)
        chapters.append((t, chapter_title(x)))
        clips.append(out)
        t += d
    outro_png = shoot(outro_html(day), work / "outro.png", work, 999)
    d = card_clip(outro_png, "오늘 정리는 여기까지입니다. 내일도 확인된 소식만 모아 전해 드리겠습니다.",
                  work / "c999.mp4", work, "outro", hold=1.5)
    clips.append(work / "c999.mp4")
    chapters.append((t, "마무리"))
    t += d

    lst = work / "list.txt"
    lst.write_text("".join("file '%s'\n" % Path(c).as_posix() for c in clips), encoding="utf-8")
    joined = work / "joined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    final = outdir / "video.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-c:v", "copy", "-af",
         "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", str(final)])

    # 썸네일: 첫 소식 쇼츠의 표지 장면(자료 사진 + 궁금증 제목)을 배경으로
    frames = []
    for k, x in enumerate(items[:2]):
        f = work / ("thumb%d.jpg" % k)
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.2", "-i", str(x["short"]), "-frames:v", "1", str(f)])
        frames.append(make_cards.file_url(f))
    thumb = shoot(thumb_html(day, frames, plain(items[0]["spec"]["topic"]), n), work / "thumb.png", work, 1000)
    from PIL import Image
    Image.open(thumb).crop((0, 0, 1280, 720)).convert("RGB").save(outdir / "thumb.jpg", quality=90)

    top = [plain(x["spec"]["topic"]) for x in items[:2]]
    title = "%s 세계 뉴스 %d건 | %s" % (kday(day), n, " · ".join(top))
    if len(title) > 95:
        title = "%s 세계 뉴스 %d건 | %s" % (kday(day), n, top[0])[:95]
    desc = ("%s 하루 동안 지금 세계가 전한 해외 소식 %d건을 한 번에 정리했습니다.\n"
            "각 소식은 여러 해외 매체 보도를 교차 확인한 내용이며, 확인되지 않은 부분은 따로 표시했습니다.\n\n"
            % (kday(day), n) +
            "\n".join("%s %s" % (ts(a), b) for a, b in chapters) +
            "\n\n출처: 각 소식별 BBC·로이터·AP 등 해외 매체 보도 종합\n\n" +
            " ".join("#" + x for x in TAGS))
    meta = {"day": day, "title": title, "description": desc, "chapters": chapters, "duration": round(t, 1),
            "slugs": [x["slug"] for x in items], "views": {x["slug"]: x["views"] for x in items}}
    (outdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, p in [("intro", intro_png), ("story01", work / "p001.png"), ("outro", outro_png)]:
        os.replace(p, outdir / ("%s.png" % k))
    print("LONGFORM %s (%.1f초, %d건)\n%s\n%s" % (final, t, n, title, desc))
    return final, meta


# ────────────────────────────────────────────── 업로드
def yt_access():
    data = urllib.parse.urlencode({"client_id": os.environ["YOUTUBE_CLIENT_ID"],
                                   "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
                                   "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
                                   "grant_type": "refresh_token"}).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def upload(final, meta, privacy):
    tok = yt_access()
    auth = {"Authorization": "Bearer " + tok}
    body = {"snippet": {"title": meta["title"], "description": meta["description"][:4900],
                        "tags": TAGS + ["세계뉴스정리", kday(meta["day"]).replace(" ", "")], "categoryId": "25",
                        "defaultLanguage": "ko", "defaultAudioLanguage": "ko"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    video = Path(final).read_bytes()
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps(body).encode(), method="POST", headers=dict(auth, **{
            "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(len(video))}))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            loc = r.headers["Location"]
        req = urllib.request.Request(loc, data=video, method="PUT", headers=dict(auth, **{"Content-Type": "video/mp4"}))
        with urllib.request.urlopen(req, timeout=900) as r:
            vid = json.loads(r.read())["id"]
    except urllib.error.HTTPError as ex:
        raise SystemExit("업로드 실패 %d %s" % (ex.code, ex.read()[:400]))
    print("YOUTUBE 롱폼 올림 https://youtu.be/%s (%s)" % (vid, privacy))
    thumb = Path(final).parent / "thumb.jpg"
    try:   # 썸네일은 전화번호 인증된 채널만 된다 — 안 되면 유튜브 자동 썸네일
        req = urllib.request.Request("https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId=" + vid,
                                     data=thumb.read_bytes(), method="POST",
                                     headers=dict(auth, **{"Content-Type": "image/jpeg"}))
        with urllib.request.urlopen(req, timeout=60):
            print("YOUTUBE 썸네일 올림")
    except urllib.error.HTTPError as ex:
        print("YOUTUBE 썸네일 실패 %d %s" % (ex.code, ex.read()[:200]))
    return vid


def main():
    a = sys.argv[1:]
    day = a[a.index("--date") + 1] if "--date" in a else \
        (datetime.datetime.now(KST) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    done = json.loads(DAILY.read_text(encoding="utf-8")) if DAILY.exists() else {}
    if "--upload" in a and day in done and "--force" not in a:
        print("이미 올림", day, done[day])
        return
    final, meta = build(day)
    if "--upload" in a:
        privacy = os.environ.get("DAILY_PRIVACY") or "private"
        vid = upload(final, meta, privacy)
        done[day] = {"id": vid, "privacy": privacy, "at": int(time.time()), "title": meta["title"]}
        DAILY.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
