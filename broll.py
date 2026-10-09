# -*- coding: utf-8 -*-
"""
숏폼 장면 뒤에 까는 자료 사진·자료 화면.

- 현지 풍경 사진: 위키미디어 커먼즈(키 필요 없음). 사건 장소(place_query)의 '추천 사진'·'우수 사진'만 쓴다.
  CC BY·BY-SA 는 저작자 표시가 조건이라 화면 구석에 "자료 사진 · 작성자 · 라이선스" 를 늘 띄운다
- 자료 화면(영상): Pixabay·Pexels (PIXABAY_API_KEY / PEXELS_API_KEY 가 있을 때만). 사건과 같은 종류의
  장소·사물(유조선, 기차역 등). 실제 사건 영상처럼 보이면 안 되니 "자료 화면" 표시를 늘 띄운다

어느 것도 못 구하면 None — 그 장면은 지구본·글자 카드로 간다 (영상 실패보다 자료 없는 영상이 낫다).
헤드리스 촬영은 <video> 재생을 프레임 단위로 맞출 수 없어서, 영상은 JPEG 프레임으로 풀어 한 장씩 바꿔 끼운다.
"""
import hashlib
import html as _html
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

W, H, FPS = 1080, 1920, 30
UA = "news-factory/1.0 (https://github.com/01033199305k-stack/news-factory)"
CACHE = Path.home() / ".cache" / "news-factory" / "broll"

# 사건 사진이 아니라 '그곳 풍경'만 쓴다. 전쟁·재난 사진은 지금 사건으로 오해된다. 인물·지도·문장(紋章)도 뺀다
_BAD_PHOTO = re.compile(
    r"\b(map|flag|coat of arms|logo|emblem|seal|diagram|chart|plan|svg|portrait|president|minister|"
    r"people|crowd|protest|demonstration|funeral|victim|soldier|army|military|war|battle|bomb|attack|"
    r"destroyed|damage|ruins|fire|flood|earthquake|explosion|accident|crash|police|prison|grave|cemetery|"
    r"memorial|church interior|museum exhibit|painting|drawing|engraving|stamp|coin|banknote|1[0-9]{3}s?)\b", re.I)
_OK_LICENSE = re.compile(r"^(CC0|Public domain|PD|CC BY(-SA)? [0-9.]+)", re.I)


def _get(url, timeout=25):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
        return r.read()


def _text(s):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _fold(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower()


def _commons(search, n=12):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": search,
        "gsrnamespace": 6, "gsrlimit": n, "prop": "imageinfo",
        "iiprop": "url|size|extmetadata|mime", "iiurlwidth": 1600})
    d = json.loads(_get(url))
    return sorted(d.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))


def place_photos(place_query, out_dir, n=2):
    """사건 장소의 풍경 사진 n 장. [{"type":"photo","src":경로,"credit":"자료 사진 · ..."}]"""
    place_query = (place_query or "").strip()
    if not place_query:
        return []
    head = place_query.split(",")[0].strip()          # "Lviv, Ukraine" → "Lviv"
    words = [_fold(w) for w in re.findall(r"[A-Za-z][A-Za-z.'-]+", head) if len(w) > 2]
    out, seen = [], set()
    # "Washington, D.C." 는 통째로 먼저 (머리말 "Washington" 만 찾으면 워싱턴주 사진이 섞인다)
    names = [place_query] + ([head] if head != place_query else [])
    tries = [(nm, cat) for nm in names
             for cat in ('"Featured pictures on Wikimedia Commons"', '"Quality images"')]
    for nm, cat in tries:
        if len(out) >= n:
            break
        try:
            pages = _commons('"%s" filetype:bitmap incategory:%s' % (nm, cat))
        except Exception as ex:
            print("  broll: commons search failed", nm, ex)
            return out
        for p in pages:
            if len(out) >= n:
                break
            ii = (p.get("imageinfo") or [{}])[0]
            m = ii.get("extmetadata") or {}
            title = p.get("title", "")
            lic = _text((m.get("LicenseShortName") or {}).get("value"))
            desc = _text((m.get("ImageDescription") or {}).get("value"))
            cats = _text((m.get("Categories") or {}).get("value"))
            about = _fold(" ".join((title, desc[:400], cats)))
            if title in seen or ii.get("mime") not in ("image/jpeg", "image/png"):
                continue
            if ii.get("width", 0) < 1400 or ii.get("height", 0) < 900:
                continue
            if not _OK_LICENSE.match(lic) or _BAD_PHOTO.search(title + " " + desc[:200]):
                continue
            if words and not any(w in about for w in words):   # 다른 곳 사진이 검색에 섞여 나온다
                continue
            try:
                CACHE.mkdir(parents=True, exist_ok=True)
                dst = CACHE / ("c_%s.jpg" % hashlib.md5(title.encode("utf-8")).hexdigest()[:16])
                if not dst.exists():
                    dst.write_bytes(_get(ii.get("thumburl") or ii["url"], timeout=60))
                local = Path(out_dir) / ("photo_%d.jpg" % len(out))
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(dst.read_bytes())
            except Exception as ex:
                print("  broll: photo download failed", title, ex)
                continue
            seen.add(title)
            artist = _text((m.get("Artist") or {}).get("value"))[:40] or "Wikimedia Commons"
            out.append({"type": "photo", "src": str(local),
                        "credit": "자료 사진 · %s · %s · Wikimedia Commons" % (artist, lic)})
            print("  broll: photo", title[:70], "|", lic)
    return out


# ── 자료 화면 (Pixabay·Pexels) — issue-shorts/stock.py 와 같은 거름 규칙 ──
CHROMA = ("green screen", "greenscreen", "chroma", "blue screen", "alpha channel", "transparent")
FAKE = re.compile(r"\b(ai[ -]?generated|generative|anthropomorphic|animation|animated|cartoon|3d|render(ing)?|cgi)\b")
ANIMAL = re.compile(r"\b(animals?|owls?|birds?|cats?|kittens?|dogs?|puppy|monkeys?|bears?|fox(es)?|rabbits?|wildlife|pets?)\b")
# 뉴스 자료 화면으로 쓰면 안 되는 것: 실제 피해 장면처럼 보이는 것, 사람이 다치는 것
GRIM = re.compile(r"\b(blood|corpse|dead|body|bodies|injur\w*|wound\w*|gun|shoot\w*|weapon|bomb|explosion|"
                  r"war|soldier|riot|fight\w*|crash\w*|accident)\b")


def _search_pixabay(query, key):
    url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(
        {"key": key, "q": query, "per_page": 10, "safesearch": "true"})
    hits = json.loads(_get(url)).get("hits", [])
    out = []
    for h in hits:
        files = [{"file_type": "video/mp4", "width": f.get("width", 0), "link": f["url"]}
                 for f in (h.get("videos") or {}).values() if f.get("url")]
        out.append({"id": "pb%s" % h["id"], "duration": h.get("duration", 0), "url": h.get("pageURL", ""),
                    "tags": h.get("tags", ""), "src": "Pixabay", "video_files": files})
    return out


def _search_pexels(query, key):
    url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
        {"query": query, "size": "medium", "per_page": 10})
    req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        vids = json.loads(r.read()).get("videos", [])
    for v in vids:
        v["src"] = "Pexels"
    return vids


def _describe(v):
    slug = (v.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
    return ("%s %s" % (v.get("tags", ""), slug.replace("-", " "))).lower()


def _relevant(v, query):
    desc = _describe(v)
    head = query.split()[0].lower()
    if FAKE.search(desc) or GRIM.search(desc) or (ANIMAL.search(desc) and not ANIMAL.search(query.lower())):
        return False
    return head in desc and not any(c in desc for c in CHROMA)


def _best_file(v):
    files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
    files = [f for f in files if f["width"] >= 960] or files
    return min(files, key=lambda f: abs(f["width"] - 1280)) if files else None


def _keyed(frame):
    from PIL import Image
    with Image.open(frame) as im:
        px = list(im.convert("RGB").resize((48, 32)).getdata())
    keyed = sum(1 for r, g, b in px if (g > 140 and g > r + 60 and g > b + 60) or (b > 140 and b > r + 60 and b > g + 40))
    return keyed > 0.3 * len(px)


def has_stock_key():
    return bool(os.environ.get("PIXABAY_API_KEY", "").strip() or os.environ.get("PEXELS_API_KEY", "").strip())


def stock_clip(query, seconds, out_dir, used):
    """검색어에 맞는 자료 화면을 세로(1080×1920)로 잘라 프레임으로 푼다. 못 구하면 None."""
    query = re.sub(r"[^A-Za-z0-9 ]", " ", query or "").strip()
    if not query or not has_stock_key() or GRIM.search(query.lower()):
        return None
    vids = []
    for env, search in (("PIXABAY_API_KEY", _search_pixabay), ("PEXELS_API_KEY", _search_pexels)):
        key = os.environ.get(env, "").strip()
        if not key:
            continue
        try:
            vids = [v for v in search(query, key) if _relevant(v, query)]
        except Exception as ex:
            print("  broll: stock search failed", repr(query), ex)
        if vids:
            break
    for v in vids:
        if v["id"] in used or v.get("duration", 0) < 3:
            continue
        f = _best_file(v)
        if not f:
            continue
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            src = CACHE / ("%s_%s.mp4" % (v["id"], f["width"]))
            if not src.exists():
                src.write_bytes(_get(f["link"], timeout=90))
            d = Path(out_dir)
            d.mkdir(parents=True, exist_ok=True)
            for old in d.glob("f_*.jpg"):
                old.unlink()
            dur = min(seconds + 0.4, max(1.0, v["duration"] - 0.6))
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.3", "-t", "%.2f" % dur, "-i", str(src),
                 "-vf", "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,fps=%d" % (W, H, W, H, FPS),
                 "-q:v", "4", str(d / "f_%04d.jpg")], check=True)
        except Exception as ex:
            print("  broll: clip failed", v["id"], ex)
            continue
        frames = sorted(Path(out_dir).glob("f_*.jpg"))
        if frames and _keyed(frames[len(frames) // 2]):
            for x in frames:
                x.unlink()
            continue
        if frames:
            used.add(v["id"])
            print("  broll: clip", v["id"], repr(query), len(frames), "frames")
            return {"type": "clip", "dir": str(out_dir), "n": len(frames),
                    "credit": "자료 화면 · %s" % v.get("src", "")}
    print("  broll: no usable clip for", repr(query))
    return None


def attach(scenes, spec, work):
    """배경이 필요한 장면(표지·사실 카드)에 자료를 하나씩 붙인다. 순서: 자료 화면 → 현지 사진.
    같은 자료를 두 장면에 쓰지 않는다. 못 붙인 장면은 그대로(지구본·글자 카드)."""
    want = [s for s in scenes if s["kind"] == "cover" or (s["kind"] == "point" and s.get("layout") == "fact")]
    if not want:
        return scenes
    queries = [q for q in (spec.get("broll") or []) if q][:2]
    used, pool = set(), []
    for n, q in enumerate(queries):
        if len(pool) >= len(want):
            break
        # 자료 화면 길이는 붙일 장면 길이에 맞춘다 (모자라면 영상 끝에서 되감아 이어 붙인다)
        s = want[len(pool)]
        clip = stock_clip(q, s["end"] - s["start"] + 0.5, Path(work) / ("clip_%d" % n), used)
        if clip:
            pool.append(clip)
    if len(pool) < len(want):
        try:
            pool += place_photos(spec.get("place_en"), Path(work), n=len(want) - len(pool))
        except Exception as ex:
            print("  broll: photos failed", ex)
    for s, m in zip(want, pool):
        s["media"] = m
    return scenes
