"""
FastAPI Web Dashboard Backend for the Financial Multi-Agent System.
Supports Multi-User Authentication, Isolated Portfolios, and Bring-Your-Own-Key (BYOK) Fernet Encrypted Gemini API Keys with HKDF-SHA256.
"""
import sys
import os
import json
import csv
import io
import time
import secrets
import re
from datetime import datetime
from typing import Dict, Any, Optional, List


import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi import FastAPI, Request, HTTPException, Response, UploadFile, File, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from orchestrator import FinancialSentinelOrchestrator
from models import (
    Portfolio, PortfolioHolding, UserRegisterRequest,
    UserLoginRequest, UserSettingsUpdateRequest
)
from config import config
from auth.crypto import (
    create_session_token, mask_api_key,
    decrypt_api_key, validate_gemini_api_key
)
from channels.telegram_bot import FinancialSentinelTelegramBot
from scheduler import DailyMarketScheduler
from analytics.market_data import update_portfolio_live_prices, fetch_live_quote
from web.auth_deps import (
    require_user, require_admin, require_cron_or_admin,
    get_current_user_optional
)

get_current_user = get_current_user_optional

logger = logging.getLogger("WebApp")

# Dedicated ThreadPoolExecutor to prevent thread starvation on single-core Cloud Run
sentinel_executor = ThreadPoolExecutor(
    max_workers=16,
    thread_name_prefix="sentinel_worker"
)

from services import (
    IdentityService,
    PortfolioService,
    AnalysisService,
    BriefingService,
)

orchestrator = FinancialSentinelOrchestrator()
daily_scheduler = DailyMarketScheduler(
    orchestrator=orchestrator,
    portfolio_loader=orchestrator.get_active_portfolio
)
portfolio_service = PortfolioService(orchestrator=orchestrator)
analysis_service = AnalysisService(orchestrator=orchestrator)
briefing_service = BriefingService(orchestrator=orchestrator, scheduler=daily_scheduler)
identity_service = IdentityService(state_store=orchestrator.state_store)

telegram_bot = FinancialSentinelTelegramBot(
    orchestrator=orchestrator,
    identity_service=identity_service,
    portfolio_service=portfolio_service,
    analysis_service=analysis_service,
    briefing_service=briefing_service,
)



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Configure dedicated thread pool executor for all asyncio.to_thread operations
    try:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(sentinel_executor)
        logger.info("Configured default ThreadPoolExecutor with 16 dedicated workers.")
    except Exception as e:
        logger.warning(f"Could not set default executor: {e}")

    # Startup actions
    try:
        orchestrator.state_store.restore_from_gcs()
        orchestrator.state_store._init_db()
    except Exception as e:
        logger.warning(f"Startup GCS restore: {e}")

    is_cloud_run = bool(os.getenv("K_SERVICE") or os.getenv("PORT"))
    if not is_cloud_run:
        daily_scheduler.start()
        logger.info("DailyMarketScheduler daemon thread started for local development.")
    else:
        logger.info("Cloud Run serverless environment detected. Crons driven via Google Cloud Scheduler.")

    if telegram_bot.is_configured():
        public_url = os.getenv("SERVICE_URL", "https://financial-sentinel-272533633552.us-central1.run.app")
        if public_url and is_cloud_run:
            try:
                telegram_bot.setup_webhook(public_url)
                logger.info(f"Configured Telegram webhook at {public_url}")
            except Exception as e:
                logger.warning(f"Webhook setup failed ({e}), falling back to long-polling.")
                telegram_bot.start_polling()
        else:
            telegram_bot.start_polling()
            logger.info("Started Telegram long-polling for local development.")

    yield

    # Shutdown actions
    try:
        daily_scheduler.stop()
    except Exception:
        pass
    try:
        telegram_bot.stop_polling()
    except Exception:
        pass
    try:
        sentinel_executor.shutdown(wait=False)
    except Exception:
        pass


from fastapi.middleware.gzip import GZipMiddleware

app = FastAPI(
    title="Financial Sentinel Web Dashboard",
    version=config.version,
    lifespan=lifespan
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Templates directory
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Static assets directory
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.state.store = orchestrator.state_store
app.state.orchestrator = orchestrator



class FeedbackRequest(BaseModel):
    target_id: str
    feedback_type: str
    user_notes: str = ""


class HoldingUpdateRequest(BaseModel):
    ticker: str
    name: str
    shares: float
    avg_price: float
    current_price: float
    sector: str


class HoldingItemPayload(BaseModel):
    ticker: str
    name: Optional[str] = None
    shares: float
    avg_price: Optional[float] = 0.0
    current_price: Optional[float] = 0.0
    sector: Optional[str] = "Technology"
    thematic_tags: Optional[List[str]] = Field(default_factory=list)


class PortfolioSavePayload(BaseModel):
    name: Optional[str] = "Custom Managed Portfolio"
    cash: Optional[float] = 0.0
    holdings: List[HoldingItemPayload]



class VerifyKeyRequest(BaseModel):
    api_key: str


@app.middleware("http")
async def security_and_auth_middleware(request: Request, call_next):
    path = request.url.path

    # Layer 2 API Authentication Guard (Exact Path Matching)
    if config.dashboard_auth_enabled:
        exempt_paths = {
            "/api/auth/login",
            "/api/auth/register",
            "/api/telegram/webhook",
            "/api/schedule/status",
            "/api/economic/calendar.ics",
            "/healthz",
            "/health",
            "/api/health"
        }
        cron_hdr = request.headers.get("X-Cron-Secret", "")
        has_valid_cron = bool(config.cron_secret and cron_hdr and secrets.compare_digest(cron_hdr, config.cron_secret))
        if path.startswith("/api/") and path not in exempt_paths and not has_valid_cron:
            user = get_current_user(request)
            if not user:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized: Session expired or login required"})

    response = await call_next(request)

    # Layer 4 HTTPS & Browser Defense Security Headers
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"

    return response


@app.get("/login", response_class=HTMLResponse)
async def login_view(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(request=request, name="login.html", context={"config": config})


TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))

def get_client_ip(request: Request) -> str:
    """Return client IP, counting from the right past trusted proxy hops (R-2).
    The leftmost XFF entry is client-supplied and must never be trusted.
    """
    xff = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if parts:
        idx = len(parts) - TRUSTED_PROXY_HOPS
        if 0 <= idx < len(parts):
            return parts[idx]
        return parts[-1]
    return request.client.host if request.client else "unknown"


login_attempts: Dict[str, List[float]] = {}
registration_attempts: Dict[str, List[float]] = {}


@app.post("/api/auth/register")
async def api_register(payload: UserRegisterRequest, request: Request, response: Response):
    client_ip = get_client_ip(request)
    now = time.time()

    # Rate-limit registrations: max 5 per 15 minutes per IP
    if len(registration_attempts) > 500:
        stale_cutoff = now - 900
        stale_ips = [ip for ip, timestamps in registration_attempts.items() if not timestamps or timestamps[-1] < stale_cutoff]
        for ip in stale_ips:
            registration_attempts.pop(ip, None)

    recent_reg = [t for t in registration_attempts.get(client_ip, []) if now - t < 900]
    if len(recent_reg) >= 5:
        raise HTTPException(status_code=429, detail="Too many registration attempts. Please wait 15 minutes.")
    registration_attempts.setdefault(client_ip, []).append(now)

    clean_user = payload.username.strip()
    clean_email = payload.email.strip().lower()

    if not clean_user or not clean_email or not payload.password:
        raise HTTPException(status_code=400, detail="Username, email, and password are required.")

    # Check uniqueness
    if orchestrator.state_store.get_user_by_username(clean_user):
        raise HTTPException(status_code=400, detail="Username is already taken.")

    # Create user (role is forced to 'user')
    try:
        user = orchestrator.state_store.create_user(
            username=clean_user,
            email=clean_email,
            password=payload.password,
            telegram_username=payload.telegram_username or "",
            raw_gemini_key=payload.gemini_api_key or "",
            role="user"
        )
    except Exception as e:
        logger.warning(f"Registration attempt failed: {e}")
        raise HTTPException(status_code=400, detail="Registration failed: An account with this username or email already exists.")

    # Create session token
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https" or bool(os.getenv("K_SERVICE"))
    token = create_session_token(user["id"], user["username"], role="user", epoch=user.get("token_epoch", 1))
    response.set_cookie(
        key="sentinel_token",
        value=token,
        httponly=True,
        secure=is_https,
        samesite="lax",
        max_age=60 * 60 * 24 * 7
    )
    return {
        "status": "success",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
            "has_gemini_key": bool(user.get("encrypted_gemini_key"))
        }
    }


@app.post("/api/auth/login")
async def api_login(payload: UserLoginRequest, request: Request, response: Response):
    client_ip = get_client_ip(request)
    now = time.time()

    # Bounded cache pruning for login attempts (H-7)
    if len(login_attempts) > 500:
        stale_cutoff = now - 180
        stale_ips = [ip for ip, timestamps in login_attempts.items() if not timestamps or timestamps[-1] < stale_cutoff]
        for ip in stale_ips:
            login_attempts.pop(ip, None)

    recent = [t for t in login_attempts.get(client_ip, []) if now - t < 60]
    if len(recent) >= 8:
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait 60 seconds.")

    clean_id = payload.username_or_email.strip()

    # 1. Check user database authentication via PBKDF2 hash verification
    user = orchestrator.state_store.authenticate_user(clean_id, payload.password)

    if user:
        login_attempts.pop(client_ip, None)
        is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https" or bool(os.getenv("K_SERVICE"))
        token = create_session_token(user["id"], user["username"], role=user.get("role", "user"), epoch=user.get("token_epoch", 1))
        response.set_cookie(
            key="sentinel_token",
            value=token,
            httponly=True,
            secure=is_https,
            samesite="lax",
            max_age=60 * 60 * 24 * 7
        )
        return {
            "status": "success",
            "token": token,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "email": user["email"],
                "has_gemini_key": bool(user.get("encrypted_gemini_key"))
            }
        }

    recent.append(now)
    login_attempts[client_ip] = recent
    raise HTTPException(status_code=401, detail="Invalid username/email or password.")


@app.post("/api/auth/logout")
async def api_logout(response: Response):
    response.delete_cookie("sentinel_token")
    response.delete_cookie("sentinel_auth")
    return {"status": "success", "message": "Logged out successfully"}


@app.get("/api/user/me")
async def api_get_user_me(user: Dict[str, Any] = Depends(require_user)):
    raw_key = decrypt_api_key(user.get("encrypted_gemini_key", "")) if user.get("encrypted_gemini_key") else ""
    masked = mask_api_key(raw_key)

    portfolio = orchestrator.get_active_portfolio(user_id=user["id"])

    return {
        "user_id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "telegram_username": user.get("telegram_username") or "",
        "telegram_chat_id": user.get("telegram_chat_id") or "",
        "has_gemini_key": bool(user.get("encrypted_gemini_key")),
        "masked_gemini_key": masked,
        "role": user.get("role", "user"),
        "cash": portfolio.cash
    }


@app.post("/api/user/settings")
async def api_update_user_settings(payload: UserSettingsUpdateRequest, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    updated_user = orchestrator.state_store.update_user_settings(
        user_id=user_id,
        telegram_username=payload.telegram_username,
        raw_gemini_key=payload.gemini_api_key if payload.gemini_api_key is not None else None,
        email=payload.email,
        password=payload.new_password if payload.new_password else None
    )

    if payload.cash is not None:
        p = orchestrator.get_active_portfolio(user_id=user_id)
        p.cash = max(0.0, float(payload.cash))
        orchestrator.persist_active_portfolio(p, user_id=user_id)

    raw_key = decrypt_api_key(updated_user.get("encrypted_gemini_key", "")) if updated_user.get("encrypted_gemini_key") else ""

    return {
        "status": "success",
        "message": "Settings updated successfully (Gemini key Fernet encrypted at rest with HKDF-SHA256)",
        "user": {
            "username": updated_user["username"],
            "email": updated_user["email"],
            "telegram_username": updated_user.get("telegram_username") or "",
            "has_gemini_key": bool(updated_user.get("encrypted_gemini_key")),
            "masked_gemini_key": mask_api_key(raw_key)
        }
    }


@app.post("/api/user/verify-key")
async def api_verify_gemini_key(payload: VerifyKeyRequest, user: Dict[str, Any] = Depends(require_user)):
    result = await asyncio.to_thread(validate_gemini_api_key, payload.api_key)
    return result


@app.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    user_id = user["id"]
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    portfolio.recalculate_weights()
    stress = orchestrator.quant_engine.analyze_portfolio(portfolio)
    latest_briefing = orchestrator.state_store.get_latest_user_scan(user_id)

    total_equity = portfolio.total_equity()
    total_wealth = total_equity + max(0.0, portfolio.cash)

    top_gainer = None
    top_laggard = None
    if portfolio.holdings:
        sorted_by_pnl = sorted(portfolio.holdings, key=lambda h: h.unrealized_pnl_pct, reverse=True)
        top_gainer = sorted_by_pnl[0] if sorted_by_pnl else None
        top_laggard = sorted_by_pnl[-1] if sorted_by_pnl else None

    raw_key = decrypt_api_key(user.get("encrypted_gemini_key", "")) if user.get("encrypted_gemini_key") else ""
    masked_key = mask_api_key(raw_key)

    return templates.TemplateResponse(request=request, name="index.html", context={
        "user": user,
        "masked_gemini_key": masked_key,
        "has_gemini_key": bool(user.get("encrypted_gemini_key")),
        "portfolio": portfolio,
        "stress": stress,
        "latest_briefing": latest_briefing,
        "latest_briefing_json": latest_briefing.model_dump_json() if latest_briefing else "null",
        "total_wealth": total_wealth,
        "top_gainer": top_gainer,
        "top_laggard": top_laggard,
        "version": config.version,
        "auth_enabled": config.dashboard_auth_enabled
    })


@app.get("/api/portfolio")
async def api_get_portfolio(user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    portfolio.recalculate_weights()
    stress = orchestrator.quant_engine.analyze_portfolio(portfolio)
    latest_briefing = orchestrator.state_store.get_latest_user_scan(user_id)
    return {
        "portfolio": portfolio.model_dump(),
        "stress": stress.model_dump(),
        "briefing": latest_briefing.model_dump(mode="json") if latest_briefing else None
    }



@app.post("/api/portfolio/cash")
async def api_update_portfolio_cash(request: Request, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    try:
        body = await request.json()
        new_cash = float(body.get("cash", 0.0))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cash payload")

    res = await asyncio.to_thread(portfolio_service.update_cash_balance, user_id=user_id, new_cash=new_cash)
    return res




@app.post("/api/portfolio/upload")
async def api_upload_portfolio(file: UploadFile = File(...), user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    filename = file.filename.lower()
    content = await file.read()

    holdings = []

    if filename.endswith(".json"):
        try:
            data = json.loads(content.decode("utf-8"))
            if isinstance(data, dict) and "holdings" in data:
                p = Portfolio(**data)
            elif isinstance(data, list):
                p = Portfolio(name="Uploaded Portfolio", holdings=[PortfolioHolding(**h) for h in data])
            else:
                raise ValueError("Invalid JSON portfolio structure")
            dumped = orchestrator.persist_active_portfolio(p, user_id=user_id)
            stress = orchestrator.quant_engine.analyze_portfolio(p)
            return {
                "status": "success",
                "message": f"Successfully imported {len(p.holdings)} holdings",
                "portfolio": dumped,
                "stress": stress.model_dump(mode="json")
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse JSON: {e}")

    elif filename.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
            def clean_num(val, default=0.0):
                if val is None or str(val).strip() == "":
                    return default
                try:
                    s = str(val).replace("$", "").replace(",", "").replace("%", "").strip()
                    return float(s)
                except Exception:
                    return default

            reader = csv.DictReader(io.StringIO(text))
            imported_cash = 0.0

            for row in reader:
                clean_row = {}
                for k, v in row.items():
                    if k:
                        norm_k = "".join(c for c in k.lower() if c.isalnum())
                        clean_row[norm_k] = v.strip() if isinstance(v, str) else v

                ticker = (
                    clean_row.get("ticker") or clean_row.get("symbol") or clean_row.get("stock")
                    or clean_row.get("sym") or clean_row.get("security") or clean_row.get("symbolticker")
                )
                ticker_upper = str(ticker).strip().upper() if ticker else ""
                name = clean_row.get("name") or clean_row.get("description") or clean_row.get("company") or clean_row.get("securityname") or ticker_upper
                name_upper = str(name).upper()

                shares = clean_num(
                    clean_row.get("shares") or clean_row.get("quantity") or clean_row.get("qty")
                    or clean_row.get("units") or clean_row.get("sharecount") or clean_row.get("totalshares")
                )

                avg_price = clean_num(
                    clean_row.get("avgprice") or clean_row.get("costbasis") or clean_row.get("averagecost")
                    or clean_row.get("averageprice") or clean_row.get("costbasispershare") or clean_row.get("unitcost")
                    or clean_row.get("purchaseprice") or clean_row.get("price")
                )
                current_price = clean_num(
                    clean_row.get("currentprice") or clean_row.get("lastprice") or clean_row.get("marketprice")
                    or clean_row.get("price") or clean_row.get("latestprice") or avg_price
                )

                if ticker_upper in ("CASH", "USD", "SPAXX", "FDRXX", "SWVXX", "MMF", "CORE", "FCASH") or "CASH" in name_upper or "MONEY MARKET" in name_upper:
                    cash_amount = (shares * avg_price) if (shares > 0 and avg_price > 0) else (shares if shares > 0 else (avg_price if avg_price > 0 else current_price))
                    if cash_amount > 0:
                        imported_cash += cash_amount
                    continue

                if not ticker or ticker_upper in ("TOTAL", "--", "ACCOUNT TOTAL", "TOTALS"):
                    continue

                if shares <= 0:
                    continue

                if current_price <= 0 and avg_price > 0:
                    current_price = avg_price
                elif current_price <= 0 and avg_price <= 0:
                    current_price = 100.0
                    avg_price = 100.0

                sector = clean_row.get("sector") or clean_row.get("industry") or clean_row.get("assetclass") or "Technology"
                tags_str = clean_row.get("thematictags") or clean_row.get("tags") or clean_row.get("theme") or ""
                tags = [t.strip() for t in str(tags_str).split(",") if t.strip()]

                holdings.append(PortfolioHolding(
                    ticker=ticker_upper,
                    name=str(name).strip(),
                    shares=shares,
                    avg_price=avg_price,
                    current_price=current_price,
                    sector=str(sector).strip(),
                    thematic_tags=tags
                ))

            if not holdings and imported_cash <= 0:
                raise ValueError("No valid stock positions or cash found.")

            cur_p = orchestrator.get_active_portfolio(user_id=user_id)
            final_cash = imported_cash if imported_cash > 0 else cur_p.cash

            p = Portfolio(name="Imported Portfolio", cash=final_cash, holdings=holdings)
            dumped = orchestrator.persist_active_portfolio(p, user_id=user_id)
            stress = orchestrator.quant_engine.analyze_portfolio(p)
            return {
                "status": "success",
                "message": f"Successfully imported {len(p.holdings)} holdings (Cash: ${p.cash:,.2f})",
                "portfolio": dumped,
                "stress": stress.model_dump(mode="json")
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {e}")
@app.post("/api/portfolio/save")
async def api_save_portfolio(payload: PortfolioSavePayload, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]

    new_holdings = []
    for item in payload.holdings:
        ticker = item.ticker.strip().upper()
        if not ticker or item.shares <= 0:
            continue
        cur_p = item.current_price if (item.current_price and item.current_price > 0) else (item.avg_price or 0.0)
        new_holdings.append(PortfolioHolding(
            ticker=ticker,
            name=item.name or ticker,
            shares=float(item.shares),
            avg_price=float(item.avg_price or 0.0),
            current_price=float(cur_p),
            sector=item.sector or "Technology",
            thematic_tags=item.thematic_tags or []
        ))

    portfolio = Portfolio(
        name=payload.name or "Custom Managed Portfolio",
        cash=float(payload.cash or 0.0),
        holdings=new_holdings,
        last_updated=datetime.utcnow()
    )

    # Concurrently sync live quotes for current market prices
    try:
        portfolio, _ = update_portfolio_live_prices(portfolio)
    except Exception:
        pass

    dumped = orchestrator.persist_active_portfolio(portfolio, user_id=user_id)
    stress = orchestrator.quant_engine.analyze_portfolio(portfolio)

    return {
        "status": "success",
        "portfolio": dumped,
        "stress": stress.model_dump(mode="json")
    }


@app.post("/api/portfolio/add")
async def api_add_holding_alias(holding: HoldingUpdateRequest, user: Dict[str, Any] = Depends(require_user)):
    return await api_update_holding(holding, user=user)


@app.post("/api/portfolio/holding")
async def api_update_holding(holding: HoldingUpdateRequest, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    try:
        res = await asyncio.to_thread(
            portfolio_service.add_or_update_holding,
            user_id=user_id,
            ticker=holding.ticker,
            shares=holding.shares,
            price=holding.avg_price,
            name=holding.name,
            sector=holding.sector,
            current_price=holding.current_price,
            incremental=False,
            deduct_cash=True
        )
        return {
            "status": "success",
            "portfolio": res["portfolio"],
            "stress": res["stress"]
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@app.post("/api/portfolio/holding/delete")
async def api_delete_holding(request: Request, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    try:
        body = await request.json()
        ticker = body.get("ticker", "").strip().upper()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload")

    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    try:
        res = await asyncio.to_thread(
            portfolio_service.remove_or_trim_holding,
            user_id=user_id,
            ticker=ticker,
            shares_to_remove="all",
            credit_cash=True
        )

        return {
            "status": "success",
            "message": f"Sold {ticker}. Credited ${res['liquidated_val']:,.2f} to cash reserve.",
            "portfolio": res["portfolio"],
            "stress": res["stress"]
        }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))



@app.post("/api/scan")
async def api_trigger_scan(force_fresh: bool = False, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    user_key = orchestrator.resolve_user_api_key(user_id)

    if not user_key:
        raise HTTPException(
            status_code=400,
            detail="Gemini API Key Required: Please configure GEMINI_API_KEY on the server or add your personal Gemini API key in Dashboard Settings."
        )


    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    try:
        briefing = await asyncio.to_thread(
            orchestrator.run_monitoring_cycle,
            portfolio=portfolio,
            live=True,
            force_fresh=force_fresh,
            user_id=user_id,
            api_key=user_key,
            auto_dispatch=True,
            async_dispatch=True
        )
        return {
            "status": "success",
            "report_id": briefing.report_id,
            "briefing": briefing.model_dump(mode="json"),
            "portfolio": portfolio.model_dump(),
            "stress": briefing.portfolio_stress.model_dump() if briefing.portfolio_stress else None
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Scan execution error: {str(e)}")


@app.get("/api/scan/stream")
@app.post("/api/scan/stream")
async def api_scan_stream(request: Request, force_fresh: bool = False, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    user_key = orchestrator.resolve_user_api_key(user_id)

    if not user_key:
        raise HTTPException(
            status_code=400,
            detail="Gemini API Key Required: Please configure GEMINI_API_KEY on the server or add your personal Gemini API key in Dashboard Settings."
        )

    portfolio = orchestrator.get_active_portfolio(user_id=user_id)

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress_callback(stage: str, percent: int, msg: str):
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "progress", "stage": stage, "percent": percent, "message": msg}
            )

        async def run_scan():
            try:
                b = await asyncio.to_thread(
                    orchestrator.run_monitoring_cycle,
                    portfolio=portfolio,
                    live=True,
                    force_fresh=force_fresh,
                    user_id=user_id,
                    api_key=user_key,
                    auto_dispatch=True,
                    async_dispatch=True,
                    on_progress=progress_callback
                )
                await queue.put({
                    "type": "complete",
                    "status": "success",
                    "report_id": b.report_id,
                    "briefing": b.model_dump(mode="json"),
                    "portfolio": portfolio.model_dump(),
                    "stress": b.portfolio_stress.model_dump() if b.portfolio_stress else None
                })
            except Exception as ex:
                import traceback
                traceback.print_exc()
                await queue.put({"type": "error", "message": str(ex)})

        _scan_task = asyncio.create_task(run_scan())

        while True:
            item = await queue.get()
            event_type = item.get("type", "progress")
            yield f"data: {json.dumps(item, default=str)}\n\n"
            if event_type in ("complete", "error"):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Encoding": "identity"
        }
    )


@app.get("/api/news/live")
async def api_get_live_news(user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    portfolio_tickers = [h.ticker for h in portfolio.holdings]
    news_items = await asyncio.to_thread(orchestrator.news_agent.ingest_all_feeds, live=True, portfolio_tickers=portfolio_tickers)
    return {
        "status": "success",
        "count": len(news_items),
        "news": [n.model_dump(mode="json") for n in news_items]
    }


@app.get("/api/quote/{ticker}")
async def api_get_quote(ticker: str, user: Dict[str, Any] = Depends(require_user)):
    clean_ticker = ticker.strip().upper()
    if not re.match(r"^[A-Z0-9.\-]{1,10}$", clean_ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol format")
    quote = await asyncio.to_thread(fetch_live_quote, clean_ticker)
    return quote


@app.get("/api/quotes/refresh")
async def api_refresh_quotes(user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    portfolio, quotes = await asyncio.to_thread(update_portfolio_live_prices, portfolio)
    dumped = orchestrator.persist_active_portfolio(portfolio, user_id=user_id)
    stress = orchestrator.quant_engine.analyze_portfolio(portfolio)
    return {
        "status": "success",
        "quotes": quotes,
        "portfolio": dumped,
        "stress": stress.model_dump(mode="json")
    }


@app.get("/api/earnings/calendar")
async def api_get_earnings_calendar(user: Dict[str, Any] = Depends(require_user)):
    from analytics.earnings_calendar import fetch_7day_earnings_schedule
    user_id = user["id"]
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    portfolio_tickers = [h.ticker for h in portfolio.holdings]
    schedule = fetch_7day_earnings_schedule(portfolio_tickers=portfolio_tickers)
    return {
        "status": "success",
        "schedule": schedule
    }


@app.get("/api/economic/calendar.ics")
async def api_get_economic_calendar_ics(catalytic_only: bool = True):
    """
    Serves the live RFC 5545 iCalendar (.ics) subscription feed.
    Compatible with Google Calendar, Apple Calendar (macOS/iOS), and Microsoft Outlook.
    Does not require session cookies so background calendar crawlers can poll reliably.
    """
    from fastapi.responses import Response
    from analytics.economic_calendar import generate_economic_calendar_ics
    ics_text = generate_economic_calendar_ics(catalytic_only=catalytic_only)
    return Response(
        content=ics_text,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'inline; filename="financial_sentinel_macro.ics"',
            "Cache-Control": "max-age=1800, public",
        }
    )


@app.get("/api/economic/calendar")
async def api_get_economic_calendar(
    catalytic_only: bool = False,
    user: Dict[str, Any] = Depends(require_user)
):
    from analytics.economic_calendar import get_economic_calendar_context
    ctx = get_economic_calendar_context(catalytic_only=catalytic_only)
    return {
        "status": "success",
        "calendar": ctx
    }



@app.post("/api/analyze/{ticker}")
@app.get("/api/analyze/{ticker}")
async def api_analyze_ticker(ticker: str, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    try:
        res = await asyncio.to_thread(
            analysis_service.run_single_ticker_analysis,
            ticker=ticker,
            user_id=user_id,
        )
        res_copy = dict(res)
        res_copy.pop("structured", None)
        return res_copy
    except ValueError as ve:
        err_msg = str(ve)
        status_code = 404 if "not recognized" in err_msg.lower() else 400
        raise HTTPException(status_code=status_code, detail=err_msg)
    except Exception as e:
        logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to analyze {ticker}: {str(e)}")


@app.get("/api/deepdives")
async def api_get_user_deepdives(limit: int = 50, ticker: Optional[str] = None, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    deepdives = orchestrator.state_store.get_user_deepdives(user_id=user_id, limit=limit, ticker=ticker)
    return {"status": "success", "deepdives": deepdives}


@app.get("/api/deepdives/{deepdive_id}")
async def api_get_deepdive_detail(deepdive_id: str, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    is_admin = bool(user.get("role") == "admin")

    dd = orchestrator.state_store.get_deepdive_by_id(deepdive_id, user_id=user_id if not is_admin else None)
    if not dd:
        raise HTTPException(status_code=404, detail="Archived deep dive not found.")
    return {"status": "success", "deepdive": dd}


@app.delete("/api/deepdives/{deepdive_id}")
async def api_delete_deepdive(deepdive_id: str, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    is_admin = bool(user.get("role") == "admin")

    existing = orchestrator.state_store.get_deepdive_by_id(deepdive_id, user_id=user_id if not is_admin else None)
    if not existing:
        raise HTTPException(status_code=404, detail="Deep dive record not found.")

    deleted = orchestrator.state_store.delete_deepdive(deepdive_id, user_id=user_id, is_admin=is_admin)
    if not deleted:
        raise HTTPException(status_code=404, detail="Deep dive record not found or could not be deleted.")
    return {"status": "success", "message": "Archived deep dive deleted successfully."}




@app.post("/api/feedback")
async def api_submit_feedback(req: FeedbackRequest, user: Dict[str, Any] = Depends(require_user)):
    clean_notes = (req.user_notes or "").strip()
    if len(clean_notes) > 2000:
        raise HTTPException(status_code=400, detail="Feedback notes exceed maximum allowed length (2,000 characters).")
    clean_type = req.feedback_type.strip().lower()
    allowed_types = {"accurate", "noise", "helpful", "unhelpful", "flag"}
    if clean_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Invalid feedback type '{clean_type}'.")
    orchestrator.state_store.record_feedback(
        target_id=req.target_id.strip()[:100],
        feedback_type=clean_type,
        user_notes=clean_notes
    )
    return {"status": "success", "message": "Feedback recorded"}


class ChatMessageRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []


@app.post("/api/chat")
async def api_chat(req: ChatMessageRequest, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    user_key = orchestrator.resolve_user_api_key(user_id)

    if user and user.get("role") != "admin" and not user_key:
        return {
            "reply": "🔒 **Gemini API Key Required:** Please add your personal Gemini API key in **Settings** (top right) to activate conversational AI advisory. *(Your key is AES-256 encrypted at rest and used only for your account)*",
            "status": "key_required"
        }

    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    stress = orchestrator.quant_engine.analyze_portfolio(portfolio)
    recent_briefings = orchestrator.state_store.get_recent_briefings(limit=1)
    briefing_payload = recent_briefings[0].get("payload", {}) if recent_briefings else {}

    holdings_summary = []
    for h in portfolio.holdings:
        holdings_summary.append(
            f"{h.ticker} ({h.name}): {h.shares} sh @ avg ${h.avg_price:.2f}, price ${h.current_price:.2f}, val ${h.market_value:,.2f} ({h.weight_pct:.1f}%), PnL {h.unrealized_pnl_pct:+.2f}% (${h.unrealized_pnl:+,.2f})"
        )

    context = f"""
PORTFOLIO SUMMARY:
- Total Equity: ${portfolio.total_equity():,.2f} | Cash: ${portfolio.cash:,.2f}
- Total Unrealized PnL: ${portfolio.total_unrealized_pnl():+,.2f} ({portfolio.total_unrealized_pnl_pct():+.2f}%)
- Estimated Beta: {stress.estimated_portfolio_beta} | Top 3 Concentration: {stress.top_3_concentration_pct}%

CURRENT HOLDINGS:
{chr(10).join(holdings_summary)}

LATEST EXECUTIVE BRIEFING:
{briefing_payload.get('executive_summary', 'No scan run yet.')}

QUANT MACRO SHOCK SIMULATIONS:
{json.dumps(stress.macro_shock_scenarios, indent=2)}
"""

    system_instruction = f"""
You are the Lead Portfolio Manager and Senior AI Investment Advisor for Financial Sentinel, powered by Gemini 3.8 Flash.
You have real-time access to the user's active holdings, risk metrics, deployable cash, and multi-agent intelligence.

LIVE PORTFOLIO & CASH RESERVES:
{context}

GUIDELINES:
1. Provide authoritative, concise, and institutional-grade financial analysis.
2. Avoid unwarranted puffery, hyperbole, and false profundity. Ground all reasoning in verifiable data, earnings, and valuation realities.
3. Be critical and selective with stock recommendations: Do not casually advise buying random tickers. Reserve Buy recommendations for companies with durable moats, high forward growth potential, or credible positive institutional analyst revisions.
4. Focus on specific asset weights, dollar exposures, sector tilts, risk sensitivities, and tactical opportunities.
5. Proactively calculate and suggest specific dollar allocations from the investor's active cash reserve (${portfolio.cash:,.2f}) whenever discussing hedging, dollar-cost averaging, or rebalancing.
6. Be conversational, analytical, and direct. Format with clean markdown and bullet points.
7. Incorporate real-time market search intelligence when evaluating specific companies, latest quarterly earnings, macro developments, or ticker comparisons.
"""

    history_prompt = ""
    for msg in (req.history or [])[-6:]:
        role = msg.get("role", "user").upper()
        content = msg.get("content", "")
        history_prompt += f"{role}: {content}\n"

    full_prompt = f"{history_prompt}USER: {req.message}\nASSISTANT:"

    reply = await asyncio.to_thread(
        orchestrator.notification_agent.query_llm_text,
        prompt=full_prompt,
        system_instruction=system_instruction,
        api_key=user_key,
        enable_grounding=True
    )

    if not reply:
        top_tickers = [h.ticker for h in portfolio.holdings[:3]]
        top_str = f" (major exposures: {', '.join(top_tickers)})" if top_tickers else ""
        reply = (
            f"⚠️ **AI Advisory Notice:** Gemini was temporarily unable to complete the requested analysis. "
            f"Your active portfolio remains synchronized with ${portfolio.total_equity():,.2f} equity{top_str} and ${portfolio.cash:,.2f} in deployable cash. "
            "Please try re-submitting your question or verify your Gemini API quota."
        )

    return {"reply": reply, "status": "success"}


class DiscoverOpportunitiesRequest(BaseModel):
    theme: Optional[str] = None
    count: Optional[int] = 4


@app.post("/api/opportunities/discover")
async def api_discover_opportunities(req: DiscoverOpportunitiesRequest, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    user_key = orchestrator.resolve_user_api_key(user_id)
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    opps = await asyncio.to_thread(
        orchestrator.opportunity_agent.discover_more_opportunities,
        portfolio=portfolio,
        theme=req.theme,
        count=req.count or 4,
        api_key=user_key
    )
    critic_reviews = [orchestrator.critic_agent.review_opportunity(o) for o in opps]
    return {
        "status": "success",
        "theme": req.theme,
        "opportunities": [o.model_dump(mode="json") for o in opps],
        "critic_reviews": [c.model_dump(mode="json") for c in critic_reviews]
    }


@app.post("/api/opportunities/moonshots")
async def api_discover_moonshots(count: int = 4, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    user_key = orchestrator.resolve_user_api_key(user_id)
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    moonshots = await asyncio.to_thread(
        orchestrator.opportunity_agent.discover_moonshot_opportunities,
        portfolio=portfolio,
        count=count,
        api_key=user_key
    )
    critic_reviews = [orchestrator.critic_agent.review_opportunity(o) for o in moonshots]
    return {
        "status": "success",
        "moonshots": [o.model_dump(mode="json") for o in moonshots],
        "critic_reviews": [c.model_dump(mode="json") for c in critic_reviews]
    }



@app.post("/api/telegram/webhook")
async def api_telegram_webhook(request: Request):
    expected_secret = config.resolved_telegram_webhook_secret

    if not expected_secret:
        logger.error("Telegram webhook received but secret is not configured on server. Rejecting update.")
        return JSONResponse(status_code=403, content={"detail": "Forbidden: Webhook secret not configured on server"})

    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(received_secret, expected_secret):
        logger.warning("Rejected unauthenticated Telegram webhook update (missing or invalid secret token).")
        return JSONResponse(status_code=401, content={"detail": "Unauthorized: Invalid Telegram webhook secret token"})

    try:
        data = await request.json()
        return await asyncio.to_thread(telegram_bot.process_webhook_update, data)
    except Exception as e:
        logger.error(f"Error processing Telegram webhook: {e}")
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Invalid payload"})


@app.get("/api/schedule/status")
async def api_get_schedule_status(user: Dict[str, Any] = Depends(require_user)):

    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_pst = datetime.now(ZoneInfo("America/Los_Angeles"))
    return {
        "status": "active",
        "timezone": "America/Los_Angeles (PST/PDT)",
        "current_time_pst": now_pst.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
        "weekday_schedule": {
            "06:30 PST": "Pre-Market Intelligence & Opening Catalysts",
            "10:00 PST": "Mid-Market Macro & Momentum Pulse",
            "13:30 PST": "Post-Market Wrap & Day-End Recap",
            "17:00 PST": "Daily Earnings Wrap & Guidance Breakdown"
        },
        "weekend_schedule": {
            "21:00 PST": "Weekend Macro & Week-Ahead Preview"
        },
        "scheduler_running": daily_scheduler.is_running
    }


@app.post("/api/schedule/trigger/{slot}")
async def api_trigger_scheduled_briefing(slot: str, force: bool = False, caller: Dict[str, Any] = Depends(require_cron_or_admin)):
    if slot not in ("premarket", "midmarket", "postmarket", "weekend", "earnings"):
        raise HTTPException(status_code=400, detail="Invalid slot. Choose premarket, midmarket, postmarket, weekend, or earnings.")

    target_user_id = caller.get("id") if caller.get("id") != "cron_scheduler" else None
    msg = await asyncio.to_thread(briefing_service.generate_briefing, slot, target_user_id, None, True, force)
    return {
        "status": "success",
        "slot": slot,
        "message": f"Successfully processed {slot} briefing slot.",
        "preview": (msg[:300] + "...") if msg else ""
    }


SLOT_METADATA = {
    "premarket": {
        "title": "Pre-Market Intelligence & Opening Catalysts",
        "icon": "🌅",
        "schedule": "6:30 AM PST (Mon-Fri)",
        "desc": "Overnight macro, index futures, pre-market movers, and opening alpha catalysts."
    },
    "midmarket": {
        "title": "Mid-Market Macro & Momentum Pulse",
        "icon": "☀️",
        "schedule": "10:00 AM PST (Mon-Fri)",
        "desc": "Intraday momentum, economic data digestion, leading/lagging sectors, and afternoon posture."
    },
    "postmarket": {
        "title": "Post-Market Wrap & Day-End Recap",
        "icon": "🌙",
        "schedule": "3:00 PM PST (Mon-Fri)",
        "desc": "Closing bell summary, after-hours earnings call takeaways, top gainers/losers, and tomorrow's watchlist."
    },
    "weekend": {
        "title": "Weekend Macro & Week-Ahead Preview",
        "icon": "🌟",
        "schedule": "9:00 PM PST (Sun)",
        "desc": "Weekend geopolitics, Sunday futures sentiment, macro calendar, and secular opportunities."
    },
    "earnings": {
        "title": "7-Day Corporate Earnings Outlook",
        "icon": "📅",
        "schedule": "Weekly & On-Demand",
        "desc": "Verified Nasdaq earnings schedule, BMO/AMC releases, and portfolio cross-exposure."
    }
}


class GenerateBriefingPayload(BaseModel):
    slot: str
    dispatch_telegram: bool = False


@app.get("/api/briefings")
async def api_get_market_briefings(slot: Optional[str] = None, limit: int = 20, user: Dict[str, Any] = Depends(require_user)):
    user_id = user["id"]
    is_admin = bool(user.get("role") == "admin")
    # Non-admin users strictly only see their own briefings
    briefings = briefing_service.list_briefings(
        user_id=user_id if not is_admin else None,
        slot=slot,
        limit=min(limit, 100)
    )
    enriched = []
    for b in briefings:
        s = b.get("slot") or "general"
        meta = SLOT_METADATA.get(s, {
            "title": f"{s.capitalize()} Briefing",
            "icon": "📋",
            "schedule": "On-Demand",
            "desc": "Executive market intelligence report."
        })
        b["slot_title"] = meta["title"]
        b["icon"] = meta["icon"]
        b["schedule"] = meta["schedule"]
        b["desc"] = meta["desc"]
        enriched.append(b)
    return {"status": "success", "briefings": enriched, "slots": SLOT_METADATA}


@app.get("/api/briefings/{report_id}")
async def api_get_market_briefing_detail(report_id: str, user: Dict[str, Any] = Depends(require_user)):
    b = briefing_service.get_briefing(report_id)
    if not b:
        raise HTTPException(status_code=404, detail="Briefing report not found.")

    user_id = user["id"]
    is_admin = bool(user.get("role") == "admin")

    # Strict tenant isolation: private user-scoped briefings can only be viewed by their owner or an admin
    b_user = b.get("user_id")
    if b_user and not is_admin and b_user != user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You do not have permission to view this private briefing.")

    s = b.get("slot") or "general"
    meta = SLOT_METADATA.get(s, {
        "title": f"{s.capitalize()} Briefing",
        "icon": "📋",
        "schedule": "On-Demand",
        "desc": "Executive market intelligence report."
    })
    b["slot_title"] = meta["title"]
    b["icon"] = meta["icon"]
    b["schedule"] = meta["schedule"]
    b["desc"] = meta["desc"]
    return {"status": "success", "briefing": b}


@app.post("/api/briefings/generate")
async def api_generate_market_briefing(payload: GenerateBriefingPayload, user: Dict[str, Any] = Depends(require_user)):
    slot = payload.slot.lower().strip()
    if slot not in SLOT_METADATA:
        raise HTTPException(status_code=400, detail=f"Invalid briefing slot. Choose one of: {list(SLOT_METADATA.keys())}")

    user_id = user["id"]

    target_chat = None
    if payload.dispatch_telegram:
        if user and user.get("telegram_chat_id"):
            target_chat = user["telegram_chat_id"]
        else:
            target_chat = telegram_bot.get_effective_chat_id() or config.telegram_chat_id

    msg = await asyncio.to_thread(
        briefing_service.generate_briefing,
        slot,
        user_id,
        target_chat if payload.dispatch_telegram else None,
        payload.dispatch_telegram,
        True
    )

    meta = SLOT_METADATA.get(slot, {})
    return {
        "status": "success",
        "slot": slot,
        "slot_title": meta.get("title", slot),
        "icon": meta.get("icon", "📋"),
        "message_html": msg,
        "dispatched_telegram": bool(payload.dispatch_telegram and target_chat)
    }


@app.post("/api/briefings/{report_id}/dispatch")
async def api_dispatch_market_briefing(report_id: str, user: Dict[str, Any] = Depends(require_user)):
    b = briefing_service.get_briefing(report_id)
    if not b:
        raise HTTPException(status_code=404, detail="Briefing report not found.")

    user_id = user["id"]
    is_admin = bool(user and user.get("role") == "admin")

    # Strict tenant isolation for private briefing dispatch
    b_user = b.get("user_id")
    if b_user and not is_admin and b_user != user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You do not have permission to dispatch this private briefing.")

    target_chat = None
    if user and user.get("telegram_chat_id"):
        target_chat = user["telegram_chat_id"]
    else:
        target_chat = telegram_bot.get_effective_chat_id() or config.telegram_chat_id

    if not target_chat:
        raise HTTPException(status_code=400, detail="Telegram chat ID is not configured. Please link Telegram in Settings.")

    msg = b.get("message_html") or b.get("executive_summary")
    try:
        telegram_bot.send_message(msg, chat_id=target_chat)
        return {"status": "success", "message": f"Dispatched {b.get('slot')} briefing to Telegram."}
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Failed to send to Telegram: {str(ex)}")


@app.post("/api/briefings/prune")
async def api_prune_market_briefings(retention_days: int = 30, user: Dict[str, Any] = Depends(require_admin)):
    pruned_count = briefing_service.prune_briefings(retention_days=retention_days)
    return {
        "status": "success",
        "message": f"Successfully pruned {pruned_count} duplicate and expired briefing entries.",
        "pruned_count": pruned_count
    }


class TelegramConfigPayload(BaseModel):
    bot_token: str
    chat_id: Optional[str] = None


@app.post("/api/telegram/configure")
async def api_configure_telegram(payload: TelegramConfigPayload, user: Dict[str, Any] = Depends(require_admin)):
    config.telegram_bot_token = payload.bot_token
    if payload.chat_id:
        config.telegram_chat_id = payload.chat_id

    telegram_bot.bot_token = payload.bot_token
    telegram_bot.chat_id = payload.chat_id or telegram_bot.chat_id
    telegram_bot.start_polling()
    return {"status": "configured", "polling": telegram_bot.is_running}


@app.post("/api/cache/clear")
async def api_cache_clear(user: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint to invalidate in-memory quote, bars, and general caches."""
    from analytics.market_data import PRICE_CACHE
    from analytics.technical_indicators import BARS_CACHE
    from storage.cache_manager import cache_manager
    p_cleared = len(PRICE_CACHE)
    b_cleared = len(BARS_CACHE)
    PRICE_CACHE.clear()
    BARS_CACHE.clear()
    cm_cleared = cache_manager.clear()
    return {
        "status": "success",
        "cleared": {
            "price_cache_entries": p_cleared,
            "bars_cache_entries": b_cleared,
            "cache_manager_entries": cm_cleared
        }
    }


@app.get("/api/cache/stats")
async def api_cache_stats(user: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint to monitor cache telemetry."""
    from analytics.market_data import PRICE_CACHE
    from analytics.technical_indicators import BARS_CACHE
    from storage.cache_manager import cache_manager
    return {
        "price_cache_entries": len(PRICE_CACHE),
        "bars_cache_entries": len(BARS_CACHE),
        "manager_stats": cache_manager.stats()
    }


@app.get("/healthz")
@app.get("/health")
@app.get("/api/health")
async def healthz():
    """Healthcheck endpoint for Cloud Run and monitoring probes."""
    db_ok = False
    try:
        with orchestrator.state_store._get_connection() as conn:
            conn.execute("SELECT 1;").fetchone()
        db_ok = True
    except Exception as e:
        logger.error(f"Healthcheck database probe failed: {e}")

    status_code = 200 if db_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if db_ok else "unhealthy",
            "database": "connected" if db_ok else "unavailable",
            "scheduler": "running" if daily_scheduler.is_running else "stopped",
            "version": config.version
        }
    )


if __name__ == "__main__":
    import uvicorn
    server_port = int(os.getenv("PORT", str(config.web_port)))
    print(f"🚀 Launching Financial Sentinel Dashboard on http://{config.web_host}:{server_port}")
    uvicorn.run(app, host=config.web_host, port=server_port)
