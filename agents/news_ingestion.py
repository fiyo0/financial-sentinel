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
    "finance.yahoo.com": 0.80,
    "seekingalpha.com": 0.70,
    "benzinga.com": 0.65,
}


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

        # 3. Dynamic Sector Identification based on contextual semantics
        found_sectors = set()
        text_lower = text.lower()
        if any(w in text_lower for w in ["semiconductor", "chip", "gpu", "wafer", "foundry"]):
            found_sectors.add("Semiconductors")
        if any(w in text_lower for w in ["software", "cloud", "saas", "cybersecurity", "ai model"]):
            found_sectors.add("Technology")
        if any(w in text_lower for w in ["oil", "gas", "energy", "nuclear", "power grid", "utility"]):
            found_sectors.add("Energy")
        if any(w in text_lower for w in ["bank", "fed", "interest rate", "yield", "treasury", "credit"]):
            found_sectors.add("Financials")
        if any(w in text_lower for w in ["fda", "drug", "clinical", "biotech", "pharma", "trial"]):
            found_sectors.add("Healthcare")

        return {
            "tickers": sorted(list(found_tickers)),
            "sectors": sorted(list(found_sectors)) if found_sectors else ["Technology"]
        }

    def infer_category(self, title: str, summary: str, feed_category: str) -> NewsCategory:
        text = f"{title} {summary}".lower()
        if "sec" in text or "form 8-k" in text or "10-q" in text or "item 1.01" in text:
            return NewsCategory.SEC_FILING
        if "federal reserve" in text or "fomc" in text or "cpi" in text or "inflation" in text or "interest rate" in text:
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
        return 0.80

    def fetch_live_feed(self, feed_cfg: Dict[str, str]) -> List[NewsItem]:
        items: List[NewsItem] = []
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        default_cat = feed_cfg.get("category", "BREAKING")

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 FinancialSentinel/1.0"
            }
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

                raw_hash = self.state_store.compute_hash(title, name)
                entities = self.extract_entities(f"{title} {clean_summary}")
                category = self.infer_category(title, clean_summary, default_cat)
                reliability = self.get_source_reliability(url)

                pub_time = datetime.utcnow()
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        pub_time = datetime(*entry.published_parsed[:6])
                    except Exception:
                        pass

                item = NewsItem(
                    id=f"news_{raw_hash[:12]}",
                    title=title,
                    source=name,
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

            all_feed_configs = list(config.rss_feeds)
            if portfolio_tickers:
                for ticker in set(t.strip().upper() for t in portfolio_tickers if t.strip()):
                    all_feed_configs.append({
                        "name": f"Yahoo Finance ({ticker})",
                        "url": f"https://finance.yahoo.com/rss/headline?s={ticker}",
                        "category": "EARNINGS",
                        "_target_ticker": ticker
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
                        for item in fetched:
                            if target_ticker and target_ticker not in item.related_tickers:
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

        # Update in-memory TTL cache for live feeds
        if live and new_items and not custom_items:
            cache_key = ",".join(sorted([t.upper() for t in (portfolio_tickers or [])]))
            NEWS_FEED_CACHE[cache_key] = (time.time(), new_items)

        return new_items

