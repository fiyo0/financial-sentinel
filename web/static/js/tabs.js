/**
 * tabs.js - Navigation, Adaptive HUD State, and Application Utilities
 * Part of Financial Sentinel Modular Architecture
 */

let currentTabId = 'tab-portfolio';
let hudUserOverride = null; // null = auto (tab-driven), true = expanded, false = collapsed

/**
 * Switch active dashboard tab and synchronize Adaptive HUD.
 * @param {string} tabId Target tab DOM ID (e.g. 'tab-portfolio', 'tab-deepdive')
 * @param {boolean} updateUrl Whether to update browser URL query params
 */
function switchTab(tabId, updateUrl = true) {
    currentTabId = tabId;
    hudUserOverride = null; // Reset manual override on explicit tab change

    // Hide all tab panes
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));

    // Reset button styles
    document.querySelectorAll('.tab-btn').forEach(el => {
        el.className = 'tab-btn px-3.5 py-2 border-b-2 rounded-t-lg transition flex items-center space-x-1.5 tab-inactive';
    });

    // Activate target pane
    const activeContent = document.getElementById(tabId);
    if (activeContent) {
        activeContent.classList.remove('hidden');
    }

    // Activate target button
    const activeBtn = document.getElementById('btn-' + tabId);
    if (activeBtn) {
        activeBtn.className = 'tab-btn px-3.5 py-2 border-b-2 rounded-t-lg transition flex items-center space-x-1.5 tab-active';
    }

    // Update Adaptive HUD (full on portfolio, slim sticky bar on all others)
    updateHudVisibility(tabId);

    // Lazy load tab data if required
    if (tabId === 'tab-earnings') {
        if (typeof loadEarningsCalendar === 'function') loadEarningsCalendar();
    } else if (tabId === 'tab-briefings') {
        if (typeof loadMarketBriefings === 'function') loadMarketBriefings();
    } else if (tabId === 'tab-scans') {
        const riskCont = document.getElementById('risk-alerts-container');
        const hasRiskCards = riskCont && riskCont.children.length > 0 && !riskCont.children[0].textContent.includes('No risk alerts generated yet');
        if (!hasRiskCards) {
            const briefingData = (window._portfolioCache && window._portfolioCache.briefing) || window.INITIAL_BRIEFING;
            if (briefingData && typeof renderBriefing === 'function') {
                renderBriefing(briefingData);
            }
        }
    } else if (tabId === 'tab-deepdive') {
        if (typeof loadDeepDivesArchive === 'function') loadDeepDivesArchive();
        if (typeof restoreLastDeepDive === 'function') restoreLastDeepDive();
    }

    // Update browser URL query string without reloading
    if (updateUrl && window.history && window.history.replaceState) {
        const cleanTabName = tabId.replace('tab-', '');
        const currentUrl = new URL(window.location);
        currentUrl.searchParams.set('tab', cleanTabName);
        window.history.replaceState({}, '', currentUrl);
    }
}

/**
 * Updates Adaptive HUD visibility between full 6-card grid and slim 1-line ticker.
 */
function updateHudVisibility(tabId) {
    const fullHud = document.getElementById('portfolio-hud-full');
    const compactHud = document.getElementById('portfolio-hud-compact');
    if (!fullHud || !compactHud) return;

    let showFull;
    if (hudUserOverride !== null) {
        showFull = hudUserOverride;
    } else {
        // Auto: full on portfolio tab, compact on every other tab
        showFull = (tabId === 'tab-portfolio');
    }

    if (showFull) {
        fullHud.classList.remove('hidden');
        compactHud.classList.add('hidden');
    } else {
        fullHud.classList.add('hidden');
        compactHud.classList.remove('hidden');
    }

    // Update toggle button text if present
    const toggleBtnCompact = document.getElementById('btn-toggle-hud-compact');
    const toggleBtnFull = document.getElementById('btn-toggle-hud-full');
    if (toggleBtnCompact) {
        toggleBtnCompact.innerHTML = '<span>↕ Expand HUD</span>';
    }
    if (toggleBtnFull) {
        toggleBtnFull.innerHTML = '<span>↕ Collapse HUD</span>';
    }
}

/**
 * Manual user toggle to expand or collapse the HUD regardless of active tab.
 */
function toggleHudManual() {
    const fullHud = document.getElementById('portfolio-hud-full');
    const isCurrentlyFull = fullHud && !fullHud.classList.contains('hidden');
    hudUserOverride = !isCurrentlyFull;
    updateHudVisibility(currentTabId);
}

/**
 * Display toast status banner at the top of the interface.
 */
function showBanner(text, isError = false) {
    const banner = document.getElementById('status-banner');
    const bannerContent = document.getElementById('status-banner-content');
    const bannerText = document.getElementById('status-banner-text');
    if (!banner || !bannerText) return;

    bannerText.textContent = text;
    if (isError) {
        bannerContent.className = 'p-3 bg-rose-950/50 border border-rose-800/80 rounded-xl text-xs text-rose-300 flex items-center justify-between';
    } else {
        bannerContent.className = 'p-3 bg-emerald-950/40 border border-emerald-800/80 rounded-xl text-xs text-emerald-300 flex items-center justify-between';
    }
    banner.classList.remove('hidden');
}

/**
 * Clock and timezone formatting utilities.
 */
function getPstTimeString() {
    const now = new Date();
    return new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/Los_Angeles',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true
    }).format(now);
}

function updateHeaderPstClock() {
    const clock = document.getElementById('header-pst-clock') || document.getElementById('pst-clock');
    if (clock) {
        clock.textContent = getPstTimeString() + ' PT';
    }
}

function toPstDate(isoStr) {
    if (!isoStr) return new Date();
    try {
        let clean = isoStr;
        if (!isoStr.endsWith('Z') && !isoStr.includes('+')) {
            clean += 'Z';
        }
        return new Date(clean);
    } catch(e) {
        return new Date(isoStr);
    }
}

/**
 * Markdown & HTML sanitization and styling helpers.
 * Delegates to centralized escaper.js primitive (R-3).
 */
const escapeHtml = (typeof window !== 'undefined' && window.esc) ? window.esc : function (string) {
    if (!string) return '';
    return String(string)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
};

function formatMarkdown(text) {
    if (!text) return '';
    let html = text;

    // 1. Strip potentially malicious tags
    html = html.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '');
    html = html.replace(/<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>/gi, '');

    // 2. Unescape accidental HTML entities (&lt;b&gt; -> <b>, &lt;code&gt; -> <code>)
    html = html.replace(/&lt;(\/?[a-z0-9]+)&gt;/gi, '<$1>');
    html = html.replace(/&quot;/gi, '"');
    html = html.replace(/&#039;/gi, "'");

    // 3. Parse Telegram HTML tags into styled Tailwind elements
    html = html.replace(/<b>([\s\S]*?)<\/b>/gi, '<strong class="text-white font-semibold">$1</strong>');
    html = html.replace(/<strong>([\s\S]*?)<\/strong>/gi, '<strong class="text-white font-semibold">$1</strong>');
    html = html.replace(/<i>([\s\S]*?)<\/i>/gi, '<em class="text-slate-300 italic">$1</em>');
    html = html.replace(/<em>([\s\S]*?)<\/em>/gi, '<em class="text-slate-300 italic">$1</em>');
    html = html.replace(/<code>([\s\S]*?)<\/code>/gi, '<code class="px-1.5 py-0.5 bg-dark-950 text-cyan-300 rounded font-mono text-[11px] border border-slate-800">$1</code>');
    html = html.replace(/<pre>([\s\S]*?)<\/pre>/gi, '<pre class="p-2 bg-dark-950 text-cyan-300 rounded font-mono text-[11px] border border-slate-800 overflow-x-auto">$1</pre>');
    html = html.replace(/<a\s+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi, '<a href="$1" target="_blank" rel="noopener noreferrer" class="text-cyan-400 hover:underline">$2</a>');

    // 3. Parse Markdown bold (**text**) & inline code (`text`) in case model outputs markdown
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong class="text-white font-semibold">$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 bg-dark-950 text-cyan-300 rounded font-mono text-[11px] border border-slate-800">$1</code>');

    // 4. Bullet points (supporting •, -, *)
    html = html.replace(/^\s*[•\-\*]\s+(.*)$/gm, '<li class="ml-4 list-disc text-slate-300 my-0.5">$1</li>');
    // Numbered points
    html = html.replace(/^\s*(\d+)\.\s+(.*)$/gm, '<li class="ml-4 list-decimal text-slate-300 my-0.5">$2</li>');

    // 5. Line breaks and paragraphs
    html = html.replace(/\n\n/g, '<div class="h-2"></div>');
    html = html.replace(/\n/g, '<br>');
    html = html.replace(/<\/li>\s*<br\s*\/?>/gi, '</li>');

    return html;
}

/**
 * Authentication logout helper.
 */
async function lockSession() {
    if (confirm('Lock this Sentinel session and return to authentication portal?')) {
        try {
            await fetch('/api/auth/logout', { method: 'POST' });
        } catch (e) {}
        window.location.href = '/login';
    }
}

// Global exports
window.switchTab = switchTab;
window.updateHudVisibility = updateHudVisibility;
window.toggleHudManual = toggleHudManual;
window.showBanner = showBanner;
window.getPstTimeString = getPstTimeString;
window.updateHeaderPstClock = updateHeaderPstClock;
window.toPstDate = toPstDate;
window.escapeHtml = escapeHtml;
window.formatMarkdown = formatMarkdown;
window.lockSession = lockSession;

// Auto-start header PST clock ticker immediately
if (typeof window !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            updateHeaderPstClock();
            setInterval(updateHeaderPstClock, 1000);
        });
    } else {
        updateHeaderPstClock();
        setInterval(updateHeaderPstClock, 1000);
    }
}
