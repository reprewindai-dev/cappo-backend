import re
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Optional

class RetrievalRoute(str, Enum):
    VECTOR = "vector"
    PAGEINDEX = "pageindex"
    HYBRID = "hybrid"

@dataclass
class SourceDescriptor:
    document_id: str
    source_type: str
    mime_type: str
    page_count: int | None = None
    has_table_of_contents: bool = False
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class QueryIntent:
    asks_for_exact_evidence: bool
    asks_for_clause_or_section: bool
    asks_for_comparison_within_doc: bool
    asks_for_multi_doc_discovery: bool
    asks_for_citation: bool

EVIDENCE_PATTERNS = [
    r"\bpage\b", r"\bsection\b", r"\bclause\b", r"\bappendix\b",
    r"\bcite\b", r"\bproof\b", r"\bshow me where\b", r"\bexactly where\b",
    r"\bwhich table\b", r"\bwhat page\b"
]

DISCOVERY_PATTERNS = [
    r"\bfind documents\b", r"\bsearch across\b", r"\ball docs\b",
    r"\bwhich document\b", r"\brelated docs\b", r"\bsimilar\b"
]

COMPARE_PATTERNS = [
    r"\bcompare\b", r"\bversus\b", r"\bvs\b", r"\bdifference between sections\b"
]

def classify_query_intent(query: str) -> QueryIntent:
    """Classifies the user's intent to determine the best retrieval route."""
    q = query.lower()
    return QueryIntent(
        asks_for_exact_evidence=any(re.search(p, q) for p in EVIDENCE_PATTERNS),
        asks_for_clause_or_section=bool(re.search(r"\b(section|clause|appendix|table|footnote)\b", q)),
        asks_for_comparison_within_doc=any(re.search(p, q) for p in COMPARE_PATTERNS),
        asks_for_multi_doc_discovery=any(re.search(p, q) for p in DISCOVERY_PATTERNS),
        asks_for_citation=bool(re.search(r"\b(cite|citation|source|reference)\b", q)),
    )

def is_structured_long_pdf(source: SourceDescriptor, min_pages: int = 20) -> bool:
    """Checks if the document matches the profile for tree-based indexing."""
    return (
        source.mime_type == "application/pdf"
        and (source.page_count or 0) >= min_pages
        and source.source_type in {
            "whitepaper", "contract", "filing", "manual",
            "policy_pdf", "governance_doc", "financial_report"
        }
    )

def choose_retrieval_route(
    query: str, 
    source: Optional[SourceDescriptor], 
    pageindex_enabled: bool = True
) -> RetrievalRoute:
    """Deterministically chooses between Vector RAG and PageIndex Tree RAG."""
    intent = classify_query_intent(query)

    if source is None:
        return RetrievalRoute.VECTOR

    if not pageindex_enabled:
        return RetrievalRoute.VECTOR

    if is_structured_long_pdf(source):
        if intent.asks_for_exact_evidence or intent.asks_for_clause_or_section or intent.asks_for_citation:
            return RetrievalRoute.PAGEINDEX
        if intent.asks_for_comparison_within_doc:
            return RetrievalRoute.PAGEINDEX

    if intent.asks_for_multi_doc_discovery:
        return RetrievalRoute.HYBRID

    return RetrievalRoute.VECTOR
