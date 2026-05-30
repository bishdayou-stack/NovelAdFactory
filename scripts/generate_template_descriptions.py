"""
用 DeepSeek (纯文本) 批量生成 Facebook 爆款广告模板描述。
不需要 vision API — DeepSeek 原生了解各种广告创意的构图和风格。
"""
import os
import sys
import json
import argparse
from pathlib import Path

BASE_PATH = Path(__file__).parent.parent.resolve()
OUTPUT_PATH = BASE_PATH / "templates_index.json"

API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-337151b4ad8c4023ad5208240186ff0b")
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

BATCH_SIZE = 8  # 每批生成 8 条 (DeepSeek 输出长度限制)
TOTAL_SQUARE = 120
TOTAL_PORTRAIT = 60

SYSTEM_PROMPT = """\
You are an expert in social media ad creative analysis. You have studied thousands of viral Facebook ad images across romance, thriller, fantasy, drama, and lifestyle genres.

Generate diverse, realistic descriptions of viral Facebook ad templates. Each description should read like it was written by a vision model analyzing an actual ad image.

IMPORTANT: Make each template UNIQUE and VARIED. Cover different:
- Genres (dark romance, sweet romance, thriller/suspense, fantasy/magic, urban drama, historical, billionaire, werewolf/vampire, contemporary)
- Compositions (centered figure, split screen, rule of thirds, diagonal, close-up, wide shot, silhouette, duo/couple, solo figure)
- Color schemes (dark moody, bright pastel, golden warm, cold blue, neon, natural, monochrome, jewel tones)
- Moods (intense, tender, mysterious, epic, sensual, melancholic, hopeful, dangerous)

Output pure JSON array, no markdown, no explanation:
[{"style": "...", "composition": "...", "color_scheme": "...", "key_elements": "...", "mood": "...", "description": "..."}]

Fields:
- style: 3-5 comma-separated English style tags
- composition: 1 sentence describing layout, figure placement, text areas
- color_scheme: 5-10 words describing dominant colors
- key_elements: 5-10 comma-separated concrete visual elements
- mood: 2-3 words
- description: one dense paragraph (50-80 words) in comma-separated tag style, suitable as image generation prompt reference
"""


def call_deepseek(messages: list, max_tokens: int = 8000) -> str:
    import requests
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"model": MODEL, "messages": messages, "temperature": 0.9, "max_tokens": max_tokens}
    r = requests.post(API_URL, json=payload, headers=headers, timeout=120)
    if r.status_code >= 400:
        print(f"  HTTP {r.status_code}: {r.text[:300]}")
        return ""
    return r.json()["choices"][0]["message"]["content"].strip()


def extract_json(text: str) -> list:
    """从 LLM 响应中提取 JSON 数组"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # 找到最外层的 [ 和 ]
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            print(f"  JSON解析错误 @pos{e.pos}: {e}")
            snippet = text[max(0, e.pos - 80):e.pos + 80]
            print(f"  错误附近: ...{snippet}...")
            return []
    return []


def generate_batch(ratio: str, count: int, batch_idx: int, max_retries: int = 3) -> list:
    """生成一批模板描述，解析失败自动重试"""
    size_hint = "portrait 9:16 vertical (e.g. 1080x1920)" if ratio == "9:16" else "square 1:1 (e.g. 1080x1080)"
    for attempt in range(1, max_retries + 1):
        user_msg = (
            f"Generate {count} diverse viral Facebook ad template descriptions for {size_hint} images. "
            f"Make each one completely different in genre, style, mood, and composition. "
            f"Cover romance, thriller, fantasy, drama, and lifestyle genres. "
            f"This is batch {batch_idx + 1}. "
            f"IMPORTANT: Output ONLY a valid JSON array. Ensure all strings use double quotes, and double quotes inside strings are properly escaped with backslash."
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        raw = call_deepseek(messages)
        items = extract_json(raw)
        if items:
            for item in items:
                item["ratio"] = ratio
            return items
        print(f"  第{attempt}次重试失败，原始响应前300字: {raw[:300]}")
    return []


def main():
    global API_KEY
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", default=API_KEY)
    parser.add_argument("--square", type=int, default=TOTAL_SQUARE)
    parser.add_argument("--portrait", type=int, default=TOTAL_PORTRAIT)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    API_KEY = args.api_key

    # 断点续传：读取已有条目
    all_items = []
    existing_ids = set()
    if OUTPUT_PATH.exists():
        try:
            all_items = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            existing_ids = {item["id"] for item in all_items if "id" in item}
            if all_items:
                sq = sum(1 for t in all_items if t.get("ratio") == "1:1")
                pt = sum(1 for t in all_items if t.get("ratio") == "9:16")
                print(f"从已有索引恢复 {len(all_items)} 条 (1:1={sq}, 9:16={pt})")
        except (json.JSONDecodeError, KeyError):
            all_items = []

    print(f"API: {API_URL}, 模型: {MODEL}")
    print(f"目标: {args.square} 张 1:1 + {args.portrait} 张 9:16 = {args.square + args.portrait} 条描述")
    print(f"每批 {args.batch_size} 条\n")

    for ratio, total in [("1:1", args.square), ("9:16", args.portrait)]:
        existing_count = sum(1 for t in all_items if t.get("ratio") == ratio)
        remaining = max(0, total - existing_count)
        if remaining == 0:
            print(f"[{ratio}] 已有 {existing_count} 条，跳过")
            continue
        print(f"[{ratio}] 已有 {existing_count} 条，还需生成 {remaining} 条")
        batch_idx = 0
        while remaining > 0:
            n = min(args.batch_size, remaining)
            print(f"[{ratio}] 第{batch_idx + 1}批: 请求 {n} 条...")
            items = generate_batch(ratio, n, batch_idx)
            print(f"  获取 {len(items)} 条")
            for item in items:
                item["id"] = f"gen_{ratio.replace(':','')}_{len(all_items) + 1:04d}"
                item["size"] = "1080x1920" if ratio == "9:16" else "1080x1080"
            all_items.extend(items)
            remaining -= len(items)
            batch_idx += 1
            if len(items) < n:
                print(f"  产出不足，跳过剩余 {remaining} 条")
                break

    OUTPUT_PATH.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成! {len(all_items)} 条描述 -> {OUTPUT_PATH}")

    # 统计
    square = [t for t in all_items if t.get("ratio") == "1:1"]
    portrait = [t for t in all_items if t.get("ratio") == "9:16"]
    print(f"  1:1 = {len(square)}, 9:16 = {len(portrait)}")


if __name__ == "__main__":
    main()
