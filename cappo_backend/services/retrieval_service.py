from typing import Any, Optional

from sqlalchemy.orm import Session

from cappo_backend.models.retrieval import RetrievalSession, RetrievalTrace
from cappo_backend.services.pageindex_mcp import PageIndexMCPClient
from cappo_backend.services.retrieval_router import (
    RetrievalRoute,
    SourceDescriptor,
    choose_retrieval_route,
)


def _content_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    body = result.get("result")
    if not isinstance(body, dict) or not isinstance(body.get("content"), list):
        return []
    return [item for item in body["content"] if isinstance(item, dict)]


def extract_page_refs(result: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for item in _content_items(result):
        meta = item.get("metadata")
        if not isinstance(meta, dict):
            continue
        if "page" in meta or "pages" in meta:
            refs.append({"page": meta.get("page"), "pages": meta.get("pages"), "title": meta.get("title")})
    return refs


def extract_section_refs(result: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for item in _content_items(result):
        meta = item.get("metadata")
        if isinstance(meta, dict) and "section" in meta:
            refs.append({"section": meta.get("section"), "title": meta.get("title")})
    return refs


def extract_evidence(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": item.get("type"), "text": item.get("text"), "metadata": item.get("metadata", {})}
        for item in _content_items(result)
    ]


class RetrievalDependencyUnavailable(RuntimeError):
    """Raised when a selected retrieval route has no real provider behind it."""


class RetrievalService:
    """Unified retrieval service with no simulated provider responses."""

    def __init__(self, db: Session, pageindex_mcp: PageIndexMCPClient):
        self.db = db
        self.pageindex_mcp = pageindex_mcp

    async def answer_query(
        self,
        query: str,
        workspace_id: str,
        agent_id: str,
        source: Optional[SourceDescriptor],
        provider_doc_id: Optional[str],
    ) -> dict[str, Any]:
        if not 1 <= len(query) <= 4096:
            raise ValueError("query must contain between 1 and 4096 characters")

        route = choose_retrieval_route(
            query, source, pageindex_enabled=bool(self.pageindex_mcp.api_key)
        )
        session = RetrievalSession(
            workspace_id=workspace_id,
            agent_id=agent_id,
            document_id=source.document_id if source else None,
            query=query,
            route=route.value,
            tool_used="doc_reason" if route == RetrievalRoute.PAGEINDEX else "doc_discover",
        )
        self.db.add(session)
        self.db.flush()

        if route in {RetrievalRoute.VECTOR, RetrievalRoute.HYBRID}:
            self.db.rollback()
            raise RetrievalDependencyUnavailable(
                f"{route.value} retrieval provider is not configured; query was not executed"
            )

        if not provider_doc_id:
            self.db.rollback()
            raise ValueError("Missing provider_doc_id for PageIndex route")

        mcp_result = await self.pageindex_mcp.call_tool(
            tool_name="pageindex_query",
            arguments={"doc_id": provider_doc_id, "query": query, "session_id": session.id},
        )
        if not isinstance(mcp_result, dict) or mcp_result.get("error") or not _content_items(mcp_result):
            self.db.rollback()
            raise RetrievalDependencyUnavailable("PageIndex returned no usable evidence")

        trace = RetrievalTrace(
            session_id=session.id,
            provider="pageindex",
            page_refs=extract_page_refs(mcp_result),
            section_refs=extract_section_refs(mcp_result),
            evidence=extract_evidence(mcp_result),
            raw_response=mcp_result,
        )
        self.db.add(trace)
        self.db.commit()
        return {"route": route.value, "result": mcp_result, "session_id": session.id}
