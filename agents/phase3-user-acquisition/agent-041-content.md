# Agent-041 — CONTENT (Enhanced with Ptah Multimodal Research)

**Phase:** 3 — User Acquisition  
**Timeline:** Days 15-30  
**Committee:** Growth  
**Priority:** HIGH  

---

## Mission

Enhanced content creation with Ptah-inspired multimodal deep research capabilities. Create verifiable, visually-grounded content that synthesizes scattered evidence into compelling user acquisition materials with factual verification and cross-modal consistency.

## Enhanced Capabilities (Based on Ptah Research)

### Multi-Agent Content Harness
- **Planning Agent**: Creates visual-aware content plans and research strategies
- **Research Agents**: Collect claim-grounded multimodal evidence for content topics
- **Writing Agents**: Compose content with declarative multimodal tools
- **Verifier Agent**: Ensures factual grounding, citation fidelity, and visual-textual consistency

### Visual Working Memory for Content
- **Source-Aligned Images**: Maintain image provenance and context for content
- **Visual-Textual Mapping**: Link content claims to supporting visual evidence
- **Cross-Modal Indexing**: Enable retrieval of relevant visual and textual evidence
- **Consistency Verification**: Ensure visual elements align with content messaging

### Verifiable Content Creation
- **Factual Grounding**: Verify all content claims against collected evidence
- **Citation Fidelity**: Ensure accurate source attribution in all content
- **Cross-Modal Consistency**: Validate that images and text align properly
- **Presentation Quality**: Assess content usability and engagement potential

## Enhanced Implementation

```python
import asyncio
import base64
import json
import time
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class ContentType(Enum):
    """Different types of content for user acquisition"""
    BLOG_POST = "blog_post"
    LANDING_PAGE = "landing_page"
    SOCIAL_MEDIA = "social_media"
    EMAIL_CAMPAIGN = "email_campaign"
    CASE_STUDY = "case_study"
    WHITEPAPER = "whitepaper"

@dataclass
class ContentBrief:
    """Content creation brief with requirements"""
    brief_id: str
    content_type: ContentType
    target_audience: str
    primary_message: str
    key_points: List[str]
    visual_requirements: List[str]
    tone: str
    call_to_action: str
    seo_keywords: List[str]
    deadline: Optional[datetime] = None

@dataclass
class ContentEvidence:
    """Evidence supporting content creation"""
    evidence_id: str
    content_type: str  # textual, visual, statistical
    source_url: str
    content: str
    credibility_score: float
    relevance_score: float
    visual_elements: List[Dict[str, Any]] = field(default_factory=list)
    supporting_claims: List[str] = field(default_factory=list)
    verified: bool = False

@dataclass
class MultimodalContent:
    """Final content with multimodal elements"""
    content_id: str
    brief_id: str
    title: str
    body_content: str
    visual_elements: List[Dict[str, Any]]
    evidence_citations: List[Dict[str, Any]]
    seo_optimization: Dict[str, Any]
    verification_summary: Dict[str, Any]
    performance_predictions: Dict[str, float]
    created_at: datetime

class PtahContentAgent:
    """Ptah-inspired multimodal content creation agent"""
    
    def __init__(self):
        self.planning_agent = ContentPlanningAgent()
        self.research_agents = [ContentResearchAgent() for _ in range(3)]
        self.writing_agent = ContentWritingAgent()
        self.verifier_agent = ContentVerifierAgent()
        self.visual_working_memory = ContentVisualMemory()
        self.evidence_synthesizer = ContentEvidenceSynthesizer()
        
    async def create_multimodal_content(
        self,
        brief: ContentBrief
    ) -> MultimodalContent:
        """Create multimodal content with Ptah methodology"""
        
        # Stage 1: Content Planning
        content_plan = await self.planning_agent.create_visual_aware_plan(brief)
        
        # Stage 2: Multimodal Research (parallel execution)
        research_tasks = []
        for agent in self.research_agents:
            task = agent.collect_content_evidence(brief, content_plan)
            research_tasks.append(task)
        
        research_results = await asyncio.gather(*research_tasks)
        
        # Consolidate research results
        all_textual_evidence = []
        all_visual_evidence = []
        
        for result in research_results:
            all_textual_evidence.extend(result["textual_evidence"])
            all_visual_evidence.extend(result["visual_evidence"])
        
        # Store in visual working memory
        for visual_evidence in all_visual_evidence:
            self.visual_working_memory.store_visual_evidence(visual_evidence)
        
        # Stage 3: Evidence Synthesis
        synthesized_claims = await self.evidence_synthesizer.synthesize_content_evidence(
            all_textual_evidence, all_visual_evidence, brief
        )
        
        # Stage 4: Content Creation
        draft_content = await self.writing_agent.compose_multimodal_content(
            brief, content_plan, synthesized_claims, all_visual_evidence
        )
        
        # Stage 5: Content Verification
        verified_content = await self.verifier_agent.verify_content(
            draft_content, synthesized_claims, all_textual_evidence, all_visual_evidence
        )
        
        return verified_content

class ContentPlanningAgent:
    """Creates visual-aware content plans"""
    
    async def create_visual_aware_plan(
        self,
        brief: ContentBrief
    ) -> Dict[str, Any]:
        """Create content plan with visual requirements"""
        
        plan = {
            "plan_id": f"plan_{brief.brief_id}_{int(time.time())}",
            "content_analysis": await self._analyze_content_brief(brief),
            "research_strategy": await self._design_research_strategy(brief),
            "visual_strategy": self._specify_visual_strategy(brief),
            "evidence_requirements": await self._identify_evidence_requirements(brief),
            "content_structure": self._design_content_structure(brief),
            "quality_criteria": self._define_quality_criteria(brief)
        }
        
        return plan
    
    async def _analyze_content_brief(self, brief: ContentBrief) -> Dict[str, Any]:
        """Analyze content brief to determine creation needs"""
        
        analysis = {
            "content_complexity": self._assess_content_complexity(brief),
            "audience_specificity": self._assess_audience_specificity(brief.target_audience),
            "visual_intensity": len(brief.visual_requirements),
            "research_depth": self._determine_research_depth(brief),
            "key_themes": self._extract_key_themes(brief),
            "content_angles": self._generate_content_angles(brief)
        }
        
        return analysis
    
    def _specify_visual_strategy(self, brief: ContentBrief) -> Dict[str, Any]:
        """Specify visual content requirements"""
        
        visual_specs = {
            "required_visual_types": brief.visual_requirements,
            "image_quality_standards": {
                "min_resolution": "1200x800",
                "preferred_formats": ["png", "jpg", "webp"],
                "max_file_size": "2MB"
            },
            "visual_context_requirements": {
                "need_captions": True,
                "need_alt_text": True,
                "need_brand_consistency": True
            },
            "visual_hierarchy": self._design_visual_hierarchy(brief),
            "brand_guidelines": self._extract_brand_guidelines(brief)
        }
        
        return visual_specs

class ContentResearchAgent:
    """Collects claim-grounded multimodal evidence for content"""
    
    def __init__(self):
        self.text_collector = TextualEvidenceCollector()
        self.visual_collector = VisualEvidenceCollector()
        self.claim_extractor = ContentClaimExtractor()
        
    async def collect_content_evidence(
        self,
        brief: ContentBrief,
        content_plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Collect multimodal evidence for content creation"""
        
        # Collect textual evidence
        textual_evidence = await self.text_collector.collect_textual_evidence(
            brief, content_plan
        )
        
        # Collect visual evidence
        visual_evidence = await self.visual_collector.collect_visual_evidence(
            brief, content_plan
        )
        
        # Extract content claims from textual evidence
        for evidence in textual_evidence:
            claims = await self.claim_extractor.extract_content_claims(evidence)
            evidence.supporting_claims = claims
        
        # Link visual evidence to claims
        for visual in visual_evidence:
            related_claims = await self._link_visual_to_content_claims(
                visual, textual_evidence
            )
            visual.supporting_claims = related_claims
        
        return {
            "agent_id": id(self),
            "textual_evidence": textual_evidence,
            "visual_evidence": visual_evidence,
            "collection_timestamp": datetime.now()
        }
    
    async def _link_visual_to_content_claims(
        self,
        visual_evidence: ContentEvidence,
        textual_evidence: List[ContentEvidence]
    ) -> List[str]:
        """Link visual evidence to related content claims"""
        
        related_claims = []
        
        # Keyword matching between visual content and claims
        visual_keywords = set(visual_evidence.content.lower().split())
        
        for evidence in textual_evidence:
            for claim in evidence.supporting_claims:
                claim_keywords = set(claim.lower().split())
                
                # Calculate keyword overlap
                overlap = len(visual_keywords & claim_keywords)
                if overlap > 2:  # Threshold for relatedness
                    related_claims.append(claim)
        
        return related_claims

class ContentVisualMemory:
    """Manages visual evidence with cross-modal indexing for content"""
    
    def __init__(self):
        self.visual_store = {}
        self.cross_modal_index = {}
        self.brand_consistency_cache = {}
        
    def store_visual_evidence(self, visual_evidence: ContentEvidence):
        """Store visual evidence with content indexing"""
        
        self.visual_store[visual_evidence.evidence_id] = visual_evidence
        
        # Create cross-modal index entries
        for claim in visual_evidence.supporting_claims:
            if claim not in self.cross_modal_index:
                self.cross_modal_index[claim] = []
            self.cross_modal_index[claim].append(visual_evidence.evidence_id)
    
    def retrieve_visual_for_claim(self, claim: str) -> List[ContentEvidence]:
        """Retrieve visual evidence supporting a content claim"""
        
        if claim in self.cross_modal_index:
            visual_ids = self.cross_modal_index[claim]
            return [self.visual_store[vid] for vid in visual_ids]
        
        return []
    
    def verify_brand_consistency(
        self,
        visual_evidence: ContentEvidence,
        brand_guidelines: Dict[str, Any]
    ) -> float:
        """Verify visual content consistency with brand guidelines"""
        
        consistency_score = 0.8  # Base score
        
        # Check color consistency
        if "colors" in brand_guidelines:
            color_consistency = self._check_color_consistency(
                visual_evidence, brand_guidelines["colors"]
            )
            consistency_score = consistency_score * 0.7 + color_consistency * 0.3
        
        # Check typography consistency
        if "typography" in brand_guidelines:
            typo_consistency = self._check_typography_consistency(
                visual_evidence, brand_guidelines["typography"]
            )
            consistency_score = consistency_score * 0.8 + typo_consistency * 0.2
        
        return min(1.0, consistency_score)

class ContentWritingAgent:
    """Composes content with declarative multimodal tools"""
    
    async def compose_multimodal_content(
        self,
        brief: ContentBrief,
        content_plan: Dict[str, Any],
        claims: List[Dict[str, Any]],
        visual_evidence: List[ContentEvidence]
    ) -> MultimodalContent:
        """Compose multimodal content"""
        
        content = MultimodalContent(
            content_id=f"content_{brief.brief_id}_{int(time.time())}",
            brief_id=brief.brief_id,
            title=self._generate_title(brief, claims),
            body_content=await self._write_body_content(brief, claims, visual_evidence),
            visual_elements=await self._create_visual_elements(visual_evidence, brief),
            evidence_citations=await self._generate_citations(claims),
            seo_optimization=await self._optimize_for_seo(brief, claims),
            verification_summary={},  # Will be filled by verifier
            performance_predictions=await self._predict_performance(brief, claims),
            created_at=datetime.now()
        )
        
        return content
    
    async def _write_body_content(
        self,
        brief: ContentBrief,
        claims: List[Dict[str, Any]],
        visual_evidence: List[ContentEvidence]
    ) -> str:
        """Write body content with claim support"""
        
        content_sections = []
        
        # Introduction
        intro = self._write_introduction(brief)
        content_sections.append(intro)
        
        # Main content sections based on key points
        for i, point in enumerate(brief.key_points):
            section = await self._write_content_section(
                point, claims, visual_evidence, i
            )
            content_sections.append(section)
        
        # Call to action
        cta = self._write_call_to_action(brief)
        content_sections.append(cta)
        
        return "\n\n".join(content_sections)
    
    async def _write_content_section(
        self,
        key_point: str,
        claims: List[Dict[str, Any]],
        visual_evidence: List[ContentEvidence],
        section_index: int
    ) -> str:
        """Write individual content section with evidence support"""
        
        # Find supporting claims for this key point
        supporting_claims = [
            claim for claim in claims
            if self._claim_supports_point(claim, key_point)
        ]
        
        # Find relevant visual evidence
        relevant_visuals = []
        for claim in supporting_claims:
            visuals_for_claim = [
                v for v in visual_evidence
                if claim["claim_id"] in v.supporting_claims
            ]
            relevant_visuals.extend(visuals_for_claim)
        
        # Write section content
        section_content = f"## {key_point}\n\n"
        
        # Add claim-supported content
        for claim in supporting_claims:
            section_content += f"{claim['claim_text']}\n\n"
            
            # Add evidence citations
            if claim.get("evidence_support"):
                section_content += f"*Source: {claim['evidence_support']}*\n\n"
        
        # Add visual content references
        if relevant_visuals:
            section_content += "![]()\n\n"
            section_content += f"*Image: {relevant_visuals[0].content}*\n\n"
        
        return section_content

class ContentVerifierAgent:
    """Verifies content quality and accuracy"""
    
    async def verify_content(
        self,
        draft_content: MultimodalContent,
        claims: List[Dict[str, Any]],
        textual_evidence: List[ContentEvidence],
        visual_evidence: List[ContentEvidence]
    ) -> MultimodalContent:
        """Verify content quality and accuracy"""
        
        verification_results = {
            "factual_grounding": await self._verify_factual_grounding(
                claims, textual_evidence
            ),
            "citation_fidelity": await self._verify_citation_fidelity(
                draft_content.evidence_citations, textual_evidence, visual_evidence
            ),
            "visual_textual_consistency": await self._verify_visual_textual_consistency(
                draft_content, visual_evidence
            ),
            "content_quality": await self._assess_content_quality(draft_content),
            "seo_effectiveness": await self._assess_seo_effectiveness(draft_content)
        }
        
        # Update content with verification summary
        draft_content.verification_summary = verification_results
        
        return draft_content
    
    async def _verify_factual_grounding(
        self,
        claims: List[Dict[str, Any]],
        textual_evidence: List[ContentEvidence]
    ) -> Dict[str, Any]:
        """Verify that content claims are grounded in factual evidence"""
        
        verification_results = {
            "total_claims": len(claims),
            "well_grounded_claims": 0,
            "unsubstantiated_claims": 0,
            "grounding_score": 0.0,
            "issues": []
        }
        
        for claim in claims:
            # Check if claim has supporting textual evidence
            if claim.get("evidence_support"):
                # Verify evidence quality
                supporting_evidence = [
                    e for e in textual_evidence
                    if e.evidence_id in claim.get("supporting_evidence", [])
                ]
                
                avg_credibility = np.mean([e.credibility_score for e in supporting_evidence])
                
                if avg_credibility > 0.7:
                    verification_results["well_grounded_claims"] += 1
                else:
                    verification_results["unsubstantiated_claims"] += 1
                    verification_results["issues"].append({
                        "claim_id": claim["claim_id"],
                        "issue": "Low credibility supporting evidence",
                        "average_credibility": avg_credibility
                    })
            else:
                verification_results["unsubstantiated_claims"] += 1
                verification_results["issues"].append({
                    "claim_id": claim["claim_id"],
                    "issue": "No supporting evidence found"
                })
        
        # Calculate grounding score
        if verification_results["total_claims"] > 0:
            verification_results["grounding_score"] = (
                verification_results["well_grounded_claims"] / 
                verification_results["total_claims"]
            ) * 100
        
        return verification_results

## Enhanced Content Creation Workflow

### 1. Visual-Aware Planning
- Analyze content briefs for visual requirements
- Design research strategies with visual components
- Plan content structure with visual-textual integration

### 2. Multimodal Evidence Collection
- Gather textual evidence from authoritative sources
- Collect visual evidence that supports content claims
- Link visual and textual evidence for cross-modal support

### 3. Evidence-Based Content Creation
- Write content supported by collected evidence
- Integrate visual elements seamlessly
- Maintain factual accuracy throughout

### 4. Comprehensive Verification
- Verify factual grounding of all claims
- Ensure citation fidelity and source accuracy
- Validate visual-textual consistency

## Enhanced Success Metrics

| Metric | Target | Ptah-Inspired |
|---|---|---|
| Factual grounding accuracy | > 95% | Evidence verification |
| Visual-textual consistency | > 90% | Cross-modal alignment |
| Citation fidelity | > 98% | Source attribution |
| Content engagement prediction | > 85% | Quality assessment |
| SEO effectiveness | > 80% | Optimized content |

## Dependencies

- Agent-040 (SEO agent - for keyword optimization)
- Agent-042 (Community agent - for audience insights)
- Visual content creation tools
- Content management system
- Analytics and performance tracking

---

## Research References

1. **Ptah**: Verifiable Multimodal Deep Research (arXiv:2605.29861)
2. **Cookie-Bench**: Continuous On-screen Key Interaction Evaluation (arXiv:2605.30000)
3. **MEMENTO**: Web as Learning Signal for Low-Data Domains (arXiv:2605.29795)
