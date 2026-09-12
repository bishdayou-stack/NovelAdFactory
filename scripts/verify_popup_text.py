# -*- coding: utf-8 -*-
"""验证：弹屏视频的文案不会被图片下边缘裁掉。

原来的问题：弹屏底图是 1024x1024 正方形，文字区从高度 60% 开始（可用约 380px），
字号 46 + 行距 8 = 每行 54px，只放得下 7 行；但切分文案时写死「最多 10 行」，
跟图片尺寸和字号都没关系 → 多出来的行被裁掉，文字显示不全。

检查（都用像素级判定，不靠复算公式）：
  1. 容量函数算得对（1024 方形 + 字号 46 应只有 7 行）
  2. 切分后的每一屏，行数都不超过容量
  3. 真正画出来时，白色文字像素不碰图片下边缘（黑底白字，好判定）
  4. 超长单屏走字号自适应兜底，也一样不碰下边缘
  5. 整段视频字号统一
  6. 切分不丢字（拼回去和原文的词序一致）
"""
import sys
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

W = H = 1024          # 弹屏底图实际尺寸
FONT_SIZE = 46
LINE_SPACING = 8

SAMPLE = (
    "My husband served me divorce papers on stage at his company's annual gala, "
    "while my own sister stood next to him holding his arm. Everyone in the room "
    "was laughing at me. I walked out without saying a word. Three years later I "
    "came back as the owner of the company that was about to buy his. He had no "
    "idea who he was begging for a second chance."
)


def pick_font() -> str:
    fonts = sorted((ROOT / "ziti").glob("*.ttf"))
    assert fonts, "ziti/ 里没有 ttf 字体"
    return str(fonts[0])


def text_rows(img: Image.Image) -> np.ndarray:
    """白字所在的行号（底图全黑 + 白字，直接按亮度判）"""
    a = np.asarray(img.convert("L"))
    return np.where((a > 200).any(axis=1))[0]


def main():
    import main

    font = pick_font()

    # 1) 容量
    cap = main.popup_line_capacity(H, FONT_SIZE, LINE_SPACING)
    assert cap == 7, f"1024 方形 + 字号 {FONT_SIZE} 应只放得下 7 行，算出 {cap}"

    # 2) 切分不超过容量
    chars = max(8, int((W * 0.85) / (FONT_SIZE * 0.5)))
    segments = main.split_text_smartly(SAMPLE, chars, max_lines=cap)
    assert segments, "没切出任何分屏"
    for i, seg in enumerate(segments):
        n = len(textwrap.wrap(seg, width=chars))
        assert n <= cap, f"第 {i+1} 屏 {n} 行，超过容量 {cap}：{seg[:60]}"

    # 3) 像素级：黑底白字，文字不能碰到图片下边缘
    bg = Image.new("RGB", (W, H), (0, 0, 0))
    worst = 0
    for i, seg in enumerate(segments):
        img = main.create_popup_frame(seg, bg, font, FONT_SIZE, "#FFFFFF", "#000000",
                                      150, LINE_SPACING, 0, "full_bar")
        rows = text_rows(img)
        assert len(rows) > 0, f"第 {i+1} 屏没画出任何文字"
        assert rows.max() < H - 2, f"第 {i+1} 屏文字画到了第 {rows.max()} 行（图高 {H}），被裁了"
        worst = max(worst, int(rows.max()))
    print(f"   {len(segments)} 屏文案，最低一行文字在第 {worst} 行（图高 {H}）")

    # 4) 超长单屏（远超容量）也要靠缩字号兜住
    huge = " ".join(["This is one extremely long single sentence without any period"] * 8) + "."
    img = main.create_popup_frame(huge, bg, font, FONT_SIZE, "#FFFFFF", "#000000",
                                  150, LINE_SPACING, 0, "full_bar")
    rows = text_rows(img)
    assert rows.max() < H - 2, f"超长单屏被裁了：最低文字在第 {rows.max()} 行"

    # 5) 整段视频字号统一
    fitted = main.fit_popup_font_size(segments, W, H, FONT_SIZE, LINE_SPACING, 0)
    cap2 = main.popup_line_capacity(H, fitted, LINE_SPACING)
    chars2 = max(8, int((W * 0.85) / (fitted * 0.5)))
    for seg in segments:
        assert len(textwrap.wrap(seg, width=chars2)) <= cap2, f"字号 {fitted} 下仍有屏幕装不下"
    print(f"   统一字号：{FONT_SIZE} → {fitted}")

    # 6) 不丢字
    def words(s):
        return [w.strip(".,").lower() for w in s.split() if w.strip(".,")]

    assert words(" ".join(segments)) == words(SAMPLE), "切分把文案弄丢了/弄乱了"

    print("OK: 弹屏文案按实际尺寸分行、装不下自动缩字号，不再被图片下边缘裁掉")


if __name__ == "__main__":
    main()
