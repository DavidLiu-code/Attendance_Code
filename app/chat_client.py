import json
import os
from dataclasses import dataclass

import httpx

MODELSCOPE_BASE_URL = os.getenv("MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1")
MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY", "")

DEFAULT_MODEL_CATALOG = {
    "fast": "Qwen/YourFastInstructModel",
    "balanced": "Qwen/YourBalancedInstructModel",
    "long": "Qwen/YourLongContextInstructModel",
    "summarizer": "Qwen/YourCheaperModelForSummaries",
}


def load_model_catalog() -> dict:
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
DEFAULT_MODEL_KEY = os.getenv("DEFAULT_MODEL_KEY", "balanced")


def get_model_name(model_key: str) -> str:
    catalog = MODEL_CATALOG
    return catalog.get(model_key) or catalog.get(DEFAULT_MODEL_KEY) or "Qwen/YourBalancedInstructModel"


@dataclass
class LLMResponse:
    content: str


class LLMClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or MODELSCOPE_API_KEY
        self.base_url = (base_url or MODELSCOPE_BASE_URL).rstrip("/")

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


def get_llm_client() -> LLMClient:
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = LLMClient()
    return _DEFAULT_CLIENT
