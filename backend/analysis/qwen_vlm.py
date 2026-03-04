# backend/analysis/qwen_vlm.py
from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


# DashScope OpenAI compatible endpoint
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-vl-plus"
DEFAULT_TIMEOUT = 60


def _load_env() -> None:
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    load_dotenv(os.path.join(root_dir, ".env"), override=True)
    load_dotenv(os.path.join(root_dir, "default.env"), override=False)


def _to_data_url(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _extract_content(resp: Dict[str, Any]) -> str:
    return resp["choices"][0]["message"]["content"]


def _post_qwen_chat_completions(
    *,
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    data_url: str,
    temperature: float,
    max_tokens: Optional[int],
    timeout: int,
    inject_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Post a single image+text request to the Qwen (DashScope OpenAI-compatible) endpoint.

    inject_text:
      - Optional injected system-like text that will be prepended to the prompt.
      - This is useful to pass numeric temperature stats, downsampled matrices, region stats, etc.
    """
    text = prompt
    if inject_text:
        # Prepend injected data so the model reads it first
        text = inject_text.strip() + "\n\n" + prompt

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": float(temperature),
    }

    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f"Qwen request failed: {type(e).__name__}: {e}") from e

    if r.status_code != 200:
        raise RuntimeError(f"Qwen API error {r.status_code}: {r.text}")

    try:
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"Qwen API returned non-JSON: {r.text[:500]}") from e

    # Basic schema check
    try:
        _ = data["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError(f"Unexpected Qwen response format: {data}")

    return data


def analyze_image(
    image_bytes: bytes,
    prompt: str,
    *,
    mime: str = "image/jpeg",
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
    inject_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Backward-compatible single-prompt call.

    Returns raw JSON response:
      response["choices"][0]["message"]["content"]

    inject_text:
      - Optional injected text prepended to the prompt.
    """
    _load_env()
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY not set")
    api_key = api_key.strip()

    base_url = os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    use_model = model or os.getenv("QWEN_VL_MODEL", DEFAULT_MODEL)

    if timeout is None:
        try:
            timeout = int(os.getenv("QWEN_TIMEOUT", str(DEFAULT_TIMEOUT)))
        except Exception:
            timeout = DEFAULT_TIMEOUT

    data_url = _to_data_url(image_bytes, mime=mime)

    return _post_qwen_chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=use_model,
        prompt=prompt,
        data_url=data_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        inject_text=inject_text,
    )
    
def analyze_image_one_prompt(
    image_bytes: bytes,
    prompt: str,
    *,
    mime: str = "image/jpeg",
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
    inject_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Single-call Qwen VL interface (preferred).
    Returns a compact dict:
      {
        "prompt": <final prompt without inject>,
        "content": <assistant text>,
        "raw": <raw response json>
      }
    """
    resp = analyze_image(
        image_bytes=image_bytes,
        prompt=prompt,
        mime=mime,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        inject_text=inject_text,
    )
    return {
        "prompt": prompt,
        "content": _extract_content(resp),
        "raw": resp,
    }


