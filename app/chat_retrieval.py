import json
import os
import re

from sqlalchemy.orm import Session

from .models import ChatEmbedding, ChatMessage

ENABLE_CHAT_RETRIEVAL = os.getenv("ENABLE_CHAT_RETRIEVAL", "false").lower() == "true"
CHAT_RETRIEVAL_K = int(os.getenv("CHAT_RETRIEVAL_K", "5"))


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return set(tokens)


def store_embedding(db: Session, message_id: int, content: str) -> None:
    if not ENABLE_CHAT_RETRIEVAL:
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


def retrieve_memory(db: Session, session_id: str, query: str) -> list[dict]:
    if not ENABLE_CHAT_RETRIEVAL:
        return []
    query_tokens = _tokenize(query)
    if not query_tokens:
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
    for _, message in scored[:CHAT_RETRIEVAL_K]:
        memory.append(
            {
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at.isoformat(sep=" "),
            }
        )
    return memory
