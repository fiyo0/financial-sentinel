"""
News Ingestion Agent: Scrapes, parses, normalizes, and scores incoming financial feeds and filings
using dynamic entity and sector reasoning.
"""
import json
import re
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
import feedparser
import httpx
from models import NewsItem, NewsCategory
from config import config
from storage.state_store import StateStore
from agents.base_agent import BaseAgent

# In-memory news cache: cache_key -> (timestamp, List[NewsItem])
NEWS_FEED_CACHE: Dict[str, tuple[float, List[NewsItem]]] = {}
NEWS_CACHE_TTL_SECONDS = 180.0  # 3 minutes TTL


# Source reliability index based on domain governance
SOURCE_RELIABILITY_MAP = {
    "sec.gov": 0.99,
    "federalreserve.gov": 0.99,
    "reuters.com": 0.92,
    "bloomberg.com": 0.92,
    "wsj.com": 0.90,
    "cnbc.com": 0.85,
    "marketwatch.com": 0.85,
    "news.google.com": 0.85,
    "google.com": 0.85,
    "finance.yahoo.com": 0.80,
    "seekingalpha.com": 0.70,
    "benzinga.com": 0.65,
}

# In-memory runtime dynamic ticker alias cache (seeded with common corporate/brand divergences)
_DYNAMIC_TICKER_CACHE: Dict[str, List[str]] = {}

COMMON_TICKER_ALIASES: Dict[str, List[str]] = {
    "HOOD": ["Robinhood", "Robinhood Markets"],
    "COIN": ["Coinbase", "Coinbase Global"],
    "GOOGL": ["Google", "Alphabet"],
    "GOOG": ["Google", "Alphabet"],
    "META": ["Meta", "Facebook", "Meta Platforms", "Instagram"],
    "NVDA": ["Nvidia", "NVIDIA"],
    "TSLA": ["Tesla"],
    "AAPL": ["Apple"],
    "MSFT": ["Microsoft"],
    "AMZN": ["Amazon"],
    "PLTR": ["Palantir", "Palantir Technologies"],
    "MSTR": ["MicroStrategy"],
    "AMD": ["Advanced Micro Devices"],
    "NFLX": ["Netflix"],
    "INTC": ["Intel"],
    "AVGO": ["Broadcom"],
    "QCOM": ["Qualcomm"],
    "CRM": ["Salesforce"],
    "ORCL": ["Oracle"],
    "UBER": ["Uber"],
    "DIS": ["Disney", "Walt Disney"],
    "SQ": ["Block", "Square", "Cash App"],
    "BKNG": ["Booking Holdings", "Booking.com", "Priceline", "Kayak"],
}

CORPORATE_SUFFIX_REGEX = re.compile(
    r'\b(?:Inc\.?|Corp\.?|Corporation|Holdings|Holding|Technologies|Technology|Platforms|Platform|Co\.?|Company|Ltd\.?|Limited|Plc|Class [A-Z]|Common Stock|S\.?A\.?|N\.?V\.?|A\.?G\.?)\b',
    re.IGNORECASE
)

CLICKBAIT_HEADLINE_PATTERNS = [
    re.compile(r'^\d+\s+(?:best\s+)?stocks?\s+to\s+(?:buy|watch|sell)', re.IGNORECASE),
    re.compile(r'^forget\s+.*\bbuy\b', re.IGNORECASE),
    re.compile(r'\bmillionaire-maker\b', re.IGNORECASE),
    re.compile(r'^\d+\s+(?:artificial intelligence|ai)\s+stocks?\b', re.IGNORECASE),
    re.compile(r'\bhere\'s why\b.*\bsoared\b', re.IGNORECASE),
    re.compile(r'^\s*is\s+.*\ba\s+(?:buy|sell)\b', re.IGNORECASE),
    re.compile(r'\bwhy\s+.*\b(?:soared|crashed|plunged|jumped)\b', re.IGNORECASE),
]

CATALYST_OVERRIDE_REGEX = re.compile(
    r'\b(sec\b|tokeniz\w+|innovation exemption|exemption|antitrust|fda\b|patent|lawsuit|earnings beat|acquisition|merger|buyout|restructuring)\b',
    re.IGNORECASE
)


def matches_ticker_equity_context(text: str, ticker: str, aliases: Optional[List[str]] = None) -> bool:
    """
    Validates whether an article legitimately references an equity asset rather than an unrelated homonym.
    Checks for cashtag ($TICKER), recognized brand aliases, or the ticker symbol accompanied by financial/market terms.
    """
    clean_ticker = ticker.strip().upper().replace("$", "")
    text_lower = text.lower()

    # 1. Direct cashtag ($TICKER) is definitive equity proof
    if f"${clean_ticker.lower()}" in text_lower:
        return True

    # 2. Recognized company/brand aliases (e.g. "Robinhood", "Caterpillar", "Palantir")
    if aliases:
        for a in aliases:
            if a.upper() != clean_ticker:
                if re.search(r'\b' + re.escape(a.lower()) + r'\b', text_lower):
                    return True

    # 3. Ticker symbol as standalone word — verify financial context to eliminate homonym false positives
    if re.search(r'\b' + re.escape(clean_ticker.lower()) + r'\b', text_lower):
        financial_context_words = [
            "stock", "stocks", "shares", "nasdaq", "nyse", "market", "trading", "investor", "investors",
            "valuation", "earnings", "broker", "brokerage", "revenue", "quarterly", "analyst", "price target",
            "bullish", "bearish", "sec", "finra", "etf", "holdings", "options", "dividend", "yield"
        ]
        if any(re.search(r'\b' + kw + r'\b', text_lower) for kw in financial_context_words):
            return True

    return False


def extract_stem_aliases(clean_ticker: str, company_name: str) -> List[str]:
    """
    Algorithmic corporate suffix stripping and brand root stem extractor.
    Operates dynamically on any equity or ETF name without hardcoding.
    """
    aliases = set()
    clean_name = re.sub(r'[,.]', ' ', company_name.strip()).strip()
    stripped = CORPORATE_SUFFIX_REGEX.sub('', clean_name).strip()
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    if stripped and len(stripped) >= 3 and stripped.upper() != clean_ticker:
        aliases.add(stripped)
        parts = stripped.split()
        if len(parts) > 1 and len(parts[0]) >= 4 and parts[0].lower() not in ("the", "global", "american", "national", "united", "first"):
            aliases.add(parts[0])
    return list(aliases)


def resolve_ticker_aliases(
    ticker: str,
    company_name: Optional[str] = None,
    state_store: Optional[StateStore] = None,
    use_market_lookup: bool = False
) -> List[str]:
    """
    Dynamically resolves search and entity-matching aliases for a given ticker:
    1. Checks in-memory cache and persistent SQLite store.
    2. Algorithmic corporate suffix stripping on company_name.
    3. If company_name is missing and use_market_lookup=True, queries live quote metadata.
    4. Caches and returns normalized aliases sorted by length descending.
    """
    clean_ticker = ticker.strip().upper().replace("$", "")
    if not clean_ticker:
        return []

    # Check cache first if company_name not explicitly provided
    if not company_name and clean_ticker in _DYNAMIC_TICKER_CACHE:
        return _DYNAMIC_TICKER_CACHE[clean_ticker]

    if not company_name and state_store:
        cached = state_store.get_ticker_aliases(clean_ticker)
        if cached:
            _DYNAMIC_TICKER_CACHE[clean_ticker] = cached
            return cached

    aliases = {clean_ticker}

    # Include bootstrap seed aliases if available
    if clean_ticker in COMMON_TICKER_ALIASES:
        for alias in COMMON_TICKER_ALIASES[clean_ticker]:
            aliases.add(alias)

    # Dynamic algorithmic stemming from company_name
    resolved_name = company_name
    if not resolved_name and use_market_lookup:
        try:
            from analytics.market_data import fetch_live_quote
            q = fetch_live_quote(clean_ticker)
            if q and q.get("name") and q.get("name").upper() != clean_ticker:
                resolved_name = q.get("name")
        except Exception:
            pass

    if resolved_name:
        for s in extract_stem_aliases(clean_ticker, resolved_name):
            aliases.add(s)

    result = sorted(list(aliases), key=lambda x: (-len(x), x))
    _DYNAMIC_TICKER_CACHE[clean_ticker] = result

    if state_store and resolved_name:
        state_store.save_ticker_aliases(clean_ticker, resolved_name, result)

    return result


def is_primary_headline_subject(title: str, ticker: str, aliases: Optional[List[str]] = None) -> bool:
    """
    Returns True if the ticker or any of its brand aliases appear directly in the headline.
    """
    title_lower = title.lower()
    search_tokens = [ticker.lower()]
    if aliases:
        search_tokens.extend([a.lower() for a in aliases])

    for token in search_tokens:
        if re.search(r'\b' + re.escape(token) + r'\b', title_lower):
            return True
    return False


def is_low_signal_clickbait(title: str) -> bool:
    """
    Detects low-signal SEO listicles, speculative retail clickbait, and content-farm headlines.
    High-impact structural regulatory or corporate catalysts are strictly exempted.
    """
    clean_title = title.strip()
    if CATALYST_OVERRIDE_REGEX.search(clean_title):
        return False
    return any(pat.search(clean_title) for pat in CLICKBAIT_HEADLINE_PATTERNS)


def categorize_catalyst_provenance(item: NewsItem) -> str:
    """
    Categorizes the evidentiary provenance of a news headline.
    """
    src = (item.source or "").lower()
    title = (item.title or "").lower()

    if (
        "sec.gov" in src or
        bool(re.search(r'\bsec\b', title)) or
        "securities and exchange commission" in title or
        item.category == NewsCategory.SEC_FILING
    ):
        return "REGULATORY / SEC ACTION"
    if (
        "federalreserve.gov" in src or
        "federal reserve" in src or
        bool(re.search(r'\b(fomc|federal reserve)\b', title)) or
        "interest rate" in title
    ):
        return "CENTRAL BANK / MACRO"
    if (
        item.category == NewsCategory.EARNINGS or
        any(k in title for k in ["earnings", "revenue", "eps", "quarterly result", "guidance", "q1", "q2", "q3", "q4"])
    ):
        return "EARNINGS / GUIDANCE"
    if any(k in title for k in ["launches", "acquires", "acquisition", "merger", "partnership", "unveils", "fda approval", "tokenized", "tokenization", "innovation exemption"]):
        return "CORPORATE CATALYST"
    return "MARKET MOVERS & CONTEXT"


class NewsIngestionAgent(BaseAgent):
    def __init__(self, state_store: Optional[StateStore] = None):
        super().__init__(
            name="News Ingestion Agent",
            role_description="Scrapes RSS feeds, financial filings, and macro news with source scoring and entity extraction."
        )
        self.state_store = state_store or StateStore(config.db_path)

    def extract_entities(self, text: str) -> Dict[str, List[str]]:
        """
        Dynamically extracts ticker symbols and relevant market sectors from headline and article text.
        """
        found_tickers = set()

        # 1. Match $TICKER pattern (e.g. $NVDA, $AAPL, $CEG)
        dollar_tickers = re.findall(r'\$([A-Z]{1,5})\b', text)
        for t in dollar_tickers:
            found_tickers.add(t.upper())

        # 2. Match standard ticker patterns inside parentheses (e.g. "NVIDIA (NVDA)", "Tesla (TSLA)")
        paren_tickers = re.findall(r'\(([A-Z]{1,5})\)', text)
        for t in paren_tickers:
            if t not in ("US", "USA", "SEC", "CEO", "CFO", "AI", "ETF", "Fed", "FED", "GDP", "CPI", "IPO", "LLC", "INC", "EST", "PST", "UTC"):
                found_tickers.add(t.upper())

        # 3. Match common company brand names to tickers dynamically
        text_lower = text.lower()
        active_aliases = dict(COMMON_TICKER_ALIASES)
        if self.state_store:
            try:
                db_aliases = self.state_store.get_all_ticker_aliases()
                active_aliases.update(db_aliases)
            except Exception:
                pass
        active_aliases.update(_DYNAMIC_TICKER_CACHE)

        for sym, aliases in active_aliases.items():
            for alias in aliases:
                if re.search(r'\b' + re.escape(alias.lower()) + r'\b', text_lower):
                    found_tickers.add(sym)
                    break

        # 4. Dynamic Sector Identification based on contextual semantics
        found_sectors = set()
        if any(w in text_lower for w in ["semiconductor", "chip", "gpu", "wafer", "foundry"]):
            found_sectors.add("Semiconductors")
        if any(w in text_lower for w in ["software", "cloud", "saas", "cybersecurity", "ai model"]):
            found_sectors.add("Technology")
        if any(w in text_lower for w in ["oil", "gas", "energy", "nuclear", "power grid", "utility"]):
            found_sectors.add("Energy")
        if any(w in text_lower for w in ["bank", "fed", "interest rate", "yield", "treasury", "credit", "broker", "brokerage", "tokenized", "tokenization"]):
            found_sectors.add("Financials")
        if any(w in text_lower for w in ["fda", "drug", "clinical", "biotech", "pharma", "trial"]):
            found_sectors.add("Healthcare")

        return {
            "tickers": sorted(list(found_tickers)),
            "sectors": sorted(list(found_sectors)) if found_sectors else ["Technology"]
        }

    def infer_category(self, title: str, summary: str, feed_category: str) -> NewsCategory:
        text = f"{title} {summary}".lower()
        if (
            bool(re.search(r'\bsec\b', text)) or
            "securities and exchange commission" in text or
            "form 8-k" in text or
            "10-q" in text or
            "item 1.01" in text or
            "innovation exemption" in text or
            "exemptive order" in text
        ):
            return NewsCategory.SEC_FILING
        if (
            "federal reserve" in text or
            bool(re.search(r'\b(fomc|fed)\b', text)) or
            "cpi" in text or
            "inflation" in text or
            "interest rate" in text
        ):
            return NewsCategory.MACRO
        if "earnings" in text or "revenue" in text or "eps" in text or "quarterly result" in text or "guidance" in text:
            return NewsCategory.EARNINGS
        if "war" in text or "sanctions" in text or "tariff" in text or "geopolitical" in text:
            return NewsCategory.GEOPOLITICAL

        try:
            return NewsCategory[feed_category]
        except (KeyError, ValueError):
            return NewsCategory.BREAKING

    def get_source_reliability(self, source_url_or_name: str) -> float:
        s = source_url_or_name.lower()
        for domain, score in SOURCE_RELIABILITY_MAP.items():
            if domain in s:
                return score
            base = domain.split('.')[0]
            if len(base) >= 4 and base in s:
                return score
        return 0.80

    def fetch_live_feed(self, feed_cfg: Dict[str, str]) -> List[NewsItem]:
        items: List[NewsItem] = []
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        default_cat = feed_cfg.get("category", "BREAKING")

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 FinancialSentinel/2.4"
            }
            # Comply strictly with SEC EDGAR Fair Access Policy (mandatory declared organization and contact email)
            if "sec.gov" in url.lower():
                headers["User-Agent"] = "FinancialSentinel/2.4 (admin@financialsentinel.io; Automated Research System)"

            resp = httpx.get(url, headers=headers, timeout=8.0, follow_redirects=True)
            if resp.status_code != 200:
                return []

            parsed = feedparser.parse(resp.text)
            for entry in parsed.entries[:15]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                summary = entry.get("summary", "") or entry.get("description", "")
                clean_summary = re.sub(r'<[^>]+>', '', summary).strip()

                if not title:
                    continue

                source_name = name
                entry_src = entry.get("source")
                if isinstance(entry_src, dict) and entry_src.get("title"):
                    source_name = f"{entry_src['title']} (via Google News)" if "google" in name.lower() else entry_src['title']
                elif hasattr(entry_src, "title") and entry_src.title:
                    source_name = f"{entry_src.title} (via Google News)" if "google" in name.lower() else entry_src.title

                raw_hash = self.state_store.compute_hash(title, source_name)
                entities = self.extract_entities(f"{title} {clean_summary}")
                category = self.infer_category(title, clean_summary, default_cat)
                reliability = self.get_source_reliability(source_name) if source_name != name else self.get_source_reliability(url)

                pub_time = datetime.utcnow()
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        pub_time = datetime(*entry.published_parsed[:6])
                    except Exception:
                        pass

                item = NewsItem(
                    id=f"news_{raw_hash[:12]}",
                    title=title,
                    source=source_name,
                    url=link or url,
                    published_at=pub_time,
                    summary=clean_summary[:800],
                    category=category,
                    source_reliability_score=reliability,
                    related_tickers=entities["tickers"],
                    related_sectors=entities["sectors"],
                    raw_hash=raw_hash
                )
                items.append(item)
        except Exception:
            pass
        return items

    def enrich_items_with_gemini(self, items: List[NewsItem], api_key: Optional[str] = None) -> List[NewsItem]:
        """
        Uses Gemini to semantically extract accurate corporate tickers, granular sectors, and event classifications.
        """
        effective_key = api_key or self.api_key
        if not self.use_llm or not effective_key or not items:
            return items

        # Process up to 15 items in a single fast structured batch
        batch_input = [
            {"id": item.id, "title": item.title, "summary": item.summary[:250]}
            for item in items[:15]
        ]

        prompt = f"""
        You are an expert financial news ingestion and entity classification system.
        Analyze each article headline and summary. Extract all relevant stock tickers (US exchange symbols),
        accurate market sectors, and event categories.

        ARTICLES:
        {json.dumps(batch_input, indent=2)}

        Return JSON matching this schema:
        {{
            "items": [
                {{
                    "id": string (must match article id),
                    "tickers": ["AAPL", "MSFT"],
                    "sectors": ["Technology", "Semiconductors"],
                    "category": "BREAKING" | "MACRO" | "EARNINGS" | "SEC_FILING" | "GEOPOLITICAL"
                }}
            ]
        }}
        """

        try:
            res = self.query_llm_json(prompt, api_key=effective_key)
            if res and "items" in res:
                enriched_map = {r["id"]: r for r in res["items"] if "id" in r}
                for item in items:
                    if item.id in enriched_map:
                        enr = enriched_map[item.id]
                        if enr.get("tickers"):
                            item.related_tickers = sorted(list(set(item.related_tickers + [t.upper() for t in enr["tickers"]])))
                        if enr.get("sectors"):
                            item.related_sectors = enr["sectors"]
                        if enr.get("category"):
                            try:
                                item.category = NewsCategory[enr["category"].upper()]
                            except Exception:
                                pass
        except Exception:
            pass

        return items

    def ingest_all_feeds(
        self,
        live: bool = True,
        custom_items: Optional[List[Dict[str, Any]]] = None,
        force_fresh: bool = False,
        portfolio_tickers: Optional[List[str]] = None,
        api_key: Optional[str] = None
    ) -> List[NewsItem]:
        # Check in-memory TTL cache for live feeds
        if live and not force_fresh and not custom_items:
            cache_key = ",".join(sorted([t.upper() for t in (portfolio_tickers or [])]))
            now = time.time()
            if cache_key in NEWS_FEED_CACHE:
                cached_time, cached_items = NEWS_FEED_CACHE[cache_key]
                if now - cached_time < NEWS_CACHE_TTL_SECONDS and cached_items:
                    return cached_items

        new_items: List[NewsItem] = []

        # 1. If custom / fixture items are passed
        if custom_items:
            for raw in custom_items:
                raw_hash = self.state_store.compute_hash(raw["title"], raw["source"])
                entities = self.extract_entities(f"{raw['title']} {raw.get('summary', '')}")
                cat = self.infer_category(raw["title"], raw.get("summary", ""), raw.get("category", "BREAKING"))
                rel = self.get_source_reliability(raw.get("url", raw["source"]))

                item = NewsItem(
                    id=raw.get("id", f"news_{raw_hash[:12]}"),
                    title=raw["title"],
                    source=raw["source"],
                    url=raw.get("url", "https://news.example.com"),
                    published_at=datetime.utcnow(),
                    summary=raw.get("summary", ""),
                    category=cat,
                    source_reliability_score=raw.get("reliability", rel),
                    related_tickers=raw.get("related_tickers", entities["tickers"]),
                    related_sectors=raw.get("related_sectors", entities["sectors"]),
                    raw_hash=raw_hash
                )
                if force_fresh or not self.state_store.is_news_processed(raw_hash):
                    if not self.state_store.is_news_processed(raw_hash):
                        self.state_store.save_news_item(item)
                    new_items.append(item)
            return new_items

        # 2. Live RSS feeds fetch (Global macro + Targeted ticker feeds in parallel)
        if live:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import urllib.parse

            all_feed_configs = list(config.rss_feeds)
            if portfolio_tickers:
                for ticker in set(t.strip().upper() for t in portfolio_tickers if t.strip()):
                    aliases = resolve_ticker_aliases(ticker, state_store=self.state_store, use_market_lookup=True)
                    brand_aliases = [a for a in aliases if a.upper() != ticker]
                    primary_brand = brand_aliases[0] if brand_aliases else ticker

                    # Universal equity search query anchoring:
                    # Combines the primary brand name, cashtag $TICKER, and equity descriptor.
                    # Universally prevents homonym noise for dictionary-word tickers (e.g. HOOD, ON, CAT, NOW, BOX)
                    if brand_aliases:
                        search_expr = f'("{primary_brand}" OR "${ticker}" OR "{ticker} stock")'
                    else:
                        search_expr = f'("${ticker}" OR "{ticker} stock")'
                    search_query = urllib.parse.quote(search_expr)

                    # Google News RSS for real-time breaking ticker & brand news
                    all_feed_configs.append({
                        "name": f"Google News ({ticker})",
                        "url": f"https://news.google.com/rss/search?q={search_query}&hl=en-US&gl=US&ceid=US:en",
                        "category": "BREAKING",
                        "_target_ticker": ticker,
                        "_target_aliases": aliases
                    })
                    # Yahoo Finance RSS 2.0 headline feed
                    all_feed_configs.append({
                        "name": f"Yahoo Finance ({ticker})",
                        "url": f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
                        "category": "EARNINGS",
                        "_target_ticker": ticker,
                        "_target_aliases": aliases
                    })

            with ThreadPoolExecutor(max_workers=min(16, len(all_feed_configs) or 1)) as executor:
                future_to_cfg = {
                    executor.submit(self.fetch_live_feed, cfg): cfg
                    for cfg in all_feed_configs
                }
                for future in as_completed(future_to_cfg):
                    cfg = future_to_cfg[future]
                    try:
                        fetched = future.result()
                        target_ticker = cfg.get("_target_ticker")
                        target_aliases = cfg.get("_target_aliases")
                        for item in fetched:
                            if target_ticker and target_ticker not in item.related_tickers:
                                if matches_ticker_equity_context(f"{item.title} {item.summary}", target_ticker, target_aliases):
                                    item.related_tickers.append(target_ticker)
                            if force_fresh or not self.state_store.is_news_processed(item.raw_hash):
                                if not self.state_store.is_news_processed(item.raw_hash):
                                    self.state_store.save_news_item(item)
                                new_items.append(item)
                    except Exception:
                        pass

        # 3. Apply Gemini semantic intelligence to enrich extracted tickers and categories
        # NOTE: Skip during live scans to eliminate 12-15s redundant latency, as AnalysisAgent
        # already performs deep Gemini semantic reasoning directly over the news items and portfolio holdings.
        if new_items and self.use_llm and not live:
            new_items = self.enrich_items_with_gemini(new_items, api_key=api_key)

        # Sort news items chronologically so freshest breaking news appears first
        new_items.sort(key=lambda x: x.published_at, reverse=True)

        # Update in-memory TTL cache for live feeds
        if live and new_items and not custom_items:
            cache_key = ",".join(sorted([t.upper() for t in (portfolio_tickers or [])]))
            NEWS_FEED_CACHE[cache_key] = (time.time(), new_items)

        return new_items

    def resolve_brand_aliases_with_gemini(self, ticker: str, company_name: str, api_key: Optional[str] = None) -> List[str]:
        """
        Dynamically queries Gemini to extract popular consumer brand names, product brands,
        and colloquial aliases for holding companies or parent corporations.
        Caches the result permanently in SQLite state_store.
        """
        effective_key = api_key or self.api_key
        clean_ticker = ticker.strip().upper()
        if not self.use_llm or not effective_key:
            return resolve_ticker_aliases(clean_ticker, company_name, state_store=self.state_store)

        prompt = f"""
        Identify all widely recognized consumer brands, product names, subsidiary brands, and colloquial aliases
        associated with stock ticker {clean_ticker} (Official Corporate Name: "{company_name}").
        Return only genuine brand names that financial journalists frequently use in news headlines instead of the ticker.

        Return JSON matching this schema:
        {{
            "ticker": "{clean_ticker}",
            "brand_aliases": ["Brand1", "Brand2"]
        }}
        """
        try:
            res = self.query_llm_json(prompt, api_key=effective_key)
            if res and "brand_aliases" in res and isinstance(res["brand_aliases"], list):
                dynamic_aliases = [str(b).strip() for b in res["brand_aliases"] if str(b).strip()]
                all_aliases = resolve_ticker_aliases(clean_ticker, company_name, state_store=self.state_store)
                combined = sorted(list(set(all_aliases + dynamic_aliases)), key=lambda x: (-len(x), x))
                _DYNAMIC_TICKER_CACHE[clean_ticker] = combined
                if self.state_store:
                    self.state_store.save_ticker_aliases(clean_ticker, company_name, combined)
                return combined
        except Exception:
            pass
        return resolve_ticker_aliases(clean_ticker, company_name, state_store=self.state_store)

