/**
 * deepdive.js - Single Ticker Institutional Deep Dive & Retail Sentiment Engine
 * Part of Financial Sentinel Modular Architecture
 */

let _currentDeepDive = null;
let _archivedDeepDives = [];

/**
 * Jump from holdings table row click into Ticker Deep Dive tab, prefill input, but DO NOT auto-run.
 * @param {string} sym Ticker symbol
 */
function analyzeHoldingTicker(sym) {
    if (!sym) return;
    const clean = sym.trim().toUpperCase().replace('$', '');
    
    // Switch to Deep Dive tab
    if (typeof switchTab === 'function') {
        switchTab('tab-deepdive', false);
    }
    
    // Populate input and focus, but DO NOT run automatically
    const input = document.getElementById('single-ticker-input');
    if (input) {
        input.value = clean;
        input.focus();
        input.select();
    }

    // Retain previous deep dive if already loaded, or restore last
    if (!_currentDeepDive) {
        restoreLastDeepDive();
    }
    
    // Show a helpful hint banner if banner function exists
    if (typeof showBanner === 'function') {
        showBanner(`🔬 Selected ${clean}. Press "Run Deep Dive" or hit Enter to analyze.`);
    }
}

/**
 * Select quick chip suggestion into input without auto-running.
 * @param {string} sym Ticker symbol
 */
function analyzeQuickTicker(sym) {
    const input = document.getElementById('single-ticker-input');
    if (input) {
        input.value = sym;
        input.focus();
        input.select();
    }
}

/**
 * Execute real-time single ticker institutional and retail crowd analysis.
 */
async function runSingleTickerAnalysis() {
    const input = document.getElementById('single-ticker-input');
    if (!input) return;
    const ticker = input.value.trim().toUpperCase().replace('$', '');
    if (!ticker) return;

    const btn = document.getElementById('btn-single-ticker');
    const resultCard = document.getElementById('single-ticker-result-card');
    const title = document.getElementById('st-result-title');
    const meta = document.getElementById('st-result-meta');
    const indRow = document.getElementById('st-indicators-row');
    const body = document.getElementById('st-analysis-body');

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="pulse-subtle">⏳ Running Deep Dive...</span>';
    }
    if (resultCard) {
        resultCard.classList.remove('hidden');
        resultCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (title) title.textContent = `Analyzing ${ticker}...`;
    if (meta) meta.textContent = 'Computing RSI, MACD, Bollinger Bands & Ingesting Social Sentiment...';
    if (indRow) indRow.innerHTML = '<div class="col-span-full text-center text-xs text-slate-400 py-3">Fetching verified 250 daily bars, StockTwits streams & Reddit threads...</div>';
    if (body) body.innerHTML = '<div class="text-center py-6 text-slate-500">Gemini 3.8 Flash evaluating fundamental moats, technical momentum & capital sizing...</div>';

    // Synchronize URL with active ticker
    if (window.history && window.history.replaceState) {
        const currentUrl = new URL(window.location);
        currentUrl.searchParams.set('tab', 'deepdive');
        currentUrl.searchParams.set('ticker', ticker);
        window.history.replaceState({}, '', currentUrl);
    }

    try {
        const res = await fetch(`/api/analyze/${ticker}`, { credentials: 'include', method: 'POST' });
        const data = await res.json();

        if (data.status === 'success') {
            renderDeepDiveResult(data);
            // Refresh archive sidebar with the newly saved deep dive
            loadDeepDivesArchive();
        } else {
            if (title) title.textContent = `Analysis Failed for ${ticker}`;
            if (body) body.innerHTML = `<div class="text-rose-400 text-xs">${esc(data.detail || 'Unable to generate analysis.')}</div>`;
        }
    } catch (e) {
        if (title) title.textContent = `Error Analyzing ${ticker}`;
        if (body) body.innerHTML = `<div class="text-rose-400 text-xs">Failed to reach analysis endpoint: ${esc(e.message)}</div>`;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<span>⚡ Run Deep Dive</span>';
        }
    }
}

/**
 * Render deep dive result into the reader panel and persist to memory & localStorage.
 */
function renderDeepDiveResult(data) {
    if (!data || data.status !== 'success') return;
    _currentDeepDive = data;
    try {
        localStorage.setItem('financial_sentinel_last_deepdive', JSON.stringify(data));
    } catch (e) {}

    const resultCard = document.getElementById('single-ticker-result-card');
    const title = document.getElementById('st-result-title');
    const meta = document.getElementById('st-result-meta');
    const indRow = document.getElementById('st-indicators-row');
    const body = document.getElementById('st-analysis-body');
    const input = document.getElementById('single-ticker-input');

    if (input && data.ticker) {
        input.value = data.ticker;
    }
    if (resultCard) {
        resultCard.classList.remove('hidden');
    }
    if (title) title.textContent = `Stock Analysis: ${data.ticker} (${data.quote?.name || data.ticker})`;
    if (meta) meta.textContent = `Price: $${data.quote?.current_price || data.current_price || 0} | Sector: ${data.quote?.sector || 'N/A'}`;

    const t = data.technicals;
    const s = data.sentiment;

    // Render Data Provenance Badge (Phase 2)
    const prov = t?.provenance;
    const provBadge = document.getElementById('st-provenance-badge');
    if (provBadge) {
        if (prov && prov.source) {
            const srcName = prov.source === 'robinhood' ? 'Robinhood' : (prov.source === 'yahoo' ? 'Yahoo Finance' : esc(prov.source));
            const barsText = prov.bar_count ? `${esc(prov.bar_count)} daily bars` : 'live';
            provBadge.innerHTML = `
                <span class="inline-flex items-center space-x-1.5 px-2 py-0.5 rounded-full text-[10px] font-mono bg-emerald-950/70 text-emerald-300 border border-emerald-800/60" title="Ground truth data source: ${srcName} (${barsText})">
                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                    <span class="uppercase tracking-wider font-semibold">${srcName}</span>
                    <span class="text-emerald-500">·</span>
                    <span>${barsText}</span>
                    <span class="text-emerald-500">·</span>
                    <span>Verified</span>
                </span>
            `;
            provBadge.classList.remove('hidden');
        } else {
            provBadge.innerHTML = '';
            provBadge.classList.add('hidden');
        }
    }

    let indHtml = '';

    // Render Technical Momentum Cards with Null-Safe Missing Data Guards
    if (t && t.is_live) {
        const hasRsi = t.rsi_14 !== null && t.rsi_14 !== undefined;
        const rsiVal = hasRsi ? Number(t.rsi_14).toFixed(1) : '—';
        const rsiColor = hasRsi ? (t.rsi_14 >= 70 ? 'text-rose-400' : (t.rsi_14 <= 30 ? 'text-emerald-400' : 'text-slate-200')) : 'text-slate-500';
        const rsiLabel = hasRsi ? (t.rsi_status || '').replace(/_/g, ' ') : 'Insufficient Bars (<14)';

        const hasMacdHist = t.macd_hist !== null && t.macd_hist !== undefined;
        const macdColor = (t.macd_status || '').includes('BULLISH') ? 'text-emerald-400' : ((t.macd_status || '').includes('BEARISH') ? 'text-rose-400' : 'text-slate-200');
        const macdHistStr = hasMacdHist ? `Hist: ${t.macd_hist > 0 ? '+' : ''}${Number(t.macd_hist).toFixed(2)}` : 'Hist: —';
        const macdLabel = (t.macd_status || '').replace(/_/g, ' ') || '— (<35 bars)';

        const has50 = t.dist_from_50_dma_pct !== null && t.dist_from_50_dma_pct !== undefined;
        const has200 = t.dist_from_200_dma_pct !== null && t.dist_from_200_dma_pct !== undefined;
        const dma50Str = has50 ? `50 DMA: ${t.dist_from_50_dma_pct > 0 ? '+' : ''}${Number(t.dist_from_50_dma_pct).toFixed(1)}%` : '50 DMA: —';
        const dma200Str = has200 ? `200 DMA: ${t.dist_from_200_dma_pct > 0 ? '+' : ''}${Number(t.dist_from_200_dma_pct).toFixed(1)}%` : '200 DMA: — (<200 bars)';

        indHtml += `
            <div class="p-3 bg-dark-950 border border-slate-800 rounded-xl space-y-1">
                <div class="text-[10px] text-slate-400 uppercase font-semibold">RSI (14-Day)</div>
                <div class="text-base font-bold ${rsiColor}">${rsiVal}</div>
                <div class="text-[10px] text-slate-400 truncate">${rsiLabel}</div>
            </div>
            <div class="p-3 bg-dark-950 border border-slate-800 rounded-xl space-y-1">
                <div class="text-[10px] text-slate-400 uppercase font-semibold">MACD Momentum</div>
                <div class="text-xs font-bold ${macdColor} truncate">${macdLabel}</div>
                <div class="text-[10px] text-slate-400 font-mono">${macdHistStr}</div>
            </div>
            <div class="p-3 bg-dark-950 border border-slate-800 rounded-xl space-y-1">
                <div class="text-[10px] text-slate-400 uppercase font-semibold">Moving Averages</div>
                <div class="text-xs font-bold text-cyan-300 font-mono">${dma50Str}</div>
                <div class="text-[10px] text-slate-400 font-mono">${dma200Str}</div>
            </div>
        `;
    }

    // Render Retail Social Sentiment Stream Cards
    if (s && s.is_live) {
        const sentScore = s.composite_sentiment_score || s.retail_bull_pct || 50;
        const sentColor = sentScore >= 65 ? 'text-emerald-400' : (sentScore <= 35 ? 'text-rose-400' : 'text-slate-200');
        const rvolColor = s.relative_volume >= 1.2 ? 'text-emerald-400' : (s.relative_volume <= 0.85 ? 'text-amber-400' : 'text-slate-200');
        
        let recencyStr = '';
        if (s.span_hours) {
            recencyStr = s.span_hours < 1.0 ? `${Math.round(s.span_hours * 60)}m` : (s.span_hours < 48.0 ? `${s.span_hours.toFixed(1)}h` : `${(s.span_hours / 24.0).toFixed(1)}d`);
        }
        const accelVal = s.acceleration_factor ? Number(s.acceleration_factor).toFixed(1) : '1.0';
        const rateVal = s.messages_per_hour ? `${Number(s.messages_per_hour).toFixed(1)}/hr` : '';
        const metaLine = recencyStr ? `${s.total_messages_analyzed} in ${recencyStr} · ${accelVal}x accel` : `${s.social_velocity}`;

        indHtml += `
            <div class="p-3 bg-dark-950 border border-slate-800 rounded-xl space-y-1">
                <div class="text-[10px] text-slate-400 uppercase font-semibold">Retail Sentiment</div>
                <div class="text-base font-bold ${sentColor}">${s.retail_bull_pct || 50}% Bullish</div>
                <div class="text-[10px] text-slate-400 truncate">${(s.sentiment_verdict || '').replace(/_/g, ' ')} · ${s.social_velocity || 'MODERATE'} ${rateVal}</div>
                <div class="text-[9px] text-slate-500 truncate">(${metaLine})</div>
            </div>
            <div class="p-3 bg-dark-950 border border-slate-800 rounded-xl space-y-1">
                <div class="text-[10px] text-slate-400 uppercase font-semibold">Relative Volume (RVOL)</div>
                <div class="text-base font-bold ${rvolColor}">${s.relative_volume ? Number(s.relative_volume).toFixed(2) : '1.00'}x</div>
                <div class="text-[10px] text-slate-400">${s.relative_volume >= 1.2 ? 'Above 20D Avg' : (s.relative_volume <= 0.85 ? 'Below 20D Avg' : 'Normal Volume')}</div>
            </div>
        `;
    }

    if (indRow) indRow.innerHTML = indHtml || '<div class="col-span-full text-xs text-slate-400">Indicators loaded into report text.</div>';

    // Render Structured Executive KPI Cards (Phase 2)
    const kpiRow = document.getElementById('st-kpi-row');
    if (kpiRow) {
        if (data.verdict || data.target_price || data.suggested_allocation_usd !== undefined) {
            const isBullish = (data.verdict || '').toUpperCase().includes('BUY') || (data.verdict || '').toUpperCase().includes('BULLISH');
            const isHold = (data.verdict || '').toUpperCase().includes('HOLD');
            const isPass = (data.verdict || '').toUpperCase().includes('PASS') || (data.verdict || '').toUpperCase().includes('AVOID');
            
            const verdictBg = isBullish ? 'border-emerald-500/40 bg-emerald-950/20' : (isHold ? 'border-amber-500/40 bg-amber-950/20' : (isPass ? 'border-rose-500/40 bg-rose-950/20' : 'border-slate-800 bg-dark-950'));
            const verdictColor = isBullish ? 'text-emerald-400' : (isHold ? 'text-amber-400' : (isPass ? 'text-rose-400' : 'text-slate-200'));
            const verdictEmoji = isBullish ? '🟢' : (isHold ? '🟡' : (isPass ? '🔴' : '⚖️'));

            const curPrice = Number(data.quote?.current_price || data.current_price || 0);
            let targetUpsideStr = '';
            if (data.target_price && curPrice > 0) {
                const upPct = (((Number(data.target_price) - curPrice) / curPrice) * 100).toFixed(1);
                targetUpsideStr = `${upPct > 0 ? '+' : ''}${upPct}% Upside`;
            }

            kpiRow.innerHTML = `
                <div class="p-3 rounded-xl border ${verdictBg} space-y-1">
                    <div class="text-[10px] uppercase font-semibold tracking-wider text-slate-400">Institutional Verdict</div>
                    <div class="text-sm font-extrabold ${verdictColor} flex items-center space-x-1 truncate">
                        <span>${verdictEmoji}</span>
                        <span>${esc(data.verdict || 'ANALYZED')}</span>
                    </div>
                    <div class="text-[10px] text-slate-400 font-mono">Conviction: ${data.conviction_score ? esc(data.conviction_score) + '%' : '—'}</div>
                </div>
                <div class="p-3 bg-dark-950 border border-slate-800 rounded-xl space-y-1">
                    <div class="text-[10px] uppercase font-semibold tracking-wider text-slate-400">12M Target Price</div>
                    <div class="text-base font-bold text-cyan-300 font-mono">$${data.target_price ? Number(data.target_price).toFixed(2) : '—'}</div>
                    <div class="text-[10px] text-emerald-400 font-mono font-medium truncate">${targetUpsideStr || 'Consensus Target'}</div>
                </div>
                <div class="p-3 bg-dark-950 border border-slate-800 rounded-xl space-y-1">
                    <div class="text-[10px] uppercase font-semibold tracking-wider text-slate-400">Dynamic ATR Stop</div>
                    <div class="text-base font-bold text-rose-300 font-mono">$${data.stop_floor ? Number(data.stop_floor).toFixed(2) : '—'}</div>
                    <div class="text-[10px] text-slate-400 font-mono">Volatility Trailing Band</div>
                </div>
                <div class="p-3 bg-dark-950 border border-slate-800 rounded-xl space-y-1">
                    <div class="text-[10px] uppercase font-semibold tracking-wider text-slate-400">Suggested Sizing</div>
                    <div class="text-base font-bold text-white font-mono">$${data.suggested_allocation_usd !== undefined ? Number(data.suggested_allocation_usd).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) : '0.00'}</div>
                    <div class="text-[10px] text-slate-400 font-mono">From Cash Reserves</div>
                </div>
                <div class="p-3 bg-dark-950 border border-slate-800 rounded-xl space-y-1">
                    <div class="text-[10px] uppercase font-semibold tracking-wider text-slate-400">Core Thesis</div>
                    <div class="text-xs text-slate-200 line-clamp-2 leading-tight font-sans" title="${esc(data.thesis || '')}">${esc(data.thesis || 'Structured model synthesis')}</div>
                </div>
            `;
            kpiRow.classList.remove('hidden');
        } else {
            kpiRow.innerHTML = '';
            kpiRow.classList.add('hidden');
        }
    }
    
    // Format analysis report
    if (body) {
        body.innerHTML = formatAnalysisContent(data.analysis);
    }
}

/**
 * Retain / restore the last deep dive on screen when navigating to the tab.
 */
function restoreLastDeepDive() {
    if (_currentDeepDive) {
        renderDeepDiveResult(_currentDeepDive);
        return;
    }
    try {
        const saved = localStorage.getItem('financial_sentinel_last_deepdive');
        if (saved) {
            const parsed = JSON.parse(saved);
            if (parsed && parsed.status === 'success') {
                renderDeepDiveResult(parsed);
                return;
            }
        }
    } catch (e) {}

    // Fallback: fetch latest from archive repository
    fetch('/api/deepdives?limit=1')
        .then(r => r.json())
        .then(d => {
            if (d && d.deepdives && d.deepdives.length > 0) {
                selectArchivedDeepDive(d.deepdives[0].id, false);
            }
        })
        .catch(e => console.warn('Could not restore latest deep dive:', e));
}

/**
 * Load archived deep dives from the database repository.
 */
async function loadDeepDivesArchive() {
    const listCont = document.getElementById('deepdives-list-container');
    const badge = document.getElementById('deepdives-count-badge');
    if (!listCont) return;

    try {
        const res = await fetch('/api/deepdives?limit=50');
        const data = await res.json();
        if (data.status === 'success') {
            _archivedDeepDives = data.deepdives || [];
            if (badge) badge.textContent = `${_archivedDeepDives.length} Reports`;
            renderDeepDivesList(_archivedDeepDives);
        }
    } catch (err) {
        console.warn('Failed to load deep dives archive:', err);
        if (listCont) listCont.innerHTML = '<div class="p-3 text-xs text-rose-400 text-center">Failed to load archive.</div>';
    }
}

/**
 * Render scrollable list of archived deep dives in sidebar.
 */
function renderDeepDivesList(items) {
    const listCont = document.getElementById('deepdives-list-container');
    if (!listCont) return;

    if (!items || items.length === 0) {
        listCont.innerHTML = `
            <div class="p-4 text-center text-xs text-slate-500">
                No archived deep dives yet.<br>Enter a ticker above and click "⚡ Run Deep Dive" to save reports.
            </div>
        `;
        return;
    }

    listCont.innerHTML = items.map(d => {
        const isBullish = (d.verdict || '').toUpperCase().includes('BULLISH');
        const isBearish = (d.verdict || '').toUpperCase().includes('BEARISH');
        const badgeColor = isBullish ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' :
                           (isBearish ? 'bg-rose-500/20 text-rose-300 border-rose-500/30' : 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30');
        
        let dateStr = '';
        if (d.created_at) {
            try {
                const dt = new Date(d.created_at + (d.created_at.endsWith('Z') ? '' : 'Z'));
                dateStr = dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' +
                          dt.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
            } catch (e) {
                dateStr = d.created_at;
            }
        }

        return `
            <div class="p-2.5 bg-dark-950 hover:bg-slate-800/70 border border-slate-800/90 rounded-xl cursor-pointer transition group flex items-center justify-between" onclick="selectArchivedDeepDive('${esc(d.id)}')">
                <div class="space-y-0.5">
                    <div class="flex items-center space-x-2">
                        <span class="font-bold font-mono text-sm text-white group-hover:text-cyan-300 transition">${esc(d.ticker)}</span>
                        <span class="px-1.5 py-0.5 rounded text-[9px] font-bold border ${badgeColor}">${esc(d.verdict || 'NEUTRAL')}${d.conviction_score ? ` · ${esc(d.conviction_score)}%` : ''}</span>
                    </div>
                    <div class="text-[11px] text-slate-400 truncate max-w-[150px]">${esc(d.company_name || d.ticker)}</div>
                    <div class="text-[10px] font-mono text-slate-500">${esc(dateStr)}</div>
                </div>
                <div class="text-right space-y-1">
                    <div class="text-xs font-bold text-slate-200">$${d.current_price ? Number(d.current_price).toFixed(2) : '--'}</div>
                    <button onclick="event.stopPropagation(); deleteArchivedDeepDive('${esc(d.id)}', event)" class="text-[10px] text-slate-500 hover:text-rose-400 p-1 transition" title="Delete from archive">🗑️</button>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * Filter deep dives archive by ticker or company name search query.
 */
function filterDeepDivesArchive() {
    const input = document.getElementById('deepdives-search-input');
    const query = (input?.value || '').trim().toUpperCase();
    if (!query) {
        renderDeepDivesList(_archivedDeepDives);
        return;
    }
    const filtered = _archivedDeepDives.filter(d => 
        (d.ticker || '').toUpperCase().includes(query) || 
        (d.company_name || '').toUpperCase().includes(query)
    );
    renderDeepDivesList(filtered);
}

/**
 * Replay full deep dive report from repository.
 */
async function selectArchivedDeepDive(deepdiveId, scrollIntoView = true) {
    try {
        const res = await fetch(`/api/deepdives/${deepdiveId}`);
        const data = await res.json();
        if (data.status === 'success' && data.deepdive) {
            const dd = data.deepdive;
            const fullPayload = dd.payload ? {
                ...dd.payload,
                verdict: dd.payload.verdict || dd.verdict,
                conviction_score: dd.payload.conviction_score || dd.conviction_score
            } : {
                status: 'success',
                ticker: dd.ticker,
                company_name: dd.company_name,
                current_price: dd.current_price,
                quote: { name: dd.company_name, current_price: dd.current_price },
                technicals: dd.technicals,
                sentiment: dd.sentiment,
                analysis: dd.analysis_text,
                verdict: dd.verdict,
                conviction_score: dd.conviction_score
            };
            renderDeepDiveResult(fullPayload);
            if (scrollIntoView) {
                const card = document.getElementById('single-ticker-result-card');
                if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    } catch (e) {
        console.warn('Failed to load archived deep dive details:', e);
    }
}

/**
 * Delete an archived deep dive record.
 */
async function deleteArchivedDeepDive(deepdiveId, event) {
    if (event) event.stopPropagation();
    if (!confirm('Are you sure you want to remove this archived report?')) return;
    try {
        const res = await fetch(`/api/deepdives/${deepdiveId}`, { method: 'DELETE' });
        if (res.ok) {
            _archivedDeepDives = _archivedDeepDives.filter(d => d.id !== deepdiveId);
            renderDeepDivesList(_archivedDeepDives);
            const badge = document.getElementById('deepdives-count-badge');
            if (badge) badge.textContent = `${_archivedDeepDives.length} Reports`;
            if (_currentDeepDive && _currentDeepDive.deepdive_id === deepdiveId) {
                _currentDeepDive = null;
                localStorage.removeItem('financial_sentinel_last_deepdive');
            }
        }
    } catch (e) {
        console.warn('Failed to delete archived deep dive:', e);
    }
}

/**
 * Robust institutional report formatter: converts Telegram HTML, markdown, and bullets to styled Tailwind HTML.
 */
function formatAnalysisContent(text) {
    if (!text) return '';
    const safeEsc = window.esc || function (s) {
        if (s === null || s === undefined) return '';
        return String(s).replace(/[&<>"']/g, c => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[c]));
    };

    // 1. Fully escape all untrusted user / LLM input first (prevents any injected tags or entity tricks)
    let html = safeEsc(text);

    // 2. Safely transform whitelisted Telegram HTML tags into styled Tailwind classes
    html = html.replace(/&lt;b&gt;([\s\S]*?)&lt;\/b&gt;/gi, '<strong class="text-white font-semibold">$1</strong>');
    html = html.replace(/&lt;strong&gt;([\s\S]*?)&lt;\/strong&gt;/gi, '<strong class="text-white font-semibold">$1</strong>');
    html = html.replace(/&lt;i&gt;([\s\S]*?)&lt;\/i&gt;/gi, '<em class="text-slate-300 italic">$1</em>');
    html = html.replace(/&lt;em&gt;([\s\S]*?)&lt;\/em&gt;/gi, '<em class="text-slate-300 italic">$1</em>');
    html = html.replace(/&lt;code&gt;([\s\S]*?)&lt;\/code&gt;/gi, '<code class="px-1.5 py-0.5 bg-dark-950 text-cyan-300 rounded font-mono text-[11px] border border-slate-800">$1</code>');
    html = html.replace(/&lt;pre&gt;([\s\S]*?)&lt;\/pre&gt;/gi, '<pre class="p-2 bg-dark-950 text-cyan-300 rounded font-mono text-[11px] border border-slate-800 overflow-x-auto">$1</pre>');
    // Only allow http:// and https:// URLs for anchors; reject javascript: or data: schemes
    html = html.replace(/&lt;a\s+href=&quot;(https?:\/\/[^&"]+)&quot;[^&]*&gt;([\s\S]*?)&lt;\/a&gt;/gi, '<a href="$1" target="_blank" rel="noopener noreferrer" class="text-cyan-400 hover:underline">$2</a>');

    // 3. Format markdown bold & inline code
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong class="text-white font-semibold">$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 bg-dark-950 text-cyan-300 rounded font-mono text-[11px] border border-slate-800">$1</code>');

    // 4. Bullet points (•, -, *)
    html = html.replace(/^\s*[•\-\*]\s+(.*)$/gm, '<li class="ml-4 list-disc text-slate-300 my-0.5">$1</li>');
    // Numbered lists
    html = html.replace(/^\s*(\d+)\.\s+(.*)$/gm, '<li class="ml-4 list-decimal text-slate-300 my-0.5">$2</li>');

    // 5. Line breaks & spacing
    html = html.replace(/\n\n/g, '<div class="h-2"></div>');
    html = html.replace(/\n/g, '<br>');
    html = html.replace(/<\/li>\s*<br\s*\/?>/gi, '</li>');

    return html;
}


// Global exports
window.analyzeHoldingTicker = analyzeHoldingTicker;
window.analyzeQuickTicker = analyzeQuickTicker;
window.runSingleTickerAnalysis = runSingleTickerAnalysis;
window.renderDeepDiveResult = renderDeepDiveResult;
window.restoreLastDeepDive = restoreLastDeepDive;
window.loadDeepDivesArchive = loadDeepDivesArchive;
window.renderDeepDivesList = renderDeepDivesList;
window.filterDeepDivesArchive = filterDeepDivesArchive;
window.selectArchivedDeepDive = selectArchivedDeepDive;
window.deleteArchivedDeepDive = deleteArchivedDeepDive;
window.formatAnalysisContent = formatAnalysisContent;

