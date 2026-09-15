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
    for must in ("text_single_count", "story_card_count", "caption_video_count", "bg_count", "n_square"):
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
                  bg_count=main.CAPTION_BG_COUNT_MULTI, n_square=3)
    assert "caption_video=1" in cs, "生成中心那套的 caption_video 计数没渲染对"
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

    # 5) 冒烟：分析页路由不能 500，且必须回 JSON
    #    （前端那句 `Unexpected token 'I', "Internal S"...` 就是因为拿到了非 JSON 的 500 页面）
    from fastapi.testclient import TestClient
    client = TestClient(main.app, raise_server_exceptions=False)
    r = client.post("/api/analyze-novel", json={
        "api_key": "x", "api_url": "https://example.invalid/v1",
        "novel_content": "test", "analysis_prompt": "", "story_card_count": 1})
    assert r.status_code != 500, f"分析页仍然 500：{r.text[:200]}"
    try:
        r.json()
    except Exception as e:
        raise AssertionError(f"分析页返回的不是 JSON（前端会报解析错误）：{r.text[:200]} ({e})")

    print(f"OK: 系统提示词模板接线正常（{len(got)} 个占位符自动发现；少传字段补 0 不炸；"
          f"分析页不再 500 且返回 JSON）")


if __name__ == "__main__":
    main()
