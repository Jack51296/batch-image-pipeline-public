"""
从 JSONL 读取 prompt，调用 GPT-Image (OpenAI images/generations 或 images/edits) API 批量生成图片。

依赖: pip install requests tqdm
edit 模式下如果原图不是 jpeg/png/webp（比如 bmp/tiff），需要额外: pip install Pillow（自动转码成 png）
追色后处理（见 COLOR_MATCH_ENABLED）需要: pip install opencv-python-headless numpy

JSONL 格式（每行一条）:
  {"id": "0001", "prompt": "...", "image_path": "跑前原图路径（edit 模式必填，generate 模式可选）",
   "color_ref_path": "可选，追色参考图路径，留空默认用 image_path（跑前原图）"}

两种模式（见下方 IMAGE_MODE）:
  - "generate": 文生图，调用 images/generations，只需要 prompt
  - "edit":     图生图，调用 images/edits，需要 prompt + input_image_path（原图），
                可选再带 mask_path 做局部编辑

追色后处理:
  生成完图片后，若 COLOR_MATCH_ENABLED=True，会自动把生成图的色调迁移到参考图
  （默认用跑前原图）。"mean" 模式只追颜色（LAB 的 a/b 通道）、不动 L 通道；
  "mean_std" 模式连 L（亮度）一起做均值+标准差对齐，明暗也追到参考图；
  "local" 模式做空间自适应低频迁移，肤色/背景各自对齐（要求两图构图基本一致）。
"""

import base64
import csv
import io
import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from tqdm import tqdm

# ============ 配置区 ============
# "generate": 文生图 images/generations；"edit": 图生图 images/edits
IMAGE_MODE = "edit"

DEPLOYMENT_BASE_URL = (
    "http://llm-gateway.example.internal/<gateway-user-key>/openai/deployments/"
    "<gpt-image-2-deployment>/images"
)
GENERATIONS_API_VERSION = "2024-02-01"
EDITS_API_VERSION = "2025-04-01-preview"

GENERATIONS_URL = f"{DEPLOYMENT_BASE_URL}/generations?api-version={GENERATIONS_API_VERSION}"
EDITS_URL = f"{DEPLOYMENT_BASE_URL}/edits?api-version={EDITS_API_VERSION}"

def _load_api_key() -> str:
    """出图网关 key 不入库：优先环境变量 GATEWAY_API_KEY，其次脚本旁 secrets.json 的 "api_key"（见 secrets.example.json）。"""
    import os
    key = (os.environ.get("GATEWAY_API_KEY") or "").strip()
    if key:
        return key
    p = Path(__file__).resolve().parent / "secrets.json"
    if p.is_file():
        try:
            return str(json.loads(p.read_text(encoding="utf-8")).get("api_key") or "").strip()
        except Exception as e:  # 配置坏了给提示，不吞掉
            print("读取 secrets.json 失败: %s" % e)
    return ""


API_KEY = _load_api_key()
USER_KEY = "<gateway-user-key>"

MODEL_ID = "<gpt-image-2-deployment>"

BIZ_SCENE = "offline"

# 图片生成参数
# size 支持: "1024x1024"/"1536x1024"/"1024x1536" 等标准尺寸，或任意 "宽x高"（gpt-image-2）
# "auto" = 让模型自动根据 prompt / 原图选择合适画幅比例（图生图时更贴合原图比例）
IMAGE_SIZE = "auto"
IMAGE_QUALITY = "low"
IMAGE_OUTPUT_COMPRESSION = 100
IMAGE_OUTPUT_FORMAT = "png"
IMAGE_N = 1

TIMEOUT_SECONDS = 300

# JSONL 输入路径（绝对路径，请按需修改）
INPUT_JSONL = "/ytech_milm/frank/chatgpt_ketu/美妆/0827/新中式妆容round_1/新中式妆_Prompt_with_images.jsonl"
OUTPUT_JSONL = "/ytech_milm/frank/chatgpt_ketu/美妆/0827/新中式妆容round_1/Prompt_with_image.jsonl"
OUTPUT_CSV = "/ytech_milm/frank/chatgpt_ketu/美妆/0827/新中式妆容round_1/Prompt_with_images.csv"

IMAGE_OUT_DIR = "/ytech_milm/frank/chatgpt_ketu/美妆/0827/新中式妆容round_1-"

PROMPT_KEY = "prompt"
ID_KEY = "id"
INPUT_IMAGE_PATH_KEY = "image_path"  # 输入 JSONL 中"跑前"原图路径字段名，edit 模式必填
MASK_PATH_KEY = "mask_path"  # 可选：局部编辑用的 mask 图路径字段名

# ---- 追色（Color Match）后处理配置 ----
# 生成图片后，自动把色调迁移到参考图，只迁移颜色（LAB 的 a/b 通道），
# 亮度 / 高光结构保持生成结果原样不变（不追高光，只追颜色）。
# AI 直出图始终保存为 "<id>.png"；追色开启且成功时，额外保存追色结果为 "<id>_colormatched.png"。
COLOR_MATCH_ENABLED = False
COLOR_MATCH_MODE = "mean"  # "mean" = 只迁移整体色调、不动亮度；"mean_std" = L/a/b 均值+标准差全局迁移；"local" = 空间自适应低频迁移（人像/图生图推荐，肤色和背景分别对齐）
COLOR_MATCH_STRENGTH = 1.0  # 0~1，1 = 完全迁移到参考图色调，调小可让追色效果更柔和
REFERENCE_IMAGE_PATH_KEY = "color_ref_path"  # JSONL 中可选字段：显式指定追色参考图路径；留空则默认用 input_image_path（跑前原图）

MAX_IMAGE_WORKERS = 5  # S0 计费档位限流较严，建议先用较小并发试跑，视 429 情况再调大
REQUESTS_PER_MINUTE = 8  # 全局限速：所有线程加起来平均每分钟最多发起多少次生成请求，按实际配额调整
RETRY_TIMES = 3  # 非限流类错误（超时/5xx 等）的重试次数
RATE_LIMIT_MAX_RETRIES = 6  # 命中 429 限流时的最大重试次数（按 Retry-After 等待后重试）
RATE_LIMIT_DEFAULT_WAIT_SECONDS = 60  # 响应中解析不到 Retry-After 时的默认等待秒数
# ================================


class RateLimitError(RuntimeError):
    """标记 HTTP 429 限流错误，携带服务端建议的等待时间（秒）。"""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


class ModerationBlockedError(RuntimeError):
    """内容被安全审核拦截，重试无意义，应直接判失败。"""


class _RateLimiter:
    """全局请求节流器，保证所有线程加起来的请求间隔不小于 min_interval。"""

    def __init__(self, requests_per_minute: float):
        self._min_interval = (
            60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        )
        self._lock = threading.Lock()
        self._next_allowed_time = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            start_at = max(now, self._next_allowed_time)
            self._next_allowed_time = start_at + self._min_interval
        sleep_s = start_at - now
        if sleep_s > 0:
            time.sleep(sleep_s)


_rate_limiter = _RateLimiter(REQUESTS_PER_MINUTE)


def _parse_retry_after(resp: requests.Response) -> float:
    header_val = resp.headers.get("Retry-After")
    if header_val:
        try:
            return float(header_val)
        except ValueError:
            pass
    match = re.search(r"retry after (\d+(?:\.\d+)?) seconds", resp.text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return RATE_LIMIT_DEFAULT_WAIT_SECONDS


def _api_headers(with_json_content_type: bool = True) -> Dict[str, str]:
    headers = {
        "x-api-key": API_KEY,
        "x-ks-user-key": USER_KEY,
        "x-ks-llm-model": MODEL_ID,
        "x-ks-biz-scene": BIZ_SCENE,
    }
    if with_json_content_type:
        headers["Content-Type"] = "application/json"
    return headers


def extract_image_bytes(resp_json: dict) -> bytes:
    data_list = resp_json.get("data") or []
    if not data_list:
        raise ValueError(
            "未找到 data。响应: "
            + json.dumps(resp_json, ensure_ascii=False)[:500]
        )

    item = data_list[0]
    b64_data = item.get("b64_json")
    if b64_data:
        return base64.b64decode(b64_data)

    url = item.get("url")
    if url:
        img_resp = requests.get(url, timeout=TIMEOUT_SECONDS)
        img_resp.raise_for_status()
        return img_resp.content

    raise ValueError(
        "响应中未找到图片 b64_json/url。响应: "
        + json.dumps(resp_json, ensure_ascii=False)[:500]
    )


def match_color_to_reference(
    generated_png_bytes: bytes,
    reference_image_path: str,
    strength: float = 1.0,
    mode: str = "mean",
) -> bytes:
    """把生成图的色调迁移到参考图。

    strength: 0~1，控制迁移强度，1 表示完全对齐。
    mode:
      - "mean"（默认）: 只迁移 a/b 通道的均值（整体色调偏移/白平衡），不动 L 通道，
        原图自身的高光、彩色光影分布完全保留，不会把参考图里的彩色光效果带过来。
      - "mean_std": L/a/b 三个通道的均值 + 标准差一起对齐（Reinhard 颜色迁移），
        亮度和整体明暗对比也会追到参考图，风格最贴近参考图，但会连带迁移参考图的
        光照（可能出现"连光线颜色/明暗都被追过去了"的效果）。
      - "local": 空间自适应迁移（人像推荐）。要求生成图和参考图构图基本对齐
        （图生图场景天然满足）。把两图的 L/a/b 低频（大尺度）颜色场做逐像素对齐，
        肤色区域用肤色的修正量、背景用背景的修正量，解决全局统计对齐后
        肤色/背景仍有细微色差的问题；高频细节（纹理、五官边缘）保持生成结果不变。
    """
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise RuntimeError(
            "追色功能需要 opencv-python-headless 和 numpy，请先: "
            "pip install opencv-python-headless numpy"
        ) from e

    gen_img = cv2.imdecode(
        np.frombuffer(generated_png_bytes, dtype=np.uint8), cv2.IMREAD_UNCHANGED
    )
    if gen_img is None:
        raise ValueError("追色失败：无法解码生成图片")

    # 不用 cv2.imread(path) 读参考图：Windows 上 OpenCV 对中文/非 ASCII 路径的 imread 会读取失败，
    # 改为用 Python 自带文件 IO 读字节再交给 imdecode，规避该限制。
    with open(reference_image_path, "rb") as f:
        ref_bytes = f.read()
    ref_img = cv2.imdecode(np.frombuffer(ref_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if ref_img is None:
        raise ValueError(f"追色失败：无法读取参考图 {reference_image_path}")

    has_alpha = gen_img.ndim == 3 and gen_img.shape[2] == 4
    if has_alpha:
        alpha = gen_img[:, :, 3]
        gen_bgr = gen_img[:, :, :3]
    elif gen_img.ndim == 2:
        gen_bgr = cv2.cvtColor(gen_img, cv2.COLOR_GRAY2BGR)
    else:
        gen_bgr = gen_img[:, :, :3]

    gen_lab = cv2.cvtColor(gen_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    ref_lab = cv2.cvtColor(ref_img, cv2.COLOR_BGR2LAB).astype(np.float32)

    if mode == "local":
        # 空间自适应：对齐两图的低频颜色场（逐像素修正量随位置变化）。
        # 做法：把 LAB 图缩到很小的网格（只剩大尺度光色分布，纹理/五官细节全部丢弃），
        # 求参考图与生成图的低频差，再平滑放大回原尺寸后加到生成图上。
        h, w = gen_lab.shape[:2]
        grid = 32  # 低频网格边长：越小修正越"全局"，越大越贴参考图局部颜色（也越依赖两图对齐）
        gen_low = cv2.resize(gen_lab, (grid, grid), interpolation=cv2.INTER_AREA)
        ref_low = cv2.resize(ref_lab, (grid, grid), interpolation=cv2.INTER_AREA)
        # 网格上再模糊一次，消除构图轻微不对齐（人物姿态/边缘位移）造成的错位修正
        diff_low = cv2.GaussianBlur(ref_low - gen_low, (0, 0), sigmaX=1.5)
        diff_full = cv2.resize(diff_low, (w, h), interpolation=cv2.INTER_CUBIC)
        matched_lab = gen_lab + diff_full * strength
    else:
        matched_lab = gen_lab.copy()
        # mean 模式只处理 a/b（色度）通道，L（亮度/高光）原样保留；
        # mean_std 模式连 L（亮度）一起做均值+标准差对齐，把参考图的明暗也迁移过来
        channels = (0, 1, 2) if mode == "mean_std" else (1, 2)
        for ch in channels:
            g = gen_lab[:, :, ch]
            r = ref_lab[:, :, ch]
            g_mean = float(g.mean())
            r_mean = float(r.mean())
            if mode == "mean_std":
                g_std = float(g.std()) + 1e-6
                r_std = float(r.std()) + 1e-6
                transferred = (g - g_mean) * (r_std / g_std) + r_mean
            else:
                # 只做整体偏移，不缩放方差：保留原图自身的局部光影/彩色高光结构
                transferred = g + (r_mean - g_mean)
            matched_lab[:, :, ch] = g + (transferred - g) * strength

    matched_bgr = cv2.cvtColor(
        np.clip(matched_lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR
    )

    if has_alpha:
        matched_out = cv2.cvtColor(matched_bgr, cv2.COLOR_BGR2BGRA)
        matched_out[:, :, 3] = alpha
    else:
        matched_out = matched_bgr

    ok, encoded = cv2.imencode(".png", matched_out)
    if not ok:
        raise ValueError("追色失败：编码 PNG 失败")
    return encoded.tobytes()


def build_generate_payload(prompt: str) -> dict:
    """文生图 (images/generations) 的 JSON body。"""
    return {
        "prompt": prompt,
        "size": IMAGE_SIZE,
        "quality": IMAGE_QUALITY,
        "output_compression": IMAGE_OUTPUT_COMPRESSION,
        "output_format": IMAGE_OUTPUT_FORMAT,
        "n": IMAGE_N,
    }


# images/edits 接口只接受这三种 mimetype，其余格式（bmp/tiff/gif 等）需转码
_SUPPORTED_EDIT_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _detect_mime_from_bytes(header: bytes) -> Optional[str]:
    """按文件真实内容（magic bytes）判断格式，不依赖文件名/扩展名。"""
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def _load_image_for_upload(path: str) -> Tuple[bytes, str, str]:
    """读取图片字节；若不是接口支持的 jpeg/png/webp，自动转码成 png。"""
    with open(path, "rb") as f:
        raw = f.read()

    mime = _detect_mime_from_bytes(raw[:16])
    if mime in _SUPPORTED_EDIT_MIME_TYPES:
        return raw, Path(path).name, mime

    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(
            f"图片格式不受支持（需 jpeg/png/webp）且未安装 Pillow 无法自动转换: {path}"
            "，请先 pip install Pillow"
        ) from e

    with Image.open(io.BytesIO(raw)) as img:
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, format="PNG")
        return buf.getvalue(), Path(path).stem + ".png", "image/png"


def _call_generations_api(prompt: str) -> requests.Response:
    return requests.post(
        GENERATIONS_URL,
        headers=_api_headers(with_json_content_type=True),
        json=build_generate_payload(prompt),
        timeout=TIMEOUT_SECONDS,
    )


def _call_edits_api(
    prompt: str, input_image_path: str, mask_path: str = ""
) -> requests.Response:
    """图生图 (images/edits)，multipart/form-data 上传原图 + 可选 mask。"""
    data = {
        "prompt": prompt,
        "size": IMAGE_SIZE,
        "quality": IMAGE_QUALITY,
        "output_compression": str(IMAGE_OUTPUT_COMPRESSION),
        "output_format": IMAGE_OUTPUT_FORMAT,
        "n": str(IMAGE_N),
    }

    image_bytes, image_name, image_mime = _load_image_for_upload(input_image_path)
    files = {"image": (image_name, image_bytes, image_mime)}

    if mask_path:
        mask_bytes, mask_name, mask_mime = _load_image_for_upload(mask_path)
        files["mask"] = (mask_name, mask_bytes, mask_mime)

    # 不手动设置 Content-Type，交给 requests 自动带上 multipart boundary
    return requests.post(
        EDITS_URL,
        headers=_api_headers(with_json_content_type=False),
        data=data,
        files=files,
        timeout=TIMEOUT_SECONDS,
    )


def generate_and_save_image(
    prompt: str,
    ai_output_path: str,
    input_image_path: str = "",
    mask_path: str = "",
    reference_image_path: str = "",
    colormatch_output_path: str = "",
) -> str:
    """调用接口生成图片。AI 直出的原图始终保存到 ai_output_path；

    若开启追色且成功，额外把追色结果保存到 colormatch_output_path。
    返回最终应使用的图片路径（追色成功则是追色结果，否则是 AI 直出图）。
    """
    _rate_limiter.wait()

    if IMAGE_MODE == "edit":
        if not input_image_path:
            raise ValueError("edit 模式需要 input_image_path，但该行为空")
        if not Path(input_image_path).is_file():
            raise FileNotFoundError(f"找不到原图: {input_image_path}")
        resp = _call_edits_api(prompt, input_image_path, mask_path)
    else:
        resp = _call_generations_api(prompt)

    if resp.status_code == 429:
        raise RateLimitError(f"HTTP 429: {resp.text}", _parse_retry_after(resp))
    if resp.status_code == 400:
        try:
            err_code = (resp.json().get("error") or {}).get("code")
        except ValueError:
            err_code = None
        if err_code == "moderation_blocked":
            raise ModerationBlockedError(f"HTTP 400: {resp.text}")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

    img_bytes = extract_image_bytes(resp.json())

    # AI 直出图（追色前）始终保存，方便追溯/对比
    with open(ai_output_path, "wb") as f:
        f.write(img_bytes)

    if COLOR_MATCH_ENABLED and reference_image_path:
        try:
            matched_bytes = match_color_to_reference(
                img_bytes, reference_image_path, COLOR_MATCH_STRENGTH, COLOR_MATCH_MODE
            )
        except Exception as e:
            tqdm.write(f"[COLOR MATCH] 追色失败，使用未追色的 AI 直出图: {e}")
        else:
            with open(colormatch_output_path, "wb") as f:
                f.write(matched_bytes)
            return colormatch_output_path

    return ai_output_path


def process_row(index: int, row: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    prompt = (row.get(PROMPT_KEY) or row.get("Prompt") or "").strip()
    row_id = str(row.get(ID_KEY) or f"{index:04d}").strip()
    input_image_path = str(
        row.get(INPUT_IMAGE_PATH_KEY) or row.get("Image_path") or ""
    ).strip()
    mask_path = str(row.get(MASK_PATH_KEY) or "").strip()
    reference_image_path = (
        str(row.get(REFERENCE_IMAGE_PATH_KEY) or "").strip() or input_image_path
    )
    result = {
        ID_KEY: row_id,
        PROMPT_KEY: prompt,
        "input_image_path": input_image_path,
        "ai_output_image_path": "",
        "output_image_path": "",
        "image_status": "",
        "model_id": MODEL_ID,
        "color_ref_path": reference_image_path if COLOR_MATCH_ENABLED else "",
    }

    if not prompt:
        result["image_status"] = "skipped_empty_prompt"
        return result

    if IMAGE_MODE == "edit" and not input_image_path:
        result["image_status"] = "skipped_missing_input_image"
        return result

    ai_output_path = out_dir / f"{row_id}.png"
    colormatch_output_path = out_dir / f"{row_id}_colormatched.png"

    normal_attempt = 0
    rate_limit_attempt = 0

    while True:
        try:
            final_path = generate_and_save_image(
                prompt,
                str(ai_output_path),
                input_image_path,
                mask_path,
                reference_image_path,
                str(colormatch_output_path),
            )
            result["ai_output_image_path"] = str(ai_output_path.absolute())
            result["output_image_path"] = str(Path(final_path).absolute())
            result["image_status"] = "success"
            return result
        except RateLimitError as e:
            rate_limit_attempt += 1
            if rate_limit_attempt > RATE_LIMIT_MAX_RETRIES:
                result["image_status"] = f"failed: {e}"
                return result
            wait_s = e.retry_after + random.uniform(0.5, 3.0)
            tqdm.write(
                f"[RATE LIMIT] id={row_id} 触发限流，等待 {wait_s:.1f}s 后重试 "
                f"({rate_limit_attempt}/{RATE_LIMIT_MAX_RETRIES})"
            )
            time.sleep(wait_s)
        except ModerationBlockedError as e:
            # 内容被安全审核拦截，重试也不会成功，直接判失败
            result["image_status"] = f"blocked_by_moderation: {e}"
            return result
        except Exception as e:
            normal_attempt += 1
            if normal_attempt >= RETRY_TIMES:
                result["image_status"] = f"failed: {e}"
                return result


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"JSONL 第 {line_no} 行解析失败: {e}") from e
    return rows


CSV_FIELDNAMES = [
    ID_KEY,
    PROMPT_KEY,
    "input_image_path",
    "ai_output_image_path",  # AI 直出图（追色前）
    "output_image_path",  # 最终图：追色开启且成功时为追色结果，否则等于 ai_output_image_path
    "image_status",
    "model_id",
    "color_ref_path",
]


def main() -> None:
    input_file = Path(INPUT_JSONL).resolve()
    output_jsonl = Path(OUTPUT_JSONL).resolve()
    output_csv = Path(OUTPUT_CSV).resolve()
    img_out_dir = Path(IMAGE_OUT_DIR)

    print(f"JSONL 输入路径: {input_file}")

    if not input_file.exists():
        print(f"找不到输入文件: {input_file}")
        return

    rows = read_jsonl(input_file)

    if not rows:
        print("JSONL 无数据行")
        return

    if not any(row.get(PROMPT_KEY) or row.get("Prompt") for row in rows):
        print(f"JSONL 缺少字段: {PROMPT_KEY}")
        print(f"首行字段: {list(rows[0].keys())}")
        return

    if IMAGE_MODE == "edit" and not any(
        row.get(INPUT_IMAGE_PATH_KEY) or row.get("Image_path") for row in rows
    ):
        print(f"当前为 edit（图生图）模式，但 JSONL 缺少字段: {INPUT_IMAGE_PATH_KEY}")
        print(f"首行字段: {list(rows[0].keys())}")
        return

    img_out_dir.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    print(f"共 {len(rows)} 条 prompt")
    print(f"模式: {IMAGE_MODE}（{'图生图 images/edits' if IMAGE_MODE == 'edit' else '文生图 images/generations'}）")
    print(f"图片保存到: {img_out_dir.absolute()}")
    print(f"并发数: {MAX_IMAGE_WORKERS}，模型: {MODEL_ID}")
    if COLOR_MATCH_ENABLED:
        print(
            f"追色: 开启（模式 {COLOR_MATCH_MODE}，强度 {COLOR_MATCH_STRENGTH}，"
            f"参考图默认用 {INPUT_IMAGE_PATH_KEY}，可用 {REFERENCE_IMAGE_PATH_KEY} 字段覆盖，"
            + {
                "mean_std": "全局迁移亮度+颜色",
                "local": "空间自适应低频迁移（肤色/背景分别对齐）",
            }.get(COLOR_MATCH_MODE, "只追颜色不追亮度")
            + "）"
        )
    else:
        print("追色: 关闭")
    print(f"限速: 约 {REQUESTS_PER_MINUTE} 次/分钟（全局节流，命中 429 会自动等待重试）")
    print(f"结果 CSV / JSONL 会实时写入，中断也不会丢已完成的部分: {output_csv}")

    success_count = 0
    fail_count = 0
    skip_count = 0

    with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as ex, output_csv.open(
        "w", encoding="utf-8-sig", newline=""
    ) as csv_f, output_jsonl.open("w", encoding="utf-8") as jsonl_f:
        csv_writer = csv.DictWriter(csv_f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        csv_writer.writeheader()
        csv_f.flush()

        futures = {
            ex.submit(process_row, i + 1, row, img_out_dir): i
            for i, row in enumerate(rows)
        }
        for fut in tqdm(as_completed(futures), total=len(futures), desc="生成图片", unit="张"):
            idx = futures[fut]
            res = fut.result()

            # 每完成一条立即写盘并 flush，中断/断电也只丢失最后未完成的部分
            csv_writer.writerow(res)
            csv_f.flush()
            jsonl_f.write(json.dumps(res, ensure_ascii=False) + "\n")
            jsonl_f.flush()

            status = res.get("image_status", "")
            if status == "success":
                success_count += 1
            elif status in ("skipped_empty_prompt", "skipped_missing_input_image"):
                skip_count += 1
            else:
                fail_count += 1
                tqdm.write(f"[FAIL] 行 {idx + 1}: {status}")

    print("\n" + "=" * 40)
    print("任务完成")
    print(f"成功: {success_count}")
    print(f"失败: {fail_count}")
    print(f"跳过(空 prompt): {skip_count}")
    print(f"结果 JSONL: {output_jsonl}")
    print(f"结果 CSV:  {output_csv}")
    print("=" * 40)


if __name__ == "__main__":
    main()
