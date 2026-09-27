# -*- coding: utf-8 -*-
"""프로필 사진 (1080x1080). 원형으로 잘리니 내용은 가운데 원 안에만 둔다."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import make_cards as mc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1080

HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>
@font-face{font-family:'Pretendard';font-weight:100 900;src:url('%(font)s') format('woff2')}
*{margin:0;padding:0;box-sizing:border-box}
body{width:%(s)dpx;height:%(s)dpx;overflow:hidden;background:#0B0E14;
  font-family:'Pretendard',sans-serif;display:flex;align-items:center;justify-content:center;
  position:relative}
body::before{content:'';position:absolute;inset:0;
  background:radial-gradient(60%% 60%% at 50%% 42%%,rgba(255,59,48,.22),transparent 70%%)}
.c{position:relative;display:flex;flex-direction:column;align-items:center}
.dot{position:relative;width:150px;height:150px;margin-bottom:36px}
.dot i{position:absolute;border-radius:50%%;border:5px solid #FF3B30;left:50%%;top:50%%;
  transform:translate(-50%%,-50%%)}
.dot i:nth-child(1){width:150px;height:150px;opacity:.35}
.dot i:nth-child(2){width:98px;height:98px;opacity:.65}
.dot b{position:absolute;left:50%%;top:50%%;width:52px;height:52px;border-radius:50%%;
  background:#FF3B30;transform:translate(-50%%,-50%%);box-shadow:0 0 0 9px #fff}
h1{color:#F4F6FA;font-weight:900;font-size:265px;line-height:1.0;letter-spacing:-12px;
  text-align:center}
</style></head><body><div class="c">
<div class="dot"><i></i><i></i><b></b></div>
<h1>지금<br>세계</h1></div></body></html>"""


def main():
    html = HTML % {"font": mc.file_url(os.path.join(mc.VENDOR, "PretendardVariable.woff2")), "s": S}
    hp = os.path.join(HERE, "avatar.html")
    with open(hp, "w", encoding="utf-8") as fp:
        fp.write(html)
    work = mc.work_dir()
    old = (mc.W, mc.H)
    mc.W, mc.H = S, S
    try:
        mc.shoot(mc.find_chrome(), hp, os.path.join(HERE, "avatar.png"), work, 1)
    finally:
        mc.W, mc.H = old
    print("OK", os.path.join(HERE, "avatar.png"))


if __name__ == "__main__":
    main()
