"""
services/portfolio_service.py - Unified Portfolio Mutation, Valuation, and Cash Management Service.
Single source of truth for portfolio modifications, cash balance adjustments, and live valuation.
Used identically across Web API, Telegram Bot, and CLI.
"""
import logging
from typing import Optional, Dict, Any
from models import Portfolio, PortfolioHolding
import analytics.market_data as market_data
from orchestrator import FinancialSentinelOrchestrator

logger = logging.getLogger(__name__)


class PortfolioService:
    def __init__(self, orchestrator: FinancialSentinelOrchestrator):
        self.orchestrator = orchestrator

    def get_portfolio(self, user_id: Optional[str] = None, update_prices: bool = False) -> Portfolio:
        """Retrieves active portfolio for a user, optionally refreshing real-time quotes."""
        portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
        if update_prices:
            portfolio, _ = market_data.update_portfolio_live_prices(portfolio)
        portfolio.recalculate_weights()
        return portfolio

    def get_portfolio_valuation(self, user_id: Optional[str] = None, update_prices: bool = True) -> Dict[str, Any]:
        """Calculates comprehensive portfolio valuation, stress metrics, and cash reserves."""
        portfolio = self.get_portfolio(user_id=user_id, update_prices=update_prices)
        stress = self.orchestrator.quant_engine.analyze_portfolio(portfolio)
        total_equity = portfolio.total_equity()
        stock_equity = round(sum(h.market_value for h in portfolio.holdings), 2)
        cash = portfolio.cash or 0.0

        sorted_by_pnl = []
        if portfolio.holdings:
            sorted_by_pnl = sorted(portfolio.holdings, key=lambda h: h.unrealized_pnl_pct, reverse=True)

        return {
            "portfolio": portfolio,
            "stress": stress,
            "total_equity": total_equity,
            "stock_equity": stock_equity,
            "total_wealth": total_equity,
            "cash": cash,
            "holdings_count": len(portfolio.holdings),
            "top_performer": sorted_by_pnl[0] if sorted_by_pnl else None,
            "bottom_performer": sorted_by_pnl[-1] if sorted_by_pnl else None,
        }

    def update_cash_balance(self, user_id: Optional[str], new_cash: float) -> Dict[str, Any]:
        """Updates user's liquid cash balance and persists change."""
        cash_val = max(0.0, float(new_cash))
        portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
        portfolio.cash = cash_val
        dumped = self.orchestrator.persist_active_portfolio(portfolio, user_id=user_id)
        stress = self.orchestrator.quant_engine.analyze_portfolio(portfolio)
        return {
            "status": "success",
            "message": f"Cash balance updated to ${cash_val:,.2f}",
            "cash": cash_val,
            "portfolio": dumped,
            "stress": stress.model_dump(mode="json"),
        }

    def add_or_update_holding(
        self,
        user_id: Optional[str],
        ticker: str,
        shares: float,
        price: Optional[float] = None,
        name: Optional[str] = None,
        sector: Optional[str] = None,
        current_price: Optional[float] = None,
        incremental: bool = False,
        deduct_cash: bool = True,
    ) -> Dict[str, Any]:
        """
        Adds a new holding or updates an existing holding.
        - incremental=True: increments existing shares and calculates weighted average cost basis (e.g. Telegram /add).
        - incremental=False: replaces holding shares with specified amount (e.g. Web form edit).
        - deduct_cash=True: deducts purchases from cash reserves, or credits reductions back to cash.
        """
        clean_ticker = ticker.strip().upper().replace("$", "")
        if not clean_ticker:
            raise ValueError("Ticker symbol cannot be empty.")
        if shares <= 0:
            raise ValueError("Shares must be greater than zero.")

        # Resolve live market quote if name, sector, or price are missing
        quote = {}
        if not name or not sector or price is None or price <= 0:
            try:
                quote = market_data.fetch_live_quote(clean_ticker)
            except Exception as e:
                logger.warning(f"Failed to fetch live quote for {clean_ticker}: {e}")

        resolved_name = name or quote.get("name") or clean_ticker
        canonical_sec = None
        if hasattr(self.orchestrator, "state_store") and self.orchestrator.state_store:
            try:
                canonical_sec = self.orchestrator.state_store.get_ticker_sector(clean_ticker)
            except Exception as e:
                logger.debug(f"Could not retrieve canonical sector for {clean_ticker}: {e}")
        resolved_sector = sector or quote.get("sector") or canonical_sec or "Unclassified"


        quote_price = float(quote.get("current_price", 0.0) or 0.0)
        resolved_price = price if (price is not None and price > 0) else (quote_price if quote_price > 0 else None)
        if resolved_price is None or resolved_price <= 0:
            raise ValueError(f"Cannot resolve current price for {clean_ticker}. Please provide an explicit price.")
        resolved_current_price = current_price if (current_price is not None and current_price > 0) else (quote_price if quote_price > 0 else resolved_price)

        # Dynamically register ticker brand aliases in SQLite
        try:
            from agents.news_ingestion import resolve_ticker_aliases
            resolve_ticker_aliases(clean_ticker, resolved_name, state_store=self.orchestrator.state_store)
        except Exception as e:
            logger.debug(f"Could not register ticker aliases for {clean_ticker}: {e}")

        portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)
        existing = next((h for h in portfolio.holdings if h.ticker.upper() == clean_ticker), None)

        action = "created"
        delta_cash = 0.0

        if existing:
            action = "updated"
            if incremental:
                # Incremental add: average cost basis with new shares
                old_shares = existing.shares
                old_avg = existing.avg_price
                new_shares = old_shares + shares
                new_avg = ((old_shares * old_avg) + (shares * resolved_price)) / new_shares
                cost = shares * resolved_price
                if deduct_cash:
                    portfolio.cash = max(0.0, float(portfolio.cash or 0.0) - cost)
                    delta_cash = -cost

                existing.shares = new_shares
                existing.avg_price = new_avg
                existing.current_price = resolved_current_price
                existing.name = resolved_name
                existing.sector = resolved_sector
                holding_ref = existing
            else:
                # Absolute update: compare delta shares
                share_diff = shares - existing.shares
                if share_diff > 0:
                    cost = share_diff * (resolved_price or existing.avg_price or 0.0)
                    if deduct_cash:
                        portfolio.cash = max(0.0, float(portfolio.cash or 0.0) - cost)
                        delta_cash = -cost
                elif share_diff < 0:
                    proceeds = abs(share_diff) * (resolved_current_price or existing.current_price or resolved_price or 0.0)
                    if deduct_cash:
                        portfolio.cash = max(0.0, float(portfolio.cash or 0.0) + proceeds)
                        delta_cash = proceeds

                existing.shares = shares
                existing.avg_price = resolved_price
                existing.current_price = resolved_current_price
                existing.name = resolved_name
                existing.sector = resolved_sector
                holding_ref = existing
        else:
            # New position
            purchase_cost = shares * resolved_price
            if deduct_cash:
                if (portfolio.cash or 0.0) >= purchase_cost:
                    portfolio.cash = max(0.0, float(portfolio.cash or 0.0) - purchase_cost)
                    delta_cash = -purchase_cost

            holding_ref = PortfolioHolding(
                ticker=clean_ticker,
                name=resolved_name,
                shares=shares,
                avg_price=resolved_price,
                current_price=resolved_current_price,
                sector=resolved_sector,
                thematic_tags=[]
            )
            portfolio.holdings.append(holding_ref)

        market_data.update_portfolio_live_prices(portfolio)
        dumped = self.orchestrator.persist_active_portfolio(portfolio, user_id=user_id)
        stress = self.orchestrator.quant_engine.analyze_portfolio(portfolio)

        return {
            "status": "success",
            "action": action,
            "holding": holding_ref,
            "delta_cash": delta_cash,
            "portfolio": dumped,
            "stress": stress.model_dump(mode="json"),
        }

    def remove_or_trim_holding(
        self,
        user_id: Optional[str],
        ticker: str,
        shares_to_remove: Optional[Any] = None,
        exit_price: Optional[float] = None,
        credit_cash: bool = True,
    ) -> Dict[str, Any]:
        """
        Removes or trims a holding from user's portfolio.
        - shares_to_remove=None or 'all' or >= current_shares: liquidates entire position.
        - shares_to_remove < current_shares: trims position by specified shares.
        - Proceeds are credited back to cash reserve if credit_cash=True.
        """
        clean_ticker = ticker.strip().upper().replace("$", "")
        portfolio = self.orchestrator.get_active_portfolio(user_id=user_id)

        existing = next((h for h in portfolio.holdings if h.ticker.upper() == clean_ticker), None)
        if not existing:
            raise ValueError(f"Ticker {clean_ticker} not found in portfolio.")

        old_shares = existing.shares
        company_name = existing.name
        cur_price = exit_price or existing.current_price or existing.avg_price or 0.0

        is_full_removal = (
            shares_to_remove is None
            or str(shares_to_remove).strip().lower() == "all"
            or (isinstance(shares_to_remove, (int, float)) and float(shares_to_remove) >= old_shares)
        )

        liquidated_val = 0.0
        if is_full_removal:
            portfolio.holdings = [h for h in portfolio.holdings if h.ticker.upper() != clean_ticker]
            liquidated_val = old_shares * cur_price
            action_desc = f"Completely closed position (removed all {old_shares:g} shares)"
            remaining_shares = 0.0
        else:
            trim_count = float(shares_to_remove)
            remaining_shares = old_shares - trim_count
            existing.shares = remaining_shares
            liquidated_val = trim_count * cur_price
            action_desc = f"Trimmed -{trim_count:g} shares from position"

        if credit_cash and liquidated_val > 0:
            portfolio.cash = max(0.0, float(portfolio.cash or 0.0) + liquidated_val)

        market_data.update_portfolio_live_prices(portfolio)
        dumped = self.orchestrator.persist_active_portfolio(portfolio, user_id=user_id)
        stress = self.orchestrator.quant_engine.analyze_portfolio(portfolio)

        return {
            "status": "success",
            "ticker": clean_ticker,
            "company_name": company_name,
            "is_full_removal": is_full_removal,
            "action_desc": action_desc,
            "liquidated_val": liquidated_val,
            "remaining_shares": remaining_shares,
            "portfolio": dumped,
            "stress": stress.model_dump(mode="json"),
        }

    def save_portfolio(
        self,
        user_id: Optional[str],
        portfolio: Portfolio,
        update_prices: bool = True
    ) -> Dict[str, Any]:
        """Directly persists an updated Portfolio object with live pricing and stress testing."""
        if update_prices:
            portfolio, _ = market_data.update_portfolio_live_prices(portfolio)
        portfolio.recalculate_weights()
        dumped = self.orchestrator.persist_active_portfolio(portfolio, user_id=user_id)
        stress = self.orchestrator.quant_engine.analyze_portfolio(portfolio)
        return {
            "status": "success",
            "portfolio": dumped,
            "stress": stress.model_dump(mode="json"),
        }
