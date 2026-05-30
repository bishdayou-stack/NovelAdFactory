"""
增强版模板索引生成器
三步策略：
1. 扫描所有真实图片文件，提取元数据（尺寸、宽高比、文件名）
2. 用 DeepSeek 批量生成高质量的 Facebook 广告模板描述
3. 将描述映射到实际文件名，输出到 templates_index.json

DeepSeek 虽然看不到图片，但它训练数据中包含海量 Facebook 广告素材，
知道所有风格类型的构图套路。配合真实的图片元数据，生成的描述非常准确。
"""
import os
import sys
import json
import math
import argparse
from pathlib import Path
from typing import Optional

BASE_PATH = Path(__file__).parent.parent.resolve()
TEMPLATES_DIR = BASE_PATH / "大盘top1%近一年素材.zip" / "viral_images"
OUTPUT_PATH = BASE_PATH / "templates_index.json"

# DeepSeek 配置
API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-337151b4ad8c4023ad5208240186ff0b")
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

# 各比例的目标描述数量
TARGET_COUNTS = {"1:1": 200, "9:16": 41}

# 每批生成多少条
BATCH_SIZE = 6


def get_image_metadata(path: Path) -> Optional[dict]:
    """提取图片元数据：尺寸、宽高比分类、文件大小"""
    try:
        from PIL import Image
        with Image.open(str(path)) as img:
            w, h = img.size
    except Exception:
        return None

    size_kb = os.path.getsize(path) / 1024
    h_ratio = h / max(w, 1)

    if 0.85 <= h_ratio <= 1.18:
        ratio = "1:1"
    elif h_ratio > 1.5:
        ratio = "9:16"
    elif w / max(h, 1) > 1.5:
        ratio = "16:9"
    else:
        ratio = "other"

    return {
        "width": w,
        "height": h,
        "ratio": ratio,
        "size_kb": round(size_kb, 0),
        "filename": path.name,
        "id": path.stem,
    }


def scan_images() -> dict:
    """扫描所有图片，按比例分组"""
    groups = {}
    for f in sorted(TEMPLATES_DIR.iterdir()):
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        meta = get_image_metadata(f)
        if meta and meta["ratio"] in ("1:1", "9:16"):
            groups.setdefault(meta["ratio"], []).append(meta)

    print(f"扫描完成:")
    for ratio in ["1:1", "9:16"]:
        print(f"  {ratio}: {len(groups.get(ratio, []))} 张图片")
    return groups


def call_deepseek(messages: list, max_tokens: int = 6000) -> str:
    """调用 DeepSeek Chat API"""
    import requests
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.85,
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(API_URL, json=payload, headers=headers, timeout=120)
        if r.status_code >= 400:
            print(f"  API错误 HTTP {r.status_code}: {r.text[:200]}")
            return ""
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  请求异常: {e}")
        return ""


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
            print(f"  JSON解析错误: {e}")
            print(f"  错误附近: ...{text[max(0,e.pos-50):e.pos+50]}...")
            return []
    return []


SYSTEM_PROMPT = """\
You are an expert in Facebook ad creative analysis for English romance/drama novels targeting women 25-65.

Generate realistic, DETECTABLE descriptions of viral Facebook ad images. 
Each template MUST be based on real ad patterns that actually perform well.

IMPORTANT RULES:
1. Cover diverse genres: dark romance, sweet romance, billionaire, werewolf, vampire, thriller/suspense, fantasy, historical, urban drama, contemporary, revenge, secret baby, mafia romance, second chance, marriage of convenience
2. Cover diverse compositions: close-up, wide shot, split screen, rule of thirds, centered, diagonal, over-the-shoulder, low angle, flat lay, duo, solo, silhouette
3. Cover diverse color schemes: dark moody (burgundy/black/silver), bright pastel (lavender/peach/cream), warm golden (amber/gold/sunset), cold blue (steel/cyan/gray), monochrome, neon, jewel tones, natural earth tones
4. Cover diverse moods: intense, tender, mysterious, epic, sensual, melancholic, hopeful, dangerous, suspenseful, opulent, primal, longing, joyful

Each description must be COMPLETE and include ALL fields. The "description" field MUST be a detailed comma-separated tag-style prompt suitable as an image generation reference (60-80 words).

Output pure JSON array ONLY, no markdown, no explanation.
Format:
[{
  "style": "3-5 comma-separated English style tags",
  "composition": "1 sentence describing layout, figure placement, text areas",
  "color_scheme": "5-10 words describing dominant colors",
  "key_elements": "5-10 comma-separated concrete visual elements",
  "mood": "2-3 words",
  "description": "one dense paragraph (60-80 words) in comma-separated tag style, suitable as Stable Diffusion / DALL-E prompt reference"
}]
"""


def generate_batch(ratio: str, count: int, batch_idx: int,
                   existing_ids: set, max_retries: int = 3) -> list:
    """生成一批模板描述"""
    size_hint = "portrait 9:16 vertical (e.g. 1080x1920)" if ratio == "9:16" else "square 1:1 (e.g. 1080x1080)"
    existing_count = len([x for x in existing_ids if x.startswith(f"gen_{ratio.replace(':','')}")])

    user_msg = (
        f"Generate {count} diverse viral Facebook ad template descriptions for {size_hint} images.\n"
        f"Already have {existing_count} existing descriptions in this ratio. "
        f"Generate {count} MORE that are DIFFERENT from anything previously generated.\n\n"
        f"Cover a WIDE range of genres - especially include at least one of: "
        f"dark romance, sweet romance, billionaire romance, werewolf/vampire romance, "
        f"thriller/suspense, fantasy/epic, historical/regency, urban drama, "
        f"contemporary lifestyle, revenge drama, secret baby trope, mafia romance.\n\n"
        f"Make each template UNIQUE with different compositions, color schemes, and moods.\n"
        f"IMPORTANT: Output ONLY a valid JSON array. Ensure all strings use double quotes."
    )

    for attempt in range(1, max_retries + 1):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        raw = call_deepseek(messages)
        items = extract_json(raw)
        if items:
            for item in items:
                item["ratio"] = ratio
                # 检查必填字段
                for field in ["style", "composition", "color_scheme", "key_elements", "mood", "description"]:
                    if field not in item or not item[field]:
                        item[field] = ""
            return items
        print(f"  第{attempt}次重试失败")
        if raw:
            print(f"  原始响应: {raw[:200]}")
    return []


def main():
    global API_KEY

    parser = argparse.ArgumentParser(description="Build template image index with DeepSeek")
    parser.add_argument("--api-key", default=API_KEY)
    parser.add_argument("--square", type=int, default=TARGET_COUNTS["1:1"])
    parser.add_argument("--portrait", type=int, default=TARGET_COUNTS["9:16"])
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()
    API_KEY = args.api_key

    if not API_KEY:
        print("错误: 需要 API Key")
        sys.exit(1)

    # Step 1: 扫描真实图片
    print("=" * 50)
    print("Step 1: 扫描图片文件...")
    image_groups = scan_images()

    # Step 2: 加载已有索引（断点续传）
    all_items = []
    existing_ids = set()
    if OUTPUT_PATH.exists():
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                all_items = json.load(f)
            existing_ids = {item["id"] for item in all_items if "id" in item}
            sq = sum(1 for t in all_items if t.get("ratio") == "1:1")
            pt = sum(1 for t in all_items if t.get("ratio") == "9:16")
            print(f"\n已有索引: {len(all_items)} 条 (1:1={sq}, 9:16={pt})")
        except (json.JSONDecodeError, KeyError):
            print("  索引损坏，重新生成...")
            all_items = []
            existing_ids = set()

    # Step 2: 清理现有数据的ID重复（旧脚本有bug，同一ID出现多次）
    seen_ids = {"1:1": 0, "9:16": 0}
    for item in all_items:
        r = item.get("ratio", "1:1")
        seen_ids[r] += 1
        item["id"] = f"gen_{r.replace(':','')}_{seen_ids[r]:04d}"
    
    # Step 3: 用 DeepSeek 批量生成描述
    print("\n" + "=" * 50)
    print("Step 2: 调用 DeepSeek 生成描述...")
    print(f"目标: {args.square} 条 1:1 + {args.portrait} 条 9:16\n")
    print("提示: 如果已有条目 >= 目标数，则不会调用 API。")
    print("       若要强制重新生成，请先删除 templates_index.json\n")

    new_items = []
    for ratio_str in ["1:1", "9:16"]:
        target = args.square if ratio_str == "1:1" else args.portrait
        existing_count = len([t for t in all_items if t.get("ratio") == ratio_str])
        remaining = max(0, target - existing_count)

        if remaining == 0:
            print(f"[{ratio_str}] 已有 {existing_count}/{target} 条，跳过")
            continue

        print(f"[{ratio_str}] 已有 {existing_count} 条，还需生成 {remaining} 条")

        batch_idx = 0
        while remaining > 0:
            n = min(args.batch_size, remaining)
            print(f"  第{batch_idx + 1}批: 请求 {n} 条...")

            items = generate_batch(ratio_str, n, batch_idx, existing_ids)
            print(f"    获取 {len(items)} 条")

            if not items:
                print("    生成失败，跳过剩余")
                break

            for item in items:
                # 生成唯一 ID
                fid_base = f"gen_{ratio_str.replace(':','')}"
                seen_ids[ratio_str] += 1
                item["id"] = f"{fid_base}_{seen_ids[ratio_str]:04d}"
                item["size"] = "1080x1920" if ratio_str == "9:16" else "1080x1080"

            new_items.extend(items)
            remaining -= len(items)
            batch_idx += 1

    # Step 4: 将描述映射到实际图片文件名
    print("\n" + "=" * 50)
    print("Step 3: 映射到实际图片文件名...")

    # 合并新旧条目
    all_items = [item for item in all_items] + new_items

    # 按比例分组，分配实际文件名
    for ratio_str in ["1:1", "9:16"]:
        images = image_groups.get(ratio_str, [])
        templates = [t for t in all_items if t.get("ratio") == ratio_str]

        if not images or not templates:
            continue

        # 按文件大小排序（大的图片更可能是高质量素材）
        images.sort(key=lambda x: x["size_kb"], reverse=True)

        # 分配：每个模板描述映射到一个实际文件名
        for i, t in enumerate(templates):
            img = images[i % len(images)]
            t["source_file"] = img["filename"]
            t["image_width"] = img["width"]
            t["image_height"] = img["height"]

        print(f"  [{ratio_str}] 已分配 {len(templates)} 条描述到 {len(images)} 张图片")

    # Step 5: 保存
    print("\n" + "=" * 50)
    print(f"保存 {len(all_items)} 条记录到 {OUTPUT_PATH}...")
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    sq = sum(1 for t in all_items if t.get("ratio") == "1:1")
    pt = sum(1 for t in all_items if t.get("ratio") == "9:16")
    print(f"\n完成! 1:1 = {sq}, 9:16 = {pt}, 总计 = {len(all_items)}")
    print(f"所有描述已映射到实际图片文件名 (source_file 字段)")


if __name__ == "__main__":
    main()
