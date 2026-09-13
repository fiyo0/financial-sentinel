#!/bin/bash
# ==============================================================================
# FINANCIAL SENTINEL — INSTANT SECURE HTTPS TUNNEL (CLOUDFLARE)
# Gives you a secure, TLS-encrypted public URL to access your dashboard from anywhere.
# ==============================================================================

echo "🔒 Starting Financial Sentinel Secure HTTPS Tunnel..."

if ! command -v cloudflared &> /dev/null; then
    echo "📦 cloudflared not found. Installing via Homebrew..."
    brew install cloudflared
fi

echo "🚀 Launching tunnel to http://localhost:8000..."
cloudflared tunnel --url http://localhost:8000
