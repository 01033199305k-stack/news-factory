"""Final assembly: cross-faded video, concatenated narration, burned-in
overlay, ducked music bed, loudness-normalised 9:16 H.264 out.

Note on timing: each clip except the last is rendered `transition` seconds
longer than its narration segment, and every xfade consumes exactly that
overlap, so the faded timeline lands back on the narration length.
"""
from pathlib import Path

from video.util import ffprobe_duration, run

FPS = 30


def _filter_path(p: Path) -> str:
    """Escape a Windows path for use *inside* a filtergraph argument."""
    return str(Path(p).resolve()).replace("\\", "/").replace(":", "\\:")


# Cycled so consecutive joins do not look identical. A single repeated
# transition is what makes an edit read as a template.
TRANSITION_CYCLE = ["fade", "smoothleft", "zoomin", "smoothright"]


def concat_videos(
    clip_paths: list[Path],
    out_path: Path,
    transition: float = 0.0,
    cycle: list[str] | None = None,
) -> Path:
    """transition=0 concatenates; >0 cross-fades each join by that many seconds."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clips = [Path(p) for p in clip_paths]

    if len(clips) == 1:
        run(["ffmpeg", "-y", "-i", str(clips[0]), "-c", "copy", str(out_path)])
        return out_path

    if transition <= 0:
        list_file = out_path.with_suffix(".txt")
        list_file.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in clips), encoding="utf-8")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)])
        list_file.unlink(missing_ok=True)
        return out_path

    lengths = [ffprobe_duration(p) for p in clips]
    cmd = ["ffmpeg", "-y"]
    for p in clips:
        cmd += ["-i", str(p)]

    kinds = cycle or TRANSITION_CYCLE
    steps, prev, acc = [], "[0:v]", lengths[0]
    for i in range(1, len(clips)):
        label = f"[vx{i}]"
        offset = max(0.0, acc - transition)
        kind = kinds[(i - 1) % len(kinds)]
        steps.append(
            f"{prev}[{i}:v]xfade=transition={kind}:duration={transition:.3f}:offset={offset:.3f}{label}"
        )
        acc = acc + lengths[i] - transition
        prev = label

    graph = ";".join(steps) + f";{prev}format=yuv420p,fps={FPS}[v]"
    cmd += ["-filter_complex", graph, "-map", "[v]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-an",
            str(out_path)]
    run(cmd)
    return out_path


def concat_audio(audio_paths: list[Path], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = out_path.with_suffix(".txt")
    list_file.write_text(
        "\n".join(f"file '{Path(p).resolve().as_posix()}'" for p in audio_paths), encoding="utf-8"
    )
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)])
    list_file.unlink(missing_ok=True)
    return out_path


def mux_final(
    video_path: Path,
    narration_path: Path,
    ass_path: Path,
    out_path: Path,
    bgm_path: Path | None = None,
    fonts_dir: Path | None = None,
    sfx: list[tuple[float, Path]] | None = None,
    bgm_gain_db: float = -16.0,
    sfx_gain_db: float = -9.0,
) -> Path:
    """sfx: (time_seconds, file) one-shots dropped onto the timeline, e.g. a
    whoosh on each cut. Missing files are skipped rather than failing a render."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sub = f"subtitles='{_filter_path(ass_path)}'"
    if fonts_dir and Path(fonts_dir).exists():
        sub += f":fontsdir='{_filter_path(fonts_dir)}'"

    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-i", str(narration_path)]
    steps: list[str] = []
    mix_labels: list[str] = []
    idx = 2

    has_bgm = bool(bgm_path and Path(bgm_path).exists())
    if has_bgm:
        cmd += ["-stream_loop", "-1", "-i", str(bgm_path)]
        # Music is keyed off the narration itself, so it lifts in the gaps and
        # pulls back under speech instead of sitting at one flat level.
        steps.append("[1:a]asplit=2[nar][key]")
        steps.append(f"[{idx}:a]volume={bgm_gain_db}dB[bed]")
        steps.append(
            "[bed][key]sidechaincompress=threshold=0.03:ratio=12:attack=15:release=350[duck]"
        )
        mix_labels += ["[nar]", "[duck]"]
        idx += 1
    else:
        mix_labels.append("[1:a]")

    for n, (at, path) in enumerate(sfx or []):
        if not Path(path).exists():
            continue
        cmd += ["-i", str(path)]
        ms = max(0, int(at * 1000))
        steps.append(f"[{idx}:a]adelay={ms}|{ms},volume={sfx_gain_db}dB[sx{n}]")
        mix_labels.append(f"[sx{n}]")
        idx += 1

    if len(mix_labels) > 1:
        steps.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0[mix]"
        )
        steps.append("[mix]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    else:
        steps.append(f"{mix_labels[0]}loudnorm=I=-14:TP=-1.5:LRA=11[a]")

    graph = f"[0:v]{sub}[v];" + ";".join(steps)
    cmd += ["-filter_complex", graph,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
            # loudnorm 은 내부적으로 192kHz 로 올려 둔다 — 인스타 릴스는 48kHz 까지만 받으므로 되돌린다
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", "-shortest",
            str(out_path)]
    run(cmd)
    return out_path
