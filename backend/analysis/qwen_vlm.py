# backend/analysis/qwen_vlm.py
from __future__ import annotations

import base64
import os
import requests
from dotenv import load_dotenv
from typing import Any, Dict, Optional


# DashScope OpenAI compatible endpoint
# 你也可以通过环境变量覆盖：DASHSCOPE_BASE_URL
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 默认模型：你可按需换成控制台支持的 VL 模型
# 也可以通过环境变量覆盖：QWEN_VL_MODEL
DEFAULT_MODEL = "qwen-vl-plus"

# 默认超时（秒），可用环境变量覆盖：QWEN_TIMEOUT
DEFAULT_TIMEOUT = 60


def _load_env() -> None:
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    load_dotenv(os.path.join(root_dir, ".env"), override=True)
    load_dotenv(os.path.join(root_dir, "default.env"), override=False)


def _to_data_url(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def analyze_image(
    image_bytes: bytes,
    prompt: str,
    *,
    mime: str = "image/jpeg",
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Call Qwen VL (DashScope OpenAI-compatible API) with an image and a text prompt.

    Returns the raw JSON response which should contain:
      response["choices"][0]["message"]["content"]
    """

    _load_env()
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY not set")
    
    print("API Key repr:", repr(api_key))
    api_key = api_key.strip()
    print("API Key stripped repr:", repr(api_key))
    print("base url:", os.getenv("DASHSCOPE_BASE_URL"))

    base_url = os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    use_model = model or os.getenv("QWEN_VL_MODEL", DEFAULT_MODEL)

    if timeout is None:
        try:
            timeout = int(os.getenv("QWEN_TIMEOUT", str(DEFAULT_TIMEOUT)))
        except Exception:
            timeout = DEFAULT_TIMEOUT

    data_url = _to_data_url(image_bytes, mime=mime)

    payload: Dict[str, Any] = {
        "model": use_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": float(temperature),
    }

    # 可选限制输出长度（不设也行）
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f"Qwen request failed: {type(e).__name__}: {e}") from e

    # 非 200 直接抛错，带上响应内容方便排查
    if r.status_code != 200:
        # 有时会返回 JSON 结构的错误信息；但也可能是纯文本
        body = r.text
        raise RuntimeError(f"Qwen API error {r.status_code}: {body}")

    try:
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"Qwen API returned non-JSON: {r.text[:500]}") from e

    # 做一个最基本的健壮性校验：确保 content 在
    try:
        _ = data["choices"][0]["message"]["content"]
    except Exception:
        # 不直接失败也行，但这能让你更早发现接口返回结构不一致
        raise RuntimeError(f"Unexpected Qwen response format: {data}")

    return data
