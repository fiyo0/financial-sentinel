/**
 * web/static/js/escaper.js
 * Zero-dependency tagged template literal HTML escaping primitive for Financial Sentinel.
 * Prevents client-side Cross-Site Scripting (XSS) in dynamic DOM updates.
 */

(function (window) {
  'use strict';

  /**
   * Escape unsafe HTML special characters in string.
   * @param {*} s - Input value to escape.
   * @returns {string} HTML-escaped string.
   */
  const esc = (s) => {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }[c]));
  };

  /**
   * Tagged template literal that auto-escapes all interpolations unless marked raw.
   * Usage: html`<div>${untrustedName}</div>`
   */
  const html = (strings, ...vals) => {
    return strings.reduce((out, s, i) => {
      let valStr = '';
      if (i < vals.length) {
        const v = vals[i];
        if (v && typeof v === 'object' && v.__raw) {
          valStr = String(v.value ?? '');
        } else if (Array.isArray(v)) {
          valStr = v.map((item) => (item && item.__raw ? String(item.value ?? '') : esc(item))).join('');
        } else {
          valStr = esc(v);
        }
      }
      return out + s + valStr;
    }, '');
  };

  /**
   * Mark HTML content as explicitly trusted/raw to prevent double-escaping.
   * @param {*} s - Trusted HTML string.
   * @returns {{__raw: boolean, value: string}}
   */
  const raw = (s) => ({ __raw: true, value: String(s ?? '') });

  // Expose globally to window
  window.esc = esc;
  window.html = html;
  window.raw = raw;
})(typeof window !== 'undefined' ? window : this);
