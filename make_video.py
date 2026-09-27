# -*- coding: utf-8 -*-
"""
카드뉴스 spec → 45초 안팎 세로 숏폼 (1080×1920 mp4). 여자 아나운서(SunHi) 나레이션 + 단어 강조 자막.

    python make_video.py specs/auto/<slug>.json   # → output/<slug>/video.mp4
    python make_video.py --queue                  # 게시 대기열에서 영상이 아직 없는 항목 전부

- 나레이션 원고는 spec["narration"] (없으면 카드 문구로 만든다 — narration.py)
- 화면은 motion.py 가 그리는 세로 모션 그래픽 (장면·자막 모두 HTML → 크롬 프레임 촬영)
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
RATE = "+8%"                  # 또박또박하되 늘어지지 않게 (+0% 은 55초까지 늘어났다)
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


def write_srt(cues, path):
    """문장 단위 자막 파일. 유튜브에 한국어 자막 트랙으로 올리면 시청자가 자동 번역으로 볼 수 있다."""
    def ts(t):
        ms = int(round(t * 1000))
        return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)
    Path(path).write_text("".join("%d\n%s --> %s\n%s\n\n" % (i + 1, ts(a), ts(b), text.strip())
                                  for i, (a, b, text) in enumerate(cues)), encoding="utf-8")


GAP = 0.16   # 문장 사이 숨 — 붙여 읽으면 기계 같다


def render(spec):
    """나레이션 → 모션 그래픽(motion.py) → mp4. 화면 요소는 문장이 시작하는 순간 들어온다."""
    import subprocess

    import motion
    from video import assemble, tts
    from video.tts import Word

    outdir = ROOT / "output" / spec["slug"]
    outdir.mkdir(parents=True, exist_ok=True)
    srcs = [x for c in spec["cards"] if c["type"] == "outro"
            for x in c.get("sources", []) if not x.endswith("기준")]
    segs = narration.refresh(spec.get("narration") or narration.from_cards(spec), srcs)
    work = Path(make_cards.work_dir())   # 영문 경로 — 크롬이 한글 경로에서 실패한다

    wavs, words, times, cursor = [], [], [], 0.0
    for i, seg in enumerate(segs):
        raw = work / ("seg_%02d.wav" % i)
        res = tts.synthesize_tight(seg["text"], VOICE, raw, rate=RATE)
        wav = work / ("pad_%02d.wav" % i)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
                        "-af", "apad=pad_dur=%.2f" % GAP, str(wav)], check=True)
        times.append((round(cursor, 3), round(cursor + res.duration, 3)))
        words.append([Word(w.text, w.start + cursor, w.end + cursor) for w in res.words])
        wavs.append(wav)
        cursor += res.duration + GAP
    total = cursor + motion.TAIL

    voice = assemble.concat_audio(wavs, work / "narration.wav")
    page = work / "motion.html"
    page.write_text(motion.build_html(spec, segs, times, words, total), encoding="utf-8")
    silent = motion.capture(page, work / "silent.mp4", total)
    final = outdir / "video.mp4"
    motion.mux(silent, voice, final, total)
    write_srt([(a, b, s["text"]) for (a, b), s in zip(times, segs)], outdir / "captions.srt")
    print("VIDEO %s (%.1fs, %d문장)" % (final, total, len(segs)))
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
