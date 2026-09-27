"""Narration backends. Both return the same TTSResult (audio + word timings)
so captions work identically whichever engine a channel picks.

- "edge": Microsoft neural voices. Best Korean quality, but the endpoint
  carries no commercial use rights - fine for drafts, a risk on a monetised
  channel. See README.
- "kokoro": Kokoro-82M, Apache 2.0, local. Free and clear for commercial use,
  English/Japanese and others, but has no Korean voices.

edge-tts emits WordBoundary events during streaming (offset/duration in
100-nanosecond units). We use those to drive karaoke-style captions and to
time Ken Burns / b-roll segment cuts to the narration.
"""
import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import edge_tts

TICKS_PER_SECOND = 10_000_000  # edge-tts offsets are in 100ns ticks


@dataclass
class Word:
    text: str
    start: float  # seconds
    end: float    # seconds


@dataclass
class TTSResult:
    audio_path: Path
    words: list[Word] = field(default_factory=list)
    duration: float = 0.0


async def _synthesize(text: str, voice: str, out_path: Path, rate: str, pitch: str) -> TTSResult:
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    words: list[Word] = []
    with open(out_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / TICKS_PER_SECOND
                dur = chunk["duration"] / TICKS_PER_SECOND
                words.append(Word(text=chunk["text"], start=start, end=start + dur))
    duration = words[-1].end if words else 0.0
    return TTSResult(audio_path=out_path, words=words, duration=duration)


def synthesize(text: str, voice: str, out_path: Path, rate: str = "+0%", pitch: str = "+0Hz") -> TTSResult:
    """Synchronous entry point. Writes an mp3 to out_path and returns word timings."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_synthesize(text, voice, out_path, rate, pitch))


_ESPEAK_MIRROR = Path("C:/ProgramData/espeak-ng")
_kokoro_pipelines: dict[str, object] = {}


def _ensure_ascii_espeak() -> None:
    """espeak-ng is a C library that cannot open a data path containing
    non-ASCII characters. When the Python install sits under a non-ASCII
    username (e.g. C:/Users/강민혁/...) phonemisation fails with a bare
    "No such file or directory", so mirror the data somewhere plain and point
    the loader at it. misaki reads these paths at import time, which is why
    the patch has to happen before kokoro is imported.
    """
    import shutil

    import espeakng_loader

    src_data = Path(espeakng_loader.get_data_path())
    src_lib = Path(espeakng_loader.get_library_path())
    if str(src_data).isascii():
        return

    dst_data = _ESPEAK_MIRROR / "espeak-ng-data"
    dst_lib = _ESPEAK_MIRROR / src_lib.name
    if not dst_data.exists():
        _ESPEAK_MIRROR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_data, dst_data)
    if not dst_lib.exists():
        shutil.copy2(src_lib, dst_lib)

    espeakng_loader.get_data_path = lambda: str(dst_data)
    espeakng_loader.get_library_path = lambda: str(dst_lib)


def _kokoro_pipeline(lang_code: str, device: str):
    if lang_code not in _kokoro_pipelines:
        _ensure_ascii_espeak()
        from kokoro import KPipeline
        _kokoro_pipelines[lang_code] = KPipeline(lang_code=lang_code, device=device)
    return _kokoro_pipelines[lang_code]


def synthesize_kokoro(
    text: str,
    voice: str,
    out_path: Path,
    speed: float = 1.0,
    lang_code: str = "a",
    device: str = "cpu",
) -> TTSResult:
    """device defaults to cpu: the istftnet stage hits a cuFFT failure on some
    CUDA builds, and at ~7x realtime on CPU the GPU buys nothing here."""
    import numpy as np
    import soundfile as sf

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pipeline = _kokoro_pipeline(lang_code, device)

    audio_parts, words, offset = [], [], 0.0
    for result in pipeline(text, voice=voice, speed=speed):
        chunk = result.audio.numpy() if hasattr(result.audio, "numpy") else np.asarray(result.audio)
        for tok in (getattr(result, "tokens", None) or []):
            start, end = getattr(tok, "start_ts", None), getattr(tok, "end_ts", None)
            label = (tok.text or "").strip()
            # Punctuation arrives as its own token; it would render as a
            # caption word of its own.
            if start is None or end is None or not any(c.isalnum() for c in label):
                continue
            words.append(Word(label, start + offset, end + offset))
        audio_parts.append(chunk)
        offset += len(chunk) / 24000

    audio = np.concatenate(audio_parts) if audio_parts else np.zeros(1, dtype="float32")
    sf.write(out_path, audio, 24000)
    return TTSResult(audio_path=out_path, words=words, duration=len(audio) / 24000)


def synthesize_tight(
    text: str,
    voice: str,
    out_wav: Path,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    head_pad: float = 0.05,
    tail_pad: float = 0.12,
    engine: str = "edge",
    speed: float = 1.0,
    lang_code: str = "a",
) -> TTSResult:
    """Synthesize, then strip the dead air edge-tts pads onto every utterance.

    Without this, concatenated segments leave ~1s of silence at every cut -
    a third of a short can end up silent. Word timestamps are shifted by the
    amount trimmed off the head so captions stay locked to the audio.
    """
    from video.util import ffprobe_duration, speech_bounds, to_wav

    out_wav = Path(out_wav)
    if engine == "kokoro":
        raw = out_wav.with_suffix(".raw.wav")
        res = synthesize_kokoro(text, voice, raw, speed=speed, lang_code=lang_code)
    else:
        raw = out_wav.with_suffix(".raw.mp3")
        res = synthesize(text, voice, raw, rate, pitch)

    if not res.words:
        to_wav(raw, out_wav)
        raw.unlink(missing_ok=True)
        return TTSResult(audio_path=out_wav, words=[], duration=ffprobe_duration(out_wav))

    raw_duration = ffprobe_duration(raw)
    audio_start, audio_end = speech_bounds(raw)

    # Reported word times run ahead of the rendered audio by a per-voice amount;
    # align word 0 onto the real onset so captions do not lead the voice.
    drift = audio_start - res.words[0].start

    start = max(0.0, audio_start - head_pad)
    end = min(raw_duration, audio_end + tail_pad)
    to_wav(raw, out_wav, start=start, end=end)
    raw.unlink(missing_ok=True)

    duration = ffprobe_duration(out_wav)
    # The drift shift can push the last word's end past the trimmed audio. A cue
    # that outlives its clip overlaps the next segment's first cue at the same
    # position and the two render as garbled text, so keep words inside the clip.
    shifted = [
        Word(w.text,
             min(max(0.0, w.start + drift - start), duration),
             min(max(0.0, w.end + drift - start), duration))
        for w in res.words
    ]
    return TTSResult(audio_path=out_wav, words=shifted, duration=duration)


if __name__ == "__main__":
    import sys
    text = sys.argv[1] if len(sys.argv) > 1 else "이 제품 진짜 대박이에요. 지금 바로 확인해보세요."
    voice = sys.argv[2] if len(sys.argv) > 2 else "ko-KR-SunHiNeural"
    res = synthesize(text, voice, Path("test_output/test.mp3"))
    print(f"duration={res.duration:.2f}s words={len(res.words)}")
    for w in res.words[:10]:
        print(f"  {w.start:.2f}-{w.end:.2f} {w.text}")
