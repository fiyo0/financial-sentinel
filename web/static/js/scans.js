/**
 * scans.js - Multi-Agent Synthesis, Segmented Pill Views, Market Briefings, Chat, and Earnings
 * Part of Financial Sentinel Modular Architecture
 */

let currentScanSubView = 'defense';
let currentBriefingsArchive = [];
let activeBriefingId = null;
let currentBriefingFilter = 'all';
const chatHistory = [];

const SLOT_STYLES = {
    premarket: {
        icon: '🌅',
        badgeBg: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
        btnActive: 'border-cyan-500/50 bg-cyan-950/40 text-cyan-300'
    },
    midmarket: {
        icon: '☀️',
        badgeBg: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
        btnActive: 'border-amber-500/50 bg-amber-950/40 text-amber-300'
    },
    postmarket: {
        icon: '🌙',
        badgeBg: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
        btnActive: 'border-indigo-500/50 bg-indigo-950/40 text-indigo-300'
    },
    weekend: {
        icon: '🌟',
        badgeBg: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
        btnActive: 'border-purple-500/50 bg-purple-950/40 text-purple-300'
    },
    earnings: {
        icon: '📅',
        badgeBg: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
        btnActive: 'border-emerald-500/50 bg-emerald-950/40 text-emerald-300'
    }
};

/**
 * 3-Way Segmented Pill View Switcher for Multi-Agent Scans Tab.
 * Eliminates vertical scrolling wall by grouping into:
 * - 'defense': Executive briefing + portfolio health & defensive risk
 * - 'alpha': Vetted alpha candidates + moonshot radar
 * - 'critic': Adversarial risk & critic audit log
 */
function switchScanSubView(view) {
    currentScanSubView = view;
    const defView = document.getElementById('scan-view-defense');
    const alphaView = document.getElementById('scan-view-alpha');
    const criticView = document.getElementById('scan-view-critic');

    const btnDef = document.getElementById('btn-scan-defense');
    const btnAlpha = document.getElementById('btn-scan-alpha');
    const btnCritic = document.getElementById('btn-scan-critic');

    // Reset buttons
    [btnDef, btnAlpha, btnCritic].forEach(b => {
        if (b) {
            b.className = 'scan-pill-btn px-4 py-2 bg-dark-950 hover:bg-slate-800 text-slate-400 hover:text-white rounded-xl border border-slate-800 transition font-semibold text-xs flex items-center space-x-2';
        }
    });

    if (view === 'defense') {
        if (defView) defView.classList.remove('hidden');
        if (alphaView) alphaView.classList.add('hidden');
        if (criticView) criticView.classList.add('hidden');
        if (btnDef) {
            btnDef.className = 'scan-pill-btn px-4 py-2 bg-cyan-600 text-white rounded-xl border border-cyan-500 shadow-md shadow-cyan-600/30 transition font-bold text-xs flex items-center space-x-2';
        }
    } else if (view === 'alpha') {
        if (defView) defView.classList.add('hidden');
        if (alphaView) alphaView.classList.remove('hidden');
        if (criticView) criticView.classList.add('hidden');
        if (btnAlpha) {
            btnAlpha.className = 'scan-pill-btn px-4 py-2 bg-emerald-600 text-white rounded-xl border border-emerald-500 shadow-md shadow-emerald-600/30 transition font-bold text-xs flex items-center space-x-2';
        }
    } else if (view === 'critic') {
        if (defView) defView.classList.add('hidden');
        if (alphaView) alphaView.classList.add('hidden');
        if (criticView) criticView.classList.remove('hidden');
        if (btnCritic) {
            btnCritic.className = 'scan-pill-btn px-4 py-2 bg-yellow-600 text-white rounded-xl border border-yellow-500 shadow-md shadow-yellow-600/30 transition font-bold text-xs flex items-center space-x-2';
        }
    } else if (view === 'all') {
        if (defView) defView.classList.remove('hidden');
        if (alphaView) alphaView.classList.remove('hidden');
        if (criticView) criticView.classList.remove('hidden');
    }
}

/**
 * Trigger full multi-agent scan cycle with Server-Sent Events telemetry stream.
 */
async function triggerScan(live=true, useDemo=false) {
    const btnScan = document.getElementById('btn-scan');
    const summaryElem = document.getElementById('briefing-summary');
    
    if (typeof showBanner === 'function') {
        showBanner('⏳ Initiating live multi-agent analysis cycle...');
    }

    if (btnScan) {
        btnScan.disabled = true;
        btnScan.innerHTML = '<span class="pulse-subtle">⏳ Analyzing...</span>';
    }
    if (summaryElem) {
        summaryElem.innerHTML = '<span class="text-cyan-400 pulse-subtle">📡 Connecting to live multi-agent telemetry stream...</span>';
    }

    try {
        const response = await fetch(`/api/scan/stream?live=${live}&force_fresh=false`, {
            headers: { 'Accept': 'text/event-stream' }
        });

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}: ${response.statusText}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let scanData = null;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop(); // Keep partial trailing chunk

            for (const chunk of lines) {
                const trimmed = chunk.trim();
                if (!trimmed.startsWith('data:')) continue;
                const jsonStr = trimmed.replace(/^data:\s*/, '');
                try {
                    const event = JSON.parse(jsonStr);
                    if (event.type === 'progress') {
                        const msg = event.message || '';
                        const pct = event.percent || 0;
                        if (typeof showBanner === 'function') showBanner(`⏳ [${pct}%] ${msg}`);
                        if (summaryElem) {
                            summaryElem.innerHTML = `<span class="text-cyan-400 pulse-subtle"><b>[${pct}%]</b> ${esc(msg)}</span>`;
                        }
                    } else if (event.type === 'complete') {
                        scanData = event;
                    } else if (event.type === 'error') {
                        throw new Error(event.message || 'Scan error');
                    }
                } catch (parseErr) {
                    console.warn("Error parsing telemetry stream chunk:", parseErr);
                }
            }
        }

        if (scanData) {
            if (scanData.portfolio && typeof renderHoldingsTable === 'function') {
                renderHoldingsTable(scanData.portfolio);
            }
            if (scanData.stress && typeof renderStressMatrix === 'function') {
                renderStressMatrix(scanData.stress);
            }
            if (scanData.briefing) {
                renderBriefing(scanData.briefing);
            }
            const pst = typeof getPstTimeString === 'function' ? getPstTimeString() : '';
            if (typeof showBanner === 'function') {
                showBanner(`✅ Analysis Complete at ${pst}! Synced live market prices & synthesized Gemini 3.8 briefing.`);
            }
            if (typeof invalidatePortfolioCache === 'function') invalidatePortfolioCache();
            if (typeof fetchPortfolio === 'function') await fetchPortfolio(true);
        }

    } catch (err) {
        console.warn("Scan stream connection issue, attempting automatic recovery:", err);
        if (typeof showBanner === 'function') showBanner('⏳ Checking if analysis finished on server...', false);
        if (summaryElem) {
            summaryElem.innerHTML = '<span class="text-amber-300 pulse-subtle">🔄 Syncing briefing results from server...</span>';
        }

        let recovered = false;
        for (let retry = 0; retry < 3; retry++) {
            try {
                await new Promise(r => setTimeout(r, 2000));
                const recoveryRes = await fetch('/api/portfolio');
                if (recoveryRes.ok) {
                    const recData = await recoveryRes.json();
                    if (recData && recData.briefing) {
                        if (recData.portfolio && typeof renderHoldingsTable === 'function') renderHoldingsTable(recData.portfolio);
                        if (recData.stress && typeof renderStressMatrix === 'function') renderStressMatrix(recData.stress);
                        renderBriefing(recData.briefing);
                        const pst = typeof getPstTimeString === 'function' ? getPstTimeString() : '';
                        if (typeof showBanner === 'function') showBanner(`✅ Analysis Complete at ${pst}! Successfully loaded Gemini 3.8 briefing.`);
                        recovered = true;
                        break;
                    }
                }
            } catch (recErr) {
                console.warn(`Recovery attempt ${retry + 1} failed:`, recErr);
            }
        }

        if (!recovered) {
            if (typeof showBanner === 'function') showBanner(`❌ Scan error: ${err.message || err}`, true);
            if (summaryElem) summaryElem.textContent = 'Scan encountered an error. Please retry.';
        }
    } finally {
        if (btnScan) {
            btnScan.disabled = false;
            btnScan.innerHTML = '<span>⚡ Analyze</span>';
        }
    }
}

/**
 * Render multi-agent intelligence synthesis into defensive risk, vetted alpha, and critic panels.
 */
function renderBriefing(b) {
    if (!b) return;
    try {
        const sumText = b.executive_summary || `Multi-agent scan complete across ${b.total_holdings_monitored || 0} monitored holdings.`;
        const sumEl = document.getElementById('briefing-summary');
        if (sumEl) sumEl.textContent = sumText;

        if (b.portfolio_stress && typeof renderStressMatrix === 'function') {
            try {
                renderStressMatrix(b.portfolio_stress);
            } catch (stressErr) {
                console.warn("Stress matrix render warning:", stressErr);
            }
        }

        // 1. Render Risks & Holding Catalyst Cards
        const riskCont = document.getElementById('risk-alerts-container');
        if (riskCont) {
            try {
                const allRisks = [...(b.critical_risk_alerts || []), ...(b.notable_risk_alerts || [])];
                if (allRisks.length === 0) {
                    riskCont.innerHTML = `
                        <div class="p-4 bg-dark-900 border border-emerald-900/40 rounded-xl text-xs text-emerald-400 flex items-center space-x-2">
                            <span>✅</span>
                            <span>All ${b.total_holdings_monitored || 0} monitored positions are in healthy operational posture with no acute downside risks.</span>
                        </div>
                    `;
                } else {
                    riskCont.innerHTML = allRisks.map(r => {
                        const isBearish = r.impact === 'BEARISH';
                        const isBullish = r.impact === 'BULLISH';
                        const isVolatile = r.impact === 'HIGH_VOLATILITY';
                        const isCrit = r.priority === 'P0_CRITICAL';
                        
                        let badgeBg = 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30';
                        let borderClass = 'border-slate-800 bg-dark-900';
                        let titleColor = 'text-cyan-400';
                        let sign = '';

                        if (isBullish) {
                            badgeBg = 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30';
                            borderClass = 'border-emerald-900/40 bg-emerald-950/10';
                            titleColor = 'text-emerald-400';
                            sign = '+';
                        } else if (isBearish) {
                            badgeBg = 'bg-rose-500/20 text-rose-300 border-rose-500/30';
                            borderClass = 'border-rose-900/50 bg-rose-950/10';
                            titleColor = 'text-rose-400';
                            sign = '-';
                        } else if (isVolatile) {
                            badgeBg = 'bg-amber-500/20 text-amber-300 border-amber-500/30';
                            borderClass = 'border-amber-900/40 bg-amber-950/10';
                            titleColor = 'text-amber-400';
                            sign = '±';
                        } else {
                            badgeBg = 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30';
                            borderClass = 'border-slate-800 bg-dark-900';
                            titleColor = 'text-cyan-400';
                            sign = '~';
                        }

                        const critBadge = isCrit ? raw(`
                            <span class="px-2 py-0.5 rounded text-[10px] font-black bg-red-600/30 text-red-300 border border-red-500/50 animate-pulse flex items-center space-x-1" title="P0 Critical: Expected price volatility > 3%">
                                <span>⚡</span>
                                <span>P0 CRITICAL</span>
                            </span>
                        `) : raw('');

                        const watchItems = r.key_risks && r.key_risks.length > 0 ? raw(`
                            <div class="text-[10px] text-slate-400 font-mono">
                                <span class="text-amber-400/90 font-semibold">⚠️ Watch Items:</span> ${r.key_risks.map(k => esc(k)).join(' &bull; ')}
                            </div>
                        `) : raw('');

                        return html`
                            <div class="p-4 rounded-xl border ${borderClass} text-xs space-y-2 shadow-lg transition-all">
                                <div class="flex items-center justify-between">
                                    <div class="font-bold text-sm ${titleColor} flex items-center space-x-2">
                                        <span>${r.holding_ticker}</span>
                                        <span class="text-xs text-slate-400 font-normal">(${r.holding_name || ''})</span>
                                    </div>
                                    <div class="flex items-center space-x-1.5">
                                        ${critBadge}
                                        <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${badgeBg}">
                                            ${r.impact} (${sign}${r.impact_magnitude_pct || 0}%)
                                        </span>
                                    </div>
                                </div>
                                <p class="text-slate-200 leading-relaxed">${r.rationale || ''}</p>
                                <div class="text-[11px] text-cyan-300 font-semibold flex items-center space-x-1.5">
                                    <span>🎯 Action:</span>
                                    <span>${r.recommended_action || 'Hold and monitor'}</span>
                                </div>
                                ${watchItems}
                                <div class="flex items-center justify-between pt-2.5 border-t border-slate-800/80 text-[10px] text-slate-400">
                                    <span class="font-mono text-cyan-400/90">ℹ️ Source: <b>${r.transmission_channel || 'Direct Market News'}</b></span>
                                    <div id="fb-wrap-${r.holding_ticker}_${r.news_item_id || 'item'}" class="flex items-center space-x-1.5">
                                        <button onclick="sendFeedback('risk_${r.holding_ticker}_${r.news_item_id || 'item'}', 'accurate', this)" class="px-2 py-0.5 rounded bg-slate-800/80 hover:bg-emerald-950/60 hover:text-emerald-300 hover:border-emerald-500/40 border border-slate-700/80 text-slate-400 transition-all">👍 Accurate</button>
                                        <button onclick="sendFeedback('risk_${r.holding_ticker}_${r.news_item_id || 'item'}', 'noise', this)" class="px-2 py-0.5 rounded bg-slate-800/80 hover:bg-rose-950/60 hover:text-rose-300 hover:border-rose-500/40 border border-slate-700/80 text-slate-400 transition-all">👎 Noise</button>
                                    </div>
                                </div>
                            </div>
                        `;
                    }).join('');
                }
            } catch (riskErr) {
                console.warn("Error rendering risk alerts:", riskErr);
            }
        }

        // 2. Render Opportunities
        const oppCont = document.getElementById('opportunity-container');
        if (oppCont) {
            try {
                const opps = b.top_opportunities || [];
                if (opps.length === 0) {
                    oppCont.innerHTML = '<div class="p-4 bg-dark-900 border border-slate-800 rounded-xl text-xs text-slate-500">No new asymmetric catalysts detected in this cycle.</div>';
                } else {
                    oppCont.innerHTML = opps.map(o => html`
                        <div class="p-4 bg-dark-900 border border-emerald-900/60 bg-emerald-950/10 rounded-xl text-xs space-y-2.5 shadow-lg">
                            <div class="flex items-center justify-between">
                                <div class="font-bold text-sm text-emerald-400">
                                    ${o.ticker} &bull; <span class="text-xs text-slate-300 font-normal">${o.name || ''}</span>
                                </div>
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                                    ${o.horizon || 'Medium'} &bull; ${o.asymmetric_ratio || 3}:1 R/R
                                </span>
                            </div>
                            <div class="text-xs text-cyan-300 font-semibold">🔮 Theme: ${o.theme || 'Macro Catalyst'}</div>
                            <p class="text-slate-300 leading-relaxed">${o.upside_thesis || ''}</p>
                            <div class="p-2 bg-dark-950/80 rounded-lg border border-slate-800/80 space-y-1 text-[11px] font-mono">
                                <div class="text-emerald-400">🎯 Upside Target: +${o.estimated_upside_pct || 15}% | 🛑 Stop Loss: -${o.suggested_stop_loss_pct || 5}%</div>
                                <div class="text-purple-300 font-sans text-[10px]">✨ Synergy: ${o.portfolio_synergy || 'Diversifies factor risk'}</div>
                            </div>
                        </div>
                    `).join('');
                }
            } catch (oppErr) {
                console.warn("Error rendering opportunities:", oppErr);
            }
        }

        // 3. Render Critic Reviews
        const criticCont = document.getElementById('critic-container');
        if (criticCont) {
            try {
                const verdicts = b.critic_verdicts || [];
                if (verdicts.length === 0) {
                    criticCont.innerHTML = '<div class="p-3 bg-dark-950 border border-slate-800 rounded-xl text-xs text-slate-500 col-span-full">All sources validated. No high-bias signals flagged.</div>';
                } else {
                    criticCont.innerHTML = verdicts.map(c => {
                        const isApproved = c.verdict === 'APPROVED';
                        const isCaveat = c.verdict === 'APPROVED_WITH_CAVEATS';
                        const verdictBadgeBg = isApproved ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : (isCaveat ? 'bg-amber-500/10 text-amber-400 border-amber-500/20' : 'bg-rose-500/10 text-rose-400 border-rose-500/20');
                        const grade = c.source_credibility_grade || 'B+';
                        const gradeColor = (grade === 'A+' || grade === 'A') ? 'text-emerald-300 bg-emerald-950/40 border-emerald-800/50' : ((grade === 'B+' || grade === 'B') ? 'text-cyan-300 bg-cyan-950/40 border-cyan-800/50' : 'text-amber-300 bg-amber-950/40 border-amber-800/50');

                        return `
                            <div class="p-3 bg-dark-950 border border-slate-800/90 rounded-xl text-xs space-y-2 shadow-md">
                                <div class="flex items-center justify-between">
                                    <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${verdictBadgeBg}">
                                        ⚖️ ${esc((c.verdict || '').replace(/_/g, ' '))}
                                    </span>
                                    <span class="px-2 py-0.5 rounded text-[10px] font-mono border ${gradeColor}">
                                        Grade: ${esc(grade)} (${esc(c.calibrated_confidence_pct || 85)}%)
                                    </span>
                                </div>
                                <p class="text-slate-200 text-xs leading-relaxed">${esc(c.review_summary || '')}</p>
                                ${c.counter_thesis_questions && c.counter_thesis_questions.length > 0 ? `
                                    <div class="text-[11px] text-amber-300/90 font-mono bg-amber-950/30 p-2 rounded-lg border border-amber-900/40">
                                        <span class="font-bold text-amber-400">❓ Devil's Advocate:</span> ${esc(c.counter_thesis_questions[0])}
                                    </div>
                                ` : ''}
                            </div>
                        `;
                    }).join('');
                }
            } catch (critErr) {
                console.warn("Error rendering critic reviews:", critErr);
            }
        }
    } catch (globalErr) {
        console.error("Critical error in renderBriefing:", globalErr);
    }
}

/**
 * On-demand thematic alpha discovery.
 */
async function discoverThematicAlpha(theme) {
    if (typeof showBanner === 'function') showBanner(`✨ Running on-demand alpha discovery for: "${theme}"...`);
    const oppCont = document.getElementById('opportunity-container');
    const criticCont = document.getElementById('critic-container');

    try {
        const res = await fetch('/api/opportunities/discover', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ theme: theme, count: 3 })
        });
        const data = await res.json();

        if (res.ok && data.opportunities && data.opportunities.length > 0) {
            if (typeof showBanner === 'function') showBanner(`✅ Discovered ${data.opportunities.length} new asymmetric opportunities in "${theme}"!`);
            
            const newOppHtml = data.opportunities.map(o => `
                <div class="p-4 bg-dark-900 border border-emerald-500/60 bg-emerald-950/20 rounded-xl text-xs space-y-2.5 shadow-lg relative">
                    <span class="absolute top-2 right-2 px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-500 text-black uppercase">
                        ✨ NEW DISCOVERY
                    </span>
                    <div class="flex items-center justify-between pr-24">
                        <div class="font-bold text-sm text-emerald-400">
                            ${o.ticker} &bull; <span class="text-xs text-slate-300 font-normal">${o.name}</span>
                        </div>
                    </div>
                    <div class="text-xs text-cyan-300 font-semibold">🔮 Theme: ${o.theme}</div>
                    <p class="text-slate-300 leading-relaxed">${o.upside_thesis}</p>
                    <div class="p-2 bg-dark-950/80 rounded-lg border border-slate-800/80 space-y-1 text-[11px] font-mono">
                        <div class="text-emerald-400">🎯 Upside Target: +${o.estimated_upside_pct}% | 🛑 Stop Loss: -${o.suggested_stop_loss_pct}%</div>
                        <div class="text-purple-300 font-sans text-[10px]">✨ Synergy: ${o.portfolio_synergy}</div>
                    </div>
                </div>
            `).join('');

            if (oppCont) {
                if (oppCont.innerHTML.includes('No opportunities discovered yet')) {
                    oppCont.innerHTML = newOppHtml;
                } else {
                    oppCont.innerHTML = newOppHtml + oppCont.innerHTML;
                }
            }

            if (data.critic_reviews && data.critic_reviews.length > 0 && criticCont) {
                const newCriticHtml = data.critic_reviews.map(c => `
                    <div class="p-3 bg-dark-950 border border-slate-800/90 rounded-xl text-xs space-y-2 shadow-md">
                        <div class="flex items-center justify-between">
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold border bg-emerald-500/10 text-emerald-400 border-emerald-500/20">
                                ⚖️ ${(c.verdict || '').replace('_', ' ')}
                            </span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono border text-emerald-300 bg-emerald-950/40 border-emerald-800/50">
                                Grade: ${c.source_credibility_grade} (${c.calibrated_confidence_pct}%)
                            </span>
                        </div>
                        <p class="text-slate-200 text-xs leading-relaxed">${c.review_summary}</p>
                        ${c.counter_thesis_questions && c.counter_thesis_questions.length > 0 ? `
                            <div class="text-[11px] text-amber-300/90 font-mono bg-amber-950/30 p-2 rounded-lg border border-amber-900/40">
                                <span class="font-bold text-amber-400">❓ Devil's Advocate:</span> ${c.counter_thesis_questions[0]}
                            </div>
                        ` : ''}
                    </div>
                `).join('');

                if (criticCont.innerHTML.includes('Critic agent audits will populate')) {
                    criticCont.innerHTML = newCriticHtml;
                } else {
                    criticCont.innerHTML = newCriticHtml + criticCont.innerHTML;
                }
            }
        } else {
            if (typeof showBanner === 'function') showBanner(`⚠️ No alpha candidates surfaced for "${theme}".`, true);
        }
    } catch (err) {
        if (typeof showBanner === 'function') showBanner(`⚠️ Discovery error: ${err.message}`, true);
    }
}

function toggleAlphaDiscoveryModal() {
    const customTheme = prompt("Enter a specific industry, theme, or sector to discover asymmetric alpha for (e.g. 'Space Defense & Satellites', 'Copper & Grid Infrastructure', 'AI Robotic Automation'):");
    if (customTheme && customTheme.trim()) {
        discoverThematicAlpha(customTheme.trim());
    }
}

/**
 * Scan moonshot radar candidates with asymmetric return profiles.
 */
async function scanMoonshotOpportunities() {
    const btn = document.getElementById('btn-scan-moonshots');
    const container = document.getElementById('moonshot-container');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="pulse-subtle">🚀 Scanning Moonshots...</span>';
    }
    if (container) {
        container.innerHTML = '<div class="p-6 bg-dark-950 border border-purple-900/40 rounded-xl text-xs text-purple-300 col-span-full pulse-subtle">🔭 Querying Gemini 3.8 Flash deep-tech & venture alpha models for high-beta binary catalysts...</div>';
    }

    try {
        const res = await fetch('/api/opportunities/moonshots?count=4', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.moonshots && data.moonshots.length > 0) {
            renderMoonshots(data.moonshots, data.critic_reviews || []);
            if (typeof showBanner === 'function') showBanner(`🚀 Discovered ${data.moonshots.length} high-asymmetry Moonshot opportunities!`);
        } else {
            if (container) {
                container.innerHTML = `
                    <div class="p-4 bg-dark-950 border border-amber-900/50 rounded-xl text-xs text-amber-300 col-span-full space-y-1">
                        <div class="font-bold flex items-center space-x-1.5">
                            <span>⚠️</span>
                            <span>Gemini API Quota Notice</span>
                        </div>
                        <p class="text-[11px] text-slate-400 font-normal">
                            As requested, zero hardcoded defaults exist. Upstream calls were unable to complete due to API quota or rate limiting.
                        </p>
                    </div>
                `;
            }
        }
    } catch (err) {
        if (typeof showBanner === 'function') showBanner(`⚠️ Moonshot discovery error: ${err.message}`, true);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<span>⚡ Scan Moonshots</span>';
        }
    }
}

function renderMoonshots(moonshots, criticReviews) {
    const container = document.getElementById('moonshot-container');
    if (!container) return;

    const criticMap = {};
    (criticReviews || []).forEach(c => {
        criticMap[c.target_id] = c;
    });

    container.innerHTML = moonshots.map(m => {
        const critic = criticMap[`opp_${m.ticker}_${m.news_item_id}`] || {};
        const question = (critic.counter_thesis_questions && critic.counter_thesis_questions[0]) || '';
        const riskFactorsStr = (m.risk_factors && m.risk_factors.length > 0)
            ? m.risk_factors.map(r => esc(r)).join(' &bull; ')
            : 'Regulatory delay or commercial adoption bottleneck.';

        return `
            <div class="p-4 bg-dark-950 border border-purple-900/60 hover:border-purple-500/80 rounded-2xl text-xs space-y-3 shadow-xl transition-all flex flex-col justify-between group">
                <div class="space-y-2.5">
                    <div class="flex items-center justify-between">
                        <div>
                            <div class="font-bold text-sm text-purple-300 group-hover:text-purple-200 transition-colors flex items-center space-x-1.5">
                                <span>${esc(m.ticker)}</span>
                                <span class="text-xs text-slate-400 font-normal truncate max-w-[140px]">(${esc(m.name)})</span>
                            </div>
                            <div class="text-[10px] text-purple-400/90 font-semibold">${esc(m.theme)}</div>
                        </div>
                        <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30">
                            ${esc(m.asymmetric_ratio)}:1 R/R
                        </span>
                    </div>

                    <p class="text-slate-300 text-xs leading-relaxed">${esc(m.upside_thesis)}</p>

                    <div class="p-2.5 bg-dark-900/90 rounded-xl border border-purple-900/40 space-y-1 text-[11px] font-mono">
                        <div class="text-emerald-400 font-bold flex items-center justify-between">
                            <span>🚀 Potential Upside:</span>
                            <span>+${esc(m.estimated_upside_pct)}%</span>
                        </div>
                        <div class="text-rose-400 font-bold flex items-center justify-between">
                            <span>🛑 Risk Threshold:</span>
                            <span>-${esc(m.suggested_stop_loss_pct)}%</span>
                        </div>
                    </div>

                    <div class="p-2.5 bg-rose-950/20 border border-rose-900/40 rounded-xl text-[10px] space-y-1 text-rose-300/90">
                        <span class="font-bold text-rose-400">⚠️ Key Binary Failure Mode:</span>
                        <div>${riskFactorsStr}</div>
                    </div>
                </div>

                ${question ? `
                    <div class="pt-2 border-t border-purple-950 text-[10px] text-amber-300/90 font-mono">
                        <span class="text-amber-400 font-bold">🔍 Devil's Advocate:</span> ${esc(question)}
                    </div>
                ` : ''}
            </div>
        `;
    }).join('');
}

async function sendFeedback(targetId, type, btnEl) {
    try {
        if (btnEl && btnEl.parentElement) {
            const parent = btnEl.parentElement;
            if (type === 'accurate') {
                parent.innerHTML = '<span class="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 text-[10px] font-bold">✓ Rated High Signal</span>';
            } else {
                parent.innerHTML = '<span class="px-2 py-0.5 rounded bg-rose-500/20 text-rose-300 border border-rose-500/40 text-[10px] font-bold">✓ Flagged as Noise</span>';
            }
        }
        await fetch('/api/feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_id: targetId, feedback_type: type })
        });
        if (typeof showBanner === 'function') {
            showBanner(type === 'accurate' ? '👍 Rated High Signal: Feedback logged for agent calibration.' : '👎 Flagged Noise: Feedback logged to reduce false alarms.');
        }
    } catch (err) {
        console.error('Failed to submit feedback:', err);
    }
}

/**
 * AI Portfolio Officer Chat Handler
 */
function sendQuickChat(text) {
    if (typeof switchTab === 'function') switchTab('tab-chat');
    const input = document.getElementById('chat-input');
    if (input) {
        input.value = text;
        handleChatSubmit(new Event('submit'));
    }
}

async function handleChatSubmit(e) {
    if (e) e.preventDefault();
    const input = document.getElementById('chat-input');
    const text = input ? input.value.trim() : '';
    if (!text) return;

    const chatMessages = document.getElementById('chat-messages');
    const sendBtn = document.getElementById('chat-send-btn');
    if (!chatMessages) return;

    chatHistory.push({ role: 'user', content: text });
    chatMessages.innerHTML += `
        <div class="flex items-start justify-end space-x-2.5">
            <div class="p-3.5 bg-purple-600/20 border border-purple-500/40 text-purple-100 rounded-xl max-w-2xl leading-relaxed text-xs">
                ${typeof escapeHtml === 'function' ? escapeHtml(text) : text}
            </div>
            <div class="w-6 h-6 rounded-full bg-purple-700 text-white flex items-center justify-center flex-shrink-0 text-[11px] font-bold">You</div>
        </div>
    `;
    if (input) input.value = '';
    chatMessages.scrollTop = chatMessages.scrollHeight;

    if (sendBtn) {
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<span class="pulse-subtle">Thinking...</span>';
    }

    const loadingId = 'loading-' + Date.now();
    chatMessages.innerHTML += `
        <div id="${loadingId}" class="flex items-start space-x-2.5 text-slate-400">
            <div class="w-6 h-6 rounded-full bg-purple-900/60 text-purple-300 flex items-center justify-center flex-shrink-0 text-[11px] font-bold">AI</div>
            <div class="p-3 bg-dark-900 border border-slate-800 rounded-xl max-w-2xl text-xs space-y-1">
                <div class="flex items-center space-x-2 text-purple-400 font-mono">
                    <span class="inline-block w-2 h-2 rounded-full bg-purple-400 animate-ping"></span>
                    <span>Analyzing live holdings & synthesising response...</span>
                </div>
            </div>
        </div>
    `;
    chatMessages.scrollTop = chatMessages.scrollHeight;

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text, history: chatHistory })
        });
        const data = await res.json();
        
        const loadEl = document.getElementById(loadingId);
        if (loadEl) loadEl.remove();

        if (res.ok && data.reply) {
            chatHistory.push({ role: 'assistant', content: data.reply });
            const formattedReply = typeof formatMarkdown === 'function' ? formatMarkdown(data.reply) : data.reply;
            chatMessages.innerHTML += `
                <div class="flex items-start space-x-2.5 text-slate-200">
                    <div class="w-6 h-6 rounded-full bg-purple-900/60 text-purple-300 flex items-center justify-center flex-shrink-0 text-[11px] font-bold">AI</div>
                    <div class="p-3.5 bg-dark-900 border border-slate-800 rounded-xl max-w-3xl leading-relaxed text-xs space-y-2">
                        ${formattedReply}
                    </div>
                </div>
            `;
        } else {
            chatMessages.innerHTML += `
                <div class="flex items-start space-x-2.5 text-rose-400">
                    <div class="w-6 h-6 rounded-full bg-rose-950 text-rose-400 flex items-center justify-center flex-shrink-0 text-[11px] font-bold">!</div>
                    <div class="p-3 bg-rose-950/30 border border-rose-800 rounded-xl max-w-2xl text-xs">
                        Error generating analysis. Please try again.
                    </div>
                </div>
            `;
        }
    } catch (err) {
        const loadEl = document.getElementById(loadingId);
        if (loadEl) loadEl.remove();
        chatMessages.innerHTML += `
            <div class="flex items-start space-x-2.5 text-rose-400">
                <div class="w-6 h-6 rounded-full bg-rose-950 text-rose-400 flex items-center justify-center flex-shrink-0 text-[11px] font-bold">!</div>
                <div class="p-3 bg-rose-950/30 border border-rose-800 rounded-xl max-w-2xl text-xs">
                    Network connection error: ${err.message}
                </div>
            </div>
        `;
    } finally {
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.innerHTML = '<span>Send</span> <span>🚀</span>';
        }
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
}

/**
 * Earnings Calendar Loader
 */
async function loadEarningsCalendar() {
    const container = document.getElementById('earnings-calendar-container');
    if (!container) return;

    const btn = document.getElementById('btn-refresh-earnings');
    if (btn) btn.innerHTML = '<span class="pulse-subtle">🔄 Syncing Nasdaq Feed...</span>';

    try {
        const res = await fetch('/api/earnings/calendar');
        const data = await res.json();

        if (res.ok && data.schedule && data.schedule.length > 0) {
            const ptHoldings = activePortfolioData?.holdings?.map(h => h.ticker) || [];

            const renderCompanyList = (list, isBMO) => {
                if (!list || list.length === 0) {
                    return '<div class="text-slate-500 text-[11px] italic py-1">No prominent reports scheduled.</div>';
                }
                return list.map(c => {
                    const isPortfolio = ptHoldings.includes(c.ticker);
                    return `
                        <div class="p-2.5 bg-dark-900/90 border ${isPortfolio ? 'border-cyan-500/60 bg-cyan-950/20' : 'border-slate-800/80'} rounded-xl flex items-center justify-between text-xs">
                            <div class="space-y-0.5">
                                <div class="flex items-center space-x-1.5">
                                    <span class="font-bold ${isPortfolio ? 'text-cyan-300' : 'text-slate-100'}">${c.ticker}</span>
                                    ${isPortfolio ? '<span class="px-1.5 py-0.2 bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 rounded text-[9px] font-bold">PORTFOLIO</span>' : ''}
                                    <span class="text-[11px] text-slate-400 truncate max-w-[150px] font-normal">${c.name}</span>
                                </div>
                                <div class="text-[10px] text-slate-500 font-mono">
                                    <span>Cap: ${c.market_cap_str || 'N/A'}</span>
                                    ${c.eps_forecast ? ` &bull; <span>Est EPS: <b>${c.eps_forecast}</b></span>` : ''}
                                </div>
                            </div>
                            <span class="px-2 py-0.5 text-[10px] font-mono rounded font-semibold ${isBMO ? 'bg-amber-500/10 text-amber-300 border border-amber-500/20' : 'bg-indigo-500/10 text-indigo-300 border border-indigo-500/20'}">
                                ${isBMO ? '🌅 Pre-Market' : '🌙 Post-Market'}
                            </span>
                        </div>
                    `;
                }).join('');
            };

            container.innerHTML = `
                <div class="grid grid-cols-1 xl:grid-cols-2 gap-4 w-full">
                    ${data.schedule.map(day => `
                        <div class="p-4 bg-dark-950 border border-slate-800/90 rounded-2xl space-y-3 shadow-md flex flex-col justify-between">
                            <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
                                <div class="font-bold text-sm text-cyan-400 flex items-center space-x-2">
                                    <span>📅</span>
                                    <span>${day.day_name || day.display_date}</span>
                                </div>
                                <span class="text-[10px] font-mono text-slate-400 bg-dark-900 px-2 py-0.5 rounded border border-slate-800">
                                    ${(day.bmo?.length || 0) + (day.amc?.length || 0)} Calls Monitored
                                </span>
                            </div>
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 flex-1">
                                <div class="space-y-2">
                                    <div class="text-[11px] font-semibold text-amber-400/90 flex items-center space-x-1">
                                        <span>🌅 Before Market Open (BMO)</span>
                                    </div>
                                    <div class="space-y-1.5">
                                        ${renderCompanyList(day.bmo, true)}
                                    </div>
                                </div>
                                <div class="space-y-2">
                                    <div class="text-[11px] font-semibold text-indigo-400/90 flex items-center space-x-1">
                                        <span>🌙 After Market Close (AMC)</span>
                                    </div>
                                    <div class="space-y-1.5">
                                        ${renderCompanyList(day.amc, false)}
                                    </div>
                                </div>
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;
        } else {
            container.innerHTML = '<div class="p-4 bg-dark-950 border border-slate-800 rounded-xl text-xs text-slate-400">No corporate earnings calls found for the upcoming 7 calendar days.</div>';
        }
    } catch (err) {
        container.innerHTML = `<div class="p-4 bg-rose-950/30 border border-rose-800 rounded-xl text-xs text-rose-400">Error loading earnings feed: ${esc(err.message)}</div>`;
    } finally {
        if (btn) btn.innerHTML = '<span>🔄 Refresh Calendar</span>';
    }
}

/**
 * Deterministic Macroeconomic & Central Bank Calendar Loader
 */
async function loadEconomicCalendar(isManual = false) {
    const container = document.getElementById('economic-calendar-container');
    if (!container) return;

    const btn = document.getElementById('btn-refresh-economic');
    const btnIcon = document.getElementById('btn-refresh-economic-icon');
    const btnText = document.getElementById('btn-refresh-economic-text');
    const lastUpdated = document.getElementById('macro-calendar-last-updated');

    if (btn) {
        btn.disabled = true;
        if (btnIcon) btnIcon.classList.add('animate-spin');
        if (btnText) btnText.textContent = isManual ? 'Syncing...' : 'Updating...';
    }

    if (isManual) {
        container.style.opacity = '0.6';
    }

    const minDelay = isManual ? 400 : 0;

    try {
        const fetchPromise = fetch(`/api/economic/calendar?t=${Date.now()}`, { cache: 'no-store' });
        const [res] = await Promise.all([
            fetchPromise,
            new Promise(resolve => setTimeout(resolve, minDelay))
        ]);

        const data = await res.json();

        if (res.ok && data.calendar) {
            const cal = data.calendar;
            const todayEvents = cal.today_events || [];
            const tomorrowEvents = cal.tomorrow_events || [];
            const upcomingEvents = cal.upcoming_events_7d || [];

            const renderStatusBadge = (status) => {
                if (status === 'COMPLETED') {
                    return '<span class="px-2 py-0.5 bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 rounded-full text-[10px] font-mono font-bold flex items-center space-x-1"><span>✅</span><span>COMPLETED</span></span>';
                }
                if (status === 'IMMINENT') {
                    return '<span class="px-2 py-0.5 bg-amber-500/20 text-amber-300 border border-amber-500/40 rounded-full text-[10px] font-mono font-bold flex items-center space-x-1 animate-pulse"><span>⏳</span><span>IMMINENT</span></span>';
                }
                if (status === 'TOMORROW') {
                    return '<span class="px-2 py-0.5 bg-blue-500/10 text-blue-400 border border-blue-500/30 rounded-full text-[10px] font-mono font-bold">TOMORROW</span>';
                }
                return '<span class="px-2 py-0.5 bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 rounded-full text-[10px] font-mono font-bold">SCHEDULED</span>';
            };

            const renderCategoryBadge = (cat, importance) => {
                const isCrit = importance === 'CRITICAL';
                return `<span class="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold ${isCrit ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40' : 'bg-slate-800 text-slate-300 border border-slate-700'}">${cat}</span>`;
            };

            container.innerHTML = `
                <div class="space-y-4 w-full">
                    <!-- Section 1: Today's Macro Catalysts -->
                    <div class="p-4 bg-dark-950 border border-slate-800/90 rounded-2xl space-y-3 shadow-md">
                        <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
                            <div class="font-bold text-sm text-indigo-400 flex items-center space-x-2">
                                <span>🏛️</span>
                                <span>Today's Catalysts (${cal.today_date})</span>
                            </div>
                            <span class="text-[10px] font-mono text-slate-400 bg-dark-900 px-2 py-0.5 rounded border border-slate-800">
                                ${todayEvents.length} Events Tracked Today
                            </span>
                        </div>
                        ${todayEvents.length > 0 ? `
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                                ${todayEvents.map(ev => `
                                    <div class="p-3 bg-dark-900/90 border ${ev.status === 'COMPLETED' ? 'border-emerald-500/30 bg-emerald-950/10' : 'border-indigo-500/40 bg-indigo-950/10'} rounded-xl space-y-2 transition hover:border-slate-700">
                                        <div class="flex items-start justify-between gap-2">
                                            <div class="space-y-1">
                                                <div class="flex items-center space-x-1.5">
                                                    ${renderCategoryBadge(ev.category, ev.importance)}
                                                    <span class="text-xs font-bold text-slate-100">${esc(ev.name)}</span>
                                                </div>
                                                <div class="text-[11px] text-slate-400 font-mono flex items-center space-x-1.5">
                                                    <span>🕒 ${esc(ev.time_display)}</span>
                                                </div>
                                            </div>
                                            <div>
                                                ${renderStatusBadge(ev.status)}
                                            </div>
                                        </div>
                                        <div class="text-[11px] text-slate-300 bg-dark-950/60 p-2 rounded-lg border border-slate-800/60 leading-relaxed">
                                            ${esc(ev.status_desc)}
                                        </div>
                                    </div>
                                `).join('')}
                            </div>
                        ` : `
                            <div class="text-slate-500 text-xs italic py-2">No tier-1 macroeconomic releases scheduled for today (${cal.today_date}).</div>
                        `}
                    </div>

                    <!-- Section 2: Tomorrow & Upcoming 7-Day Releases -->
                    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        <!-- Tomorrow -->
                        <div class="p-4 bg-dark-950 border border-slate-800/90 rounded-2xl space-y-3 shadow-md">
                            <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
                                <div class="font-bold text-sm text-blue-400 flex items-center space-x-2">
                                    <span>🌅</span>
                                    <span>Tomorrow's Focus (${cal.tomorrow_date})</span>
                                </div>
                                <span class="text-[10px] font-mono text-slate-400 bg-dark-900 px-2 py-0.5 rounded border border-slate-800">
                                    ${tomorrowEvents.length} Releases
                                </span>
                            </div>
                            ${tomorrowEvents.length > 0 ? `
                                <div class="space-y-2">
                                    ${tomorrowEvents.map(ev => `
                                        <div class="p-2.5 bg-dark-900/90 border border-slate-800/80 rounded-xl flex items-center justify-between text-xs">
                                            <div class="space-y-0.5">
                                                <div class="flex items-center space-x-1.5">
                                                    ${renderCategoryBadge(ev.category, ev.importance)}
                                                    <span class="font-bold text-slate-200">${esc(ev.name)}</span>
                                                </div>
                                                <div class="text-[10px] text-slate-400 font-mono">${esc(ev.time_display)}</div>
                                            </div>
                                            ${renderStatusBadge('TOMORROW')}
                                        </div>
                                    `).join('')}
                                </div>
                            ` : `
                                <div class="text-slate-500 text-xs italic py-2">No major macroeconomic releases scheduled for tomorrow (${cal.tomorrow_date}).</div>
                            `}
                        </div>

                        <!-- Next 7 Days -->
                        <div class="p-4 bg-dark-950 border border-slate-800/90 rounded-2xl space-y-3 shadow-md">
                            <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
                                <div class="font-bold text-sm text-cyan-400 flex items-center space-x-2">
                                    <span>📆</span>
                                    <span>Upcoming 7-Day Macro Horizon</span>
                                </div>
                                <span class="text-[10px] font-mono text-slate-400 bg-dark-900 px-2 py-0.5 rounded border border-slate-800">
                                    ${upcomingEvents.length} Key Catalysts
                                </span>
                            </div>
                            ${upcomingEvents.length > 0 ? `
                                <div class="space-y-2">
                                    ${upcomingEvents.map(ev => `
                                        <div class="p-2.5 bg-dark-900/90 border border-slate-800/80 rounded-xl flex items-center justify-between text-xs">
                                            <div class="space-y-0.5">
                                                <div class="flex items-center space-x-1.5">
                                                    ${renderCategoryBadge(ev.category, ev.importance)}
                                                    <span class="font-bold text-slate-200">${esc(ev.name)}</span>
                                                </div>
                                                <div class="text-[10px] text-slate-400 font-mono">${esc(ev.time_display)}</div>
                                            </div>
                                            <span class="px-2 py-0.5 bg-slate-800 text-slate-300 border border-slate-700 rounded text-[10px] font-mono">
                                                in ${ev.days_away}d
                                            </span>
                                        </div>
                                    `).join('')}
                                </div>
                            ` : `
                                <div class="text-slate-500 text-xs italic py-2">No upcoming major catalysts within the 7-day window.</div>
                            `}
                        </div>
                    </div>
                </div>
            `;

            // Update timestamp
            const nowTime = new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' });
            if (lastUpdated) {
                lastUpdated.innerHTML = `<span class="text-emerald-400">●</span> Synced ${nowTime}`;
                lastUpdated.classList.remove('hidden');
            }

            if (isManual) {
                if (typeof showBanner === 'function') {
                    showBanner(`✅ Macroeconomic Calendar refreshed at ${nowTime}. All catalysts synchronized.`);
                }
                if (btnIcon) {
                    btnIcon.classList.remove('animate-spin');
                    btnIcon.textContent = '✅';
                }
                if (btnText) btnText.textContent = 'Synced!';
            }
        } else {
            container.innerHTML = '<div class="p-4 bg-dark-950 border border-slate-800 rounded-xl text-xs text-slate-400">No macroeconomic events found.</div>';
        }
    } catch (err) {
        container.innerHTML = `<div class="p-4 bg-rose-950/30 border border-rose-800 rounded-xl text-xs text-rose-400">Error loading macroeconomic calendar: ${esc(err.message)}</div>`;
        if (isManual && typeof showBanner === 'function') {
            showBanner(`❌ Error refreshing macro calendar: ${err.message}`, true);
        }
    } finally {
        container.style.opacity = '1';
        setTimeout(() => {
            if (btn) btn.disabled = false;
            if (btnIcon) {
                btnIcon.classList.remove('animate-spin');
                btnIcon.textContent = '🔄';
            }
            if (btnText) btnText.textContent = 'Refresh Macro Calendar';
        }, isManual ? 1200 : 0);
    }
}

/**
 * Market Intelligence Briefings Archive & Reader
 */
function formatBriefingTimestamp(isoStr, includeDate = true) {
    if (!isoStr) return 'Recently';
    try {
        const d = typeof toPstDate === 'function' ? toPstDate(isoStr) : new Date(isoStr);
        if (!d) return String(isoStr).slice(0, 16);
        if (includeDate) {
            return d.toLocaleDateString('en-US', {
                timeZone: 'America/Los_Angeles',
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                timeZoneName: 'short'
            });
        } else {
            return d.toLocaleTimeString('en-US', {
                timeZone: 'America/Los_Angeles',
                hour: '2-digit',
                minute: '2-digit',
                timeZoneName: 'short'
            });
        }
    } catch (_) {
        return String(isoStr).slice(0, 16);
    }
}

function formatFullBriefingTimestamp(isoStr) {
    if (!isoStr) return 'Unknown date';
    try {
        const d = typeof toPstDate === 'function' ? toPstDate(isoStr) : new Date(isoStr);
        if (!d) return String(isoStr);
        return d.toLocaleString('en-US', {
            timeZone: 'America/Los_Angeles',
            weekday: 'short',
            month: 'short',
            day: 'numeric',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            timeZoneName: 'short'
        });
    } catch (_) {
        return String(isoStr);
    }
}

async function loadMarketBriefings() {
    const listCont = document.getElementById('briefings-list-container');
    const countBadge = document.getElementById('briefings-count-badge');
    if (!listCont) return;

    try {
        const res = await fetch('/api/briefings');
        if (!res.ok) {
            const text = await res.text();
            let errMsg = `Server returned status ${res.status}`;
            try {
                const parsed = JSON.parse(text);
                if (parsed && parsed.detail) errMsg = parsed.detail;
            } catch(_) {}
            listCont.innerHTML = `<div class="p-4 text-center text-xs text-rose-400">Briefings unavailable: ${esc(errMsg)}</div>`;
            return;
        }
        const data = await res.json();
        if (data && data.briefings && data.briefings.length > 0) {
            currentBriefingsArchive = data.briefings;
            if (countBadge) countBadge.textContent = `${currentBriefingsArchive.length} Reports`;
            renderBriefingsArchiveList();
            if (!activeBriefingId && currentBriefingsArchive.length > 0) {
                selectBriefing(currentBriefingsArchive[0].report_id);
            }
        } else {
            currentBriefingsArchive = [];
            if (countBadge) countBadge.textContent = `0 Reports`;
            listCont.innerHTML = '<div class="p-4 text-center text-xs text-slate-500">No briefings found.<br>Click any slot above to generate one.</div>';
        }
    } catch (err) {
        console.error("loadMarketBriefings error:", err);
        listCont.innerHTML = `<div class="p-4 text-xs text-rose-400">Failed to load briefings: ${esc(err.message || err)}</div>`;
    }
}

function filterBriefingsArchive(slot) {
    currentBriefingFilter = slot;
    document.querySelectorAll('.briefing-filter-btn').forEach(btn => {
        const s = btn.getAttribute('data-slot');
        if (s === slot) {
            btn.className = 'briefing-filter-btn px-2 py-1 bg-cyan-500/20 text-cyan-300 rounded border border-cyan-500/30 font-semibold';
        } else {
            btn.className = 'briefing-filter-btn px-2 py-1 bg-dark-950 text-slate-400 hover:text-white rounded border border-slate-800';
        }
    });
    renderBriefingsArchiveList();
}

function renderBriefingsArchiveList() {
    const listCont = document.getElementById('briefings-list-container');
    if (!listCont) return;

    const filtered = currentBriefingFilter === 'all' 
        ? currentBriefingsArchive 
        : currentBriefingsArchive.filter(b => (b.slot || '').toLowerCase() === currentBriefingFilter);

    if (filtered.length === 0) {
        listCont.innerHTML = `
            <div class="p-4 text-center text-xs text-slate-500">
                No briefings found for this filter.<br>Click any slot above to generate one.
            </div>
        `;
        return;
    }

    listCont.innerHTML = filtered.map(b => {
        const isSelected = b.report_id === activeBriefingId;
        const slotKey = (b.slot || 'premarket').toLowerCase();
        const style = SLOT_STYLES[slotKey] || SLOT_STYLES.premarket;
        const timeStr = formatBriefingTimestamp(b.generated_at, true);
        const preview = (b.executive_summary || '').replace(/<[^>]*>/g, '').slice(0, 110);

        return `
            <div onclick="selectBriefing('${esc(b.report_id)}')" class="p-3 bg-dark-950 hover:bg-slate-800/80 cursor-pointer rounded-xl border transition-all ${isSelected ? 'border-cyan-500/80 bg-slate-800/60 shadow-md' : 'border-slate-800/80 hover:border-slate-700'}">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-1.5 font-bold text-xs ${isSelected ? 'text-cyan-300' : 'text-slate-200'}">
                        <span>${style.icon}</span>
                        <span class="capitalize">${esc(b.slot_title || b.slot)}</span>
                    </div>
                    <span class="text-[10px] font-mono text-slate-500">${esc(timeStr)}</span>
                </div>
                <div class="text-[11px] text-slate-400 mt-1 line-clamp-2 leading-snug">
                    ${esc(preview || 'Executive briefing report...')}
                </div>
                <div class="flex items-center justify-between mt-2 pt-1.5 border-t border-slate-800/60 text-[10px] text-slate-500">
                    <span class="font-mono">${esc(b.report_id.slice(0, 18))}...</span>
                    ${b.dispatched_channels && b.dispatched_channels.length > 0 ? '<span class="text-cyan-400 font-semibold">✈️ Telegram</span>' : '<span class="text-slate-500">Web</span>'}
                </div>
            </div>
        `;
    }).join('');
}

function selectBriefing(reportId) {
    activeBriefingId = reportId;
    const b = currentBriefingsArchive.find(item => item.report_id === reportId);
    if (!b) return;

    const iconEl = document.getElementById('reader-slot-icon');
    const titleEl = document.getElementById('reader-slot-title');
    const badgeEl = document.getElementById('reader-slot-badge');
    const timeEl = document.getElementById('reader-timestamp');
    const contentEl = document.getElementById('reader-content-body');

    const slotKey = (b.slot || 'premarket').toLowerCase();
    const style = SLOT_STYLES[slotKey] || SLOT_STYLES.premarket;
    const timeStr = formatFullBriefingTimestamp(b.generated_at);

    if (iconEl) iconEl.textContent = style.icon;
    if (titleEl) titleEl.textContent = b.slot_title || `${slotKey.toUpperCase()} Intelligence Briefing`;
    if (badgeEl) {
        badgeEl.textContent = slotKey.toUpperCase();
        badgeEl.className = `px-2 py-0.5 text-[10px] font-mono border rounded font-semibold ${style.badgeBg}`;
        badgeEl.classList.remove('hidden');
    }
    if (timeEl) {
        timeEl.textContent = `Generated: ${timeStr} • Report ID: ${b.report_id}`;
    }

    if (contentEl) {
        const htmlContent = b.message_html || b.executive_summary || '<div class="text-slate-400 italic">No content available.</div>';
        contentEl.innerHTML = htmlContent;
    }

    renderBriefingsArchiveList();
}

async function triggerGenerateBriefing(slot) {
    const banner = document.getElementById('briefing-generation-banner');
    const bannerText = document.getElementById('briefing-generation-text');
    const dispatchTg = document.getElementById('briefing-dispatch-telegram')?.checked || false;

    const slotNames = {
        premarket: 'Pre-Market Intelligence (6:30 AM PST)',
        midmarket: 'Mid-Market Pulse (10:00 AM PST)',
        postmarket: 'Post-Market Wrap (3:00 PM PST)',
        weekend: 'Weekend Macro & Week-Ahead (9:00 PM PST)',
        earnings: '7-Day Corporate Earnings Outlook'
    };

    if (banner) {
        if (bannerText) bannerText.textContent = `Synthesizing ${slotNames[slot] || slot} with Gemini 3.8 Flash...`;
        banner.classList.remove('hidden');
    }

    ['premarket', 'midmarket', 'postmarket', 'weekend', 'earnings'].forEach(s => {
        const btn = document.getElementById('btn-slot-' + s);
        if (btn) btn.classList.add('opacity-50', 'pointer-events-none');
    });

    try {
        if (typeof showBanner === 'function') showBanner(`⏳ Generating ${slotNames[slot] || slot} briefing...`);
        const res = await fetch('/api/briefings/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slot: slot, dispatch_telegram: dispatchTg })
        });
        
        if (!res.ok) {
            const errText = await res.text();
            let msg = `Server error ${res.status}`;
            try {
                const parsed = JSON.parse(errText);
                if (parsed && parsed.detail) msg = parsed.detail;
            } catch (_) {}
            if (typeof showBanner === 'function') showBanner(`❌ Error: ${msg}`);
            return;
        }

        const data = await res.json();
        if (data && data.message_html) {
            if (typeof showBanner === 'function') showBanner(`✅ ${data.slot_title || slot} generated successfully!${data.dispatched_telegram ? ' (Pushed to Telegram)' : ''}`);
            await loadMarketBriefings();
            if (currentBriefingsArchive.length > 0) {
                selectBriefing(currentBriefingsArchive[0].report_id);
            }
        } else {
            if (typeof showBanner === 'function') showBanner(`❌ Error: Failed to generate briefing content.`);
        }
    } catch (err) {
        console.error("triggerGenerateBriefing error:", err);
        if (typeof showBanner === 'function') showBanner(`❌ Error generating briefing: ${err.message || err}`);
    } finally {
        if (banner) banner.classList.add('hidden');
        ['premarket', 'midmarket', 'postmarket', 'weekend', 'earnings'].forEach(s => {
            const btn = document.getElementById('btn-slot-' + s);
            if (btn) btn.classList.remove('opacity-50', 'pointer-events-none');
        });
    }
}

async function copyCurrentBriefingText() {
    const contentEl = document.getElementById('reader-content-body');
    const btnText = document.getElementById('copy-briefing-btn-text');
    if (!contentEl) return;

    const textToCopy = contentEl.innerText || contentEl.textContent;
    try {
        await navigator.clipboard.writeText(textToCopy);
        if (btnText) {
            btnText.textContent = '✅ Copied!';
            setTimeout(() => { btnText.textContent = 'Copy Text'; }, 2000);
        }
    } catch (err) {
        if (typeof showBanner === 'function') showBanner('❌ Could not copy text to clipboard.');
    }
}

async function dispatchCurrentBriefingToTelegram() {
    if (!activeBriefingId) {
        if (typeof showBanner === 'function') showBanner('⚠️ Please select a briefing to dispatch.');
        return;
    }
    try {
        if (typeof showBanner === 'function') showBanner('✈️ Dispatching briefing to Telegram...');
        const res = await fetch(`/api/briefings/${activeBriefingId}/dispatch`, {
            method: 'POST'
        });
        const data = await res.json();
        if (res.ok) {
            if (typeof showBanner === 'function') showBanner('✅ ' + (data.message || 'Briefing dispatched to Telegram!'));
        } else {
            if (typeof showBanner === 'function') showBanner('❌ ' + (data.detail || 'Failed to dispatch to Telegram.'));
        }
    } catch (err) {
        if (typeof showBanner === 'function') showBanner('❌ Network error: ' + err.message);
    }
}

// Global exports
window.currentScanSubView = currentScanSubView;
window.switchScanSubView = switchScanSubView;
window.triggerScan = triggerScan;
window.renderBriefing = renderBriefing;
window.discoverThematicAlpha = discoverThematicAlpha;
window.toggleAlphaDiscoveryModal = toggleAlphaDiscoveryModal;
window.scanMoonshotOpportunities = scanMoonshotOpportunities;
window.renderMoonshots = renderMoonshots;
window.sendFeedback = sendFeedback;
window.sendQuickChat = sendQuickChat;
window.handleChatSubmit = handleChatSubmit;
window.loadEarningsCalendar = loadEarningsCalendar;
window.loadEconomicCalendar = loadEconomicCalendar;
window.formatBriefingTimestamp = formatBriefingTimestamp;
window.formatFullBriefingTimestamp = formatFullBriefingTimestamp;
window.loadMarketBriefings = loadMarketBriefings;
window.filterBriefingsArchive = filterBriefingsArchive;
window.renderBriefingsArchiveList = renderBriefingsArchiveList;
window.selectBriefing = selectBriefing;
window.triggerGenerateBriefing = triggerGenerateBriefing;
window.copyCurrentBriefingText = copyCurrentBriefingText;
window.dispatchCurrentBriefingToTelegram = dispatchCurrentBriefingToTelegram;
