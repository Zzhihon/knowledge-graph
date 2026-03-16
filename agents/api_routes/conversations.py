"""REST endpoints for chat conversation persistence."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["conversations"])


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------

class CreateConversationReq(BaseModel):
    title: str
    mode: str = "ask"


class UpdateConversationReq(BaseModel):
    title: str


class AddMessageReq(BaseModel):
    role: str
    content: str
    sources: list[dict] | None = None


# ------------------------------------------------------------------
# Lazy singleton
# ------------------------------------------------------------------

_store = None


def _get_store():
    global _store
    if _store is None:
        from agents.chat_store import ChatStore
        _store = ChatStore()
    return _store


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("/conversations")
def list_conversations(limit: int = 50):
    return _get_store().list_conversations(limit)


@router.post("/conversations")
def create_conversation(req: CreateConversationReq):
    return _get_store().create_conversation(req.title, req.mode)


@router.get("/conversations/{cid}")
def get_conversation(cid: str):
    conv = _get_store().get_conversation(cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.put("/conversations/{cid}")
def update_conversation(cid: str, req: UpdateConversationReq):
    ok = _get_store().update_conversation(cid, req.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@router.delete("/conversations/{cid}")
def delete_conversation(cid: str):
    ok = _get_store().delete_conversation(cid)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@router.post("/conversations/{cid}/messages")
def add_message(cid: str, req: AddMessageReq):
    conv = _get_store().get_conversation(cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _get_store().add_message(cid, req.role, req.content, req.sources)
