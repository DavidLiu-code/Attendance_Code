import json

from sqlalchemy.orm import Session

from .chat_client import (
    get_default_api_key,
    get_default_base_url,
    get_default_model_key,
    load_model_catalog,
)
from .chat_retrieval import get_default_retrieval_enabled, get_default_retrieval_k
from .models import ChatSettings


def fetch_chat_settings(db: Session) -> ChatSettings | None:
    return db.query(ChatSettings).order_by(ChatSettings.id.desc()).first()


def get_chat_config(db: Session) -> dict:
    row = fetch_chat_settings(db)
    model_catalog = load_model_catalog(row.model_catalog_json if row else None)
    default_model_key = row.default_model_key if row and row.default_model_key else get_default_model_key()
    base_url = row.base_url if row and row.base_url else get_default_base_url()
    api_key = row.api_key if row and row.api_key else get_default_api_key()
    if row and row.enable_retrieval is not None:
        enable_retrieval = row.enable_retrieval
    else:
        enable_retrieval = get_default_retrieval_enabled()
    if row and row.retrieval_k is not None:
        retrieval_k = row.retrieval_k
    else:
        retrieval_k = get_default_retrieval_k()
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model_catalog": model_catalog,
        "default_model_key": default_model_key,
        "enable_retrieval": enable_retrieval,
        "retrieval_k": retrieval_k,
    }


def get_chat_settings_view(db: Session) -> dict:
    row = fetch_chat_settings(db)
    config = get_chat_config(db)
    if row and row.model_catalog_json:
        model_catalog_json = row.model_catalog_json
    else:
        model_catalog_json = json.dumps(
            config["model_catalog"],
            ensure_ascii=False,
            indent=2,
        )
    return {
        "api_key_set": bool(config["api_key"]),
        "base_url": config["base_url"],
        "default_model_key": config["default_model_key"],
        "model_catalog_json": model_catalog_json,
        "model_catalog_keys": sorted(config["model_catalog"].keys()),
        "enable_retrieval": config["enable_retrieval"],
        "retrieval_k": config["retrieval_k"],
    }


def update_chat_settings(
    db: Session,
    *,
    api_key: str | None,
    base_url: str | None,
    default_model_key: str | None,
    model_catalog_json: str | None,
    enable_retrieval: bool | None,
    retrieval_k: int | None,
) -> ChatSettings:
    row = fetch_chat_settings(db)
    if row is None:
        row = ChatSettings()
        db.add(row)

    if api_key is not None:
        row.api_key = api_key or None
    if base_url is not None:
        row.base_url = base_url or None
    if default_model_key is not None:
        row.default_model_key = default_model_key or None
    if model_catalog_json is not None:
        if model_catalog_json:
            try:
                parsed = json.loads(model_catalog_json)
            except json.JSONDecodeError as exc:
                raise ValueError("Model catalog must be valid JSON.") from exc
            if not isinstance(parsed, dict) or not parsed:
                raise ValueError("Model catalog must be a non-empty JSON object.")
            row.model_catalog_json = json.dumps(parsed, ensure_ascii=False, indent=2)
        else:
            row.model_catalog_json = None
    if enable_retrieval is not None:
        row.enable_retrieval = enable_retrieval
    if retrieval_k is not None:
        row.retrieval_k = retrieval_k

    db.commit()
    return row
