# -*- coding: utf-8 -*-
"""验证：系统提示词模板的占位符接线。

背景（实测事故）：`SYSTEM_PROMPT_TEMPLATE.format(...)` 原本在生成中心和分析页两个路由里
各写了一遍。往模板里加 `caption_video_count` / `bg_count` 时只改了生成中心那处，
分析页那处直接 KeyError → 500，前端只拿到纯文本 "Internal Server Error"，
在页面上报成 `Unexpected token 'I', "Internal S"... is not valid JSON`。

覆盖：
  1. 占位符是从模板自身解析出来的（不是手写名单，不会和模板脱节）
  2. 少传字段不再抛异常，按 0 补齐并告警
  3. 两个路由各自传的那套字段都能干净渲染（渲染完不留 {name}）
  4. 回归：只传「老字段」也必须成功 —— 这正是当时挂掉的场景
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def main():
    import main

    # 1) 占位符自动发现
    got = set(main.SYSTEM_PROMPT_FIELDS)
    assert got, "一个占位符都没解析出来，模板加载失败了？"
    for must in ("text_single_count", "story_card_count", "caption_video_count", "bg_count",
                 "cap_min", "cap_max", "n_square"):
        assert must in got, f"模板里的 {must} 没被解析出来"
    # 解析结果必须和模板正文对得上（防止解析逻辑改坏后静默漏掉字段）
    in_text = set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", main.SYSTEM_PROMPT_TEMPLATE))
    assert in_text <= got, f"模板里有但没解析出来的占位符: {sorted(in_text - got)}"

    def rendered(**kw):
        out = main.format_system_prompt(**kw)
        left = set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", out))
        assert not left, f"渲染完还留着占位符 {sorted(left)} —— 有字段没被替换"
        return out

    # 2) 什么都不传也不该炸（按 0 补齐）
    rendered()

    # 3) 两个路由各自那套字段
    cs = rendered(text_single_count=2, scroll_visual_count=1, lr_split_count=0, tb_split_count=0,
                  three_panel_count=0, story_card_count=1, caption_video_count=1,
                  bg_count=main.CAPTION_BG_COUNT_MULTI,
                  cap_min=main.CAPTION_MIN_LINES, cap_max=main.CAPTION_MAX_LINES, n_square=3)
    assert "caption_video=1" in cs, "生成中心那套的 caption_video 计数没渲染对"
    # 句数必须跟着常量走：模板里原本写死「21-24 句」，改了 CAPTION_MIN/MAX_LINES 模板不跟着变，
    # 模型照样照旧句数出稿，视频长度直接不对。现在改成占位符，两处各验一次：
    #   (a) 传进常量时渲染出来就是常量值  (b) 模板和规则文件里都不许再出现写死的句数
    assert f"{main.CAPTION_MIN_LINES}-{main.CAPTION_MAX_LINES} 句" in cs, "system prompt 里的句数没跟着常量走"
    for where, text in (("system_prompt.txt", main.SYSTEM_PROMPT_TEMPLATE),
                        ("rules_caption_video.txt", main._RULES_CAPTION_VIDEO)):
        assert "21-24" not in text and "21~24" not in text, f"{where} 里还写死着旧句数 21-24"
    assert f"长度必须正好 {main.CAPTION_BG_COUNT_MULTI}" in cs, "生成中心那套的底图张数没渲染对"
    assert f"均分成 {main.CAPTION_BG_COUNT_MULTI} 段" in cs, "生成中心那套的分段数没渲染对"
    an = rendered(text_single_count=1, scroll_visual_count=2, lr_split_count=0, tb_split_count=0,
                  three_panel_count=0, story_card_count=0, caption_video_count=0, bg_count=1,
                  n_square=1)
    assert "caption_video=0" in an, "分析页那套没渲染对"

    # 4) 回归（这条才抓得住事故）：只给老字段时，format_system_prompt 必须补齐而不是抛 KeyError。
    #    事故现场就是「某处调用只传了老字段 + 模板多了新占位符」。
    #    注意：单靠下面第 5 条路由冒烟是抓不住的 —— 路由本身也补了缺的字段，
    #    把 format_system_prompt 换回裸 .format 它照样 200（实测过，那条是无效断言）。
    old_only = rendered(text_single_count=1, scroll_visual_count=1, lr_split_count=0,
                        tb_split_count=0, three_panel_count=0, story_card_count=0, n_square=1)
    assert "caption_video=0" in old_only, "缺的字段没补成 0"

    # 4b) 静态回归：**全项目只允许 helper 内部那一处**直接调 SYSTEM_PROMPT_TEMPLATE.format。
    #     当初就是两个路由各写一遍，加占位符只改了其中一处。多出任何一个调用点，这条就红。
    # 用 ast 数真实调用点，别用正则 —— 文档里那句 `SYSTEM_PROMPT_TEMPLATE.format(...)`
    # 会被正则当成调用（实测把自己写红了）
    import ast
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "format"
             and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "SYSTEM_PROMPT_TEMPLATE"]
    assert len(calls) == 1, (
        f"main.py 里有 {len(calls)} 处直接调 SYSTEM_PROMPT_TEMPLATE.format —— 只能有 1 处"
        f"（format_system_prompt 内部）。往模板加占位符时漏改其他调用点会 500，"
        f"请改走 format_system_prompt()")

    # 5) 分析页组装提示词不能炸。
    #    这里直接调 build_analysis_prompt 而不是打 HTTP：分析改成后台任务之后，
    #    打路由只会拿到「已提交」，格式化发生在后台，HTTP 那层测不到它了。
    body = main.AnalyzeNovelRequest(api_key="x", api_url="https://example.invalid/v1",
                                    novel_content="test novel", analysis_prompt="", story_card_count=1)
    system, user_msg, rules = main.build_analysis_prompt(body)
    assert "caption_video=0" in system, "分析页的 system 没渲染对"
    assert "{bg_count}" not in system and "{" not in system.split("Counts:")[1][:80], "system 还有没替换的占位符"
    assert "绘图规则" in user_msg and "只输出 JSON" in user_msg
    # 用户自定义分析提示词要保留，且故事卡规则不能被吞掉（那条回归见 verify_story_card.py）
    body2 = main.AnalyzeNovelRequest(api_key="x", api_url="https://e.invalid/v1",
                                     novel_content="n", analysis_prompt="我的风格", story_card_count=1)
    _, user2, rules2 = main.build_analysis_prompt(body2)
    assert "我的风格" in user2 and "我的风格" in rules2

    print(f"OK: 系统提示词模板接线正常（{len(got)} 个占位符自动发现；少传字段补 0 不炸；"
          f"分析页不再 500 且返回 JSON）")


if __name__ == "__main__":
    main()
