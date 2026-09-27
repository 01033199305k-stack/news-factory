r"""Builds the .ass overlay: word-highlight captions, hook card, price badge
and end CTA - all themed from the channel's brand colours.

Two rules drive the layout:

* Captions are grouped **per segment**. Grouping a flat word list would let a
  cue straddle a cut, so a caption would hang over a picture change.
* Everything stays inside a safe box of x 200..880, y 180..1450. Below y~1450
  the Shorts title/description sits, and right of x~880 is the
  like/comment/share rail - text there is covered on a real phone.

Sentence-end punctuation is deliberately NOT used to split cues: edge-tts
strips punctuation from WordBoundary text ("대박이에요." arrives as
"대박이에요"), so any endswith(".") test silently never fires.
"""
from dataclasses import dataclass
from pathlib import Path

from video.tts import Word

MAX_WORDS_PER_CUE = 3

SIDE_MARGIN = 200
CAPTION_MARGIN_V = 550   # caption block ends ~y1370 - headroom for a 3-line number-glue cue
HOOK_MARGIN_V = 200      # from the top (Alignment 8)
BADGE_MARGIN_V = 210
CTA_MARGIN_V = 500       # takes over the caption slot; captions are muted there

# Each cue springs in slightly past full size and settles - the single
# cheapest thing that stops captions reading as a static subtitle track.
POP = r"{\fscx88\fscy88\t(0,110,\fscx104\fscy104)\t(110,190,\fscx100\fscy100)}"

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fontsize},&H00FFFFFF,{accent},{ink},&H00000000,{bold},0,0,0,100,100,0,0,1,8,4,2,{side},{side},{cap_v},1
Style: Hook,{font},58,&H00FFFFFF,&H00FFFFFF,{ink},{hook_bg},{bold},0,0,0,100,100,0,0,3,14,0,8,{side},{side},{hook_v},1
Style: Badge,{font},52,{accent},{accent},{ink},&HC0101010,0,0,0,0,100,100,0,0,3,12,0,8,{side},{side},{badge_v},1
Style: CTA,{font},56,{accent},{accent},{ink},&HB0101010,{bold},0,0,0,100,100,0,0,3,14,0,2,{side},{side},{cta_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def hex_to_ass(hex_color: str, alpha: str = "00") -> str:
    """#RRGGBB -> &HAABBGGRR (ASS stores colour reversed, alpha first)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF"
    return f"&H{alpha}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


@dataclass
class Cue:
    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end


# A cue boundary must never fall inside a spoken number. "thirty three years"
# split across two cues leaves the second cue reading "three years" - on a
# channel whose whole premise is accurate figures, that is a wrong claim on
# screen, not a formatting nit.
_NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million",
    "billion", "trillion", "point", "percent", "and", "a", "half",
}
_NUMBER_GLUE_MAX = 2  # words a cue may over-run by to keep a number whole
# Kept at 2, not 3: a spoken figure like "four hundred forty six percent" is
# four number-ish words in a row, and a glue cap of 3 let one cue balloon to
# 6-7 words - at this channel's fontsize that wraps to 3 lines and grows tall
# enough to overlap the chart footnote above it. 2 caps it at 5 words (still
# enough to keep any real number whole; nothing in this channel's numbers
# runs longer than "four hundred forty six") while keeping cues to 2 lines.


def _number_ish(word: Word) -> bool:
    token = word.text.lower().strip(".,%$()")
    return bool(token) and (token in _NUMBER_WORDS or any(c.isdigit() for c in token))


def group_words(words: list[Word], max_words: int = MAX_WORDS_PER_CUE) -> list[Cue]:
    cues: list[Cue] = []
    i = 0
    while i < len(words):
        end = min(i + max_words, len(words))
        extra = 0
        while (
            end < len(words)
            and extra < _NUMBER_GLUE_MAX
            and _number_ish(words[end - 1])
            and _number_ish(words[end])
        ):
            end += 1
            extra += 1
        cues.append(Cue(words[i:end]))
        i = end
    return cues


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    h, m, s = int(t // 3600), int((t % 3600) // 60), t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _karaoke(cue: Cue) -> str:
    parts, prev_end = [], cue.start
    for w in cue.words:
        gap_cs = max(0, round((w.start - prev_end) * 100))
        if gap_cs:
            parts.append(f"{{\\kf{gap_cs}}}")
        parts.append(f"{{\\kf{max(1, round((w.end - w.start) * 100))}}}{w.text}")
        prev_end = w.end
    return " ".join(parts)


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "").replace("}", "").replace("\n", "\\N")


def build_ass(
    segment_words: list[list[Word]],
    out_path: Path,
    hook_text: str = "",
    cta_text: str = "",
    badge_text: str = "",
    handle: str = "",
    total_duration: float = 0.0,
    brand_accent: str = "#FFB400",
    brand_ink: str = "#141414",
    brand_card: str = "",
    font: str = "Black Han Sans",
    fontsize: int = 72,
    max_words: int = MAX_WORDS_PER_CUE,
    bold: int = 0,
    hook_duration: float = 2.8,
    cta_duration: float = 1.8,
) -> Path:
    """segment_words: one word list per segment, timestamps already global."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    accent = hex_to_ass(brand_accent)
    ink = hex_to_ass(brand_ink)
    lines = [HEADER.format(
        font=font, fontsize=fontsize, side=SIDE_MARGIN, accent=accent, ink=ink,
        # On the chart channel the background already IS brand_ink, so a card
        # in the same colour vanishes. brand_card lets a channel set a
        # contrasting card surface.
        hook_bg=hex_to_ass(brand_card or brand_ink, alpha="B0"),
        bold=bold,
        cap_v=CAPTION_MARGIN_V, hook_v=HOOK_MARGIN_V,
        badge_v=BADGE_MARGIN_V, cta_v=CTA_MARGIN_V,
    )]

    if hook_text:
        lines.append(
            f"Dialogue: 0,{_fmt_time(0.15)},{_fmt_time(hook_duration)},Hook,,0,0,0,,"
            f"{{\\fad(250,350)}}{_esc(hook_text)}\n"
        )

    cta_start = max(0.0, total_duration - cta_duration) if (cta_text and total_duration > 0) else None

    # The price badge takes the top slot once the hook clears, and steps aside
    # before the CTA so the frame never carries three cards at once.
    if badge_text and total_duration > 0:
        badge_end = cta_start if cta_start else total_duration
        if badge_end > hook_duration + 0.2:
            lines.append(
                f"Dialogue: 0,{_fmt_time(hook_duration + 0.15)},{_fmt_time(badge_end)},Badge,,0,0,0,,"
                f"{{\\fad(220,220)}}{_esc(badge_text)}\n"
            )

    for words in segment_words:
        for cue in group_words(words, max_words):
            if cta_start is not None:
                if cue.start >= cta_start:
                    continue
                if cue.end > cta_start:
                    cue = Cue([w for w in cue.words if w.start < cta_start])
                    if not cue.words:
                        continue
            end = min(cue.end, cta_start) if cta_start else cue.end
            lines.append(
                f"Dialogue: 0,{_fmt_time(cue.start)},{_fmt_time(end)},Default,,0,0,0,,"
                f"{{\\fad(80,80)}}{POP}{_karaoke(cue)}\n"
            )

    if cta_start is not None:
        body = _esc(cta_text)
        if handle:
            body += f"\\N{{\\fs38}}{_esc(handle)}"
        lines.append(
            f"Dialogue: 1,{_fmt_time(cta_start)},{_fmt_time(total_duration)},CTA,,0,0,0,,"
            f"{{\\fad(250,200)}}{body}\n"
        )

    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path
