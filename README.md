# PumpIQ v4 — Memecoin Intelligence Terminal
Built by [@Madrimov_trade](https://t.me/Madrimov_trade)

## What's new in v4
- FIXED: First-20 buyers % (was showing 800M%, now correctly shows % of supply)
- FIXED: SOL liquidity (uses live SOL price, not hardcoded $170)
- FIXED: Sniper % capped at realistic values
- FIXED: No duplicate tokens — re-scanning updates existing entry
- NEW: Gated tier system — S/A/B/C/D/F tiers with hard requirements
  - S-TIER: score≥75 + LP locked + Mint revoked + rug_score<20 + no danger flags
  - A-TIER: score≥55 + (LP or Mint) + rug_score<50
  - B-TIER: score≥35 + rug_score<70
  - C-TIER: score≥20
  - D-TIER: score≥10
  - F-TIER: everything else
  - DANGER: rug_score>70 or 2+ danger flags
- NEW: Live Feed page (second tab) — auto-discovered Pump.fun tokens ≥$10K MC
- NEW: Tier distribution bar on live feed
- NEW: Danger alerts panel on live feed
- NEW: Click any live feed token → opens full analysis in scanner
- NEW: Sort live feed by time / score / MC
- NEW: Filter live feed by BUY signals / DANGER / NEW / ALERTS
- IMPROVED: Sector AI — Animal/Charity/etc detect properly
- IMPROVED: Age penalties — 7d/30d/60d tiers
- IMPROVED: SOL price updates every 60s from Jupiter

## Deploy
1. Push to GitHub
2. Railway auto-deploys
3. Add HELIUS_KEY variable in Railway for bundle/sniper data

## Add Helius (free)
1. helius.dev → sign up → copy API key
2. Railway → Variables → HELIUS_KEY = your_key

## API
- GET /api/analyze?ca=ADDRESS
- GET /api/refresh/ADDRESS
- GET /api/live?sort=time|score|mcap
- GET /api/alerts
- GET /api/stats
- GET /api/health

## Telegram
[@Madrimov_trade](https://t.me/Madrimov_trade)
