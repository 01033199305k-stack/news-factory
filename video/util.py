import json
import subprocess
from pathlib import Path


def run(cmd: list[str]) -> None:
    """Run ffmpeg/ffprobe, surfacing stderr on failure instead of an opaque exit code."""
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-12:])
        raise RuntimeError(f"command failed ({proc.returncode}):\n{' '.join(cmd)}\n{tail}")


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def speech_bounds(path: Path, noise_db: int = -40, min_silence: float = 0.05) -> tuple[float, float]:
    """Return (first, last) time where audio is actually audible.

    edge-tts WordBoundary offsets do not line up with the rendered audio - the
    lead-in differs per voice (ko-KR-InJoonNeural starts ~0.28s later than it
    reports). Detecting the real onset lets us both trim accurately and correct
    the caption drift, instead of trusting the reported timings.
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-af", f"silencedetect=n={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    total = ffprobe_duration(path)
    spans: list[tuple[float, float]] = []
    start = None
    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].strip().split()[0])
        elif "silence_end:" in line and start is not None:
            spans.append((start, float(line.split("silence_end:")[1].split("|")[0].strip())))
            start = None

    first, last = 0.0, total
    for s, e in spans:
        if s <= 0.01:
            first = max(first, e)
        if e >= total - 0.02:
            last = min(last, s)
    if last <= first:
        return 0.0, total
    return first, last


def to_wav(src: Path, dst: Path, start: float | None = None, end: float | None = None) -> Path:
    """Decode to mono wav, optionally trimming to [start, end] seconds."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", str(src), "-ar", "44100", "-ac", "1", str(dst)]
    run(cmd)
    return dst
