"""
光影光效图生图（对齐 lighting-batch-jsonl / lighting-delivery-csv skill）。

JSONL 一行 = 一次生成（id 形如 stem-r1 / stem-r2 / stem-r3）：
  {"id": "xxx-r1", "prompt": "...", "image_path": "...", "light_type": "百叶窗",
   "prompt_kind": "去光", "prompt_target": "自然光", "run": 1}

产出:
  - AI 直出:   {id}.png
  - 追色结果:  {id}_colormatched.png   （交付阶段再按标签决定用哪个）
  - pipeline CSV: Prompt_with_images.csv（长表，供 lighting-delivery-csv 建交付表）

依赖: pip install requests tqdm
edit 模式非 jpeg/png/webp 需 Pillow；追色需 opencv-python-headless numpy
"""

import base64
import csv
import functools
import io
import json
import random
import re
import signal
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

print = functools.partial(print, flush=True)
print("gen_image_lighting.py 启动 python=%s" % sys.version.split()[0])

try:
    import requests
    from tqdm import tqdm
except ImportError as e:
    print("缺少依赖: %s" % e)
    print("请执行: pip3 install requests tqdm --user")
    sys.exit(1)

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

# ---- 出图后端：gateway（Azure 部署路径 + x-ks-* 头，默认）| klink（OpenAI 兼容 /v1/images/*，Bearer Key，body 带 model，如 gpt-image-2.5）----
API_BACKEND = "gateway"
KLINK_BASE_URL = "https://klink.example.com/klink"


def _load_klink_key() -> str:
    """KLink 推理 Key 不入库：优先环境变量 KLINK_API_KEY，其次脚本旁 secrets.json 的 "klink_api_key"。"""
    import os
    key = (os.environ.get("KLINK_API_KEY") or "").strip()
    if key:
        return key
    p = Path(__file__).resolve().parent / "secrets.json"
    if p.is_file():
        try:
            return str(json.loads(p.read_text(encoding="utf-8")).get("klink_api_key") or "").strip()
        except Exception as e:
            print("读取 secrets.json 失败: %s" % e)
    return ""

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
# 建连/读取分别超时；避免停止后被单个网络请求卡住 300 秒。
REQUEST_TIMEOUT = (15, 90)

# JSONL 输入路径（绝对路径；可用 --batch 覆盖）
WORK_DIR = "/mnt/kfs/dave/8.28/光影光效2"
INPUT_JSONL = WORK_DIR + "/Prompt_with_images.jsonl"
OUTPUT_JSONL = WORK_DIR + "/Prompt_with_images_out.jsonl"
OUTPUT_CSV = WORK_DIR + "/Prompt_with_images.csv"
DELIVERY_ROOT_NAME = "260818-光影光效2-1426"
OUTPUT_REL_PREFIX = DELIVERY_ROOT_NAME + "-qt-round1"
# 输出目录名需以 -qt-round1 结尾，lighting-delivery-csv 靠这个识别
IMAGE_OUT_DIR = WORK_DIR + "/" + OUTPUT_REL_PREFIX

BATCH_INDEX_PATH = Path(__file__).resolve().parent / "batches" / "index.json"


def apply_batch_config(batch_id: str) -> None:
    """按 batches/index.json 覆盖 WORK_DIR / JSONL / 输出目录。"""
    global WORK_DIR, INPUT_JSONL, OUTPUT_JSONL, OUTPUT_CSV
    global DELIVERY_ROOT_NAME, OUTPUT_REL_PREFIX, IMAGE_OUT_DIR
    global IMAGE_QUALITY, REQUEST_TIMEOUT, COLOR_MATCH_ENABLED
    global MODEL_ID, DEPLOYMENT_BASE_URL, GENERATIONS_URL, EDITS_URL
    global API_BACKEND, API_KEY
    index = json.loads(BATCH_INDEX_PATH.read_text(encoding="utf-8"))
    if batch_id not in index:
        raise SystemExit("未知 --batch %s；可选: %s" % (batch_id, list(index)))
    cfg = index[batch_id]
    WORK_DIR = cfg["work_dir"]
    # JSONL 固定写在脚本旁 batches/<id>/，由 build_jsonl_lighting.py 生成
    local_jsonl = Path(__file__).resolve().parent / cfg["jsonl"]
    INPUT_JSONL = str(local_jsonl)
    if not local_jsonl.is_file():
        raise SystemExit(
            "找不到 JSONL: %s\n请先在本目录执行: python3 -u build_jsonl_lighting.py --batch %s"
            % (local_jsonl, batch_id)
        )
    # 三批共用同一 work_dir 时，pipeline CSV 用带 batch id 的文件名
    pipeline_name = cfg.get("pipeline_csv") or "Prompt_with_images.csv"
    OUTPUT_JSONL = str(Path(WORK_DIR) / ("Prompt_with_images_%s_out.jsonl" % batch_id))
    OUTPUT_CSV = str(Path(WORK_DIR) / Path(pipeline_name).name)
    DELIVERY_ROOT_NAME = cfg["delivery_root"]
    OUTPUT_REL_PREFIX = cfg["output_rel_prefix"]
    IMAGE_OUT_DIR = str(Path(WORK_DIR) / OUTPUT_REL_PREFIX)
    # 出图模型按批次配置：config.model_id = 网关上的 deployment 名（缺省沿用脚本默认 gpt-image-2）；
    # 也可用 config.deployment_base_url 直接给完整的 .../deployments/<name>/images 前缀
    mid = str(cfg.get("model_id") or "").strip()
    dbu = str(cfg.get("deployment_base_url") or "").strip()
    if mid or dbu:
        if mid:
            MODEL_ID = mid
        DEPLOYMENT_BASE_URL = dbu or re.sub(r"/deployments/[^/]+/images$", "/deployments/%s/images" % MODEL_ID, DEPLOYMENT_BASE_URL)
        GENERATIONS_URL = f"{DEPLOYMENT_BASE_URL}/generations?api-version={GENERATIONS_API_VERSION}"
        EDITS_URL = f"{DEPLOYMENT_BASE_URL}/edits?api-version={EDITS_API_VERSION}"
        print("model override: MODEL_ID=%s EDITS_URL=%s" % (MODEL_ID, EDITS_URL))
    # 出图后端按批次配置：config.api_backend = gateway（默认）| klink。klink 走 OpenAI 兼容接口，model_id 即模型名（如 gpt-image-2.5）
    backend = str(cfg.get("api_backend") or "").strip().lower()
    if backend:
        if backend not in ("gateway", "klink"):
            raise SystemExit("config.api_backend 只能是 gateway/klink，收到: %s" % backend)
        API_BACKEND = backend
    if API_BACKEND == "klink":
        base = str(cfg.get("klink_base_url") or KLINK_BASE_URL).strip().rstrip("/")
        GENERATIONS_URL = base + "/v1/images/generations"
        EDITS_URL = base + "/v1/images/edits"
        API_KEY = _load_klink_key()
        if not API_KEY:
            raise SystemExit('api_backend=klink 需要 KLink 推理 Key：export KLINK_API_KEY=... 或在脚本同目录 secrets.json 写 "klink_api_key"')
        print("backend=klink model=%s EDITS_URL=%s" % (MODEL_ID, EDITS_URL))
    # 出图质量按批次配置：config.image_quality = low | medium | high | auto（缺省沿用脚本默认 low）
    q = str(cfg.get("image_quality") or "").strip().lower()
    if q:
        if q not in ("low", "medium", "high", "auto"):
            raise SystemExit("config.image_quality 只能是 low/medium/high/auto，收到: %s" % q)
        IMAGE_QUALITY = q
    if IMAGE_QUALITY != "low" and REQUEST_TIMEOUT[1] < 600:
        # high 单张实测 130s+，默认 90s 读超时会直接 Read timed out
        REQUEST_TIMEOUT = (REQUEST_TIMEOUT[0], 600)
    # 追色开关按批次配置：config.colormatch_enabled = false 时只写 plain（如 水墨画/像素 等自带色板的风格）；缺省沿用脚本默认 True
    cm = cfg.get("colormatch_enabled")
    if cm is not None:
        if isinstance(cm, str):
            cm = cm.strip().lower() in ("1", "true", "yes", "on")
        COLOR_MATCH_ENABLED = bool(cm)
    print("batch=%s work_dir=%s jsonl=%s out=%s csv=%s quality=%s read_timeout=%ss colormatch=%s" % (
        batch_id, WORK_DIR, INPUT_JSONL, IMAGE_OUT_DIR, OUTPUT_CSV, IMAGE_QUALITY, REQUEST_TIMEOUT[1],
        "on" if COLOR_MATCH_ENABLED else "off",
    ))

# True：目标 PNG 已存在则跳过，不重复调接口、不覆盖已出图
SKIP_EXISTING_OUTPUTS = True

PROMPT_KEY = "prompt"
ID_KEY = "id"
INPUT_IMAGE_PATH_KEY = "image_path"  # 输入 JSONL 中"跑前"原图路径字段名，edit 模式必填
MASK_PATH_KEY = "mask_path"  # 可选：局部编辑用的 mask 图路径字段名

# ---- 追色：始终同时产出 plain + colormatched；交付 CSV 再按标签挑选 ----
COLOR_MATCH_ENABLED = True
COLOR_MATCH_MODE = "mean"
COLOR_MATCH_STRENGTH = 1.0
REFERENCE_IMAGE_PATH_KEY = "color_ref_path"

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


_stop_event = threading.Event()


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
            if _stop_event.wait(sleep_s):
                raise InterruptedError("任务已停止")


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
    if API_BACKEND == "klink":
        # OpenAI 兼容：Bearer Key；模型名放在请求体 model 字段
        headers = {"Authorization": "Bearer " + API_KEY}
    else:
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
        img_resp = requests.get(url, timeout=REQUEST_TIMEOUT)
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
    payload = {
        "prompt": prompt,
        "size": IMAGE_SIZE,
        "quality": IMAGE_QUALITY,
        "output_compression": IMAGE_OUTPUT_COMPRESSION,
        "output_format": IMAGE_OUTPUT_FORMAT,
        "n": IMAGE_N,
    }
    return _adapt_payload_for_backend(payload)


def _adapt_payload_for_backend(payload: dict) -> dict:
    """klink（OpenAI 兼容）：请求体带 model；output_format=png 时不传 output_compression（原生接口只对 jpeg/webp 接受）。gateway 原样。"""
    if API_BACKEND == "klink":
        payload["model"] = MODEL_ID
        if str(payload.get("output_format", "")).lower() == "png":
            payload.pop("output_compression", None)
    return payload


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
        timeout=REQUEST_TIMEOUT,
    )


def _call_edits_api(
    prompt: str, input_image_path: str, mask_path: str = ""
) -> requests.Response:
    """图生图 (images/edits)，multipart/form-data 上传原图 + 可选 mask。"""
    data = _adapt_payload_for_backend({
        "prompt": prompt,
        "size": IMAGE_SIZE,
        "quality": IMAGE_QUALITY,
        "output_compression": str(IMAGE_OUTPUT_COMPRESSION),
        "output_format": IMAGE_OUTPUT_FORMAT,
        "n": str(IMAGE_N),
    })

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
        timeout=REQUEST_TIMEOUT,
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


def _pick_existing_pair(row_id: str, out_dir: Path) -> Tuple[str, str]:
    """返回 (plain_abs, cm_abs)，不存在则为空串。"""
    plain = out_dir / ("%s.png" % row_id)
    cm = out_dir / ("%s_colormatched.png" % row_id)
    return (
        str(plain.absolute()) if plain.is_file() else "",
        str(cm.absolute()) if cm.is_file() else "",
    )


def process_row(index: int, row: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    """一行 JSONL = 一次生成（skill 格式 id 已含 -rN）。"""
    prompt = (row.get(PROMPT_KEY) or row.get("Prompt") or "").strip()
    row_id = str(row.get(ID_KEY) or "%04d" % index).strip()
    input_image_path = str(
        row.get(INPUT_IMAGE_PATH_KEY) or row.get("Image_path") or ""
    ).strip()
    mask_path = str(row.get(MASK_PATH_KEY) or "").strip()
    reference_image_path = (
        str(row.get(REFERENCE_IMAGE_PATH_KEY) or "").strip() or input_image_path
    )
    light_type = str(row.get("light_type") or row.get("category") or "").strip()

    result = {
        ID_KEY: row_id,
        PROMPT_KEY: prompt,
        "light_type": light_type,
        "prompt_kind": str(row.get("prompt_kind") or ""),
        "prompt_target": str(row.get("prompt_target") or ""),
        "run": row.get("run", ""),
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

    out_dir.mkdir(parents=True, exist_ok=True)
    ai_output_path = out_dir / ("%s.png" % row_id)
    colormatch_output_path = out_dir / ("%s_colormatched.png" % row_id)

    existing_plain, existing_cm = _pick_existing_pair(row_id, out_dir)
    if SKIP_EXISTING_OUTPUTS and existing_plain:
        result["ai_output_image_path"] = existing_plain
        result["output_image_path"] = existing_cm or existing_plain
        result["image_status"] = "skipped_exists"
        return result

    normal_attempt = 0
    rate_limit_attempt = 0
    final_path = ""

    while True:
        if _stop_event.is_set():
            result["image_status"] = "cancelled"
            return result
        try:
            final_path = generate_and_save_image(
                prompt,
                str(ai_output_path),
                input_image_path,
                mask_path,
                reference_image_path,
                str(colormatch_output_path),
            )
            break
        except RateLimitError as e:
            rate_limit_attempt += 1
            if rate_limit_attempt > RATE_LIMIT_MAX_RETRIES:
                result["image_status"] = "failed: %s" % e
                return result
            wait_s = e.retry_after + random.uniform(0.5, 3.0)
            tqdm.write(
                "[RATE LIMIT] id=%s 等待 %.1fs (%d/%d)"
                % (row_id, wait_s, rate_limit_attempt, RATE_LIMIT_MAX_RETRIES)
            )
            if _stop_event.wait(wait_s):
                result["image_status"] = "cancelled"
                return result
        except ModerationBlockedError as e:
            result["image_status"] = "blocked_by_moderation: %s" % e
            return result
        except Exception as e:
            normal_attempt += 1
            if normal_attempt >= RETRY_TIMES:
                result["image_status"] = "failed: %s" % e
                return result

    plain_abs = str(ai_output_path.absolute()) if ai_output_path.is_file() else ""
    cm_abs = (
        str(colormatch_output_path.absolute())
        if colormatch_output_path.is_file()
        else ""
    )
    result["ai_output_image_path"] = plain_abs
    # pipeline CSV 的 output_image_path 指向追色图（若有），delivery skill 会按标签回退 plain
    result["output_image_path"] = cm_abs or final_path or plain_abs
    result["image_status"] = "success" if (plain_abs or final_path) else "failed"
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
    "ai_output_image_path",
    "output_image_path",
    "image_status",
    "model_id",
    "color_ref_path",
    "light_type",
    "prompt_kind",
    "prompt_target",
    "run",
]


def main() -> None:
    _stop_event.clear()
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
    print("共 %s 条任务（skill 格式：一行=一轮，id 含 -rN）" % len(rows))
    print("输出目录: %s" % img_out_dir)
    print(f"模式: {IMAGE_MODE}（{'图生图 images/edits' if IMAGE_MODE == 'edit' else '文生图 images/generations'}）")
    print(f"已有图跳过: {'开启' if SKIP_EXISTING_OUTPUTS else '关闭'}")
    print(f"并发数: {MAX_IMAGE_WORKERS}，模型: {MODEL_ID}，后端: {API_BACKEND}")
    print(f"质量: {IMAGE_QUALITY} · 尺寸: {IMAGE_SIZE} · 读超时: {REQUEST_TIMEOUT[1]}s")
    if COLOR_MATCH_ENABLED:
        print(
            "追色: 开启（始终写 plain + _colormatched；交付时按 no_colormatch_tags 挑选）"
        )
    else:
        print("追色: 关闭")
    print(f"限速: 约 {REQUESTS_PER_MINUTE} 次/分钟")
    print(f"结果 CSV / JSONL 实时写入: {output_csv}")

    success_count = 0
    fail_count = 0
    skip_count = 0

    interrupt_count = 0

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal interrupt_count
        interrupt_count += 1
        if interrupt_count == 1:
            _stop_event.set()
            print("\n收到 Ctrl+C：停止派发并取消排队任务；再次 Ctrl+C 可强制退出。")
        else:
            print("\n强制退出。已完成结果此前均已写盘。")
            import os
            os._exit(130)

    old_sigint_handler = signal.signal(signal.SIGINT, request_stop)
    ex = ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS)
    pending = {}
    try:
        with output_csv.open("w", encoding="utf-8-sig", newline="") as csv_f, output_jsonl.open(
            "w", encoding="utf-8"
        ) as jsonl_f:
            csv_writer = csv.DictWriter(csv_f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            csv_writer.writeheader()
            csv_f.flush()

            next_submit = 0
            next_write = 0
            completed_buffer = {}
            progress = tqdm(total=len(rows), desc="生成图片", unit="条")

            def submit_one(i: int) -> None:
                pending[ex.submit(process_row, i, rows[i], img_out_dir)] = i

            while next_submit < min(MAX_IMAGE_WORKERS, len(rows)):
                submit_one(next_submit)
                next_submit += 1

            while pending and not _stop_event.is_set():
                done, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                for fut in done:
                    idx = pending.pop(fut)
                    completed_buffer[idx] = fut.result()
                    if next_submit < len(rows) and not _stop_event.is_set():
                        submit_one(next_submit)
                        next_submit += 1

                while next_write in completed_buffer:
                    res = completed_buffer.pop(next_write)
                    csv_writer.writerow(res)
                    csv_f.flush()
                    jsonl_f.write(json.dumps(res, ensure_ascii=False) + "\n")
                    jsonl_f.flush()
                    progress.update(1)

                    status = res.get("image_status", "")
                    if status in ("success",):
                        success_count += 1
                    elif status in (
                        "skipped_empty_prompt",
                        "skipped_missing_input_image",
                        "skipped_exists",
                    ):
                        skip_count += 1
                    else:
                        fail_count += 1
                        tqdm.write("[FAIL] 行 %s: %s" % (next_write + 1, status))
                    next_write += 1
            progress.close()
    finally:
        if _stop_event.is_set():
            for fut in pending:
                fut.cancel()
        ex.shutdown(wait=not _stop_event.is_set(), cancel_futures=True)
        signal.signal(signal.SIGINT, old_sigint_handler)

    if _stop_event.is_set():
        print("任务已停止；已按原始输入顺序写入完成结果。")
        return

    print("\n" + "=" * 40)
    print("任务完成")
    print(f"成功: {success_count}")
    print(f"失败: {fail_count}")
    print(f"跳过(已有图/空 prompt): {skip_count}")
    print(f"结果 JSONL: {output_jsonl}")
    print(f"结果 CSV:  {output_csv}")
    print("下一步: python3 -u make_lighting_delivery_csv.py")
    print("=" * 40)


if __name__ == "__main__":
    print("进入 main()")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="0818 | 0819 | 0819b，从 batches/index.json 读路径")
    ap.add_argument("--input-jsonl", help="覆盖 INPUT_JSONL")
    args, _unknown = ap.parse_known_args()
    if args.batch:
        apply_batch_config(args.batch)
    if args.input_jsonl:
        INPUT_JSONL = args.input_jsonl
    if not API_KEY:
        sys.exit("缺少出图 API key：请设置环境变量 GATEWAY_API_KEY，或在脚本同目录放 secrets.json（参考 secrets.example.json）")
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
