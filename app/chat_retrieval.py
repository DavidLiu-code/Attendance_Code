import json
import os
import re

from sqlalchemy.orm import Session

from .models import ChatEmbedding, ChatMessage


def get_default_retrieval_enabled() -> bool:
    return os.getenv("ENABLE_CHAT_RETRIEVAL", "true").lower() == "true"


def get_default_retrieval_k() -> int:
    return int(os.getenv("CHAT_RETRIEVAL_K", "5"))


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return set(tokens)


def store_embedding(
    db: Session,
    message_id: int,
    content: str,
    enabled: bool | None = None,
) -> None:
    if enabled is None:
        enabled = get_default_retrieval_enabled()
    if not enabled:
        return
    if not message_id:
        return
    tokens = sorted(_tokenize(content))
    if not tokens:
        return
    embedding = ChatEmbedding(
        message_id=message_id,
        content=content,
        tokens_json=json.dumps(tokens),
    )
    db.add(embedding)


def retrieve_memory(
    db: Session,
    session_id: str,
    query: str,
    k: int | None = None,
    enabled: bool | None = None,
) -> list[dict]:
    if enabled is None:
        enabled = get_default_retrieval_enabled()
    if not enabled:
        return []
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    if k is None:
        k = get_default_retrieval_k()
    if k <= 0:
        return []
    embeddings = (
        db.query(ChatEmbedding, ChatMessage)
        .join(ChatMessage, ChatEmbedding.message_id == ChatMessage.id)
        .filter(ChatMessage.session_id == session_id)
        .all()
    )
    scored = []
    for embedding, message in embeddings:
        try:
            tokens = set(json.loads(embedding.tokens_json))
        except json.JSONDecodeError:
            continue
        overlap = len(tokens & query_tokens)
        if overlap:
            scored.append((overlap, message))
    scored.sort(key=lambda item: item[0], reverse=True)
    memory = []
    for _, message in scored[:k]:
        memory.append(
            {
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at.isoformat(sep=" "),
            }
        )
    return memory
