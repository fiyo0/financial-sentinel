"""
Notification Agent: Synthesizes executive briefings, categorizes alerts by priority (P0/P1/P2),
and dispatches them across all configured channels (Telegram, Discord, Slack, Email, Webhook).
"""
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
from models import (
    BriefingReport, HoldingExposureAnalysis, OpportunityAnalysis,
    CriticReview, CriticVerdict, AlertPriority, DirectionalImpact,
    PortfolioStressMetric
)
from agents.base_agent import BaseAgent
from channels.telegram import TelegramChannel
from channels.discord import DiscordChannel
from channels.slack import SlackChannel
from channels.email_sink import EmailChannel
from channels.webhook import GenericWebhookChannel

PST_TZ = ZoneInfo("America/Los_Angeles")


def _format_pst_timestamp(dt: datetime, fmt: str = "%Y-%m-%d %I:%M %p %Z") -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PST_TZ).strftime(fmt)


class NotificationAgent(BaseAgent):
    def __init__(self, state_store: Optional[Any] = None):
        super().__init__(
            name="Notification Agent",
            role_description="Synthesizes briefings and formats alerts for Telegram, Discord, Slack, Email, and Webhooks.",
            state_store=state_store
        )
        self.telegram = TelegramChannel()
        self.discord = DiscordChannel()
        self.slack = SlackChannel()
        self.email = EmailChannel()
        self.webhook = GenericWebhookChannel()

    def generate_briefing(
        self,
        risk_analyses: List[HoldingExposureAnalysis],
        opportunities: List[OpportunityAnalysis],
        critic_reviews: List[CriticReview],
        portfolio_stress: Optional[PortfolioStressMetric] = None,
        total_holdings_monitored: int = 0,
        raw_news_count: int = 0,
        api_key: Optional[str] = None
    ) -> BriefingReport:
        # 1. Filter items approved by Critic Agent
        approved_target_ids = {
            r.target_id for r in critic_reviews 
            if r.verdict in (CriticVerdict.APPROVED, CriticVerdict.APPROVED_WITH_CAVEATS)
        }

        # Filter approved risk analyses
        approved_risks = [
            ra for ra in risk_analyses
            if f"risk_analysis_{ra.holding_ticker}_{ra.news_item_id}" in approved_target_ids
            or f"risk_{ra.holding_ticker}_{ra.news_item_id}" in approved_target_ids
        ]
        if not approved_risks and risk_analyses:
            # Fallback if target_id naming differed
            approved_risks = risk_analyses

        # Filter approved opportunities
        approved_opps = [
            op for op in opportunities
            if f"opportunity_{op.ticker}_{op.news_item_id}" in approved_target_ids
            or f"opp_{op.ticker}_{op.news_item_id}" in approved_target_ids
        ]
        if not approved_opps and opportunities:
            approved_opps = opportunities

        # 2. Segregate by Priority
        p0_critical = [r for r in approved_risks if r.priority == AlertPriority.P0_CRITICAL]
        p1_notable = [r for r in approved_risks if r.priority != AlertPriority.P0_CRITICAL]

        # 3. Create Executive Summary
        exec_summary = self._create_executive_summary(
            p0_critical, p1_notable, approved_opps, portfolio_stress, api_key=api_key
        )


        report = BriefingReport(
            report_id=f"rep_{uuid.uuid4().hex[:8]}",
            generated_at=datetime.utcnow(),
            executive_summary=exec_summary,
            total_holdings_monitored=total_holdings_monitored,
            portfolio_stress=portfolio_stress,
            critical_risk_alerts=p0_critical,
            notable_risk_alerts=p1_notable,
            top_opportunities=approved_opps,
            critic_verdicts=critic_reviews,
            raw_news_count=raw_news_count,
            dispatched_channels=[]
        )

        return report

    def dispatch_briefing(self, report: BriefingReport) -> List[str]:
        dispatched: List[str] = []

        # 1. Telegram
        if self.telegram.is_configured():
            tg_msg = self.format_telegram_message(report)
            if self.telegram.send_message(tg_msg):
                dispatched.append("Telegram")

        # 2. Discord
        if self.discord.is_configured():
            color = 0xE74C3C if report.critical_risk_alerts else 0x2ECC71
            fields = []
            if report.critical_risk_alerts:
                fields.append({
                    "name": "🚨 Critical Risk Alerts",
                    "value": "\n".join([f"• **{r.holding_ticker}**: {r.rationale[:120]}" for r in report.critical_risk_alerts[:3]])
                })
            if report.top_opportunities:
                fields.append({
                    "name": "🟢 Alpha Opportunities",
                    "value": "\n".join([f"• **{o.ticker}** ({o.theme}): +{o.estimated_upside_pct}% target" for o in report.top_opportunities[:3]])
                })
            if self.discord.send_embed(
                title=f"📊 Financial Sentinel Briefing — {_format_pst_timestamp(report.generated_at, '%b %d, %I:%M %p %Z')}",
                description=report.executive_summary,
                color=color,
                fields=fields
            ):
                dispatched.append("Discord")

        # 3. Slack
        if self.slack.is_configured():
            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "📊 Financial Sentinel Briefing"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": report.executive_summary}
                }
            ]
            if self.slack.send_blocks("Financial Sentinel Alert", blocks):
                dispatched.append("Slack")

        # 4. Email
        if self.email.is_configured():
            html = self.format_html_email(report)
            if self.email.send_email(
                subject=f"Financial Sentinel Briefing: {len(report.critical_risk_alerts)} Critical / {len(report.top_opportunities)} Opps",
                html_body=html
            ):
                dispatched.append("Email")

        # 5. Generic Webhook
        if self.webhook.is_configured():
            if self.webhook.send_payload("financial_briefing", report.model_dump()):
                dispatched.append("Webhook")

        report.dispatched_channels = dispatched
        return dispatched

    def format_telegram_message(self, report: BriefingReport) -> str:
        lines = [
            f"<b>🛡️ FINANCIAL SENTINEL BRIEFING</b>",
            f"<i>{_format_pst_timestamp(report.generated_at, '%Y-%m-%d %I:%M %p %Z')}</i>\n",
            f"<b>Executive Summary:</b>\n{report.executive_summary}\n"
        ]

        if report.critical_risk_alerts:
            lines.append("<b>🚨 CRITICAL RISK ALERTS (P0):</b>")
            for r in report.critical_risk_alerts:
                lines.append(f"• <b>{r.holding_ticker}</b> ({r.impact.value} -{r.impact_magnitude_pct}%)")
                lines.append(f"  {r.rationale}")
                lines.append(f"  <i>Action: {r.recommended_action}</i>\n")

        if report.notable_risk_alerts:
            lines.append("<b>⚠️ NOTABLE RISKS & HEADWINDS (P1):</b>")
            for r in report.notable_risk_alerts[:3]:
                lines.append(f"• <b>{r.holding_ticker}</b>: {r.rationale}")

        if report.top_opportunities:
            lines.append("\n<b>🟢 ALPHA OPPORTUNITIES:</b>")
            for o in report.top_opportunities[:3]:
                lines.append(f"• <b>{o.ticker}</b> | <i>{o.theme}</i>")
                lines.append(f"  Thesis: {o.upside_thesis[:150]}...")
                lines.append(f"  Risk/Reward: {o.asymmetric_ratio:.1f}:1 (+{o.estimated_upside_pct}% / -{o.suggested_stop_loss_pct}% stop)")
                lines.append(f"  Synergy: {o.portfolio_synergy}\n")

        return "\n".join(lines)

    format_telegram_digest = format_telegram_message


    def format_html_email(self, report: BriefingReport) -> str:
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #1e293b; }}
                .container {{ max-width: 680px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #0f172a; color: #ffffff; padding: 20px; border-radius: 8px 8px 0 0; }}
                .badge-crit {{ background: #ef4444; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold; }}
                .badge-opp {{ background: #10b981; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold; }}
                .card {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 15px; margin: 15px 0; }}
                .risk-card {{ border-left: 4px solid #ef4444; }}
                .opp-card {{ border-left: 4px solid #10b981; }}
                .footer {{ font-size: 12px; color: #64748b; margin-top: 30px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>🛡️ Financial Sentinel Briefing</h2>
                    <p style="margin: 0; opacity: 0.8;">{_format_pst_timestamp(report.generated_at, '%B %d, %Y - %I:%M %p %Z')}</p>
                </div>
                <div style="padding: 20px 0;">
                    <h3>Executive Summary</h3>
                    <p>{report.executive_summary}</p>
                    
                    {self._render_html_risks(report.critical_risk_alerts, report.notable_risk_alerts)}
                    {self._render_html_opps(report.top_opportunities)}
                </div>
                <div class="footer">
                    Generated by Financial Sentinel Multi-Agent System &bull; Total Holdings Monitored: {report.total_holdings_monitored}
                </div>
            </div>
        </body>
        </html>
        """

    def _render_html_risks(self, critical: List[HoldingExposureAnalysis], notable: List[HoldingExposureAnalysis]) -> str:
        html = ""
        if critical:
            html += "<h3>🚨 Critical Alerts (P0)</h3>"
            for r in critical:
                html += f"""
                <div class="card risk-card">
                    <h4>{r.holding_ticker} - <span class="badge-crit">{r.impact.value} ({r.impact_magnitude_pct}%)</span></h4>
                    <p>{r.rationale}</p>
                    <p><strong>Action:</strong> {r.recommended_action}</p>
                </div>
                """
        if notable:
            html += "<h3>⚠️ Notable Portfolio Headwinds</h3>"
            for r in notable[:3]:
                html += f"""
                <div class="card">
                    <h4>{r.holding_ticker} ({r.transmission_channel})</h4>
                    <p>{r.rationale}</p>
                </div>
                """
        return html

    def _render_html_opps(self, opps: List[OpportunityAnalysis]) -> str:
        if not opps:
            return ""
        html = "<h3>🟢 Alpha Opportunities</h3>"
        for o in opps[:3]:
            html += f"""
            <div class="card opp-card">
                <h4>{o.ticker} - {o.theme} <span class="badge-opp">{o.horizon.value}</span></h4>
                <p><strong>Thesis:</strong> {o.upside_thesis}</p>
                <p><strong>Target:</strong> +{o.estimated_upside_pct}% | <strong>Stop:</strong> -{o.suggested_stop_loss_pct}% | <strong>Reward/Risk:</strong> {o.asymmetric_ratio}:1</p>
                <p><strong>Portfolio Synergy:</strong> {o.portfolio_synergy}</p>
            </div>
            """
        return html

    def _create_executive_summary(
        self,
        critical: List[HoldingExposureAnalysis],
        notable: List[HoldingExposureAnalysis],
        opps: List[OpportunityAnalysis],
        stress: Optional[PortfolioStressMetric],
        api_key: Optional[str] = None
    ) -> str:
        # 1. Try Gemini 3.8 Dynamic Synthesis
        effective_key = api_key or self.api_key

        if self.use_llm and effective_key:
            crit_summary = [f"{c.holding_ticker}: {c.rationale}" for c in critical]
            notable_summary = [f"{n.holding_ticker}: {n.rationale}" for n in notable[:4]]
            opps_summary = [f"{o.ticker} ({o.theme})" for o in opps[:3]]

            prompt = f"""
            You are the Chief Investment Officer writing an executive briefing summary for an investor.

            CURRENT OWNED HOLDINGS STATUS:
            - Critical Risks (P0): {crit_summary or 'None (All positions healthy)'}
            - Current Holdings Catalysts & Posture: {notable_summary}

            EXTERNAL WATCHLIST CANDIDATES (STOCKS NOT CURRENTLY OWNED - NEW IDEAS TO CONSIDER):
            - New Alpha Candidates: {opps_summary}

            INSTRUCTIONS:
            Write a crisp, sober 2-3 sentence executive briefing summary:
            1. First describe the health, risks, and performance posture of the CURRENT OWNED HOLDINGS.
            2. If mentioning external alpha candidates ({', '.join([o.ticker for o in opps[:3]])}), explicitly refer to them as 'external watchlist candidates' or 'unowned diversification ideas to consider', NOT existing holdings.
            3. Avoid unwarranted puffery, hyperbole, and false profundity. Describe market conditions and holding posture objectively and with proportionate tone.

            Return JSON: {{"executive_summary": "2-3 concise, clear sentences."}}

            """
            res = self.query_llm_json(prompt, api_key=effective_key)
            if res and "executive_summary" in res:
                return res["executive_summary"].strip()


        # 2. Heuristic fallback
        parts = []
        if critical:
            crit_tickers = ", ".join([c.holding_ticker for c in critical])
            parts.append(f"🚨 URGENT: {len(critical)} critical risk alert(s) detected affecting {crit_tickers}.")
        else:
            parts.append("✅ No immediate P0 portfolio emergencies detected across monitored holdings.")

        if stress and stress.high_concentration_warning:
            top_sectors = [f"{s} ({pct}%)" for s, pct in stress.sector_concentrations.items() if pct >= 30.0]
            parts.append(f"⚠️ Concentration alert: Heavy exposure in {', '.join(top_sectors)}.")

        if opps:
            opp_tickers = ", ".join([f"{o.ticker} ({o.theme})" for o in opps[:2]])
            parts.append(f"🟢 {len(opps)} vetted market opportunity candidate(s) identified: {opp_tickers}.")

        if notable:
            parts.append(f"Monitored {len(notable)} secondary sector/macro developments.")

        return " ".join(parts)
