"""Turns a still photo into a 9:16 Ken Burns clip.

Two things here are load-bearing and easy to get wrong:

1. zoompan's x/y default to 0 (top-left anchor), so an unqualified zoom drags
   the subject off toward the bottom-right. x/y are pinned to centre here.
2. Coupang product photos are square or landscape. A hard centre-crop to 9:16
   slices the sides off a wide product, so the frame is built as a blurred
   cover-fill plate with the untouched photo fitted (contain) on top.
"""
from pathlib import Path

from video.util import run

WIDTH, HEIGHT = 1080, 1920
FPS = 30
# zoompan renders from a 2x plate so the zoom does not resolve soft.
PW, PH = WIDTH * 2, HEIGHT * 2


def make_clip(
    image_path: Path,
    out_path: Path,
    duration: float,
    zoom: str = "in",
    zoom_target: float = 1.12,
    fit: float = 1.0,
    lift: int = 200,
    bg_color: str = "",
) -> Path:
    """zoom: 'in' | 'out'. zoom_target caps the move (1.12 = a 12% drift, which
    reads as deliberate; the old 1.6 looked like a mistake). lift raises the
    fitted photo off centre (px, 1080-space) to open up the caption zone - it
    is clamped so a full-height photo is never pushed off the top.
    bg_color ("#0B0E14") replaces the blurred plate with a flat colour - for
    cards whose own background is flat, so the card blends into the frame."""
    image_path, out_path = Path(image_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(2, round(duration * FPS))

    # Smoothstep the progress so the move eases in and out. A linear ramp is
    # the giveaway that a slideshow was generated rather than animated.
    t = f"(on/{frames})"
    eased = f"({t}*{t}*(3-2*{t}))"
    span = zoom_target - 1.0
    if zoom == "out":
        z = f"{zoom_target:.6f}-{span:.6f}*{eased}"
    else:
        z = f"1+{span:.6f}*{eased}"

    fit_w, fit_h = int(PW * fit), int(PH * fit)
    if bg_color:
        plate = f"drawbox=x=0:y=0:w=iw:h=ih:color=0x{bg_color.lstrip('#')}@1.0:t=fill"
    else:
        plate = "boxblur=60:2,eq=brightness=-0.10:saturation=0.75"
    filtergraph = (
        f"[0:v]scale={PW}:{PH}:force_original_aspect_ratio=increase,"
        f"crop={PW}:{PH},{plate}[bg];"
        f"[0:v]scale={fit_w}:{fit_h}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:'max(0,(H-h)/2-{lift * 2})'[comp];"
        f"[comp]zoompan=z='{z}'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},format=yuv420p[v]"
    )
    run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
        "-filter_complex", filtergraph, "-map", "[v]",
        "-t", f"{duration:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-an",
        str(out_path),
    ])
    return out_path
