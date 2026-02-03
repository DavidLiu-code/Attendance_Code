import json
import os
from dataclasses import dataclass

import httpx

DEFAULT_BASE_URL = "https://api-inference.modelscope.cn/v1"
DEFAULT_API_KEY = ""

DEFAULT_MODEL_CATALOG = {
    "fast": "Qwen/YourFastInstructModel",
    "balanced": "Qwen/YourBalancedInstructModel",
    "long": "Qwen/YourLongContextInstructModel",
    "summarizer": "Qwen/YourCheaperModelForSummaries",
}


def get_default_base_url() -> str:
    return os.getenv("MODELSCOPE_BASE_URL", DEFAULT_BASE_URL)


def get_default_api_key() -> str:
    return os.getenv("MODELSCOPE_API_KEY", DEFAULT_API_KEY)


def get_default_model_key() -> str:
    return os.getenv("DEFAULT_MODEL_KEY", "balanced")


def load_model_catalog(raw: str | None = None) -> dict:
    if raw is None:
        raw = os.getenv("MODEL_CATALOG_JSON", "")
    if not raw:
        return DEFAULT_MODEL_CATALOG.copy()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return DEFAULT_MODEL_CATALOG.copy()
    if isinstance(data, dict) and data:
        return data
    return DEFAULT_MODEL_CATALOG.copy()


MODEL_CATALOG = load_model_catalog()
DEFAULT_MODEL_KEY = get_default_model_key()


def get_model_name(
    model_key: str,
    model_catalog: dict | None = None,
    default_model_key: str | None = None,
) -> str:
    catalog = model_catalog or MODEL_CATALOG
    default_key = default_model_key or DEFAULT_MODEL_KEY
    return (
        catalog.get(model_key)
        or catalog.get(default_key)
        or DEFAULT_MODEL_CATALOG.get("balanced")
        or "Qwen/YourBalancedInstructModel"
    )


@dataclass
class LLMResponse:
    content: str


class LLMClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        resolved_api_key = api_key if api_key is not None else get_default_api_key()
        resolved_base_url = base_url if base_url is not None else get_default_base_url()
        self.api_key = resolved_api_key
        self.base_url = (resolved_base_url or "").rstrip("/")

    def chat_completions(
        self,
        model_name: str,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 800,
        stream: bool = False,
    ) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError("MODELSCOPE_API_KEY is not set.")
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return LLMResponse(content=content)


_DEFAULT_CLIENT: LLMClient | None = None
_DEFAULT_CLIENT_CONFIG: tuple[str, str] | None = None


def get_llm_client(api_key: str | None = None, base_url: str | None = None) -> LLMClient:
    resolved_api_key = api_key if api_key is not None else get_default_api_key()
    resolved_base_url = base_url if base_url is not None else get_default_base_url()
    resolved_base_url = (resolved_base_url or "").rstrip("/")
    global _DEFAULT_CLIENT
    global _DEFAULT_CLIENT_CONFIG
    if _DEFAULT_CLIENT is None or _DEFAULT_CLIENT_CONFIG != (resolved_api_key, resolved_base_url):
        _DEFAULT_CLIENT = LLMClient(resolved_api_key, resolved_base_url)
        _DEFAULT_CLIENT_CONFIG = (resolved_api_key, resolved_base_url)
    return _DEFAULT_CLIENT
