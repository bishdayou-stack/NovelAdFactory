"""
爆款模板图片批量分析脚本
遍历 viral_images 目录，按尺寸聚类后选取代表性样本，
调用 vision API 生成结构化文本描述，输出到 templates_index.json。
支持断点续传，中断后重新运行会跳过已处理的图片。
"""
import os
import sys
import json
import base64
import time
import argparse
from pathlib import Path
from typing import Optional

BASE_PATH = Path(__file__).parent.parent.resolve()
TEMPLATES_DIR = BASE_PATH / "大盘top1%近一年素材.zip" / "viral_images"
OUTPUT_PATH = BASE_PATH / "templates_index.json"

# 配置 — 可通过命令行参数覆盖
API_URL = os.getenv("NOVEL_API_URL", "https://api.geeknow.top/v1")
API_KEY = os.getenv("NOVEL_API_KEY", "")
CHAT_MODEL = os.getenv("NOVEL_CHAT_MODEL", "gpt-4o-mini")

# 采样：每种宽高比最多处理多少张
MAX_PER_RATIO = {"1:1": 120, "9:16": 999, "16:9": 30}
# API 调用间隔（秒），避免触发限流
CALL_DELAY = 1.5

SYSTEM_PROMPT = """\
You are a visual analyst specializing in social media ad creatives.
Analyze the given image and output a JSON object describing its visual composition.
Return ONLY pure JSON, no markdown, no explanation.

Format:
{
  "style": "3-5 comma-separated English style tags (e.g. dark cinematic, dramatic lighting, high contrast)",
  "composition": "1-2 sentences describing the layout, figure placement, text areas, split screens, overlays",
  "color_scheme": "dominant color palette in 5-10 words (e.g. deep blue and gold, warm amber tones)",
  "key_elements": "comma-separated list of 5-10 concrete visual elements visible in the image",
  "mood": "2-3 words describing emotional tone",
  "description": "one dense English paragraph (50-80 words) describing this image as an image-generation prompt, using comma-separated tag style, suitable as Stable Diffusion / DALL-E prompt"
}"""


def get_image_dimensions(path: Path) -> tuple:
    """返回 (width, height)，读取尽量快"""
    from PIL import Image
    with Image.open(str(path)) as img:
        return img.size


def encode_image_base64(path: Path) -> str:
    """将图片编码为 base64 data URI"""
    import imghdr
    with open(path, "rb") as f:
        data = f.read()
    fmt = imghdr.what(None, data) or "jpeg"
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:image/{fmt};base64,{b64}"


def analyze_image(path: Path, api_url: str, api_key: str, model: str) -> Optional[dict]:
    """调用 vision API 分析单张图片，返回描述 dict 或 None"""
    import requests

    b64_uri = encode_image_base64(path)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = api_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this ad creative image."},
                    {"type": "image_url", "image_url": {"url": b64_uri}},
                ],
            },
        ],
        "temperature": 0.3,
        "max_tokens": 600,
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        if r.status_code >= 400:
            print(f"  HTTP {r.status_code}: {r.text[:300]}")
            return None
        j = r.json()
        raw = j["choices"][0]["message"]["content"].strip()
        # 清理 markdown 包裹
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  JSON 解析失败: {e}, raw={raw[:200]}")
        return None
    except Exception as e:
        print(f"  请求异常: {e}")
        return None


def load_existing_index() -> dict:
    """加载已有索引用作断点续传"""
    if OUTPUT_PATH.exists():
        try:
            data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            return {item["id"]: item for item in data}
        except Exception:
            pass
    return {}


def save_index(items: list):
    """保存索引到文件"""
    OUTPUT_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Build template image index")
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--api-key", default=API_KEY)
    parser.add_argument("--model", default=CHAT_MODEL)
    parser.add_argument("--dry-run", action="store_true", help="仅扫描统计，不调用 API")
    args = parser.parse_args()

    if not args.dry_run and not args.api_key:
        print("错误: 需要 API Key。设置 NOVEL_API_KEY 环境变量或通过 --api-key 传入")
        sys.exit(1)

    print(f"模板目录: {TEMPLATES_DIR}")
    print(f"API: {args.api_url}, 模型: {args.model}")
    print()

    # 1. 扫描模板，按宽高比分组
    print("正在扫描模板图片...")
    ratio_groups: dict = {}  # ratio_category -> [path, ...]
    total = 0
    for f in sorted(TEMPLATES_DIR.iterdir()):
        if not f.is_file() or not f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        try:
            w, h = get_image_dimensions(f)
        except Exception:
            continue
        h_ratio = h / max(w, 1)
        w_ratio = w / max(h, 1)
        if 0.85 <= h_ratio <= 1.18:
            ratio = "1:1"
        elif h_ratio > 1.5:
            ratio = "9:16"
        elif w_ratio > 1.5:
            ratio = "16:9"
        else:
            ratio = "other"
        ratio_groups.setdefault(ratio, []).append(f)
        total += 1

    print(f"共 {total} 张图片")
    for ratio in ["1:1", "9:16", "16:9", "other"]:
        files = ratio_groups.get(ratio, [])
        print(f"  {ratio}: {len(files)} 张")

    if args.dry_run:
        return

    # 2. 加载已有索引（断点续传）
    existing = load_existing_index()
    print(f"\n已有索引: {len(existing)} 条记录")

    # 3. 按比例采样
    samples: list = []
    import random
    random.seed(42)
    for ratio, max_n in MAX_PER_RATIO.items():
        files = ratio_groups.get(ratio, [])
        remaining = [f for f in files if f.stem not in existing]
        if not remaining:
            print(f"  {ratio}: 全部已处理 ({len(files)} 张)")
            continue
        n = min(max_n, len(remaining))
        picked = random.sample(remaining, n)
        print(f"  {ratio}: 选取 {n}/{len(remaining)} 张待处理 (共{len(files)}张)")
        samples.extend(picked)

    if not samples:
        print("\n所有模板已处理完毕，无需再次调用 API。")
        return

    print(f"\n共需处理 {len(samples)} 张新图片\n")

    # 4. 逐张分析
    results = list(existing.values())
    for i, f in enumerate(samples):
        fid = f.stem
        dims = get_image_dimensions(f)
        ratio = "1:1"
        if dims[1] > dims[0] * 1.3:
            ratio = "9:16"
        elif dims[0] > dims[1] * 1.3:
            ratio = "16:9"
        elif dims[0] == dims[1] * 2:
            ratio = "2:1"

        print(f"[{i+1}/{len(samples)}] {f.name} ({dims[0]}x{dims[1]}, {ratio})")
        desc = analyze_image(f, args.api_url, args.api_key, args.model)

        if desc:
            desc["id"] = fid
            desc["path"] = str(f.relative_to(BASE_PATH))
            desc["size"] = f"{dims[0]}x{dims[1]}"
            desc["ratio"] = ratio
            results.append(desc)
            # 增量保存
            save_index(results)
            print(f"  -> style: {desc.get('style', 'N/A')[:80]}")
        else:
            print(f"  -> 失败，跳过")

        if i < len(samples) - 1:
            time.sleep(CALL_DELAY)

    print(f"\n完成! 共 {len(results)} 条记录 -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
