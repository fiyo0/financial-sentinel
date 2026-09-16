"""
Specialized Market & Portfolio Briefing Agent.
Synthesizes broad market macro, pre/post-market earnings, sector rotations,
and portfolio correlation into executive Telegram briefings.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional
from agents.base_agent import BaseAgent
from models import Portfolio, NewsItem
from analytics.economic_calendar import get_economic_calendar_context, format_economic_calendar_for_prompt


BRIEFING_COMMUNICATION_RULES = """
COMMUNICATION & RECOMMENDATION DISCIPLINE:
1. Avoid unwarranted puffery, hyperbole, and false profundity.
   - Do not use dramatic, breathless, or theatrical phrasing for ordinary market developments.
   - Describe routine market fluctuations (0.2% - 1.0%) with measured, proportionate language.
   - Ground all commentary in concrete facts, verifiable catalysts, price movements, and reliable financial data.
2. Maintain high analytical standards for market opportunities:
   - Do not recommend random or speculative tickers.
   - Any highlighted opportunity must possess genuine fundamental merit: a clear competitive moat, high forward growth potential, or recent credible positive changes in institutional analyst ratings.
   - If market conditions are overextended or lack high-conviction risk/reward setups, advise patience or preserving dry powder rather than forcing low-quality picks.
3. Strict Temporal Accuracy & Catalyst Timing:
   - Ground all commentary strictly in TODAY'S calendar date and the exact current session time.
   - Check the MACROECONOMIC & CENTRAL BANK CALENDAR GROUND TRUTH in the prompt.
   - If a central bank or economic catalyst has status [COMPLETED], it CONCLUDED EARLIER TODAY. Analyze its outcome, market reaction, and day-end impact; NEVER refer to it as happening "tomorrow" or "upcoming".
   - Only refer to an event as happening "tomorrow" if it is explicitly scheduled for tomorrow's date.
   - Cross-reference news headlines against their relative age timestamps. Do not cite yesterday's preview speculation as today's market drivers.
"""


class MarketBriefingAgent(BaseAgent):
    def __init__(self, state_store: Optional[Any] = None):
        super().__init__(
            name="Market Briefing Agent",
            role_description="Synthesizes scheduled pre-market, mid-day momentum, post-market earnings wrap-ups, and weekend macro intelligence.",
            state_store=state_store
        )

    def _extract_significant_portfolio_movers(
        self,
        portfolio: Portfolio,
        news_items: List[NewsItem],
        threshold_pct: float = 1.5
    ) -> str:
        """
        Identifies holdings that had significant price movement (>= 1.5% intraday change)
        or direct breaking news catalysts.
        Returns a concise context string for the prompt.
        """
        if not portfolio.holdings:
            return "No active holdings in portfolio."

        news_tickers = set()
        for item in news_items:
            for t in item.related_tickers:
                news_tickers.add(t.upper())

        significant_movers = []
        for h in portfolio.holdings:
            chg = getattr(h, "daily_change_pct", 0.0) or 0.0
            has_price_move = abs(chg) >= threshold_pct
            has_news = h.ticker.upper() in news_tickers
            if has_price_move or has_news:
                significant_movers.append((h, chg, has_news))

        if not significant_movers:
            return (
                "PORTFOLIO MOVER STATUS: Core holdings calm with low volatility (<1.5% price movement) and no direct breaking news catalysts.\n"
                "INSTRUCTION FOR PORTFOLIO SECTION: Do NOT list or re-explain every static holding. Simply output: '💼 <b>Portfolio Standing:</b> Core holdings calm with low volatility.'"
            )

        lines = [
            f"PORTFOLIO HOLDINGS WITH SIGNIFICANT MOVEMENT / CATALYSTS (Threshold >= {threshold_pct}% or Direct News):"
        ]
        for h, chg, has_news in significant_movers:
            sign = "+" if chg > 0 else ""
            news_flag = " | ⚡ Active News Catalyst" if has_news else ""
            lines.append(f"- {h.ticker} ({h.name}, {h.sector}): ${h.current_price:.2f} ({sign}{chg:.2f}% day change){news_flag}")
        lines.append("\nINSTRUCTION: ONLY mention and analyze the specific holdings listed above that had notable moves. Do NOT recite the rest of the static holdings.")
        return "\n".join(lines)

    def _format_indices_summary(self, market_overview: Dict[str, Any]) -> str:
        indices = market_overview.get("indices", {})
        if not indices:
            return "Market Indices: Awaiting real-time opening prints."
        lines = []
        for sym, d in indices.items():
            chg = d.get("change_pct", 0.0)
            sign = "+" if chg > 0 else ""
            p = d.get("current_price", 0.0)
            price_str = f"${p:.2f}" if p > 0 else ""
            lines.append(f"• {sym} ({d.get('name', sym)}): {price_str} ({sign}{chg:.2f}%)")
        return "\n".join(lines)

    def _format_news_summary(self, news_items: List[NewsItem], as_of: Optional[datetime] = None) -> str:
        if not news_items:
            return "No major breaking macro alerts."

        ref_dt = as_of or datetime.now(ZoneInfo("America/Los_Angeles"))
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        ref_et = ref_dt.astimezone(ZoneInfo("America/New_York"))

        # Sort chronologically so newest breaking news is prioritized
        sorted_items = sorted(news_items, key=lambda x: x.published_at if x.published_at else datetime.min, reverse=True)

        lines = []
        for n in sorted_items[:15]:
            pub = n.published_at
            if pub:
                if pub.tzinfo is None:
                    pub_aware = pub.replace(tzinfo=timezone.utc)
                else:
                    pub_aware = pub
                pub_et = pub_aware.astimezone(ZoneInfo("America/New_York"))
                age_h = max(0.0, (ref_et - pub_et).total_seconds() / 3600.0)
                if pub_et.date() == ref_et.date():
                    time_tag = f"Today {pub_et.strftime('%I:%M %p')} EDT ({age_h:.1f}h ago)"
                elif (ref_et.date() - pub_et.date()).days == 1:
                    time_tag = f"Yesterday {pub_et.strftime('%b %d, %I:%M %p')} EDT ({age_h:.1f}h ago)"
                else:
                    time_tag = f"{pub_et.strftime('%b %d, %I:%M %p')} EDT ({age_h:.1f}h ago)"
            else:
                time_tag = "Recent"

            lines.append(f"- [{n.source} | {time_tag}] {n.title}: {n.summary[:150]}")
        return "\n".join(lines)

    def _format_holdings_summary(self, portfolio: Portfolio) -> str:
        if not portfolio or not portfolio.holdings:
            return "No active portfolio holdings."
        lines = []
        for h in portfolio.holdings:
            price = getattr(h, "current_price", 0.0) or 0.0
            shares = getattr(h, "shares", 0.0) or 0.0
            name = getattr(h, "name", h.ticker) or h.ticker
            sector = getattr(h, "sector", "Equities") or "Equities"
            lines.append(f"• {h.ticker} ({name}) — Sector: {sector}, Shares: {shares:g}, Price: ${price:.2f}")
        return "\n".join(lines)

    def generate_premarket_briefing(
        self,
        portfolio: Portfolio,
        market_overview: Dict[str, Any],
        news_items: List[NewsItem],
        api_key: Optional[str] = None,
        as_of: Optional[datetime] = None
    ) -> str:
        """
        6:30 AM PST Pre-Market Intelligence:
        Overnight global macro, futures, pre-market earnings, portfolio open impact & market-wide alpha ideas.
        """
        ref_dt = as_of or datetime.now(ZoneInfo("America/Los_Angeles"))
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        as_of_pst = ref_dt.astimezone(ZoneInfo("America/Los_Angeles"))
        as_of_et = ref_dt.astimezone(ZoneInfo("America/New_York"))

        economic_ctx = get_economic_calendar_context(as_of_pst)
        economic_str = format_economic_calendar_for_prompt(economic_ctx)

        movers_context = self._extract_significant_portfolio_movers(portfolio, news_items, threshold_pct=1.5)
        indices_str = self._format_indices_summary(market_overview)
        news_str = self._format_news_summary(news_items, as_of=as_of_pst)

        prompt = f"""
        You are a seasoned Chief Investment Officer delivering the 6:30 AM PST PRE-MARKET BRIEFING.

        {BRIEFING_COMMUNICATION_RULES}

        CURRENT TIME & SESSION GROUND TRUTH:
        • Calendar Date: {as_of_pst.strftime('%A, %B %d, %Y')}
        • Current Time: {as_of_pst.strftime('%I:%M %p')} PST / {as_of_et.strftime('%I:%M %p')} EDT
        • Session Phase: Pre-Market Opening (U.S. cash equity markets open at 6:30 AM PST / 9:30 AM EDT)

        {economic_str}

        BROAD BENCHMARKS & FUTURES:
        {indices_str}

        OVERNIGHT MACRO & PRE-MARKET HEADLINES:
        {news_str}

        INVESTOR'S PORTFOLIO STATUS:
        {movers_context}

        TASK:
        Generate a concise, disciplined pre-market executive brief in clean Telegram HTML format (use <b>, <i>, <code>).

        Structure the message with these exact sections:
        🌅 <b>PRE-MARKET INTELLIGENCE & OPENING CATALYSTS (6:30 AM PST)</b>

        📊 <b>Macro & Benchmark Tone:</b>
        - 2-3 bullet points on overnight global markets, futures, yields, and opening sentiment.

        💼 <b>Portfolio Standing / Notable Movers:</b>
        - If specific holdings experienced significant movement (>=1.5%) or direct breaking news, highlight ONLY those tickers and explain their driver. If no holdings had significant moves, simply write a 1-line status confirming calm conditions.

        🔥 <b>Market-Wide Opportunity Catalyst:</b>
        - Spotlight 1 to 2 high-conviction breakout setups or secular themes in the broader market OUTSIDE the portfolio. Ensure selections have genuine fundamental merit, competitive moats, or recent credible analyst revisions. Do NOT highlight speculative or random tickers.

        🎯 <b>Opening Gameplan:</b>
        - 1-2 actionable risk management or watchlist focus points for the opening bell.

        Keep the tone institutional, measured, and actionable. Avoid unwarranted puffery or hyperbole. Do not use Markdown backticks.
        """

        effective_key = api_key or self.api_key
        if not effective_key:
            return (
                "🔒 <b>Pre-Market Briefing Paused:</b> Gemini API key is missing.\n\n"
                "Please configure your Gemini API key in Settings to activate automated scheduled AI briefings."
            )

        res = self.query_llm_text(prompt, api_key=effective_key)
        if res and len(res.strip()) > 50:
            return res.strip()

        return (
            "⚠️ <b>AI Briefing Generation Unavailable:</b> Gemini was unable to generate the Pre-Market Briefing at this time.\n\n"
            "Please check network connectivity or your Gemini API quota."
        )

    def generate_midmarket_briefing(
        self,
        portfolio: Portfolio,
        market_overview: Dict[str, Any],
        news_items: List[NewsItem],
        api_key: Optional[str] = None,
        as_of: Optional[datetime] = None
    ) -> str:
        """
        10:00 AM PST Mid-Market Pulse:
        Midday momentum, Fed statements, economic releases, sector rotations, and emerging breakout opportunities.
        """
        ref_dt = as_of or datetime.now(ZoneInfo("America/Los_Angeles"))
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        as_of_pst = ref_dt.astimezone(ZoneInfo("America/Los_Angeles"))
        as_of_et = ref_dt.astimezone(ZoneInfo("America/New_York"))

        economic_ctx = get_economic_calendar_context(as_of_pst)
        economic_str = format_economic_calendar_for_prompt(economic_ctx)

        movers_context = self._extract_significant_portfolio_movers(portfolio, news_items, threshold_pct=1.5)
        indices_str = self._format_indices_summary(market_overview)
        news_str = self._format_news_summary(news_items, as_of=as_of_pst)

        prompt = f"""
        You are a seasoned Chief Investment Officer delivering the 10:00 AM PST MID-MARKET PULSE.

        {BRIEFING_COMMUNICATION_RULES}

        CURRENT TIME & SESSION GROUND TRUTH:
        • Calendar Date: {as_of_pst.strftime('%A, %B %d, %Y')}
        • Current Time: {as_of_pst.strftime('%I:%M %p')} PST / {as_of_et.strftime('%I:%M %p')} EDT
        • Session Phase: Mid-Day Trading (Morning cash session complete; entering midday positioning)

        {economic_str}

        BENCHMARK INDICES & INTRADAY BREADTH:
        {indices_str}

        MID-DAY MACRO & BREAKING NEWS:
        {news_str}

        INVESTOR'S PORTFOLIO STATUS:
        {movers_context}

        TASK:
        Generate a concise, disciplined mid-day market update in clean Telegram HTML format (use <b>, <i>, <code>).

        Structure the message with these exact sections:
        ☀️ <b>MID-MARKET PULSE & MOMENTUM (10:00 AM PST)</b>

        📈 <b>Intraday Market Action:</b>
        - Broad market direction, leading/lagging sectors, and morning economic/Fed data digestion.

        💼 <b>Portfolio Standing / Notable Movers:</b>
        - If specific holdings experienced notable intraday moves (>=1.5%) or breaking catalysts, analyze ONLY those tickers. If calm, include a crisp 1-line note.

        🚀 <b>Active Market Opportunities:</b>
        - Highlight 1-2 emerging midday setups or secular themes with credible volume or analyst catalysts. Be selective and critical—do not highlight speculative or random tickers without proven fundamental backing.

        🛡️ <b>Afternoon Posture:</b>
        - Key levels or afternoon Fed/economic events to watch before the closing session.

        Keep it institutional, balanced, objective, and formatted with clean HTML tags. Avoid unwarranted puffery or hyperbole.
        """
        effective_key = api_key or self.api_key
        if not effective_key:
            return (
                "🔒 <b>Mid-Market Briefing Paused:</b> Gemini API key is missing.\n\n"
                "Please configure your Gemini API key in Settings to activate automated scheduled AI briefings."
            )

        res = self.query_llm_text(prompt, api_key=effective_key)
        if res and len(res.strip()) > 50:
            return res.strip()

        return (
            "⚠️ <b>AI Briefing Generation Unavailable:</b> Gemini was unable to generate the Mid-Market Pulse at this time.\n\n"
            "Please check network connectivity or your Gemini API quota."
        )


    def generate_postmarket_briefing(
        self,
        portfolio: Portfolio,
        market_overview: Dict[str, Any],
        news_items: List[NewsItem],
        market_movers: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        as_of: Optional[datetime] = None
    ) -> str:
        """
        3:00 PM PST Post-Market Wrap:
        Closing bell recap, after-hours earnings call takeaways, today's top winners/losers & hot asymmetric plays.
        """
        ref_dt = as_of or datetime.now(ZoneInfo("America/Los_Angeles"))
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        as_of_pst = ref_dt.astimezone(ZoneInfo("America/Los_Angeles"))
        as_of_et = ref_dt.astimezone(ZoneInfo("America/New_York"))

        economic_ctx = get_economic_calendar_context(as_of_pst)
        economic_str = format_economic_calendar_for_prompt(economic_ctx)

        movers_context = self._extract_significant_portfolio_movers(portfolio, news_items, threshold_pct=1.5)
        indices_str = self._format_indices_summary(market_overview)
        news_str = self._format_news_summary(news_items, as_of=as_of_pst)

        movers_str = "Top Market Movers:\n"
        if market_movers:
            gainers = market_movers.get("top_gainers", [])
            losers = market_movers.get("top_losers", [])
            if gainers:
                movers_str += "🟢 Top Gainers: " + ", ".join([f"{g['ticker']} ({g['change_pct']:+.1f}%)" for g in gainers[:3]]) + "\n"
            if losers:
                movers_str += "🔴 Top Losers: " + ", ".join([f"{lsr['ticker']} ({lsr['change_pct']:+.1f}%)" for lsr in losers[:3]]) + "\n"
        else:
            movers_str += "Cross-exchange volume leaders and earnings movers."

        prompt = f"""
        You are a seasoned Chief Investment Officer delivering the 3:00 PM PST POST-MARKET WRAP-UP.

        {BRIEFING_COMMUNICATION_RULES}

        CURRENT TIME & SESSION GROUND TRUTH:
        • Calendar Date: {as_of_pst.strftime('%A, %B %d, %Y')}
        • Current Time: {as_of_pst.strftime('%I:%M %p')} PST / {as_of_et.strftime('%I:%M %p')} EDT
        • Session Phase: Post-Market Closing Wrap (Regular trading ended at 1:00 PM PST / 4:00 PM EDT)

        {economic_str}

        CLOSING BENCHMARK PERFORMANCE:
        {indices_str}

        MARKET MOVERS & EARNINGS RELEASES:
        {movers_str}

        AFTER-HOURS & CLOSING NEWS:
        {news_str}

        INVESTOR'S PORTFOLIO STATUS:
        {movers_context}

        TASK:
        Generate a comprehensive, objective post-market briefing in clean Telegram HTML format (use <b>, <i>, <code>).

        Structure the message with these exact sections:
        🌙 <b>POST-MARKET WRAP & DAY-END RECAP (3:00 PM PST)</b>

        🏁 <b>Closing Bell Summary:</b>
        - Daily closing index results and what dictated today's tape (including any macro/central bank catalysts that concluded earlier today).

        🏆 <b>Notable Market Movers:</b>
        - Recap the key movers of the day across the market, explaining the drivers behind their moves (earnings beat/miss, forward guidance, analyst revisions, or M&A).

        💼 <b>Portfolio Day-End Health:</b>
        - If specific holdings experienced significant movement (>=1.5%) or earnings releases, detail ONLY those movers. If all holdings were steady, provide a concise 1-line reassurance.

        🔮 <b>After-Hours Earnings & Tomorrow's Focus:</b>
        - Key after-hours earnings calls to note and 1 to 2 high-quality opportunity ideas to research for tomorrow's session ({economic_ctx.get('tomorrow_date')}). Exercise critical judgment—focus on companies with proven business moats, secular growth potential, or credible positive analyst revisions.
        - CRITICAL RULE: DO NOT describe events that occurred earlier today (such as completed Federal Reserve rate announcements or today's earnings) as happening tomorrow.

        Keep it comprehensive, institutional, objective, and beautifully styled with HTML tags. Avoid unwarranted puffery, hyperbole, or false profundity.
        """
        effective_key = api_key or self.api_key
        if not effective_key:
            return (
                "🔒 <b>Post-Market Wrap Paused:</b> Gemini API key is missing.\n\n"
                "Please configure your Gemini API key in Settings to activate automated scheduled AI briefings."
            )

        res = self.query_llm_text(prompt, api_key=effective_key)
        if res and len(res.strip()) > 50:
            return res.strip()

        return (
            "⚠️ <b>AI Briefing Generation Unavailable:</b> Gemini was unable to generate the Post-Market Wrap at this time.\n\n"
            "Please check network connectivity or your Gemini API quota."
        )

    def generate_weekend_eod_briefing(
        self,
        portfolio: Portfolio,
        market_overview: Dict[str, Any],
        news_items: List[NewsItem],
        api_key: Optional[str] = None,
        as_of: Optional[datetime] = None
    ) -> str:
        """
        9:00 PM PST Weekend EOD Wrap (Sat & Sun):
        Weekend macro/geopolitics, Sunday futures open sentiment, and the week ahead earnings/economic calendar.
        """
        ref_dt = as_of or datetime.now(ZoneInfo("America/Los_Angeles"))
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        as_of_pst = ref_dt.astimezone(ZoneInfo("America/Los_Angeles"))
        as_of_et = ref_dt.astimezone(ZoneInfo("America/New_York"))

        economic_ctx = get_economic_calendar_context(as_of_pst)
        economic_str = format_economic_calendar_for_prompt(economic_ctx)

        movers_context = self._extract_significant_portfolio_movers(portfolio, news_items, threshold_pct=1.5)
        news_str = self._format_news_summary(news_items, as_of=as_of_pst)

        prompt = f"""
        You are a seasoned Chief Investment Officer delivering the 9:00 PM PST WEEKEND MACRO & WEEK-AHEAD BRIEFING.

        {BRIEFING_COMMUNICATION_RULES}

        CURRENT TIME & SESSION GROUND TRUTH:
        • Calendar Date: {as_of_pst.strftime('%A, %B %d, %Y')}
        • Current Time: {as_of_pst.strftime('%I:%M %p')} PST / {as_of_et.strftime('%I:%M %p')} EDT
        • Session Phase: Weekend Transition / Week-Ahead Setup

        {economic_str}

        WEEKEND GLOBAL NEWS & MACRO DEVELOPMENTS:
        {news_str}

        INVESTOR'S PORTFOLIO STATUS:
        {movers_context}

        TASK:
        Generate a thoughtful, forward-looking weekend executive briefing in clean Telegram HTML format (use <b>, <i>, <code>).

        Structure the message with these exact sections:
        🌟 <b>WEEKEND MACRO & WEEK-AHEAD PREVIEW (9:00 PM PST)</b>

        🌍 <b>Weekend Macro & Sunday Sentiment:</b>
        - Weekend global news, geopolitical updates, commodity/crypto moves, and initial Sunday futures sentiment.

        📅 <b>The Week Ahead Catalyst Calendar:</b>
        - Key upcoming CPI/PPI, Fed speaker events, and major earnings releases to anticipate this week.

        💼 <b>Portfolio Week-Ahead Exposure:</b>
        - If any portfolio holdings have major scheduled earnings or direct catalyst events this week, mention ONLY those specific holdings. Otherwise, provide a 1-line note confirming a balanced posture.

        💡 <b>Secular Opportunities & Themes:</b>
        - 1-2 secular themes or institutional-grade investment ideas to watch as markets open. Prioritize companies with durable moats, proven cash generation, or recent positive analyst revisions.

        Keep it forward-looking, institutional, grounded, and styled with clean HTML. Avoid unwarranted puffery or false profundity.
        """

        effective_key = api_key or self.api_key
        if not effective_key:
            return (
                "🔒 <b>Weekend Briefing Paused:</b> Gemini API key is missing.\n\n"
                "Please configure your Gemini API key in Settings to activate automated scheduled AI briefings."
            )

        res = self.query_llm_text(prompt, api_key=effective_key)
        if res and len(res.strip()) > 50:
            return res.strip()

        return (
            "⚠️ <b>AI Briefing Generation Unavailable:</b> Gemini was unable to generate the Weekend Preview at this time.\n\n"
            "Please check network connectivity or your Gemini API quota."
        )


    def generate_weekly_earnings_briefing(
        self,
        portfolio: Portfolio,
        news_items: List[NewsItem],
        api_key: Optional[str] = None,
        as_of: Optional[datetime] = None
    ) -> str:
        """
        Next 7 Days Corporate Earnings Calendar & Sentiment Analysis:
        Provides 100% verified scheduled corporate earnings calls pulled live from Nasdaq official feeds,
        organized day by day, timing (Pre-Market BMO vs Post-Market AMC), consensus sentiment, and portfolio cross-exposure.
        """
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        from analytics.earnings_calendar import fetch_7day_earnings_schedule

        ref_dt = as_of or datetime.now(ZoneInfo("America/Los_Angeles"))
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        now_pst = ref_dt.astimezone(ZoneInfo("America/Los_Angeles"))
        end_date = now_pst + timedelta(days=7)
        portfolio_tickers = [h.ticker for h in portfolio.holdings]

        # Fetch 100% verified live scheduled earnings from official Nasdaq feed
        earnings_schedule = fetch_7day_earnings_schedule(portfolio_tickers=portfolio_tickers)

        schedule_text_blocks = []
        for day in earnings_schedule:
            block = [f"📅 {day['display_date']} (Total reporting: {day['total_companies']})"]
            if day.get("bmo"):
                bmo_items = [f"  • BMO: {r['ticker']} ({r['name']}) [Cap: {r['market_cap_str']}, Est EPS: {r['eps_forecast']}]" for r in day["bmo"]]
                block.extend(bmo_items)
            if day.get("amc"):
                amc_items = [f"  • AMC: {r['ticker']} ({r['name']}) [Cap: {r['market_cap_str']}, Est EPS: {r['eps_forecast']}]" for r in day["amc"]]
                block.extend(amc_items)
            schedule_text_blocks.append("\n".join(block))

        verified_schedule_str = "\n\n".join(schedule_text_blocks) if schedule_text_blocks else "No major corporate earnings scheduled this week."
        holdings_str = self._format_holdings_summary(portfolio)
        news_str = self._format_news_summary(news_items, as_of=now_pst)

        prompt = f"""
        You are an elite Wall Street Equity Research Director delivering the UPCOMING 7-DAY CORPORATE EARNINGS CALENDAR & SENTIMENT REPORT.

        VERIFIED SCHEDULED EARNINGS RELEASES (OFFICIAL NASDAQ CALENDAR DATA):
        {verified_schedule_str}

        INVESTOR'S ACTIVE PORTFOLIO:
        {holdings_str}

        RECENT CORPORATE HEADLINES & MACRO CONTEXT:
        {news_str}

        CRITICAL GROUNDING RULES:
        1. You MUST ONLY use the exact dates, companies, and session timings (BMO vs AMC) provided in the VERIFIED SCHEDULE above.
        2. DO NOT invent or hallucinate dates, tickers, or companies not in the verified schedule.
        3. For each company listed, synthesize Wall Street consensus sentiment, key focus metrics (e.g. AI revenue, enterprise renewals, margin guide), and cross-impact on the investor's portfolio.

        TASK:
        Generate a comprehensive, institutional upcoming earnings report in clean Telegram HTML format (use <b>, <i>, <code>).

        Structure the message with these exact sections:
        📅 <b>UPCOMING 7-DAY EARNINGS CALENDAR & SENTIMENT</b>
        <i>Verified Window: {now_pst.strftime('%b %d')} – {end_date.strftime('%b %d, %Y')} (PST)</i>

        (Group day-by-day in chronological order according to the verified schedule):
        <b>[Day of Week, Date]</b>
        • 🌅 <b>Pre-Market (BMO):</b>
          - <code>TICKER</code> (Company Name) — Sentiment & key metric to watch.
        • 🌙 <b>Post-Market (AMC):</b>
          - <code>TICKER</code> (Company Name) — Sentiment & key catalyst.

        💼 <b>Portfolio Cross-Exposure & Sympathy Plays:</b>
        - Direct callout of how these reporting companies impact active holdings ({', '.join(portfolio_tickers[:6])}) (e.g. AVGO/HPE/DELL impact on NVDA, AMD, SOXX, and MU).

        🔮 <b>Key Guidance Themes Wall Street Is Watching:</b>
        - 2-3 secular themes across this reporting wave (Hyperscaler AI Capex, Enterprise Software ARR, Consumer Demand).

        Keep it clean, institutional, forward-looking, and formatted strictly in Telegram HTML. Do not use Markdown backticks.
        """
        effective_key = api_key or self.api_key
        if not effective_key:
            return (
                "🔒 <b>Earnings Report Paused:</b> Gemini API key is missing.\n\n"
                "Please configure your Gemini API key in Settings to activate AI earnings intelligence."
            )

        res = self.query_llm_text(prompt, api_key=effective_key)
        if res and len(res.strip()) > 50:
            return res.strip()

        return (
            "⚠️ <b>AI Report Generation Unavailable:</b> Gemini was unable to generate the Earnings Sentiment Report at this time.\n\n"
            "Please check network connectivity or your Gemini API quota."
        )

