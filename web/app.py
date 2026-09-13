"""
FastAPI Web Dashboard Backend for the Financial Multi-Agent System.
Supports Multi-User Authentication, Isolated Portfolios, and Bring-Your-Own-Key (BYOK) AES-256 Encrypted Gemini API Keys.
"""
import sys
import os
import json
import csv
import io
import time
from datetime import datetime
from typing import Dict, Any, Optional, List


import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi import FastAPI, Request, HTTPException, Response, BackgroundTasks, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from orchestrator import FinancialSentinelOrchestrator
from models import (
    Portfolio, PortfolioHolding, UserSession, UserRegisterRequest,
    UserLoginRequest, UserSettingsUpdateRequest
)
from config import config
from auth.crypto import (
    create_session_token, verify_session_token, mask_api_key,
    decrypt_api_key, validate_gemini_api_key
)
from channels.telegram_bot import FinancialSentinelTelegramBot
from scheduler import DailyMarketScheduler

logger = logging.getLogger("WebApp")

# Dedicated ThreadPoolExecutor to prevent thread starvation on single-core Cloud Run
sentinel_executor = ThreadPoolExecutor(
    max_workers=16,
    thread_name_prefix="sentinel_worker"
)

orchestrator = FinancialSentinelOrchestrator()
daily_scheduler = DailyMarketScheduler(
    orchestrator=orchestrator,
    portfolio_loader=orchestrator.get_active_portfolio
)
telegram_bot = FinancialSentinelTelegramBot(orchestrator=orchestrator)


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



def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    Extracts and authenticates user from encrypted session cookie or legacy master pass.
    """
    # 1. Check Fernet session token cookie
    token = request.cookies.get("sentinel_token") or request.headers.get("X-Sentinel-Token")
    if token:
        payload = verify_session_token(token)
        if payload and payload.get("uid"):
            user = orchestrator.state_store.get_user_by_id(payload["uid"])
            if not user and (payload.get("uid") == "usr_admin" or payload.get("usr") == "admin"):
                user = orchestrator.state_store.get_or_create_default_admin()
            if user:
                return user


    # 2. Check legacy password cookie / header for default admin
    cookie_auth = request.cookies.get("sentinel_auth")
    header_auth = request.headers.get("X-Sentinel-Auth")
    if config.dashboard_password and (cookie_auth == config.dashboard_password or header_auth == config.dashboard_password):
        admin = orchestrator.state_store.get_or_create_default_admin()
        return admin

    # 3. If auth is completely disabled in config, fallback to default admin
    if not config.dashboard_auth_enabled:
        admin = orchestrator.state_store.get_or_create_default_admin()
        return admin

    return None


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
    
    # Layer 2 API Authentication Guard
    if config.dashboard_auth_enabled:
        exempt_paths = [
            "/api/auth/login",
            "/api/auth/register",
            "/api/telegram/webhook",
            "/api/schedule/trigger",
            "/api/schedule/status"
        ]
        if path.startswith("/api/") and not any(path.startswith(p) for p in exempt_paths):
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
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    
    return response


@app.get("/login", response_class=HTMLResponse)
async def login_view(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(request=request, name="login.html", context={"auth_enabled": config.dashboard_auth_enabled})


login_attempts: Dict[str, List[float]] = {}


@app.post("/api/auth/register")
async def api_register(payload: UserRegisterRequest, request: Request, response: Response):
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
        raise HTTPException(status_code=400, detail=f"Registration failed: {e}")

    # Create session token
    token = create_session_token(user["id"], user["username"], role="user")
    response.set_cookie(
        key="sentinel_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30
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
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    recent = [t for t in login_attempts.get(client_ip, []) if now - t < 60]
    if len(recent) >= 8:
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait 60 seconds.")

    clean_id = payload.username_or_email.strip()
    
    # 1. Check user database authentication
    user = orchestrator.state_store.authenticate_user(clean_id, payload.password)
    
    # 2. Check fallback master passcode for default admin
    if not user and config.dashboard_password and payload.password == config.dashboard_password:
        user = orchestrator.state_store.get_or_create_default_admin()

    if user:
        login_attempts.pop(client_ip, None)
        token = create_session_token(user["id"], user["username"], role=user.get("role", "user"))
        response.set_cookie(
            key="sentinel_token",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30
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

    recent.append(now)
    login_attempts[client_ip] = recent
    raise HTTPException(status_code=401, detail="Invalid username/email or password.")


@app.post("/api/auth/logout")
async def api_logout(response: Response):
    response.delete_cookie("sentinel_token")
    response.delete_cookie("sentinel_auth")
    return {"status": "success", "message": "Logged out successfully"}


@app.get("/api/user/me")
async def api_get_user_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
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
async def api_update_user_settings(payload: UserSettingsUpdateRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

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
        "message": "Settings updated successfully (Gemini key AES-256 encrypted)",
        "user": {
            "username": updated_user["username"],
            "email": updated_user["email"],
            "telegram_username": updated_user.get("telegram_username") or "",
            "has_gemini_key": bool(updated_user.get("encrypted_gemini_key")),
            "masked_gemini_key": mask_api_key(raw_key)
        }
    }


@app.post("/api/user/verify-key")
async def api_verify_gemini_key(payload: VerifyKeyRequest):
    result = validate_gemini_api_key(payload.api_key)
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
async def api_get_portfolio(request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
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
async def api_update_portfolio_cash(request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
    try:
        body = await request.json()
        new_cash = float(body.get("cash", 0.0))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cash payload")
    
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    portfolio.cash = max(0.0, new_cash)
    dumped = orchestrator.persist_active_portfolio(portfolio, user_id=user_id)
    stress = orchestrator.quant_engine.analyze_portfolio(portfolio)
    
    return {
        "status": "success",
        "message": f"Cash balance updated to ${portfolio.cash:,.2f}",
        "portfolio": dumped,
        "stress": stress.model_dump(mode="json")
    }


@app.post("/api/portfolio/upload")
async def api_upload_portfolio(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    user_id = user["id"] if user else None
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
async def api_save_portfolio(payload: PortfolioSavePayload, request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
    
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
async def api_add_holding_alias(holding: HoldingUpdateRequest, request: Request):
    return await api_update_holding(holding, request)


@app.post("/api/portfolio/holding")
async def api_update_holding(holding: HoldingUpdateRequest, request: Request):

    user = get_current_user(request)
    user_id = user["id"] if user else None
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    
    target_ticker = holding.ticker.strip().upper()
    found = False
    for i, h in enumerate(portfolio.holdings):
        if h.ticker.upper() == target_ticker:
            share_diff = holding.shares - h.shares
            if share_diff > 0:
                cost = share_diff * (holding.avg_price or h.avg_price or 0.0)
                portfolio.cash = max(0.0, float(portfolio.cash or 0.0) - cost)
            elif share_diff < 0:
                proceeds = abs(share_diff) * (holding.current_price or h.current_price or holding.avg_price or 0.0)
                portfolio.cash = max(0.0, float(portfolio.cash or 0.0) + proceeds)

            portfolio.holdings[i].name = holding.name
            portfolio.holdings[i].shares = holding.shares
            portfolio.holdings[i].avg_price = holding.avg_price
            portfolio.holdings[i].current_price = holding.current_price
            portfolio.holdings[i].sector = holding.sector
            found = True
            break
            
    if not found:
        purchase_cost = holding.shares * holding.avg_price
        if (portfolio.cash or 0.0) >= purchase_cost:
            portfolio.cash = max(0.0, float(portfolio.cash or 0.0) - purchase_cost)
        portfolio.holdings.append(PortfolioHolding(
            ticker=target_ticker,
            name=holding.name,
            shares=holding.shares,
            avg_price=holding.avg_price,
            current_price=holding.current_price,
            sector=holding.sector,
            thematic_tags=[]
        ))
        
    dumped = orchestrator.persist_active_portfolio(portfolio, user_id=user_id)
    stress = orchestrator.quant_engine.analyze_portfolio(portfolio)
    
    return {
        "status": "success",
        "portfolio": dumped,
        "stress": stress.model_dump(mode="json")
    }


@app.post("/api/portfolio/holding/delete")
async def api_delete_holding(request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
    try:
        body = await request.json()
        ticker = body.get("ticker", "").strip().upper()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload")
        
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    
    # Liquidate deleted holding directly into cash reserve
    deleted_holding = next((h for h in portfolio.holdings if h.ticker.upper() == ticker), None)
    liquidated_val = 0.0
    if deleted_holding:
        liquidated_val = deleted_holding.shares * (deleted_holding.current_price or deleted_holding.avg_price or 0.0)
        portfolio.cash = max(0.0, float(portfolio.cash or 0.0) + liquidated_val)

    portfolio.holdings = [h for h in portfolio.holdings if h.ticker.upper() != ticker]
    
    dumped = orchestrator.persist_active_portfolio(portfolio, user_id=user_id)
    stress = orchestrator.quant_engine.analyze_portfolio(portfolio)
    
    return {
        "status": "success",
        "message": f"Sold {ticker}. Credited ${liquidated_val:,.2f} to cash reserve.",
        "portfolio": dumped,
        "stress": stress.model_dump(mode="json")
    }



@app.post("/api/scan")
async def api_trigger_scan(request: Request, force_fresh: bool = False):
    user = get_current_user(request)
    user_id = user["id"] if user else None
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
async def api_scan_stream(request: Request, force_fresh: bool = False):
    user = get_current_user(request)
    user_id = user["id"] if user else None
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

        scan_task = asyncio.create_task(run_scan())

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
async def api_get_live_news(request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    portfolio_tickers = [h.ticker for h in portfolio.holdings]
    news_items = orchestrator.news_agent.ingest_all_feeds(live=True, portfolio_tickers=portfolio_tickers)
    return {
        "status": "success",
        "count": len(news_items),
        "news": [n.model_dump(mode="json") for n in news_items]
    }


@app.get("/api/quote/{ticker}")
async def api_get_quote(ticker: str):
    from analytics.market_data import fetch_live_quote
    quote = fetch_live_quote(ticker)
    return quote


@app.get("/api/quotes/refresh")
async def api_refresh_quotes(request: Request):

    user = get_current_user(request)
    user_id = user["id"] if user else None
    from analytics.market_data import update_portfolio_live_prices
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    portfolio, quotes = update_portfolio_live_prices(portfolio)
    dumped = orchestrator.persist_active_portfolio(portfolio, user_id=user_id)
    stress = orchestrator.quant_engine.analyze_portfolio(portfolio)
    return {
        "status": "success",
        "quotes": quotes,
        "portfolio": dumped,
        "stress": stress.model_dump(mode="json")
    }


@app.get("/api/earnings/calendar")
async def api_get_earnings_calendar(request: Request):
    from analytics.earnings_calendar import fetch_7day_earnings_schedule
    user = get_current_user(request)
    user_id = user["id"] if user else None
    portfolio = orchestrator.get_active_portfolio(user_id=user_id)
    portfolio_tickers = [h.ticker for h in portfolio.holdings]
    schedule = fetch_7day_earnings_schedule(portfolio_tickers=portfolio_tickers)
    return {
        "status": "success",
        "schedule": schedule
    }


@app.post("/api/analyze/{ticker}")
async def api_analyze_ticker(ticker: str, request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
    user_key = orchestrator.resolve_user_api_key(user_id)

    if not user_key:
        raise HTTPException(
            status_code=400,
            detail="Gemini API Key Required: Please configure GEMINI_API_KEY on the server or add your personal Gemini API key in Dashboard Settings."
        )


    target_ticker = ticker.strip().upper().replace("$", "")
    from analytics.market_data import fetch_live_quote
    from analytics.technical_indicators import compute_technical_snapshot
    from analytics.sentiment_stream import fetch_social_sentiment_snapshot
    from dataclasses import asdict

    # Concurrently gather live quote, technical momentum, targeted news feeds, sentiment snapshot, and active portfolio
    quote_task = asyncio.to_thread(fetch_live_quote, target_ticker)
    tech_task = asyncio.to_thread(compute_technical_snapshot, target_ticker)
    news_task = asyncio.to_thread(
        orchestrator.news_agent.ingest_all_feeds,
        live=True,
        portfolio_tickers=[target_ticker],
        api_key=user_key
    )
    sent_task = asyncio.to_thread(fetch_social_sentiment_snapshot, target_ticker)
    portfolio_task = asyncio.to_thread(orchestrator.get_active_portfolio, user_id=user_id)

    quote, tech_snap, news_items, sent_snap, portfolio = await asyncio.gather(
        quote_task, tech_task, news_task, sent_task, portfolio_task
    )
    # Dynamically calibrate sentiment volume weighting with live RVOL if available
    if tech_snap and tech_snap.is_live and tech_snap.rvol and sent_snap:
        sent_snap.relative_volume = tech_snap.rvol
    
    analysis_text = await asyncio.to_thread(
        orchestrator.analysis_agent.analyze_single_ticker,
        ticker=target_ticker,
        portfolio=portfolio,
        news_items=news_items,
        quote_data=quote,
        technical_snapshot=tech_snap,
        sentiment_snapshot=sent_snap,
        api_key=user_key
    )
    # Extract headline metadata for persistent archive repository
    current_price = 0.0
    if quote and quote.get("current_price"):
        current_price = float(quote.get("current_price") or 0.0)
    elif tech_snap and getattr(tech_snap, "current_price", None):
        current_price = float(tech_snap.current_price or 0.0)
    
    company_name = (quote.get("name") if quote else None) or target_ticker

    # Extract stance / verdict
    upper_analysis = analysis_text.upper() if analysis_text else ""
    verdict = "NEUTRAL"
    if "STRONG BUY" in upper_analysis or "BULLISH" in upper_analysis or "ACCUMULATE" in upper_analysis:
        verdict = "BULLISH"
    elif "STRONG SELL" in upper_analysis or "BEARISH" in upper_analysis or "TRIM" in upper_analysis or "LIQUIDATE" in upper_analysis:
        verdict = "BEARISH"
    elif "CAUTION" in upper_analysis or "HIGH RISK" in upper_analysis:
        verdict = "CAUTION"
    elif "HOLD" in upper_analysis:
        verdict = "HOLD"

    conviction_score = 85.0
    if tech_snap and tech_snap.is_live:
        if (tech_snap.rsi_14 <= 35 and verdict == "BULLISH") or (tech_snap.rsi_14 >= 75 and verdict == "BEARISH"):
            conviction_score = 92.0

    effective_user_id = user_id
    if not effective_user_id:
        admin = orchestrator.state_store.get_or_create_default_admin()
        effective_user_id = admin["id"] if admin else "usr_admin"

    deepdive_payload = {
        "status": "success",
        "ticker": target_ticker,
        "quote": quote,
        "technicals": asdict(tech_snap) if tech_snap else None,
        "sentiment": asdict(sent_snap) if sent_snap else None,
        "analysis": analysis_text,
        "verdict": verdict,
        "conviction_score": conviction_score
    }

    try:
        dd_id = orchestrator.state_store.save_deepdive(
            user_id=effective_user_id,
            ticker=target_ticker,
            company_name=company_name,
            current_price=current_price,
            verdict=verdict,
            conviction_score=conviction_score,
            technicals=asdict(tech_snap) if tech_snap else None,
            sentiment=asdict(sent_snap) if sent_snap else None,
            analysis_text=analysis_text,
            payload_json=deepdive_payload
        )
        deepdive_payload["deepdive_id"] = dd_id
    except Exception as save_err:
        logger.warning(f"Failed to auto-archive deep dive for {target_ticker}: {save_err}")

    return deepdive_payload


@app.get("/api/deepdives")
async def api_get_user_deepdives(request: Request, limit: int = 50, ticker: Optional[str] = None):
    user = get_current_user(request)
    user_id = user["id"] if user else None
    if not user_id:
        admin = orchestrator.state_store.get_or_create_default_admin()
        user_id = admin["id"] if admin else "usr_admin"
    deepdives = orchestrator.state_store.get_user_deepdives(user_id=user_id, limit=limit, ticker=ticker)
    return {"status": "success", "deepdives": deepdives}


@app.get("/api/deepdives/{deepdive_id}")
async def api_get_deepdive_detail(deepdive_id: str, request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
    if not user_id:
        admin = orchestrator.state_store.get_or_create_default_admin()
        user_id = admin["id"] if admin else "usr_admin"
    is_admin = bool(user and user.get("role") == "admin")

    dd = orchestrator.state_store.get_deepdive_by_id(deepdive_id, user_id=user_id if not is_admin else None)
    if not dd:
        # Check if record exists under another tenant to return 403 Forbidden instead of 404
        existing = orchestrator.state_store.get_deepdive_by_id(deepdive_id)
        if existing and not is_admin:
            raise HTTPException(status_code=403, detail="Forbidden: You do not have permission to access this archived deep dive.")
        raise HTTPException(status_code=404, detail="Archived deep dive not found.")
    return {"status": "success", "deepdive": dd}


@app.delete("/api/deepdives/{deepdive_id}")
async def api_delete_deepdive(deepdive_id: str, request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
    if not user_id:
        admin = orchestrator.state_store.get_or_create_default_admin()
        user_id = admin["id"] if admin else "usr_admin"
    is_admin = bool(user and user.get("role") == "admin")

    existing = orchestrator.state_store.get_deepdive_by_id(deepdive_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Deep dive record not found.")
    if not is_admin and existing.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You do not have permission to delete this deep dive.")

    deleted = orchestrator.state_store.delete_deepdive(deepdive_id, user_id=user_id, is_admin=is_admin)
    if not deleted:
        raise HTTPException(status_code=404, detail="Deep dive record not found or could not be deleted.")
    return {"status": "success", "message": "Archived deep dive deleted successfully."}



@app.post("/api/feedback")
async def api_submit_feedback(req: FeedbackRequest):
    orchestrator.state_store.record_feedback(
        target_id=req.target_id,
        feedback_type=req.feedback_type,
        user_notes=req.user_notes
    )
    return {"status": "success", "message": "Feedback recorded"}


class ChatMessageRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []


@app.post("/api/chat")
async def api_chat(req: ChatMessageRequest, request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
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
async def api_discover_opportunities(req: DiscoverOpportunitiesRequest, request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
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
async def api_discover_moonshots(request: Request, count: int = 4):
    user = get_current_user(request)
    user_id = user["id"] if user else None
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
    try:
        data = await request.json()
        return telegram_bot.process_webhook_update(data)
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/api/schedule/status")
async def api_get_schedule_status():
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
            "15:00 PST": "Post-Market Wrap, Earnings & Hot Movers"
        },
        "weekend_schedule": {
            "21:00 PST": "Weekend Macro & Week-Ahead Preview"
        },
        "scheduler_running": daily_scheduler.is_running
    }


@app.post("/api/schedule/trigger/{slot}")
async def api_trigger_scheduled_briefing(slot: str, force: bool = False):
    if slot not in ("premarket", "midmarket", "postmarket", "weekend", "earnings"):
        raise HTTPException(status_code=400, detail="Invalid slot. Choose premarket, midmarket, postmarket, weekend, or earnings.")
    import asyncio
    msg = await asyncio.to_thread(daily_scheduler.execute_briefing, slot, None, None, True, force)
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
async def api_get_market_briefings(request: Request, slot: Optional[str] = None, limit: int = 20):
    user = get_current_user(request)
    user_id = user["id"] if user else None
    if not user_id:
        admin = orchestrator.state_store.get_or_create_default_admin()
        if admin:
            user_id = admin["id"]
    briefings = orchestrator.state_store.get_market_briefings(user_id=user_id, slot=slot, limit=limit)
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
async def api_get_market_briefing_detail(report_id: str, request: Request):
    b = orchestrator.state_store.get_market_briefing_by_id(report_id)
    if not b:
        raise HTTPException(status_code=404, detail="Briefing report not found.")

    user = get_current_user(request)
    user_id = user["id"] if user else None
    is_admin = bool(user and user.get("role") == "admin")

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
async def api_generate_market_briefing(payload: GenerateBriefingPayload, request: Request):
    slot = payload.slot.lower().strip()
    if slot not in SLOT_METADATA:
        raise HTTPException(status_code=400, detail=f"Invalid briefing slot. Choose one of: {list(SLOT_METADATA.keys())}")
    
    user = get_current_user(request)
    user_id = user["id"] if user else None
    if not user_id:
        admin = orchestrator.state_store.get_or_create_default_admin()
        if admin:
            user_id = admin["id"]
    
    target_chat = None
    if payload.dispatch_telegram:
        if user and user.get("telegram_chat_id"):
            target_chat = user["telegram_chat_id"]
        else:
            target_chat = telegram_bot.get_effective_chat_id() or config.telegram_chat_id

    msg = await asyncio.to_thread(
        daily_scheduler.execute_briefing,
        slot,
        target_chat if payload.dispatch_telegram else None,
        user_id,
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
async def api_dispatch_market_briefing(report_id: str, request: Request):
    b = orchestrator.state_store.get_market_briefing_by_id(report_id)
    if not b:
        raise HTTPException(status_code=404, detail="Briefing report not found.")
    
    user = get_current_user(request)
    user_id = user["id"] if user else None
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
async def api_prune_market_briefings(request: Request, retention_days: int = 30):
    user = get_current_user(request)
    if not user and config.dashboard_auth_enabled:
        raise HTTPException(status_code=401, detail="Unauthorized: Please log in.")
    if user and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: Admin privileges required to prune briefing archives.")

    pruned_count = orchestrator.state_store.prune_briefings(retention_days=retention_days)
    return {
        "status": "success",
        "message": f"Successfully pruned {pruned_count} duplicate and expired briefing entries.",
        "pruned_count": pruned_count
    }


class TelegramConfigPayload(BaseModel):
    bot_token: str
    chat_id: Optional[str] = None


@app.post("/api/telegram/configure")
async def api_configure_telegram(payload: TelegramConfigPayload):
    config.telegram_bot_token = payload.bot_token
    if payload.chat_id:
        config.telegram_chat_id = payload.chat_id
    
    telegram_bot.bot_token = payload.bot_token
    telegram_bot.chat_id = payload.chat_id or telegram_bot.chat_id
    telegram_bot.start_polling()
    return {"status": "configured", "polling": telegram_bot.is_running}


@app.post("/api/cache/clear")
async def api_cache_clear(request: Request):
    """Admin endpoint to invalidate in-memory quote, bars, and general caches."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
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
async def api_cache_stats(request: Request):
    """Admin endpoint to monitor cache telemetry."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    from analytics.market_data import PRICE_CACHE
    from analytics.technical_indicators import BARS_CACHE
    from storage.cache_manager import cache_manager
    return {
        "price_cache_entries": len(PRICE_CACHE),
        "bars_cache_entries": len(BARS_CACHE),
        "manager_stats": cache_manager.stats()
    }


if __name__ == "__main__":
    import uvicorn
    server_port = int(os.getenv("PORT", str(config.web_port)))
    print(f"🚀 Launching Financial Sentinel Dashboard on http://{config.web_host}:{server_port}")
    uvicorn.run(app, host=config.web_host, port=server_port)
