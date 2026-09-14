/**
 * portfolio.js - Portfolio State, Holdings Management, Quant Stress Matrix, and Settings
 * Part of Financial Sentinel Modular Architecture
 */

let activePortfolioData = null;

// Sector Color Mapping
const SECTOR_COLORS = {
    'Technology': { bg: 'bg-blue-500', text: 'text-blue-400', hex: '#3b82f6' },
    'Semiconductors': { bg: 'bg-cyan-500', text: 'text-cyan-400', hex: '#06b6d4' },
    'Financials': { bg: 'bg-indigo-500', text: 'text-indigo-400', hex: '#6366f1' },
    'Healthcare': { bg: 'bg-emerald-500', text: 'text-emerald-400', hex: '#10b981' },
    'Energy': { bg: 'bg-amber-500', text: 'text-amber-400', hex: '#f59e0b' },
    'Consumer Discretionary': { bg: 'bg-pink-500', text: 'text-pink-400', hex: '#ec4899' },
    'Consumer Staples': { bg: 'bg-lime-500', text: 'text-lime-400', hex: '#84cc16' },
    'Utilities': { bg: 'bg-teal-500', text: 'text-teal-400', hex: '#14b8a6' },
    'Industrials': { bg: 'bg-orange-500', text: 'text-orange-400', hex: '#f97316' },
    'Real Estate': { bg: 'bg-rose-500', text: 'text-rose-400', hex: '#f43f5e' },
    'Cash Reserves': { bg: 'bg-emerald-400', text: 'text-emerald-300', hex: '#34d399' },
    'Other': { bg: 'bg-slate-500', text: 'text-slate-400', hex: '#64748b' }
};

let _portfolioCache = null;
let _portfolioCacheTime = 0;
const PORTFOLIO_CACHE_TTL_MS = 30000; // 30-second TTL for instant tab switching

function invalidatePortfolioCache() {
    _portfolioCache = null;
    _portfolioCacheTime = 0;
}

/**
 * Fetch portfolio data from backend API and populate UI components.
 * Implements Stale-While-Revalidate (SWR) caching for instant 0ms transitions between tabs.
 */
async function fetchPortfolio(forceFresh = false) {
    const now = Date.now();
    if (!forceFresh && _portfolioCache && (now - _portfolioCacheTime < PORTFOLIO_CACHE_TTL_MS)) {
        activePortfolioData = _portfolioCache.portfolio;
        if (_portfolioCache.stress) {
            renderStressMatrix(_portfolioCache.stress);
        }
        if (_portfolioCache.briefing && typeof renderBriefing === 'function') {
            renderBriefing(_portfolioCache.briefing);
        }
        return _portfolioCache.portfolio;
    }

    try {
        const res = await fetch('/api/portfolio');
        const data = await res.json();
        _portfolioCache = data;
        _portfolioCacheTime = Date.now();
        activePortfolioData = data.portfolio;
        if (data.stress) {
            renderStressMatrix(data.stress);
        }
        if (data.briefing && typeof renderBriefing === 'function') {
            renderBriefing(data.briefing);
        }
        return data.portfolio;
    } catch (err) {
        console.error("Failed to load portfolio", err);
        return null;
    }
}

/**
 * Search filter for the Monitored Holdings table.
 */
function filterHoldingsTable() {
    const query = (document.getElementById('holdings-search-input')?.value || '').trim().toLowerCase();
    const rows = document.querySelectorAll('#dashboard-holdings-tbody tr');
    rows.forEach(tr => {
        const text = tr.textContent.toLowerCase();
        tr.style.display = text.includes(query) ? '' : 'none';
    });
}

/**
 * Render the consolidated 5-column Monitored Holdings table and sync all HUD instances.
 */
function renderHoldingsTable(p) {
    if (!p || !p.holdings) return;
    activePortfolioData = p;
    
    const cashVal = p.cash || 0.0;
    let holdingsEquity = 0;
    let totalCost = 0;
    p.holdings.forEach(h => {
        const cur = h.current_price || h.avg_price || 0;
        holdingsEquity += (h.shares * cur);
        totalCost += (h.shares * (h.avg_price || 0));
    });
    const totalWealth = cashVal + holdingsEquity;
    const totalPnL = holdingsEquity - totalCost;
    const totalPnLPct = totalCost > 0 ? (totalPnL / totalCost) * 100 : 0;
    const isTotalPos = totalPnL >= 0;

    const formattedWealth = '$' + totalWealth.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const formattedEquity = '$' + holdingsEquity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const formattedCash = '$' + cashVal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const cashPct = totalWealth > 0 ? ((cashVal / totalWealth) * 100).toFixed(1) : '0.0';
    const formattedPnL = `${isTotalPos ? '+' : ''}$${totalPnL.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const formattedPnLPct = `${isTotalPos ? '+' : ''}${totalPnLPct.toFixed(1)}%`;

    // 1. Update Full HUD Elements
    const elWealth = document.getElementById('hud-total-wealth');
    if (elWealth) elWealth.textContent = formattedWealth;

    const elEquity = document.getElementById('hud-total-equity');
    if (elEquity) elEquity.textContent = formattedEquity;

    const elHoldingsCount = document.getElementById('hud-holdings-count');
    if (elHoldingsCount) elHoldingsCount.textContent = p.holdings.length + ' Active Positions';

    const elCash = document.getElementById('hud-cash-reserves');
    if (elCash) elCash.textContent = formattedCash;

    const elCashPct = document.getElementById('hud-cash-pct');
    if (elCashPct) elCashPct.textContent = cashPct + '% Dry Powder';

    const elPnL = document.getElementById('hud-unrealized-pnl');
    if (elPnL) {
        elPnL.textContent = formattedPnL;
        elPnL.className = `text-sm sm:text-base font-bold ${isTotalPos ? 'text-emerald-400' : 'text-rose-400'} mt-0.5`;
    }

    const elPnLPct = document.getElementById('hud-unrealized-pct');
    if (elPnLPct) {
        elPnLPct.textContent = `${formattedPnLPct} Return`;
        elPnLPct.className = `text-[10px] ${isTotalPos ? 'text-emerald-500/80' : 'text-rose-500/80'} font-sans truncate`;
    }

    const cntElem = document.getElementById('portfolio-holdings-count');
    if (cntElem) cntElem.textContent = p.holdings.length + ' Assets';

    // 2. Update Compact 1-Line HUD Elements
    const compWealth = document.getElementById('compact-hud-total-wealth');
    if (compWealth) compWealth.textContent = formattedWealth;

    const compEquity = document.getElementById('compact-hud-total-equity');
    if (compEquity) compEquity.textContent = formattedEquity;

    const compCash = document.getElementById('compact-hud-cash-reserves');
    if (compCash) compCash.textContent = formattedCash;

    const compCashPct = document.getElementById('compact-hud-cash-pct');
    if (compCashPct) compCashPct.textContent = cashPct + '%';

    const compPnL = document.getElementById('compact-hud-unrealized-pnl');
    if (compPnL) {
        compPnL.textContent = `${formattedPnL} (${formattedPnLPct})`;
        compPnL.className = `font-bold ${isTotalPos ? 'text-emerald-400' : 'text-rose-400'}`;
    }

    // 3. Render Consolidated 5-Column Table Body
    const tbody = document.getElementById('dashboard-holdings-tbody');
    if (!tbody) return;

    tbody.innerHTML = p.holdings.map(h => {
        const curPrice = h.current_price || h.avg_price || 0;
        const mv = curPrice * h.shares;
        const pnl = (curPrice - h.avg_price) * h.shares;
        const pnlPct = h.avg_price > 0 ? ((curPrice - h.avg_price) / h.avg_price) * 100 : 0;
        const isPos = pnl >= 0;
        const jsSafeTicker = encodeURIComponent(h.ticker || '');
        const priceDisplay = curPrice > 0
            ? raw(`$${curPrice.toFixed(2)}`)
            : raw('<span class="text-amber-400 text-xs">Fetching...</span>');

        return html`
            <tr class="hover:bg-slate-800/60 transition cursor-pointer group" onclick="analyzeHoldingTicker('${jsSafeTicker}')" title="Click to select ${h.ticker} in Ticker Deep Dive">
                <td class="py-3 font-bold">
                    <div class="flex items-center space-x-2">
                        <span class="text-cyan-400 group-hover:text-cyan-300 font-bold tracking-wide">${h.ticker}</span>
                        <span class="px-1.5 py-0.5 bg-slate-800 text-slate-300 rounded text-[10px] font-sans font-normal border border-slate-700/50">${h.sector || 'Technology'}</span>
                        <span class="text-[10px] text-cyan-400 opacity-0 group-hover:opacity-100 transition font-sans font-medium">🔬 Deep Dive →</span>
                    </div>
                    <div class="text-[10px] text-slate-400 font-normal font-sans truncate max-w-[200px] mt-0.5">${h.name || h.ticker}</div>
                </td>
                <td class="py-3 text-right">
                    <div class="text-slate-200 font-bold font-mono">${h.shares.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 4 })}</div>
                    <div class="text-[10px] text-slate-400 font-mono">@ $${(h.avg_price || 0).toFixed(2)}</div>
                </td>
                <td class="py-3 text-right">
                    <div class="font-bold text-slate-100 font-mono">
                        ${priceDisplay}
                    </div>
                    <div class="text-[10px] ${isPos ? 'text-emerald-400' : 'text-rose-400'} font-mono">
                        ${curPrice > 0 ? (isPos ? '+' : '') + pnlPct.toFixed(1) + '%' : 'Live'}
                    </div>
                </td>
                <td class="py-3 text-right">
                    <div class="font-bold text-slate-200 font-mono">
                        ${curPrice > 0 ? '$' + mv.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '$0.00'}
                    </div>
                    <div class="text-[10px] font-bold text-blue-400 font-mono">${h.weight_pct || 0}% weight</div>
                </td>
                <td class="py-3 text-right">
                    <div class="font-bold ${isPos ? 'text-emerald-400' : 'text-rose-400'} font-mono">
                        ${curPrice > 0 ? (isPos ? '+' : '') + '$' + pnl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : 'N/A'}
                    </div>
                    <div class="text-[10px] ${isPos ? 'text-emerald-500/80' : 'text-rose-500/80'} font-mono">
                        ${curPrice > 0 ? (isPos ? '+' : '') + pnlPct.toFixed(1) + '% return' : ''}
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    renderSectorDistribution(p);
}

/**
 * Render visual sector progress bar and legend chips.
 */
function renderSectorDistribution(p) {
    if (!p) return;
    const bar = document.getElementById('sector-progress-bar');
    const chips = document.getElementById('sector-legend-chips');
    if (!bar || !chips) return;

    const cashVal = p.cash || 0.0;
    let holdingsEquity = 0;
    const sectorTotals = {};

    p.holdings.forEach(h => {
        const mv = h.shares * (h.current_price || h.avg_price || 0);
        holdingsEquity += mv;
        const sec = h.sector || 'Technology';
        sectorTotals[sec] = (sectorTotals[sec] || 0) + mv;
    });

    if (cashVal > 0) {
        sectorTotals['Cash Reserves'] = cashVal;
    }

    const totalWealth = holdingsEquity + cashVal;
    if (totalWealth <= 0) return;

    const sortedSectors = Object.entries(sectorTotals).sort((a, b) => b[1] - a[1]);

    // Render Segmented Bar
    bar.innerHTML = sortedSectors.map(([sec, val]) => {
        const pct = ((val / totalWealth) * 100).toFixed(1);
        const colorObj = SECTOR_COLORS[sec] || SECTOR_COLORS['Other'];
        const safeSec = (window.esc ? window.esc(sec) : sec);
        return `
            <div style="width: ${pct}%;" class="${colorObj.bg} h-full transition-all" title="${safeSec}: ${pct}% ($${Math.round(val).toLocaleString()})"></div>
        `;
    }).join('');

    // Render Legend Chips
    chips.innerHTML = sortedSectors.map(([sec, val]) => {
        const pct = ((val / totalWealth) * 100).toFixed(1);
        const colorObj = SECTOR_COLORS[sec] || SECTOR_COLORS['Other'];
        const safeSec = (window.esc ? window.esc(sec) : sec);
        return `
            <div class="flex items-center space-x-1.5 px-2.5 py-1 bg-dark-950 border border-slate-800 rounded-lg">
                <span class="w-2.5 h-2.5 rounded-full ${colorObj.bg}"></span>
                <span class="text-slate-300 font-sans">${safeSec}:</span>
                <span class="${colorObj.text} font-bold">${pct}%</span>
                <span class="text-[10px] text-slate-500 font-sans">($${Math.round(val).toLocaleString()})</span>
            </div>
        `;
    }).join('');
}

/**
 * Render quantitative risk model results and sync beta and VaR to HUDs.
 */
function renderStressMatrix(s) {
    if (!s) return;
    const betaVal = s.estimated_portfolio_beta != null ? s.estimated_portfolio_beta : 1.0;
    const varPct = s.var_95_daily_pct != null ? s.var_95_daily_pct : 1.8;
    const varUsd = s.var_95_daily_usd || 0;

    // Beta updates
    if (document.getElementById('val-beta')) document.getElementById('val-beta').textContent = betaVal;
    if (document.getElementById('hud-beta')) document.getElementById('hud-beta').textContent = betaVal;
    if (document.getElementById('compact-hud-beta')) document.getElementById('compact-hud-beta').textContent = betaVal;

    // VaR updates
    if (document.getElementById('val-var')) {
        const usdStr = varUsd ? `($${Math.round(varUsd).toLocaleString()})` : '';
        document.getElementById('val-var').innerHTML = `-${varPct}% <span id="val-var-usd" class="text-[10px] text-slate-400 font-normal">${usdStr}</span>`;
    }
    if (document.getElementById('hud-var')) document.getElementById('hud-var').textContent = `-${varPct}%`;
    if (document.getElementById('compact-hud-var')) document.getElementById('compact-hud-var').textContent = `-${varPct}%`;
    if (document.getElementById('hud-var-usd')) document.getElementById('hud-var-usd').textContent = `-$${Math.round(varUsd).toLocaleString()} Bad Day Limit`;

    // Additional risk metrics
    if (document.getElementById('val-vol')) document.getElementById('val-vol').textContent = (s.annualized_volatility_pct != null ? s.annualized_volatility_pct : 0) + '%';
    if (document.getElementById('val-sharpe')) document.getElementById('val-sharpe').textContent = (s.sharpe_ratio != null ? s.sharpe_ratio : '—');
    if (document.getElementById('val-cash-alloc')) document.getElementById('val-cash-alloc').textContent = (s.cash_allocation_pct || 0) + '%';

    if (document.getElementById('val-top3')) {
        document.getElementById('val-top3').textContent = (s.top_3_concentration_pct || 0) + '%';
        document.getElementById('val-top3').className = `font-mono font-bold ${(s.top_3_concentration_pct >= 60) ? 'text-amber-400' : 'text-emerald-400'}`;
    }

    // Stress scenarios
    const scenList = document.getElementById('stress-scenarios-list');
    if (scenList && s.macro_shock_scenarios) {
        scenList.innerHTML = Object.entries(s.macro_shock_scenarios).map(([scen, val]) => {
            const isPos = val >= 0;
            const safeScen = (window.esc ? window.esc(scen) : scen);
            return `
                <div class="flex justify-between items-center py-1 border-b border-slate-900/60 last:border-0">
                    <span class="text-slate-400">${safeScen}</span>
                    <span class="${isPos ? 'text-emerald-400' : 'text-rose-400'} font-semibold">
                        ${isPos ? '+' : ''}${val}%
                    </span>
                </div>
            `;
        }).join('');
    }
}


/**
 * Concurrent live price sync across all portfolio holdings.
 */
async function refreshLiveQuotes() {
    const btn = document.getElementById('btn-refresh-quotes');
    if (btn) btn.innerHTML = '<span class="pulse-subtle">🔄 Syncing...</span>';
    
    try {
        const res = await fetch('/api/portfolio/refresh-quotes', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.portfolio) {
            renderHoldingsTable(data.portfolio);
            if (data.stress) renderStressMatrix(data.stress);
            if (data.failed_tickers && data.failed_tickers.length > 0) {
                showBanner(`⚠️ Could not fetch live quotes for: ${data.failed_tickers.join(', ')}. Please check ticker symbol.`, true);
            } else {
                showBanner(`✅ Synced live market prices for ${data.portfolio.holdings.length} holdings.`);
            }
        }
    } catch (err) {
        console.error("Failed to refresh quotes:", err);
        showBanner(`⚠️ Network error while syncing live quotes: ${err.message}`, true);
    } finally {
        if (btn) btn.innerHTML = '<span>🔄 Sync Quotes</span>';
    }
}

/**
 * Holdings Management Modal Handlers
 */
async function openHoldingsModal() {
    const p = await fetchPortfolio();
    const tbody = document.getElementById('holdings-modal-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const cashInput = document.getElementById('modal-cash-input');
    if (cashInput) {
        cashInput.value = (p && p.cash !== undefined) ? p.cash : 0;
    }

    const holdings = (p && p.holdings) ? p.holdings : [];
    holdings.forEach(h => addHoldingRow(h));
    if (holdings.length === 0) {
        addHoldingRow();
    }

    document.getElementById('holdings-modal').classList.remove('hidden');
}

async function openAddHoldingModal() {
    await openHoldingsModal();
    addHoldingRow();
    setTimeout(() => {
        const tbody = document.getElementById('holdings-modal-tbody');
        const rows = tbody.querySelectorAll('tr');
        if (rows.length > 0) {
            const newRow = rows[rows.length - 1];
            newRow.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            const tickerInput = newRow.querySelector('.input-ticker');
            if (tickerInput) {
                tickerInput.focus();
                tickerInput.classList.add('ring-2', 'ring-emerald-500');
                setTimeout(() => tickerInput.classList.remove('ring-2', 'ring-emerald-500'), 1500);
            }
        }
    }, 100);
}

function closeHoldingsModal() {
    document.getElementById('holdings-modal').classList.add('hidden');
}

let quoteDebounceTimer = null;

function debounceFetchQuote(inputElem) {
    clearTimeout(quoteDebounceTimer);
    quoteDebounceTimer = setTimeout(() => {
        autoFetchQuote(inputElem);
    }, 300);
}

async function autoFetchQuote(inputElem) {
    const ticker = inputElem.value.trim().toUpperCase();
    if (!ticker || ticker.length < 1) return;
    const tr = inputElem.closest('tr');
    const nameInput = tr.querySelector('.input-name');
    const priceInput = tr.querySelector('.input-price');
    const sectorSelect = tr.querySelector('.input-sector');
    
    try {
        const res = await fetch(`/api/quote/${ticker}`);
        const data = await res.json();
        if (data) {
            if (data.name && (!nameInput.value || nameInput.value === ticker || nameInput.value.length <= 4)) {
                nameInput.value = data.name;
            }
            if (data.sector && sectorSelect) {
                sectorSelect.value = data.sector;
            }
            if (data.current_price > 0) {
                priceInput.value = data.current_price;
                priceInput.classList.add('text-emerald-400');
                setTimeout(() => priceInput.classList.remove('text-emerald-400'), 1000);
            }
        }
    } catch (e) {
        console.warn('Quote lookup error:', e);
    }
}

function addHoldingRow(h = {}) {
    const tbody = document.getElementById('holdings-modal-tbody');
    if (!tbody) return;
    const tr = document.createElement('tr');
    tr.className = 'hover:bg-slate-800/40 transition';
    tr.innerHTML = `
        <td class="py-2 pr-2">
            <input type="text" oninput="debounceFetchQuote(this)" onblur="autoFetchQuote(this)" class="input-ticker w-20 px-2 py-1 bg-dark-950 border border-slate-700 rounded text-cyan-400 font-bold text-xs uppercase" value="${escapeHtml(h.ticker || '')}" placeholder="NVDA">
        </td>
        <td class="py-2 pr-2">
            <input type="text" class="input-name w-36 px-2 py-1 bg-dark-950 border border-slate-700 rounded text-slate-200 text-xs" value="${escapeHtml(h.name || '')}" placeholder="NVIDIA Corp">
        </td>
        <td class="py-2 pr-2">
            <select class="input-sector px-2 py-1 bg-dark-950 border border-slate-700 rounded text-slate-200 text-xs">
                <option value="Technology" ${h.sector === 'Technology' ? 'selected' : ''}>Technology</option>
                <option value="Semiconductors" ${h.sector === 'Semiconductors' ? 'selected' : ''}>Semiconductors</option>
                <option value="Energy" ${h.sector === 'Energy' ? 'selected' : ''}>Energy</option>
                <option value="Healthcare" ${h.sector === 'Healthcare' ? 'selected' : ''}>Healthcare</option>
                <option value="Financials" ${h.sector === 'Financials' ? 'selected' : ''}>Financials</option>
                <option value="Consumer Discretionary" ${h.sector === 'Consumer Discretionary' ? 'selected' : ''}>Consumer Discretionary</option>
                <option value="Utilities" ${h.sector === 'Utilities' ? 'selected' : ''}>Utilities</option>
                <option value="Industrials" ${h.sector === 'Industrials' ? 'selected' : ''}>Industrials</option>
                <option value="Real Estate" ${h.sector === 'Real Estate' ? 'selected' : ''}>Real Estate</option>
            </select>
        </td>
        <td class="py-2 pr-2 text-right">
            <input type="number" step="any" class="input-shares w-20 px-2 py-1 bg-dark-950 border border-slate-700 rounded text-slate-200 text-xs text-right font-mono" value="${h.shares || ''}" placeholder="50">
        </td>
        <td class="py-2 pr-2 text-right">
            <input type="number" step="any" class="input-avg-price w-24 px-2 py-1 bg-dark-950 border border-slate-700 rounded text-slate-200 text-xs text-right font-mono" value="${h.avg_price || ''}" placeholder="120.00">
        </td>
        <td class="py-2 pr-2 text-right">
            <input type="number" step="any" class="input-price w-24 px-2 py-1 bg-dark-950 border border-slate-700 rounded text-slate-200 text-xs text-right font-mono" value="${h.current_price || ''}" placeholder="Live Price">
        </td>
        <td class="py-2 text-center">
            <button onclick="this.closest('tr').remove()" class="p-1 hover:text-rose-400 text-slate-500 transition text-sm" title="Remove">🗑️</button>
        </td>
    `;
    tbody.appendChild(tr);
}

async function saveHoldingsModal() {
    const tbody = document.getElementById('holdings-modal-tbody');
    if (!tbody) return;
    const rows = tbody.querySelectorAll('tr');
    const newHoldings = [];

    rows.forEach(r => {
        const tickerInput = r.querySelector('.input-ticker');
        const nameInput = r.querySelector('.input-name');
        const sectorSelect = r.querySelector('.input-sector');
        const sharesInput = r.querySelector('.input-shares');
        const avgPriceInput = r.querySelector('.input-avg-price');
        const curPriceInput = r.querySelector('.input-price');

        if (!tickerInput || !sharesInput) return;

        const ticker = (tickerInput.value || '').trim().toUpperCase();
        const name = (nameInput && nameInput.value ? nameInput.value.trim() : '') || ticker;
        const sector = (sectorSelect ? sectorSelect.value : 'Technology') || 'Technology';
        const shares = parseFloat(sharesInput.value) || 0;
        const avg_price = parseFloat(avgPriceInput ? avgPriceInput.value : 0) || 0;
        const current_price = parseFloat(curPriceInput ? curPriceInput.value : 0) || avg_price;

        if (ticker && shares > 0) {
            newHoldings.push({
                ticker: ticker,
                name: name,
                sector: sector,
                shares: shares,
                avg_price: avg_price,
                current_price: current_price,
                thematic_tags: []
            });
        }
    });

    if (newHoldings.length === 0) {
        alert('Please enter at least one valid stock position with shares > 0.');
        return;
    }

    try {
        const cashInput = document.getElementById('modal-cash-input');
        const cashVal = cashInput ? (parseFloat(cashInput.value) || 0.0) : 0.0;

        const payload = {
            name: "Custom Managed Portfolio",
            cash: cashVal,
            holdings: newHoldings
        };

        const res = await fetch('/api/portfolio/save', {
            method: 'POST',
            headers: { 
                'Accept': 'application/json',
                'Content-Type': 'application/json' 
            },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const errData = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(errData.detail || 'Server rejected save request');
        }

        const result = await res.json();
        closeHoldingsModal();
        invalidatePortfolioCache();
        if (result.portfolio) {
            _portfolioCache = result;
            _portfolioCacheTime = Date.now();
            activePortfolioData = result.portfolio;
            renderHoldingsTable(result.portfolio);
        }
        if (result.stress) {
            renderStressMatrix(result.stress);
        }
        showBanner(`💾 Portfolio saved! Refreshed ${newHoldings.length} positions and $${cashVal.toLocaleString()} cash.`);
    } catch (err) {
        alert('Error saving portfolio: ' + (err.message || err));
    }
}

/**
 * Settings Modal & Security Configuration
 */
function openSettingsModal() {
    document.getElementById('settings-modal').classList.remove('hidden');
}

function closeSettingsModal() {
    document.getElementById('settings-modal').classList.add('hidden');
    const feedback = document.getElementById('key-test-feedback');
    if (feedback) feedback.classList.add('hidden');
    const statusMsg = document.getElementById('settings-status-msg');
    if (statusMsg) statusMsg.classList.add('hidden');
}

function togglePassVisibility(inputId) {
    const el = document.getElementById(inputId);
    if (el) el.type = el.type === 'password' ? 'text' : 'password';
}

async function testGeminiKey() {
    const keyEl = document.getElementById('settings-gemini-key');
    const btn = document.getElementById('btn-test-key');
    const feedback = document.getElementById('key-test-feedback');
    const key = keyEl.value.trim();

    if (!key || key.includes('...')) {
        feedback.className = 'mt-1.5 text-[11px] text-amber-400 font-semibold';
        feedback.textContent = 'Please enter a complete API key to test.';
        feedback.classList.remove('hidden');
        return;
    }

    btn.disabled = true;
    btn.textContent = '...';
    feedback.classList.add('hidden');

    try {
        const res = await fetch('/api/user/verify-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: key })
        });
        const data = await res.json();
        feedback.classList.remove('hidden');
        if (data.valid) {
            feedback.className = 'mt-1.5 text-[11px] text-emerald-400 font-semibold';
            feedback.textContent = '✓ Valid Gemini API key (Google AI Studio confirmed)';
        } else {
            feedback.className = 'mt-1.5 text-[11px] text-rose-400 font-semibold';
            feedback.textContent = '❌ Key validation failed: ' + (data.error || 'Invalid API Key');
        }
    } catch (err) {
        feedback.className = 'mt-1.5 text-[11px] text-rose-400 font-semibold';
        feedback.textContent = '❌ Verification request error: ' + err.message;
        feedback.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Test';
    }
}

async function saveUserSettings() {
    const keyEl = document.getElementById('settings-gemini-key');
    const tgEl = document.getElementById('settings-telegram');
    const cashEl = document.getElementById('settings-cash');
    const newPassEl = document.getElementById('settings-new-password');
    const confirmPassEl = document.getElementById('settings-confirm-password');
    const btn = document.getElementById('btn-save-settings');
    const statusMsg = document.getElementById('settings-status-msg');

    const payload = {
        telegram_username: tgEl ? tgEl.value.trim() : undefined,
        cash: cashEl ? parseFloat(cashEl.value) : undefined
    };

    const rawKey = keyEl ? keyEl.value.trim() : '';
    if (rawKey && !rawKey.includes('...')) {
        payload.gemini_api_key = rawKey;
    }

    const newPass = newPassEl ? newPassEl.value.trim() : '';
    const confirmPass = confirmPassEl ? confirmPassEl.value.trim() : '';
    if (newPass) {
        if (newPass.length < 6) {
            statusMsg.className = 'p-3 bg-rose-950/60 border border-rose-800/60 rounded-xl text-xs text-rose-300 font-semibold text-center';
            statusMsg.textContent = '❌ New password must be at least 6 characters.';
            statusMsg.classList.remove('hidden');
            return;
        }
        if (newPass !== confirmPass) {
            statusMsg.className = 'p-3 bg-rose-950/60 border border-rose-800/60 rounded-xl text-xs text-rose-300 font-semibold text-center';
            statusMsg.textContent = '❌ New password and confirmation do not match.';
            statusMsg.classList.remove('hidden');
            return;
        }
        payload.new_password = newPass;
    }

    btn.disabled = true;
    btn.innerHTML = '<span>Saving...</span>';
    statusMsg.classList.add('hidden');

    try {
        const res = await fetch('/api/user/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (res.ok && data.status === 'success') {
            statusMsg.className = 'p-3 bg-emerald-950/60 border border-emerald-800/60 rounded-xl text-xs text-emerald-300 font-semibold text-center';
            statusMsg.textContent = '✓ Settings updated and Gemini key encrypted successfully!';
            statusMsg.classList.remove('hidden');
            setTimeout(() => {
                window.location.reload();
            }, 1000);
        } else {
            statusMsg.className = 'p-3 bg-rose-950/60 border border-rose-800/60 rounded-xl text-xs text-rose-300 font-semibold text-center';
            statusMsg.textContent = '❌ Failed to update settings: ' + (data.detail || 'Unknown error');
            statusMsg.classList.remove('hidden');
            btn.disabled = false;
            btn.innerHTML = '<span>Save Settings</span>';
        }
    } catch (err) {
        statusMsg.className = 'p-3 bg-rose-950/60 border border-rose-800/60 rounded-xl text-xs text-rose-300 font-semibold text-center';
        statusMsg.textContent = '❌ Network error: ' + err.message;
        statusMsg.classList.remove('hidden');
        btn.disabled = false;
        btn.innerHTML = '<span>Save Settings</span>';
    }
}

async function handleFileUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetch('/api/portfolio/upload', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            invalidatePortfolioCache();
            _portfolioCache = data;
            _portfolioCacheTime = Date.now();
            activePortfolioData = data.portfolio;
            showBanner(`✅ Portfolio loaded from ${file.name}: ${data.portfolio.holdings.length} assets.`);
            renderHoldingsTable(data.portfolio);
            if (data.stress) renderStressMatrix(data.stress);
        } else {
            alert('Upload failed: ' + (data.detail || 'Unknown error'));
        }
    } catch (err) {
        alert('Error uploading file: ' + err);
    }
}

// Global exports
window.activePortfolioData = activePortfolioData;
window.SECTOR_COLORS = SECTOR_COLORS;
window.fetchPortfolio = fetchPortfolio;
window.invalidatePortfolioCache = invalidatePortfolioCache;
window.filterHoldingsTable = filterHoldingsTable;
window.renderHoldingsTable = renderHoldingsTable;
window.renderSectorDistribution = renderSectorDistribution;
window.renderStressMatrix = renderStressMatrix;
window.refreshLiveQuotes = refreshLiveQuotes;
window.openHoldingsModal = openHoldingsModal;
window.openAddHoldingModal = openAddHoldingModal;
window.closeHoldingsModal = closeHoldingsModal;
window.debounceFetchQuote = debounceFetchQuote;
window.autoFetchQuote = autoFetchQuote;
window.addHoldingRow = addHoldingRow;
window.saveHoldingsModal = saveHoldingsModal;
window.openSettingsModal = openSettingsModal;
window.closeSettingsModal = closeSettingsModal;
window.togglePassVisibility = togglePassVisibility;
window.testGeminiKey = testGeminiKey;
window.saveUserSettings = saveUserSettings;
window.handleFileUpload = handleFileUpload;
