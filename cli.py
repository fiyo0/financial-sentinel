"""
Interactive CLI for the Financial Multi-Agent Monitoring & Opportunity System.
"""
import sys
import os
import argparse
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from orchestrator import FinancialSentinelOrchestrator
from scheduler import MonitoringScheduler
from models import Portfolio, CriticVerdict


console = Console()


def print_banner():
    banner_text = """
    ╔════════════════════════════════════════════════════════════════════╗
    ║       🛡️  FINANCIAL SENTINEL & ALPHA DISCOVERY MULTI-AGENT        ║
    ║   [Defense: Portfolio Risk]   •   [Offense: Thematic Growth]      ║
    ╚════════════════════════════════════════════════════════════════════╝
    """
    console.print(Text(banner_text, style="bold cyan"))


def display_portfolio(portfolio: Portfolio):
    table = Table(title=f"💼 Portfolio: {portfolio.name} (Total Value: ${portfolio.total_equity():,.2f})", header_style="bold magenta")
    table.add_column("Ticker", style="cyan", justify="left")
    table.add_column("Name", style="white")
    table.add_column("Sector", style="yellow")
    table.add_column("Shares", justify="right")
    table.add_column("Avg Price", justify="right")
    table.add_column("Current Price", justify="right")
    table.add_column("Market Value", justify="right", style="green")
    table.add_column("Weight %", justify="right", style="bold blue")
    table.add_column("Unrealized PnL", justify="right")

    for h in portfolio.holdings:
        pnl = h.unrealized_pnl
        pnl_pct = h.unrealized_pnl_pct
        pnl_str = f"+${pnl:,.2f} (+{pnl_pct:.1f}%)" if pnl >= 0 else f"-${abs(pnl):,.2f} ({pnl_pct:.1f}%)"
        pnl_style = "bold green" if pnl >= 0 else "bold red"

        table.add_row(
            h.ticker,
            h.name,
            h.sector,
            f"{h.shares:,.1f}",
            f"${h.avg_price:,.2f}",
            f"${h.current_price:,.2f}",
            f"${h.market_value:,.2f}",
            f"{h.weight_pct:.1f}%",
            Text(pnl_str, style=pnl_style)
        )
    console.print(table)


def display_briefing(briefing, portfolio):
    # Executive Summary Panel
    console.print(Panel(
        f"[bold white]{briefing.executive_summary}[/bold white]\n\n"
        f"[dim]Generated: {briefing.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')} | News Processed: {briefing.raw_news_count} articles[/dim]",
        title="📋 Executive Briefing Summary",
        border_style="cyan"
    ))

    # Quantitative Stress & Concentration Table
    if briefing.portfolio_stress:
        stress = briefing.portfolio_stress
        stress_table = Table(title="📊 Quantitative Stress & Concentration Matrix", header_style="bold yellow")
        stress_table.add_column("Metric / Scenario", style="white")
        stress_table.add_column("Value / Impact", justify="right")
        stress_table.add_column("Risk Assessment", style="cyan")

        for sector, weight in stress.sector_concentrations.items():
            status = "[bold red]HIGH EXPOSURE[/bold red]" if weight >= 35.0 else "[green]Balanced[/green]"
            stress_table.add_row(f"Sector Weight: {sector}", f"{weight:.1f}%", status)

        stress_table.add_row(
            "Top 3 Holdings Concentration",
            f"{stress.top_3_concentration_pct:.1f}%",
            "[yellow]High Concentration[/yellow]" if stress.top_3_concentration_pct > 60 else "[green]Diversified[/green]"
        )
        stress_table.add_row("Estimated Portfolio Beta", f"{stress.estimated_portfolio_beta:.2f}", "Sensitivity to Market")

        for scenario, impact in stress.macro_shock_scenarios.items():
            impact_style = "green" if impact >= 0 else "red"
            impact_text = f"+{impact:.2f}%" if impact >= 0 else f"{impact:.2f}%"
            stress_table.add_row(f"Macro Shock: {scenario}", Text(impact_text, style=impact_style), "Simulated Portfolio Impact")

        console.print(stress_table)

    # Critical Risk Alerts (P0)
    if briefing.critical_risk_alerts:
        crit_table = Table(title="🚨 Critical Risk Alerts (P0 - Immediate Attention)", header_style="bold red")
        crit_table.add_column("Ticker", style="bold red")
        crit_table.add_column("Direction", style="yellow")
        crit_table.add_column("Magnitude", justify="right")
        crit_table.add_column("Transmission / Rationale", style="white")
        crit_table.add_column("Recommended Action", style="bold cyan")

        for r in briefing.critical_risk_alerts:
            crit_table.add_row(
                r.holding_ticker,
                r.impact.value,
                f"-{r.impact_magnitude_pct:.1f}%",
                f"{r.transmission_channel}: {r.rationale}",
                r.recommended_action
            )
        console.print(crit_table)

    # Notable Headwinds (P1)
    if briefing.notable_risk_alerts:
        notable_table = Table(title="⚠️ Notable Risks & Sector Headwinds (P1)", header_style="bold yellow")
        notable_table.add_column("Ticker", style="yellow")
        notable_table.add_column("Channel", style="dim white")
        notable_table.add_column("Rationale", style="white")
        notable_table.add_column("Action", style="cyan")

        for r in briefing.notable_risk_alerts:
            notable_table.add_row(
                r.holding_ticker,
                r.transmission_channel,
                r.rationale,
                r.recommended_action
            )
        console.print(notable_table)

    # Alpha Opportunities (Offensive Discovery)
    if briefing.top_opportunities:
        opp_table = Table(title="🟢 Alpha Opportunities & Thematic Discovery", header_style="bold green")
        opp_table.add_column("Ticker", style="bold green")
        opp_table.add_column("Theme", style="yellow")
        opp_table.add_column("Horizon", style="cyan")
        opp_table.add_column("Reward/Risk", justify="right", style="bold green")
        opp_table.add_column("Upside Thesis", style="white")
        opp_table.add_column("Portfolio Synergy", style="magenta")

        for o in briefing.top_opportunities:
            opp_table.add_row(
                o.ticker,
                o.theme,
                o.horizon.value,
                f"{o.asymmetric_ratio:.1f}:1 (+{o.estimated_upside_pct}% / -{o.suggested_stop_loss_pct}%)",
                o.upside_thesis,
                o.portfolio_synergy
            )
        console.print(opp_table)

    # Critic Agent Audit Verdicts
    if briefing.critic_verdicts:
        critic_table = Table(title="🔍 Risk / Critic Agent Adversarial Audit", header_style="bold blue")
        critic_table.add_column("Target ID", style="dim")
        critic_table.add_column("Verdict", style="bold")
        critic_table.add_column("Source Grade", justify="center")
        critic_table.add_column("Confidence", justify="right")
        critic_table.add_column("Critic Audit Summary", style="white")
        critic_table.add_column("Stress-Test Question", style="yellow")

        for cv in briefing.critic_verdicts:
            verdict_style = "bold green" if cv.verdict == CriticVerdict.APPROVED else "bold yellow" if cv.verdict == CriticVerdict.APPROVED_WITH_CAVEATS else "bold red"
            stress_q = cv.counter_thesis_questions[0] if cv.counter_thesis_questions else "N/A"
            critic_table.add_row(
                cv.target_id[:20],
                Text(cv.verdict.value, style=verdict_style),
                cv.source_credibility_grade,
                f"{cv.calibrated_confidence_pct:.1f}%",
                cv.review_summary,
                stress_q
            )
        console.print(critic_table)

    if briefing.dispatched_channels:
        console.print(f"[bold green]📢 Dispatched alerts to active channels: {', '.join(briefing.dispatched_channels)}[/bold green]")


def main():
    parser = argparse.ArgumentParser(description="Financial Multi-Agent Monitoring & Alpha Discovery System")
    parser.add_argument("--demo", action="store_true", help="Run comprehensive simulated demo cycle")
    parser.add_argument("--portfolio", type=str, default="data/sample_portfolio.json", help="Path to portfolio JSON or CSV")
    parser.add_argument("--live", action="store_true", help="Fetch live RSS and filing feeds")
    parser.add_argument("--daemon", action="store_true", help="Run continuous background monitoring daemon")
    parser.add_argument("--interval", type=int, default=900, help="Daemon scan interval in seconds")
    parser.add_argument("--history", action="store_true", help="Display recent briefing history from database")
    parser.add_argument("--feedback", nargs=2, metavar=("TARGET_ID", "TYPE"), help="Record user feedback (e.g. --feedback risk_NVDA_1 accurate)")

    args = parser.parse_args()
    print_banner()

    orchestrator = FinancialSentinelOrchestrator()

    if args.feedback:
        target_id, fb_type = args.feedback
        orchestrator.state_store.record_feedback(target_id, fb_type)
        console.print(f"[bold green]✅ Feedback '{fb_type}' recorded for target '{target_id}'[/bold green]")
        return

    if args.history:
        recent = orchestrator.state_store.get_recent_briefings(limit=5)
        if not recent:
            console.print("[yellow]No briefings found in history database yet.[/yellow]")
            return
        hist_table = Table(title="📜 Recent Briefing History", header_style="bold cyan")
        hist_table.add_column("Report ID", style="cyan")
        hist_table.add_column("Generated At", style="dim")
        hist_table.add_column("News Count", justify="right")
        hist_table.add_column("Executive Summary", style="white")
        hist_table.add_column("Channels", style="green")

        for r in recent:
            hist_table.add_row(
                r["report_id"],
                r["generated_at"][:19],
                str(r["raw_news_count"]),
                r["executive_summary"][:100] + "...",
                ", ".join(r["dispatched_channels"]) or "None"
            )
        console.print(hist_table)
        return

    # Load Portfolio
    portfolio_path = args.portfolio
    if not os.path.exists(portfolio_path):
        portfolio_path = os.path.join(os.path.dirname(__file__), "data/sample_portfolio.json")

    try:
        portfolio = orchestrator.load_portfolio_from_file(portfolio_path)
    except Exception as e:
        console.print(f"[red]Error loading portfolio: {e}[/red]")
        sys.exit(1)

    display_portfolio(portfolio)

    if args.demo:
        # Load sample news fixtures
        sample_news_path = os.path.join(os.path.dirname(__file__), "data/sample_news_feeds.json")
        import json
        with open(sample_news_path, "r") as f:
            mock_news = json.load(f)

        console.print("\n[bold yellow]⚡ Running Demo Multi-Agent Analysis with sample breaking news & SEC filings...[/bold yellow]\n")
        briefing = orchestrator.run_monitoring_cycle(
            portfolio, live=False, mock_news=mock_news, force_fresh=True
        )
        display_briefing(briefing, portfolio)

    elif args.daemon:
        scheduler = MonitoringScheduler(orchestrator, portfolio, interval_seconds=args.interval)
        scheduler.start()

    else:
        # Single live or fixture run
        console.print(f"\n[bold yellow]⚡ Running scan (live={args.live})...[/bold yellow]\n")
        briefing = orchestrator.run_monitoring_cycle(portfolio, live=args.live)
        display_briefing(briefing, portfolio)


if __name__ == "__main__":
    main()
