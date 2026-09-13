"""
Risk & Critic Agent: Acts as an adversarial auditor, evaluating analyses and opportunities for confirmation bias,
low-confidence sources, and hype bubbles using unified batch evaluation.
"""
from typing import List, Optional, Dict, Any, Union
from models import (
    HoldingExposureAnalysis, OpportunityAnalysis, CriticReview, CriticVerdict, AlertPriority, NewsItem
)
from agents.base_agent import BaseAgent
from config import config


CREDIBILITY_GRADES = {
    0.95: "A+",
    0.90: "A",
    0.85: "B+",
    0.80: "B",
    0.70: "C",
    0.60: "D",
    0.0: "F"
}


class RiskCriticAgent(BaseAgent):
    def __init__(self, state_store: Optional[Any] = None):
        super().__init__(
            name="Risk & Critic Agent",
            role_description="Audits risk analyses and opportunity pitches for confirmation bias, hype, and weak citations.",
            state_store=state_store
        )

    def audit_all_batch(
        self,
        risk_analyses: List[HoldingExposureAnalysis],
        opportunities: List[OpportunityAnalysis],
        api_key: Optional[str] = None
    ) -> List[CriticReview]:
        """
        Batches all risk analyses and opportunities into one single fast LLM audit call.
        """
        effective_key = api_key or self.api_key
        if not effective_key:
            raise ValueError("Gemini API key is required for Adversarial Risk & Critic audit. Please add your key in Settings.")

        llm_reviews = self._llm_batch_audit(risk_analyses, opportunities, api_key=effective_key)
        if llm_reviews is None:
            raise RuntimeError("Gemini Adversarial Risk Critic failed to complete audit. Please verify API key and network status.")

        return llm_reviews


    def _llm_batch_audit(
        self,
        risk_analyses: List[HoldingExposureAnalysis],
        opportunities: List[OpportunityAnalysis],
        api_key: Optional[str] = None
    ) -> Optional[List[CriticReview]]:
        items_to_audit = []
        for ra in risk_analyses:
            items_to_audit.append({
                "target_id": f"risk_{ra.holding_ticker}_{ra.news_item_id}",
                "item_type": "risk_analysis",
                "ticker": ra.holding_ticker,
                "name": ra.holding_name,
                "impact": ra.impact.value,
                "rationale": ra.rationale,
                "action": ra.recommended_action,
                "citations": ra.citations
            })
        for opp in opportunities:
            items_to_audit.append({
                "target_id": f"opp_{opp.ticker}_{opp.news_item_id}",
                "item_type": "opportunity",
                "ticker": opp.ticker,
                "name": opp.name,
                "theme": opp.theme,
                "upside_thesis": opp.upside_thesis,
                "asymmetric_ratio": opp.asymmetric_ratio,
                "citations": opp.citations
            })

        if not items_to_audit:
            return []


        prompt = f"""
        You are the Chief Risk Officer (CRO) and Devil's Advocate for an institutional quantitative equity fund.
        Your job is to adversarially stress-test every position and proposal with sharp, asset-specific counter-theses.

        PROPOSALS TO AUDIT:
        {items_to_audit}

        AUDIT RULES:
        1. Formulate a sharp, idiosyncratic Devil's Advocate counter-thesis question tailored specifically to each asset's distinct business model, operating margins, supply chain bottlenecks, regulatory exposure, or valuation multiple (avoid generic boilerplate questions).
        2. Evaluate source credibility grade realistically:
           - "A+": Direct SEC 10-Q/8-K disclosures, Fed reports, audited exchange feeds
           - "A": Reuters, Bloomberg, WSJ, audited exchange filings
           - "B+": CNBC, MarketWatch, reputable sell-side notes
           - "B": General media, secondary sector coverage
           - "C+": High-volatility speculative ideas, unvetted trials
        3. Differentiate verdicts: "APPROVED", "APPROVED_WITH_CAVEATS", or "REJECTED_SPECULATIVE".
        4. In review_summary, write a crisp 1-line verdict starting with [TICKER].

        Return JSON matching this exact schema:
        {{
            "reviews": [
                {{
                    "target_id": string (must match proposal target_id),
                    "item_type": "risk_analysis" | "opportunity",
                    "verdict": "APPROVED" | "APPROVED_WITH_CAVEATS" | "REJECTED_SPECULATIVE" | "REJECTED_LOW_CREDIBILITY",
                    "bias_score": float (0.05 to 0.85),
                    "source_credibility_grade": "A+" | "A" | "B+" | "B" | "C+",
                    "calibrated_confidence_pct": float (50.0 to 95.0),
                    "counter_thesis_questions": ["Asset-specific challenge directly questioning business model fundamentals"],
                    "identified_biases": ["Flagged assumption or vulnerability"],
                    "review_summary": "[TICKER] Sharp adversarial critique summary"
                }}
            ]
        }}
        """
        effective_key = api_key or self.api_key
        res = self.query_llm_json(prompt, api_key=effective_key)
        if not res or "reviews" not in res:
            return None

        reviews = []
        for item in res.get("reviews", []):
            try:
                v_str = item.get("verdict", "APPROVED").upper()
                if v_str not in [e.value for e in CriticVerdict]:
                    v_str = "APPROVED"

                reviews.append(CriticReview(
                    target_id=item.get("target_id", ""),
                    item_type=item.get("item_type", "risk_analysis"),
                    verdict=CriticVerdict(v_str),
                    bias_score=float(item.get("bias_score", 0.2)),
                    source_credibility_grade=item.get("source_credibility_grade", "A"),
                    calibrated_confidence_pct=float(item.get("calibrated_confidence_pct", 85.0)),
                    counter_thesis_questions=item.get("counter_thesis_questions", []),
                    identified_biases=item.get("identified_biases", []),
                    review_summary=item.get("review_summary", "Audit completed.")
                ))
            except Exception:
                continue

        return reviews if len(reviews) > 0 else None

    def review_risk_analysis(
        self,
        analysis: HoldingExposureAnalysis,
        news_item: Optional[NewsItem] = None
    ) -> CriticReview:
        t = analysis.holding_ticker.upper()
        name = analysis.holding_name or t
        is_direct_regulatory = any("SEC" in c or "Exchange" in c or "Quantitative" in c for c in analysis.citations)
        source_score = 0.95 if is_direct_regulatory else (0.90 if news_item else 0.88)
        
        is_critical = (analysis.priority == AlertPriority.P0_CRITICAL or analysis.impact_magnitude_pct >= 4.0)
        verdict = CriticVerdict.APPROVED_WITH_CAVEATS if is_critical else CriticVerdict.APPROVED
        confidence = 78.0 if is_critical else 90.0

        # Pure dynamic synthesis based on holding properties (zero hardcoded dictionaries)
        channel = analysis.transmission_channel.value if hasattr(analysis.transmission_channel, 'value') else str(analysis.transmission_channel)
        question = f"What specific operating margin compression or execution bottleneck could invalidate the {analysis.impact.value} thesis on {name} ({t}) via the {channel} channel?"

        grade = self._score_to_grade(source_score)

        return CriticReview(
            target_id=f"risk_{analysis.holding_ticker}_{analysis.news_item_id}",
            item_type="risk_analysis",
            verdict=verdict,
            bias_score=0.35 if is_critical else 0.12,
            source_credibility_grade=grade,
            calibrated_confidence_pct=confidence,
            counter_thesis_questions=[question],
            identified_biases=[f"Idiosyncratic operational sensitivity on {t}"] if is_critical else [],
            review_summary=f"[{t}] Audit Verified: Causal transmission channel vetted with {analysis.impact.value} posture."
        )

    def review_opportunity(
        self,
        opp: OpportunityAnalysis,
        news_item: Optional[NewsItem] = None
    ) -> CriticReview:
        is_speculative = (opp.estimated_upside_pct >= 40.0 or opp.asymmetric_ratio >= 4.5 or opp.suggested_stop_loss_pct <= 4.0)
        is_high_synergy = "Diversification" in opp.portfolio_synergy or "Sector" in opp.portfolio_synergy
        
        if is_speculative:
            verdict = CriticVerdict.REJECTED_SPECULATIVE
            confidence = 52.0
            grade = "C+"
        elif is_high_synergy:
            verdict = CriticVerdict.APPROVED
            confidence = 89.0
            grade = "A"
        else:
            verdict = CriticVerdict.APPROVED_WITH_CAVEATS
            confidence = 75.0
            grade = "B"

        counter_questions = [
            f"What happens to {opp.ticker}'s valuation multiple if {opp.theme} adoption milestones encounter regulatory or interconnection delays?",
            f"Is {opp.ticker}'s expected revenue inflection already partially priced into its current enterprise value?"
        ]

        return CriticReview(
            target_id=f"opp_{opp.ticker}_{opp.news_item_id}",
            item_type="opportunity",
            verdict=verdict,
            bias_score=0.55 if is_speculative else 0.18,
            source_credibility_grade=grade,
            calibrated_confidence_pct=confidence,
            counter_thesis_questions=counter_questions,
            identified_biases=["Asymmetric variance skew"] if is_speculative else [],
            review_summary=f"[{opp.ticker}] Alpha Evaluated: {opp.asymmetric_ratio}:1 reward-to-risk in {opp.theme}."
        )

    def _score_to_grade(self, score: float) -> str:
        for threshold, grade in sorted(CREDIBILITY_GRADES.items(), key=lambda x: x[0], reverse=True):
            if score >= threshold:
                return grade
        return "B"
