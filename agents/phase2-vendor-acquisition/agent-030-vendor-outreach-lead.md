# Agent-030 — VENDOR OUTREACH LEAD (Enhanced with PlanAhead Planning)

**Phase:** 2 — Vendor Acquisition  
**Timeline:** Days 3-10  
**Committee:** Growth  
**Priority:** HIGH  

---

## Mission

Enhanced vendor outreach coordination with PlanAhead-inspired planning optimization. Systematically evaluate different outreach plan representations to improve vendor response rates, onboarding success, and overall campaign effectiveness across different vendor types and communication channels.

## Enhanced Capabilities (Based on PlanAhead Research)

### Planning Representation Analysis
- **Sequential Subgoals**: Step-by-step outreach process with clear dependencies
- **Narrative Plans**: Story-like outreach sequences that build relationships
- **Pseudocode Plans**: Structured outreach logic with conditional branches
- **Checklist Plans**: Itemized outreach tracking with completion metrics

### Difficulty-Based Vendor Classification
- **Automatic Vendor Categorization**: Classify vendors by outreach difficulty without manual assessment
- **Consistent Difficulty Grading**: Ensure comparable evaluation across outreach strategies
- **Performance Benchmarking**: Establish baseline metrics for different vendor types

### Cross-Channel Optimization
- **Email Strategy Optimization**: Tailor email outreach plans for different vendor types
- **Social Media Planning**: Optimize LinkedIn, Twitter, and other platform strategies
- **Multi-channel Coordination**: Synchronize outreach across multiple communication channels
- **Response Rate Maximization**: Use Achievement Rate (AR) and Solved-Task Consistency (STC) metrics

## Enhanced Implementation

```python
import asyncio
import json
import time
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import numpy as np

class OutreachPlanRepresentation(Enum):
    """Different outreach plan representation types"""
    SEQUENTIAL_SUBGOALS = "sequential_subgoals"
    NARRATIVE = "narrative"
    PSEUDOCODE = "pseudocode"
    CHECKLIST = "checklist"

class VendorDifficulty(Enum):
    """Vendor outreach difficulty levels"""
    EASY = "easy"      # High response rate, low friction
    MEDIUM = "medium"  # Moderate response rate, some negotiation
    HARD = "hard"      # Low response rate, high value, requires persistence

class OutreachChannel(Enum):
    """Different outreach channels"""
    EMAIL = "email"
    LINKEDIN = "linkedin"
    TWITTER = "twitter"
    GITHUB = "github"
    COLD_CALL = "cold_call"
    REFERRAL = "referral"

@dataclass
class VendorTarget:
    """Vendor target for outreach"""
    vendor_id: str
    company_name: str
    contact_person: str
    email: str
    linkedin_profile: Optional[str]
    github_profile: Optional[str]
    project_type: str
    project_quality: float
    market_potential: float
    difficulty_level: VendorDifficulty
    preferred_channels: List[OutreachChannel]
    previous_attempts: int = 0

@dataclass
class OutreachPlan:
    """Structured outreach plan"""
    plan_id: str
    vendor_id: str
    representation_type: OutreachPlanRepresentation
    plan_content: Dict[str, Any]
    estimated_duration: int
    success_probability: float
    resource_requirements: Dict[str, Any]
    created_at: datetime

@dataclass
class OutreachExecutionResult:
    """Result of outreach plan execution"""
    execution_id: str
    plan_id: str
    vendor_id: str
    representation_type: OutreachPlanRepresentation
    steps_completed: List[Dict[str, Any]]
    response_received: bool
    response_time: Optional[timedelta]
    onboarding_completed: bool
    achievement_rate: float
    execution_time: timedelta
    errors_encountered: List[str]
    lessons_learned: List[str]

class PlanAheadOutreachOptimizer:
    """PlanAhead-inspired outreach optimization system"""
    
    def __init__(self):
        self.vendor_classifier = VendorDifficultyClassifier()
        self.plan_generator = OutreachPlanGenerator()
        self.execution_evaluator = OutreachExecutionEvaluator()
        self.performance_analyzer = OutreachPerformanceAnalyzer()
        self.channel_adapter = OutreachChannelAdapter()
        
    def optimize_outreach_planning(
        self,
        vendor_targets: List[VendorTarget],
        available_channels: List[OutreachChannel]
    ) -> Dict[str, Any]:
        """Optimize outreach planning for vendor targets"""
        
        # Step 1: Classify vendor difficulty
        classified_vendors = []
        for vendor in vendor_targets:
            if vendor.difficulty_level == VendorDifficulty.HARD:
                difficulty_analysis = self.vendor_classifier.analyze_difficulty(vendor)
                vendor.difficulty_level = difficulty_analysis["predicted_difficulty"]
            classified_vendors.append(vendor)
        
        # Step 2: Generate different plan representations for each vendor
        plan_representations = {}
        for vendor in classified_vendors:
            vendor_plans = {}
            for representation_type in OutreachPlanRepresentation:
                plan = self.plan_generator.generate_outreach_plan(
                    vendor, representation_type, available_channels
                )
                vendor_plans[representation_type] = plan
            plan_representations[vendor.vendor_id] = vendor_plans
        
        # Step 3: Execute plans across different channels
        execution_results = {}
        for vendor_id, vendor_plans in plan_representations.items():
            vendor_results = {}
            for representation_type, plan in vendor_plans.items():
                # Adapt plan for specific channels
                adapted_plan = self.channel_adapter.adapt_plan_for_channels(
                    plan, vendor.preferred_channels
                )
                
                # Execute outreach plan
                result = self.execution_evaluator.execute_outreach_plan(
                    adapted_plan, representation_type
                )
                vendor_results[representation_type] = result
            
            execution_results[vendor_id] = vendor_results
        
        # Step 4: Analyze performance and recommend optimal strategies
        performance_analysis = self.performance_analyzer.analyze_outreach_performance(
            execution_results, classified_vendors
        )
        
        # Step 5: Generate optimization recommendations
        recommendations = self._generate_outreach_recommendations(
            performance_analysis, classified_vendors, available_channels
        )
        
        return {
            "classified_vendors": classified_vendors,
            "plan_representations": plan_representations,
            "execution_results": execution_results,
            "performance_analysis": performance_analysis,
            "recommendations": recommendations,
            "optimization_timestamp": datetime.now().isoformat()
        }

class VendorDifficultyClassifier:
    """Automatically classifies vendor outreach difficulty"""
    
    def __init__(self):
        self.difficulty_features = {
            "quality_indicators": [
                "github_stars", "code_quality", "documentation_quality",
                "community_engagement", "update_frequency", "license_compatibility"
            ],
            "market_indicators": [
                "market_size", "competition_level", "revenue_potential",
                "strategic_value", "partnership_interest"
            ],
            "contact_indicators": [
                "email_deliverability", "linkedin_connectivity", "response_history",
                "referral_potential", "warm_introduction_possibility"
            ]
        }
        
    def analyze_difficulty(self, vendor: VendorTarget) -> Dict[str, Any]:
        """Analyze and predict vendor outreach difficulty"""
        
        # Calculate quality score
        quality_score = self._calculate_quality_score(vendor)
        
        # Calculate market potential score
        market_score = self._calculate_market_score(vendor)
        
        # Calculate contact accessibility score
        contact_score = self._calculate_contact_score(vendor)
        
        # Calculate previous attempts penalty
        attempts_penalty = min(0.3, vendor.previous_attempts * 0.1)
        
        # Combine scores
        overall_difficulty = (
            (1.0 - quality_score) * 0.4 +      # Lower quality = harder
            (1.0 - market_score) * 0.3 +       # Lower market = harder
            (1.0 - contact_score) * 0.2 +      # Lower contact = harder
            attempts_penalty * 0.1
        )
        
        # Classify difficulty
        if overall_difficulty < 0.3:
            predicted_difficulty = VendorDifficulty.EASY
        elif overall_difficulty < 0.7:
            predicted_difficulty = VendorDifficulty.MEDIUM
        else:
            predicted_difficulty = VendorDifficulty.HARD
        
        return {
            "predicted_difficulty": predicted_difficulty,
            "overall_score": overall_difficulty,
            "quality_score": quality_score,
            "market_score": market_score,
            "contact_score": contact_score,
            "attempts_penalty": attempts_penalty,
            "confidence": 0.85
        }
    
    def _calculate_quality_score(self, vendor: VendorTarget) -> float:
        """Calculate vendor quality score"""
        
        # Base score from project quality
        base_score = vendor.project_quality
        
        # Adjust for market potential
        market_adjustment = vendor.market_potential * 0.2
        
        # Adjust for preferred channels (more channels = easier)
        channel_bonus = min(0.2, len(vendor.preferred_channels) * 0.05)
        
        # Adjust for profile completeness
        profile_bonus = 0.0
        if vendor.linkedin_profile:
            profile_bonus += 0.1
        if vendor.github_profile:
            profile_bonus += 0.1
        
        total_score = base_score + market_adjustment + channel_bonus + profile_bonus
        return min(1.0, total_score)

class OutreachPlanGenerator:
    """Generates outreach plans in different representations"""
    
    def __init__(self):
        self.plan_templates = self._initialize_outreach_templates()
        
    def generate_outreach_plan(
        self,
        vendor: VendorTarget,
        representation_type: OutreachPlanRepresentation,
        available_channels: List[OutreachChannel]
    ) -> OutreachPlan:
        """Generate outreach plan in specified representation format"""
        
        if representation_type == OutreachPlanRepresentation.SEQUENTIAL_SUBGOALS:
            return self._generate_sequential_subgoals_plan(vendor, available_channels)
        elif representation_type == OutreachPlanRepresentation.NARRATIVE:
            return self._generate_narrative_plan(vendor, available_channels)
        elif representation_type == OutreachPlanRepresentation.PSEUDOCODE:
            return self._generate_pseudocode_plan(vendor, available_channels)
        elif representation_type == OutreachPlanRepresentation.CHECKLIST:
            return self._generate_checklist_plan(vendor, available_channels)
        else:
            raise ValueError(f"Unsupported representation type: {representation_type}")
    
    def _generate_sequential_subgoals_plan(
        self,
        vendor: VendorTarget,
        available_channels: List[OutreachChannel]
    ) -> OutreachPlan:
        """Generate sequential subgoals outreach plan"""
        
        plan_content = {
            "vendor_id": vendor.vendor_id,
            "objective": f"Onboard {vendor.company_name} as Veklom vendor",
            "subgoals": []
        }
        
        # Create sequential subgoals based on vendor difficulty
        if vendor.difficulty_level == VendorDifficulty.EASY:
            subgoals = self._easy_vendor_subgoals(vendor, available_channels)
        elif vendor.difficulty_level == VendorDifficulty.MEDIUM:
            subgoals = self._medium_vendor_subgoals(vendor, available_channels)
        else:
            subgoals = self._hard_vendor_subgoals(vendor, available_channels)
        
        for i, subgoal in enumerate(subgoals):
            plan_content["subgoals"].append({
                "subgoal_id": f"subgoal_{i+1}",
                "description": subgoal["description"],
                "channel": subgoal["channel"],
                "prerequisites": subgoal.get("prerequisites", []),
                "expected_outcome": subgoal.get("outcome", ""),
                "success_criteria": subgoal.get("success_criteria", []),
                "estimated_time": subgoal.get("time_hours", 24),
                "fallback_strategy": subgoal.get("fallback", "")
            })
        
        return OutreachPlan(
            plan_id=f"seq_plan_{vendor.vendor_id}_{int(time.time())}",
            vendor_id=vendor.vendor_id,
            representation_type=OutreachPlanRepresentation.SEQUENTIAL_SUBGOALS,
            plan_content=plan_content,
            estimated_duration=sum(s["estimated_time"] for s in plan_content["subgoals"]),
            success_probability=self._calculate_success_probability(vendor, "sequential"),
            resource_requirements=self._estimate_resources(plan_content),
            created_at=datetime.now()
        )
    
    def _easy_vendor_subgoals(
        self,
        vendor: VendorTarget,
        available_channels: List[OutreachChannel]
    ) -> List[Dict[str, Any]]:
        """Generate subgoals for easy vendors"""
        
        return [
            {
                "description": f"Send initial personalized email to {vendor.contact_person}",
                "channel": OutreachChannel.EMAIL,
                "outcome": "Email opened and response received",
                "success_criteria": ["email_delivered", "response_received"],
                "time_hours": 24
            },
            {
                "description": "Follow up with value proposition details",
                "channel": OutreachChannel.EMAIL,
                "prerequisites": ["subgoal_1"],
                "outcome": "Vendor expresses interest in learning more",
                "success_criteria": ["positive_response", "meeting_scheduled"],
                "time_hours": 48
            },
            {
                "description": "Schedule onboarding demo call",
                "channel": OutreachChannel.EMAIL,
                "prerequisites": ["subgoal_2"],
                "outcome": "Demo call completed and onboarding initiated",
                "success_criteria": ["demo_completed", "onboarding_started"],
                "time_hours": 72
            }
        ]
    
    def _medium_vendor_subgoals(
        self,
        vendor: VendorTarget,
        available_channels: List[OutreachChannel]
    ) -> List[Dict[str, Any]]:
        """Generate subgoals for medium vendors"""
        
        return [
            {
                "description": f"Connect with {vendor.contact_person} on LinkedIn",
                "channel": OutreachChannel.LINKEDIN,
                "outcome": "LinkedIn connection established",
                "success_criteria": ["connection_accepted"],
                "time_hours": 48
            },
            {
                "description": "Send personalized LinkedIn message",
                "channel": OutreachChannel.LINKEDIN,
                "prerequisites": ["subgoal_1"],
                "outcome": "Message responded to positively",
                "success_criteria": ["message_response", "interest_expressed"],
                "time_hours": 72
            },
            {
                "description": "Follow up with email containing detailed proposal",
                "channel": OutreachChannel.EMAIL,
                "prerequisites": ["subgoal_2"],
                "outcome": "Email engagement and meeting scheduled",
                "success_criteria": ["email_opened", "meeting_booked"],
                "time_hours": 96
            },
            {
                "description": "Conduct discovery call and present partnership",
                "channel": OutreachChannel.COLD_CALL,
                "prerequisites": ["subgoal_3"],
                "outcome": "Partnership agreement reached",
                "success_criteria": ["agreement_signed", "onboarding_planned"],
                "time_hours": 120
            }
        ]
    
    def _hard_vendor_subgoals(
        self,
        vendor: VendorTarget,
        available_channels: List[OutreachChannel]
    ) -> List[Dict[str, Any]]:
        """Generate subgoals for hard vendors"""
        
        return [
            {
                "description": "Research mutual connections for warm introduction",
                "channel": OutreachChannel.REFERRAL,
                "outcome": "Warm introduction identified",
                "success_criteria": ["mutual_connection_found", "introduction_secured"],
                "time_hours": 72
            },
            {
                "description": "Engage with vendor's GitHub project",
                "channel": OutreachChannel.GITHUB,
                "outcome": "Meaningful interaction established",
                "success_criteria": ["issue_commented", "pull_request_made", "interaction_positive"],
                "time_hours": 96
            },
            {
                "description": "Send warm introduction via mutual connection",
                "channel": OutreachChannel.EMAIL,
                "prerequisites": ["subgoal_1"],
                "outcome": "Introduction accepted and vendor receptive",
                "success_criteria": ["introduction_delivered", "vendor_response_positive"],
                "time_hours": 120
            },
            {
                "description": "Provide value through technical contribution",
                "channel": OutreachChannel.GITHUB,
                "prerequisites": ["subgoal_2"],
                "outcome": "Technical contribution accepted and appreciated",
                "success_criteria": ["contribution_merged", "vendor_engaged"],
                "time_hours": 168
            },
            {
                "description": "Schedule strategic partnership discussion",
                "channel": OutreachChannel.LINKEDIN,
                "prerequisites": ["subgoal_3", "subgoal_4"],
                "outcome": "Strategic discussion scheduled",
                "success_criteria": ["meeting_scheduled", "agenda_confirmed"],
                "time_hours": 192
            },
            {
                "description": "Execute partnership negotiation and onboarding",
                "channel": OutreachChannel.COLD_CALL,
                "prerequisites": ["subgoal_5"],
                "outcome": "Partnership established and onboarding completed",
                "success_criteria": ["partnership_signed", "onboarding_complete"],
                "time_hours": 240
            }
        ]

class OutreachExecutionEvaluator:
    """Evaluates outreach plan execution with AR and STC metrics"""
    
    def __init__(self):
        self.execution_tracker = OutreachExecutionTracker()
        self.metric_calculator = OutreachMetricCalculator()
        
    def execute_outreach_plan(
        self,
        plan: OutreachPlan,
        representation_type: OutreachPlanRepresentation
    ) -> OutreachExecutionResult:
        """Execute outreach plan and evaluate performance"""
        
        execution_id = f"exec_{plan.plan_id}_{int(time.time())}"
        
        # Track execution
        execution_steps = []
        start_time = datetime.now()
        
        try:
            # Execute plan based on representation type
            if representation_type == OutreachPlanRepresentation.SEQUENTIAL_SUBGOALS:
                execution_result = self._execute_sequential_subgoals(plan)
            elif representation_type == OutreachPlanRepresentation.NARRATIVE:
                execution_result = self._execute_narrative_plan(plan)
            elif representation_type == OutreachPlanRepresentation.PSEUDOCODE:
                execution_result = self._execute_pseudocode_plan(plan)
            elif representation_type == OutreachPlanRepresentation.CHECKLIST:
                execution_result = self._execute_checklist_plan(plan)
            
            execution_steps = execution_result["steps"]
            response_received = execution_result.get("response_received", False)
            response_time = execution_result.get("response_time")
            onboarding_completed = execution_result.get("onboarding_completed", False)
            errors = execution_result.get("errors", [])
            lessons = execution_result.get("lessons", [])
            
        except Exception as e:
            response_received = False
            response_time = None
            onboarding_completed = False
            errors = [f"Execution failed: {str(e)}"]
            lessons = []
        
        execution_time = datetime.now() - start_time
        
        # Calculate Achievement Rate (AR)
        achievement_rate = self.metric_calculator.calculate_outreach_achievement_rate(
            plan, execution_steps, response_received, onboarding_completed
        )
        
        return OutreachExecutionResult(
            execution_id=execution_id,
            plan_id=plan.plan_id,
            vendor_id=plan.vendor_id,
            representation_type=representation_type,
            execution_steps=execution_steps,
            response_received=response_received,
            response_time=response_time,
            onboarding_completed=onboarding_completed,
            achievement_rate=achievement_rate,
            execution_time=execution_time,
            errors_encountered=errors,
            lessons_learned=lessons
        )

class OutreachPerformanceAnalyzer:
    """Analyzes outreach performance across different plan representations"""
    
    def analyze_outreach_performance(
        self,
        execution_results: Dict[str, Dict[OutreachPlanRepresentation, OutreachExecutionResult]],
        vendors: List[VendorTarget]
    ) -> Dict[str, Any]:
        """Analyze performance and identify optimal outreach configurations"""
        
        analysis = {
            "overall_performance": {},
            "difficulty_performance": {},
            "representation_performance": {},
            "channel_performance": {},
            "optimal_configurations": {},
            "performance_insights": []
        }
        
        # Analyze overall performance
        all_results = []
        for vendor_results in execution_results.values():
            for result in vendor_results.values():
                all_results.append(result)
        
        if all_results:
            analysis["overall_performance"] = {
                "total_vendors": len(execution_results),
                "average_achievement_rate": np.mean([r.achievement_rate for r in all_results]),
                "response_rate": np.mean([1 if r.response_received else 0 for r in all_results]) * 100,
                "onboarding_rate": np.mean([1 if r.onboarding_completed else 0 for r in all_results]) * 100,
                "average_response_time": np.mean([
                    r.response_time.total_seconds() / 3600 for r in all_results
                    if r.response_time
                ]),
                "total_executions": len(all_results)
            }
        
        # Analyze performance by vendor difficulty
        difficulty_results = {difficulty: [] for difficulty in VendorDifficulty}
        for vendor in vendors:
            vendor_results = execution_results.get(vendor.vendor_id, {})
            for result in vendor_results.values():
                difficulty_results[vendor.difficulty_level].append(result)
        
        for difficulty, results in difficulty_results.items():
            if results:
                analysis["difficulty_performance"][difficulty.value] = {
                    "vendor_count": len(results),
                    "average_achievement_rate": np.mean([r.achievement_rate for r in results]),
                    "response_rate": np.mean([1 if r.response_received else 0 for r in results]) * 100,
                    "onboarding_rate": np.mean([1 if r.onboarding_completed else 0 for r in results]) * 100,
                    "best_representation": max(
                        zip([r.representation_type for r in results], [r.achievement_rate for r in results]),
                        key=lambda x: x[1]
                    )[0].value
                }
        
        # Identify optimal configurations
        analysis["optimal_configurations"] = self._identify_optimal_configurations(
            execution_results, vendors
        )
        
        return analysis

## Enhanced Outreach Success Metrics

| Metric | Target | PlanAhead-Inspired |
|---|---|---|
| Achievement Rate (AR) | > 85% | Optimized planning |
| Response Rate | > 70% | Channel-specific optimization |
| Onboarding Rate | > 40% | Difficulty-based strategies |
| Solved-Task Consistency (STC) | > 80% | Reliable execution |
| Cross-vendor consistency | > 75% | Standardized planning |

## Dependencies

- All vendor hunter agents (Agent-010 through Agent-029)
- Agent-031 (vendor success)
- Email infrastructure
- LinkedIn automation tools
- CRM system for tracking

---

## Research References

1. **PlanAhead**: Planning Representations for LLM Web Agents (arXiv:2605.29927)
2. **HiViG**: History-Aware Visually Grounded Critic for Computer Use Agents (arXiv:2606.11078)
3. **WebChallenger**: Reliable and Efficient Generalist Web Agent (arXiv:2606.10423)
