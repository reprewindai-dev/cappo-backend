from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from cappo_backend.models.retrieval import RetrievalSession, RetrievalTrace
from cappo_backend.services.pageindex_mcp import PageIndexMCPClient
from cappo_backend.services.retrieval_router import (
    RetrievalRoute,
    SourceDescriptor,
    choose_retrieval_route,
)


def extract_page_refs(result: dict) -> list[dict]:
    refs = []
    for item in result.get("result", {}).get("content", []):
        meta = item.get("metadata", {})
        if "page" in meta or "pages" in meta:
            refs.append({
                "page": meta.get("page"),
                "pages": meta.get("pages"),
                "title": meta.get("title"),
            })
    return refs

def extract_section_refs(result: dict) -> list[dict]:
    refs = []
    for item in result.get("result", {}).get("content", []):
        meta = item.get("metadata", {})
        if "section" in meta:
            refs.append({
                "section": meta.get("section"),
                "title": meta.get("title"),
            })
    return refs

def extract_evidence(result: dict) -> list[dict]:
    evidence = []
    for item in result.get("result", {}).get("content", []):
        evidence.append({
            "type": item.get("type"),
            "text": item.get("text"),
            "metadata": item.get("metadata", {}),
        })
    return evidence

class RetrievalService:
    """
    Unified retrieval service that orchestrates between Vector DB and PageIndex.
    """
    def __init__(
        self, 
        db: Session,
        pageindex_mcp: PageIndexMCPClient,
        # vector_service placeholder
    ):
        self.db = db
        self.pageindex_mcp = pageindex_mcp

    async def answer_query(
        self,
        query: str,
        workspace_id: str,
        agent_id: str,
        source: Optional[SourceDescriptor],
        provider_doc_id: Optional[str],
    ) -> Dict[str, Any]:
        
        # 1. Choose the route
        route = choose_retrieval_route(query, source, pageindex_enabled=bool(self.pageindex_mcp.api_key))

        # 2. Log the session intent
        session = RetrievalSession(
            workspace_id=workspace_id,
            agent_id=agent_id,
            document_id=source.document_id if source else None,
            query=query,
            route=route.value,
            tool_used="doc_reason" if route == RetrievalRoute.PAGEINDEX else "doc_discover"
        )
        self.db.add(session)
        self.db.flush() # get session ID

        result = {}

        if route == RetrievalRoute.VECTOR:
            # Fallback to existing vector search 
            # (To be wired into cappo-backend's actual vector DB)
            result = {"content": "Simulated Vector Search Result"}
            
            trace = RetrievalTrace(
                session_id=session.id,
                provider="vector",
                raw_response=result
            )
            self.db.add(trace)

        elif route == RetrievalRoute.PAGEINDEX:
            if not provider_doc_id:
                raise ValueError("Missing provider_doc_id for PageIndex route")

            mcp_result = await self.pageindex_mcp.call_tool(
                tool_name="pageindex_query",
                arguments={
                    "doc_id": provider_doc_id,
                    "query": query,
                    "session_id": session.id,
                },
            )

            trace = RetrievalTrace(
                session_id=session.id,
                provider="pageindex",
                page_refs=extract_page_refs(mcp_result),
                section_refs=extract_section_refs(mcp_result),
                evidence=extract_evidence(mcp_result),
                raw_response=mcp_result,
            )
            self.db.add(trace)
            result = mcp_result

        elif route == RetrievalRoute.HYBRID:
            # Multi-document discovery
            result = {"content": "Simulated Hybrid Search Candidates"}

        self.db.commit()
        return {"route": route.value, "result": result, "session_id": session.id}
