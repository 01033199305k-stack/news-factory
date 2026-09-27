# -*- coding: utf-8 -*-
"""
카드뉴스 spec → 45초 안팎 세로 숏폼 (1080×1920 mp4). 여자 아나운서(SunHi) 나레이션 + 단어 강조 자막.

    python make_video.py specs/auto/<slug>.json   # → output/<slug>/video.mp4
    python make_video.py --queue                  # 게시 대기열에서 영상이 아직 없는 항목 전부

- 나레이션 원고는 spec["narration"] (없으면 카드 문구로 만든다 — narration.py)
- 화면은 make_cards 가 그린 카드 PNG. 없으면 spec 으로 다시 그린다
- 영상 모듈(video/)은 shorts-factory/pipeline 에서 가져왔다. 필요한 것: ffmpeg, edge-tts
"""
import json
import os
import re
import sys
from pathlib import Path

import make_cards
import narration

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "state" / "queue.json"
SPECDIR = ROOT / "specs" / "auto"
FONTS = ROOT / "vendor" / "fonts"

VOICE = "ko-KR-SunHiNeural"   # 여자 아나운서
RATE = "+0%"                  # 뉴스는 또박또박
TRANSITION = 0.25
CARD_FIT, CARD_LIFT, ZOOM, BG = 0.8, 320, 1.06, "#0B0E14"   # 카드 아래(y≈1180~1370)가 자막 자리
BRAND = {"accent": "#FF3B30", "ink": "#0B0E14", "handle": "@jigeum.segye"}
CTA = "지금 세계 팔로우"


def card_files(outdir):
    files = {}
    for p in sorted(Path(outdir).glob("*.png")):
        m = re.match(r"\d\d_(\w+)\.png$", p.name)
        if m:
            files.setdefault(m.group(1), p)
    return files


def render(spec):
    from video import assemble, captions, kenburns, tts
    from video.tts import Word

    outdir = ROOT / "output" / spec["slug"]
    files = card_files(outdir)
    if "cover" not in files:  # 클라우드 다음 실행 등으로 카드가 없으면 다시 그린다
        make_cards.render(json.loads(json.dumps(spec)))
        files = card_files(outdir)

    segs = spec.get("narration") or narration.from_cards(spec)
    work = outdir / "_video"
    work.mkdir(parents=True, exist_ok=True)

    clips, wavs, words, cursor = [], [], [], 0.0
    last = len(segs) - 1
    for i, seg in enumerate(segs):
        wav = work / ("seg_%02d.wav" % i)
        res = tts.synthesize_tight(seg["text"], VOICE, wav, rate=RATE)
        wavs.append(wav)
        words.append([Word(w.text, w.start + cursor, w.end + cursor) for w in res.words])
        cursor += res.duration
        clip = work / ("clip_%02d.mp4" % i)
        card = files.get(seg["card"]) or files["cover"]
        kenburns.make_clip(card, clip, res.duration + (TRANSITION if i < last else 0.0),
                           zoom="in" if i % 2 == 0 else "out", zoom_target=ZOOM,
                           fit=CARD_FIT, lift=CARD_LIFT, bg_color=BG)
        clips.append(clip)

    video = assemble.concat_videos(clips, work / "video.mp4", transition=TRANSITION)
    voice = assemble.concat_audio(wavs, work / "narration.wav")
    ass = captions.build_ass(
        words, work / "captions.ass", cta_text=CTA, handle=BRAND["handle"],
        total_duration=cursor, brand_accent=BRAND["accent"], brand_ink=BRAND["ink"],
        font="Black Han Sans", fontsize=72, max_words=3,
    )
    final = outdir / "video.mp4"
    assemble.mux_final(video, voice, ass, final, fonts_dir=FONTS)
    print("VIDEO %s (%.1fs, %d문장)" % (final, cursor, len(segs)))
    return final


def run_queue():
    q = json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else []
    changed = False
    for item in q:
        if "video" in item:
            continue
        try:
            spec = json.loads((SPECDIR / (item["slug"] + ".json")).read_text(encoding="utf-8"))
            render(spec)
            item["video"] = "video.mp4"
        except Exception as ex:  # 영상이 실패해도 카드 게시는 그대로 간다
            print("VIDEO FAIL", item["slug"], ex)
            item["video"] = None
        changed = True
    if changed:
        QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--queue" in a:
        run_queue()
    elif a:
        render(json.loads(Path(a[0]).read_text(encoding="utf-8")))
    else:
        print(__doc__)
