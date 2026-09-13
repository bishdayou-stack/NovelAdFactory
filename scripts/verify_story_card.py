# -*- coding: utf-8 -*-
"""验证：故事卡（上图下文）合成与提示词接线。

覆盖：
  1. 两档版面的词数范围写进了规则文件（LLM 才守得住长度）
  2. 组装规则时占位符被真实词数替换；短句版和长文版给出不同词数
  3. 场景图提示词必须带「画面里不能有文字」的约束（文字由代码渲染，AI 画会糊）
  4. 场景图按宽幅请求：图片带 = 原图比例，**零裁剪**（方图兜底时才夹取）
  5. 合成结果：长文 1080x1920 / 短句 1080x1080；上半是场景图、下半是文案底色
  6. **字数少也不留白**：短文案自动放大到填满文字区（这条是回归测试 —— 旧代码只缩不放，
     50 词会只填满一半，留一大片空白）
  7. 文案不会溢出画布；字数越多字号越小；超长时有 overflow 标记（不静默裁掉）
  8. 按像素换行：超长单词不会丢、不会死循环
  9. 全部文案都渲染出来了（行数与换行结果一致）
"""
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

LONG_TEXT = (
    "On our third wedding anniversary, I unlocked my husband's phone and found six months of "
    "love letters to my older sister. Every touch, every kiss, and every promise he ever gave me "
    "was meant for her. Dominic only married me because his father forced him to wed a Whitmore "
    "girl to inherit a two billion dollar trust. My own family let me walk down the aisle knowing "
    "I was just a decoy bride. While I spent hours preparing our anniversary dinner, he was "
    "texting my sister, bragging about how easily he could throw me away once the money hit his "
    "account. He thought I was just a naive, penniless girl he could manipulate. But he didn't "
    "know my grandmother left me a secret four billion dollar fortune and an eleven percent stake "
    "in his own company. Before he left the penthouse, I handed him a stack of papers. Careless "
    "and arrogant, he signed every page without reading, unknowingly transferring all marital "
    "assets to me and surrendering his power."
)

SHORT_TEXT = (   # 约 55 词 —— 正是用户截图里那种「只占半屏、下面全空」的长度
    "My husband slid the divorce papers across the candlelit table on our third anniversary, "
    "claiming his pregnant mistress needed his family name. I signed without shedding a single "
    "tear. He thought he was leaving me penniless, unaware that the biotech startup his "
    "grandfather was begging to merge with belonged entirely to me."
)


def main():
    import main

    # 1) 两档词数
    assert main.STORY_CARD_STYLES["long"]["words"] == (280, 380), main.STORY_CARD_STYLES["long"]
    assert main.STORY_CARD_STYLES["short"]["words"] == (80, 120), main.STORY_CARD_STYLES["short"]
    rules_raw = main._RULES_STORY_CARD
    assert "{word_min}" in rules_raw and "{word_max}" in rules_raw, "规则文件里应有词数占位符"

    # 2) 占位符替换且两档不同
    long_rules = main._build_rules_text("", 0, 0, 0, 0, 0, False, story_card=2, story_card_style="long")
    short_rules = main._build_rules_text("", 0, 0, 0, 0, 0, False, story_card=1, story_card_style="short")
    assert "280-380" in long_rules and "{word_min}" not in long_rules, long_rules[-300:]
    assert "80-120" in short_rules, "短句版词数没替换进去"
    # 不带故事卡时不该混进这段规则
    assert "280-380" not in main._build_rules_text("", 1, 0, 0, 0, 0, False)

    # 3) 场景图提示词：不能有文字 + 必须点名宽幅
    p = main.finalize_story_card_prompt("a woman holds divorce papers, shocked husband", "")
    low = p.lower()
    for kw in ("no text", "no letters", "no watermark"):
        assert kw in low, f"场景图提示词缺「{kw}」约束：{p[:200]}"
    assert "caucasian" in low, "缺种族锁定"
    assert "16:9" in p and "widescreen" in low, f"提示词没要求宽幅构图：{p[-260:]}"

    # 4) 图片带 = 原图比例，零裁剪
    assert main.STORY_CARD_IMG_SIZE == "1344x768"
    band = main.story_card_band_height(1344, 768, main.STORY_CARD_W, 1920)
    assert abs(band - 1080 / (1344 / 768)) <= 1, band          # 1080/1.75 = 617
    # 方图兜底：落在夹取区间内，不能把文字区挤没
    fb = main.story_card_band_height(1024, 1024, main.STORY_CARD_W, 1920)
    assert int(1920 * main.STORY_CARD_BAND_MIN_RATIO) <= fb <= int(1920 * main.STORY_CARD_BAND_MAX_RATIO), fb
    assert 1920 - fb >= 600, f"方图兜底后文字区只剩 {1920 - fb}px"
    # 极扁的图也不能把文字区顶没
    assert main.story_card_band_height(4000, 200, main.STORY_CARD_W, 1920) == int(1920 * main.STORY_CARD_BAND_MIN_RATIO)

    tmp = Path(tempfile.mkdtemp(prefix="story_card_"))
    src = tmp / "scene.png"                                    # 纯红宽幅场景图，好判定
    Image.new("RGB", (1344, 768), (200, 40, 40)).save(src)

    # 5) 合成：上场景、下文案；两档画布尺寸
    out = tmp / "card.png"
    st = main.compose_story_card(src, out, LONG_TEXT, style="long")
    img = Image.open(out).convert("RGB")
    assert img.size == (main.STORY_CARD_W, main.STORY_CARD_H), img.size
    a = np.asarray(img)
    assert tuple(a[5, 5]) == (200, 40, 40), f"顶部应是场景图: {tuple(a[5,5])}"
    assert tuple(a[-5, 5]) == (251, 243, 228), f"底部应是文案底色: {tuple(a[-5,5])}"
    short_card = tmp / "short.png"
    main.compose_story_card(src, short_card, SHORT_TEXT, style="short")
    assert Image.open(short_card).size == (main.STORY_CARD_W, 1080), Image.open(short_card).size

    # 6) 不留白：55 词的老截图长度，现在必须填满文字区大半
    st_short = main.compose_story_card(src, tmp / "s2.png", SHORT_TEXT, style="long")
    box_h = main.STORY_CARD_H - st_short["band_h"] - 48 * 2
    filled = st_short["lines"] * st_short["font_size"] * 1.32 / box_h
    assert filled > 0.7, f"短文案只填了 {filled:.0%} 文字区（旧代码只缩不放 → 留白）"
    assert st_short["font_size"] > main.STORY_CARD_STYLES["long"]["min_size"], st_short

    # 7) 不溢出 + 字数越多字号越小 + 超长有标记
    dark = np.where((np.asarray(img.convert("L")) < 100).any(axis=1))[0]
    assert dark.max() < main.STORY_CARD_H - 4, f"文字画到第 {dark.max()} 行，快贴底了"
    assert not st["overflow"], st
    small = main.compose_story_card(src, tmp / "b.png", LONG_TEXT[:400], style="long")
    assert small["font_size"] > st["font_size"], (small["font_size"], st["font_size"])
    huge = " ".join(LONG_TEXT.split() * 12)
    st_huge = main.compose_story_card(src, tmp / "h.png", huge, style="long")
    assert st_huge["overflow"] is True, "超长文案必须报 overflow（别静默裁掉）"

    # 8) 按像素换行：超长单词不能丢
    font = main._load_font(str(main.BASE_PATH / "ziti" / "georgia.ttf"), 40)
    lines = main.wrap_text_by_pixels("short " + "W" * 200 + " end", font, 600)
    assert any("W" * 50 in ln for ln in lines), "超长单词被弄丢了"
    assert lines[0].startswith("short")

    # 9) 文案全部渲染：行数对得上
    assert st["lines"] == len(main.wrap_text_by_pixels(
        LONG_TEXT, main._load_font(str(main.BASE_PATH / "ziti" / "georgia.ttf"), st["font_size"]),
        main.STORY_CARD_W - 48 * 2))

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"OK: 故事卡排版正常（长文 {st['font_size']}px/{st['lines']} 行、短文案 {st_short['font_size']}px "
          f"填满 {filled:.0%}、超长会报 overflow；图片带 {band}px 零裁剪；提示词含宽幅+禁文字约束）")


if __name__ == "__main__":
    main()
