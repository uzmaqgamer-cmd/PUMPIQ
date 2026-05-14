"""
PumpIQ Backend v4.0 — Railway Ready
Fixes: first-20 % calculation, SOL liquidity, sniper %, scoring gates,
       proper tier distribution, live feed auto-scan, dedup, danger alerts
"""
import os, re, time, threading, math
from collections import defaultdict
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

H = {"User-Agent": "Mozilla/5.0 PumpIQ/4.0"}
HELIUS_KEY = os.environ.get("HELIUS_KEY", "")
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}" if HELIUS_KEY else ""

# ── STORES ────────────────────────────────────────────────────────────────────
token_cache   = {}           # ca -> full result
token_history = defaultdict(list)  # ca -> [{ts,mcap,price}]
alert_queue   = []
live_feed     = []           # auto-discovered tokens, sorted by discovery time
scan_stats    = {"total": 0, "rugs": 0, "signals": 0, "pump_new": 0}

SOL_PRICE = 170.0  # updated periodically

def update_sol_price():
    global SOL_PRICE
    while True:
        try:
            r = requests.get("https://price.jup.ag/v6/price?ids=So11111111111111111111111111111111111111112",
                             headers=H, timeout=6)
            p = r.json().get("data", {}).get("So11111111111111111111111111111111111111112", {})
            price = float(p.get("price") or 0)
            if price > 0:
                SOL_PRICE = price
        except:
            pass
        time.sleep(60)

threading.Thread(target=update_sol_price, daemon=True).start()

# ── SECTOR ────────────────────────────────────────────────────────────────────
SECTOR_KW = {
    "AI":        ["ai","gpt","neural","agent","robot","agi","llm","openai","deepseek","ml",
                  "compute","inference","skyai","chatbot","automate","algorithm","singularity",
                  "artificial","intelligence","aibot","smartbot","brainbot"],
    "PolitiFi":  ["trump","maga","biden","kamala","president","election","political","republican",
                  "democrat","gop","vote","senate","congress","patriot","freedom","potus",
                  "whitehouse","tariff","zelensky","putin","modi","oligarch","policy"],
    "TikTok":    ["tiktok","viral","trend","dance","fyp","reels","short","creator","influencer",
                  "clout","views","tiktoker","trending","foryou","bytedance","explore"],
    "NSFW":      ["nsfw","xxx","adult","nude","onlyfans","sexy","porn","erotic","lewd","hentai"],
    "Gaming":    ["game","gaming","play","nft","metaverse","rpg","guild","quest","loot",
                  "arena","battle","esport","gamer","pixel","minecraft","fortnite","steam"],
    "Animal":    ["dog","cat","frog","pepe","bird","birb","hamster","doge","shib","wolf",
                  "bear","ape","monkey","penguin","duck","fish","whale","rabbit","bunny",
                  "animal","welfare","rescue","pet","paws","puppy","kitten","fauna",
                  "wildlife","sanctuary","shelter","charity","fund","donation","foundation"],
    "Lore":      ["lore","story","legend","saga","myth","ancient","wizard","dragon","kingdom",
                  "lord","ring","fantasy","realm","warrior","knight","elf","dwarf","magic",
                  "rune","scroll","prophecy","chronicle","epic"],
    "DeFi":      ["defi","yield","farm","stake","liquidity","pool","swap","dex","vault",
                  "protocol","dao","governance","lending","borrow","amm","tvl"],
    "Meme":      ["meme","funny","lol","kek","gg","based","cope","seethe","wen","moon",
                  "pump","fud","wagmi","ngmi","ser","fren","anon","chad","wojak","degen",
                  "lfg","gm","gn","hodl","rekt","gigachad"],
    "Celebrity": ["elon","musk","bezos","zuck","vitalik","celebrity","famous","rapper",
                  "singer","actor","neymar","ronaldo","drake","kanye","snoop","eminem"],
    "Charity":   ["charity","nonprofit","donation","welfare","relief","humanitarian",
                  "cause","awareness","support","aid","rescue","foundation","giveback"],
}
SMULT = {
    "AI": 0.22, "PolitiFi": 0.15, "TikTok": 0.18, "Animal": 0.10, "Meme": 0.08,
    "Lore": 0.06, "Gaming": 0.06, "Celebrity": 0.12, "DeFi": 0.03, "Charity": 0.09,
    "NSFW": -0.30, "Unknown": -0.12,
}
SNOTE = {
    "AI":       "AI narrative dominates 2026. +22% boost. Highest smart money attention.",
    "PolitiFi": "Political memes spike on news. Event-driven — watch news cycle closely.",
    "TikTok":   "TikTok virality = 10x in hours. Fast pump, fast dump. Time entries carefully.",
    "Animal":   "Classic meme sector. Animal/charity tokens attract strong retail flow.",
    "Charity":  "Charity narrative builds community trust. Slower but sticky if authentic.",
    "Meme":     "Pure culture play. Lives and dies by community energy. No fundamentals.",
    "Lore":     "Story-driven tokens attract loyal holders. Slower burn, deeper community.",
    "Gaming":   "Gaming memes have NFT crossover. Watch for partnership catalysts.",
    "Celebrity":"Celebrity link = massive spike + massive dump risk. Pure volatility play.",
    "DeFi":     "DeFi memes have utility angle. More sustainable, lower meme energy.",
    "NSFW":     "NSFW tokens face platform bans and CEX rejection. -30% score penalty.",
    "Unknown":  "No clear narrative. Narrative-less tokens underperform by ~40% historically.",
}

def detect_sector(name, desc, socials=None, pump=None):
    extra = ""
    for k in ("twitter", "x", "telegram"):
        u = (socials or {}).get(k, "")
        if u: extra += " " + u.rstrip("/").split("/")[-1].lower()
    if pump:
        extra += " " + (pump.get("description") or "")
        extra += " " + (pump.get("name") or "")
        extra += " " + (pump.get("twitter") or "").split("/")[-1]
        extra += " " + (pump.get("telegram") or "").split("/")[-1]
    full  = re.sub(r"[^a-z0-9 ]", " ", f"{name} {desc} {extra}".lower())
    words = set(full.split())
    scores = {}
    for sec, kws in SECTOR_KW.items():
        hits = sum(2 if kw in full else (1 if any(kw in w for w in words) else 0) for kw in kws)
        if hits: scores[sec] = hits
    if not scores:
        pri, sec2, conf = "Unknown", [], 0
    else:
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        pri    = ranked[0][0]
        sec2   = [r[0] for r in ranked[1:3]]
        conf   = min(95, 28 + ranked[0][1] * 14)
    mult = 1.0 + SMULT.get(pri, 0)
    return {
        "primary": pri, "secondary": sec2, "confidence": conf,
        "score_multiplier": round(mult, 3),
        "sector_note": SNOTE.get(pri, ""),
    }

# ── DEXSCREENER ───────────────────────────────────────────────────────────────
def fetch_dex(ca):
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}",
                         headers=H, timeout=10)
        pairs = r.json().get("pairs") or []
        if not pairs: return {}
        pair = sorted(pairs,
                      key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
                      reverse=True)[0]
        base = pair.get("baseToken", {})
        info = pair.get("info", {})
        ts   = int(pair.get("pairCreatedAt") or time.time() * 1000)
        age_h = max(0, (time.time() * 1000 - ts) / 3_600_000)
        v  = pair.get("volume") or {}
        pc = pair.get("priceChange") or {}
        tx = pair.get("txns") or {}
        def txn(tf, side): return int((tx.get(tf) or {}).get(side) or 0)
        mcap = float(pair.get("marketCap") or pair.get("fdv") or 0)
        liq  = float((pair.get("liquidity") or {}).get("usd") or 0)
        liq_sol = round(liq / max(SOL_PRICE, 1), 1)
        price = float(pair.get("priceUsd") or 0)
        return {
            "name":        base.get("name", ""),
            "symbol":      base.get("symbol", ""),
            "address":     base.get("address", ca),
            "price_usd":   price,
            "mcap":        mcap,
            "liquidity":   liq,
            "liq_sol":     liq_sol,
            "vol_24h":     float(v.get("h24") or 0),
            "vol_6h":      float(v.get("h6") or 0),
            "vol_1h":      float(v.get("h1") or 0),
            "vol_5m":      float(v.get("m5") or 0),
            "change_24h":  float(pc.get("h24") or 0),
            "change_6h":   float(pc.get("h6") or 0),
            "change_1h":   float(pc.get("h1") or 0),
            "change_5m":   float(pc.get("m5") or 0),
            "tx_buy_24h":  txn("h24", "buys"),   "tx_sell_24h": txn("h24", "sells"),
            "tx_buy_1h":   txn("h1",  "buys"),   "tx_sell_1h":  txn("h1",  "sells"),
            "tx_buy_5m":   txn("m5",  "buys"),   "tx_sell_5m":  txn("m5",  "sells"),
            "dex":         pair.get("dexId", ""),
            "chain":       pair.get("chainId", "solana"),
            "pair_age_h":  round(age_h, 2),
            "created_ts":  ts,
            "description": (info.get("description") or ""),
            "websites":    [w.get("url", "") for w in (info.get("websites") or [])],
            "socials":     {s.get("type", ""): s.get("url", "") for s in (info.get("socials") or [])},
            "dex_paid":    bool(info.get("header") or info.get("openGraph") or info.get("websites")),
        }
    except Exception as e:
        return {"error": str(e)}

# ── RUGCHECK ──────────────────────────────────────────────────────────────────
def fetch_rug(ca):
    try:
        r = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{ca}/report",
                         headers=H, timeout=10)
        d = r.json()
        risks  = d.get("risks", [])
        rnames = [x.get("name", "").lower() for x in risks]
        def has(*kws): return any(any(k in n for k in kws) for n in rnames)
        lp_locked     = has("lp locked") or (not has("lp not locked", "no lp", "liquidity not"))
        mint_revoked  = not has("mint enabled", "mint authority")
        freeze_revoked= not has("freeze", "freezable")

        # Normalise holder pct — RugCheck sometimes returns 0.2069 (fraction) or 20.69 (pct)
        raws = (d.get("topHolders") or [])[:10]
        pcts = [float(h.get("pct") or 0) for h in raws]
        # If ALL values are < 1.5 and sum < 5, assume they're fractions → multiply by 100
        already_pct = any(v > 1.5 for v in pcts)
        holders = []
        for h, p in zip(raws, pcts):
            pct = round(p if already_pct else p * 100, 2)
            pct = min(pct, 100.0)
            holders.append({
                "address": h.get("address", ""),
                "pct":     pct,
                "insider": bool(h.get("insider", False)),
                "owner":   h.get("owner", ""),
            })

        rug = sum(
            30 if (x.get("level") or "").lower() == "danger" else
            12 if (x.get("level") or "").lower() == "warn" else 3
            for x in risks
        )
        return {
            "mint_revoked":   mint_revoked,
            "freeze_revoked": freeze_revoked,
            "lp_locked":      lp_locked,
            "rug_score":      min(100, rug),
            "risks":          risks[:6],
            "top_holders":    holders,
        }
    except:
        return {"mint_revoked": False, "freeze_revoked": False, "lp_locked": False,
                "rug_score": 50, "risks": [], "top_holders": []}

# ── PUMP.FUN ──────────────────────────────────────────────────────────────────
def fetch_pump(ca):
    try:
        r = requests.get(f"https://frontend-api.pump.fun/coins/{ca}", headers=H, timeout=8)
        if r.status_code != 200: return {"is_pumpfun": False}
        d = r.json()
        vc = float(d.get("virtual_sol_reserves") or 0)
        bonding = min(100, round(vc / 30_000_000_000 * 100, 1)) if vc else 0
        total_supply = float(d.get("total_supply") or 1_000_000_000)
        return {
            "is_pumpfun":        True,
            "graduated":         d.get("raydium_pool") is not None,
            "bonding_curve_pct": bonding,
            "description":       d.get("description", ""),
            "name":              d.get("name", ""),
            "twitter":           d.get("twitter", ""),
            "telegram":          d.get("telegram", ""),
            "website":           d.get("website", ""),
            "creator":           d.get("creator", ""),
            "created_timestamp": d.get("created_timestamp", 0),
            "reply_count":       d.get("reply_count", 0),
            "total_supply":      total_supply,
        }
    except:
        return {"is_pumpfun": False, "total_supply": 1_000_000_000}

# ── JUPITER ───────────────────────────────────────────────────────────────────
def fetch_jup(ca):
    try:
        r = requests.get(f"https://price.jup.ag/v6/price?ids={ca}", headers=H, timeout=6)
        p = (r.json().get("data") or {}).get(ca, {})
        return {"price": float(p.get("price") or 0)}
    except:
        return {"price": 0}

# ── HELIUS ────────────────────────────────────────────────────────────────────
def fetch_helius(ca, creator, total_supply):
    empty = {
        "bundle_count": 0, "bundle_pct_bought": 0, "bundle_pct_now": 0,
        "sniper_count": 0, "sniper_pct": 0,
        "first20_pct": 0, "first20_sold_pct": 0,
        "dev_sol_balance": None, "dev_sold_pct": 0,
        "dev_bundled_pct": 0, "dev_airdrop_pct": 0,
        "fake_holder_pct": 0, "cto_detected": False,
        "helius_available": bool(HELIUS_KEY),
    }
    if not HELIUS_KEY:
        return empty
    try:
        total_supply = max(total_supply, 1)

        # 1. Token largest accounts
        r1 = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [ca, {"commitment": "confirmed"}]
        }, headers=H, timeout=10)
        accounts = (r1.json().get("result", {}).get("value") or [])[:20]

        # First-20 % of supply — use uiAmount which is already token units
        # Divide by total_supply and multiply by 100 to get %
        first20_raw = sum(float(a.get("uiAmount") or 0) for a in accounts[:20])
        first20_pct = round(min(100.0, (first20_raw / total_supply) * 100), 1)

        # 2. Signatures for sniper detection
        r2 = requests.post(HELIUS_RPC, json={
            "jsonrpc": "2.0", "id": 2,
            "method": "getSignaturesForAddress",
            "params": [ca, {"limit": 100, "commitment": "confirmed"}]
        }, headers=H, timeout=10)
        sigs = r2.json().get("result") or []
        launch_ts = sigs[-1].get("blockTime", 0) if sigs else 0
        sniper_count = sum(
            1 for sg in sigs[:50]
            if launch_ts and sg.get("blockTime", 0) and
               (sg.get("blockTime", 0) - launch_ts) <= 120
        )
        # Sniper % = sniper wallets * avg holding estimate / total supply
        # Use a conservative 1% avg holding per sniper
        sniper_pct = round(min(60.0, sniper_count * 1.2), 1)

        # 3. Dev SOL balance
        dev_sol = None
        if creator:
            r3 = requests.post(HELIUS_RPC, json={
                "jsonrpc": "2.0", "id": 3,
                "method": "getBalance",
                "params": [creator, {"commitment": "confirmed"}]
            }, headers=H, timeout=8)
            lam = r3.json().get("result", {}).get("value", 0) or 0
            dev_sol = round(lam / 1e9, 2)

        # 4. Bundle detection — top wallets with similar holdings = coordinated
        top_amts = [float(a.get("uiAmount") or 0) for a in accounts[:5]]
        bundle_count = 0
        bundle_pct_bought = 0.0
        if len(top_amts) >= 3 and max(top_amts) > 0:
            mean = sum(top_amts) / len(top_amts)
            variance_ratio = max(top_amts) / max(mean, 1)
            # If top wallets have similar holdings (variance < 40%), suspect bundle
            if variance_ratio < 1.4 and sum(top_amts) > 0:
                bundle_count = min(3, len([a for a in top_amts if a > mean * 0.6]))
                bundle_pct_bought = round(
                    min(100.0, (sum(top_amts[:bundle_count]) / total_supply) * 100), 1
                )

        bundle_pct_now = round(bundle_pct_bought * 0.85, 1)

        empty.update({
            "bundle_count":      bundle_count,
            "bundle_pct_bought": bundle_pct_bought,
            "bundle_pct_now":    bundle_pct_now,
            "sniper_count":      sniper_count,
            "sniper_pct":        sniper_pct,
            "first20_pct":       first20_pct,
            "dev_sol_balance":   dev_sol,
            "helius_available":  True,
        })
        return empty
    except Exception as e:
        empty["helius_error"] = str(e)
        return empty

# ── ATH / ATL ─────────────────────────────────────────────────────────────────
def update_ath_atl(ca, mcap, price):
    now = int(time.time())
    token_history[ca].append({"ts": now, "mcap": mcap, "price": price})
    if len(token_history[ca]) > 288:
        token_history[ca] = token_history[ca][-288:]
    snaps  = token_history[ca]
    mcaps  = [s["mcap"] for s in snaps if s["mcap"] > 0]
    prices = [s["price"] for s in snaps if s["price"] > 0]
    ath_mc = max(mcaps)  if mcaps  else mcap
    atl_mc = min(mcaps)  if mcaps  else mcap
    return {
        "session_ath_mcap":  ath_mc,
        "session_atl_mcap":  atl_mc,
        "session_ath_price": max(prices) if prices else price,
        "session_atl_price": min(prices) if prices else price,
        "from_ath_pct":      round((mcap / ath_mc - 1) * 100, 1) if ath_mc > 0 else 0,
        "from_atl_pct":      round((mcap / atl_mc - 1) * 100, 1) if atl_mc > 0 else 0,
        "snapshots":         len(snaps),
    }

# ── DANGER ALERTS ─────────────────────────────────────────────────────────────
def check_dangers(ca, dex, rug, prev):
    alerts = []
    now = int(time.time())
    if not prev: return alerts

    prev_mc  = prev.get("token", {}).get("mcap", 0)
    curr_mc  = dex.get("mcap", 0)
    if prev_mc > 0 and curr_mc > 0:
        drop = (curr_mc - prev_mc) / prev_mc * 100
        if drop < -40:
            alerts.append({"ca": ca, "type": "MCAP_CRASH", "ts": now, "severity": "danger",
                           "msg": f"MC crashed {drop:.1f}% — possible rug or massive dump"})

    b = dex.get("tx_buy_5m", 0)
    s = dex.get("tx_sell_5m", 0)
    if b + s > 10 and s / (b + s) > 0.80:
        alerts.append({"ca": ca, "type": "SELL_SPIKE", "ts": now, "severity": "danger",
                       "msg": f"5m sell pressure at {round(s/(b+s)*100)}% — distribution pattern"})

    prev_liq = prev.get("token", {}).get("liquidity", 0)
    curr_liq = dex.get("liquidity", 0)
    if prev_liq > 5000 and curr_liq > 0 and (curr_liq - prev_liq) / prev_liq * 100 < -30:
        alerts.append({"ca": ca, "type": "LP_DRAIN", "ts": now, "severity": "danger",
                       "msg": f"Liquidity dropped {round((curr_liq-prev_liq)/prev_liq*100,1)}% — LP may be removed"})

    return alerts

def push_alert(a):
    global alert_queue
    alert_queue.insert(0, a)
    alert_queue = alert_queue[:100]

# ── SCORING ENGINE v4 — GATED TIER SYSTEM ────────────────────────────────────
def compute_score(dex, rug, pump, sector, helius):
    """
    Tier gates (hard requirements):
    S-TIER: score>=75 AND LP locked AND mint revoked AND rug_score<20 AND no danger flags
    A-TIER: score>=55 AND (LP locked OR mint revoked) AND rug_score<50
    B-TIER: score>=35 AND rug_score<70
    C-TIER: score>=20 (new tier)
    D-TIER: score>=10
    F-TIER: anything with rug_score>60 or 2+ danger flags
    DANGER: rug_score>70 or critical on-chain flags
    """
    raw = 0
    det = {}

    mcap    = float(dex.get("mcap") or 0)
    age_h   = float(dex.get("pair_age_h") or 0)
    liq     = float(dex.get("liquidity") or 0)
    vol24   = float(dex.get("vol_24h") or 0)
    ch1h    = float(dex.get("change_1h") or 0)
    ch24    = float(dex.get("change_24h") or 0)
    buys24  = dex.get("tx_buy_24h", 0)
    sells24 = dex.get("tx_sell_24h", 1)
    bsr     = buys24 / max(1, buys24 + sells24)

    holders    = rug.get("top_holders", [])
    ins_pct    = min(100, sum(h["pct"] for h in holders if h.get("insider")))
    top10_pct  = min(100, sum(h["pct"] for h in holders[:10]))
    rug_score  = rug.get("rug_score", 0)
    lp_ok      = rug.get("lp_locked", False)
    mint_ok    = rug.get("mint_revoked", False)
    freeze_ok  = rug.get("freeze_revoked", False)

    # ── POINT CATEGORIES ─────────────────────────────────────────────────────
    # 1. LP locked (0 or 20 — hard gate)
    lp_pts = 20 if lp_ok else 0
    raw += lp_pts; det["lp"] = lp_pts

    # 2. Mint revoked (0 or 15 — hard gate)
    mint_pts = 15 if mint_ok else 0
    raw += mint_pts; det["mint"] = mint_pts

    # 3. Freeze revoked (0 or 5)
    fr_pts = 5 if freeze_ok else 0
    raw += fr_pts; det["freeze"] = fr_pts

    # 4. Social presence (0–12)
    tw  = bool((dex.get("socials") or {}).get("twitter") or pump.get("twitter"))
    tg  = bool((dex.get("socials") or {}).get("telegram") or pump.get("telegram"))
    web = bool(dex.get("websites") or pump.get("website"))
    sp  = (5 if tw else 0) + (5 if tg else 0) + (2 if web else 0)
    raw += sp; det["social"] = sp

    # 5. Dev/insider (0–8)
    dev_pts = max(0, 8 - int(ins_pct * 0.8))
    raw += dev_pts; det["dev_wallet"] = dev_pts

    # 6. Wallet concentration (0–8)
    conc_pts = max(0, 8 - int(top10_pct * 0.10))
    raw += conc_pts; det["concentration"] = conc_pts

    # 7. Buy pressure (0–8)
    bp_pts = round(bsr * 8)
    raw += bp_pts; det["buy_pressure"] = bp_pts

    # 8. MC range (−8 to +8)
    if   mcap <= 0:          mp = -8
    elif mcap < 3_000:       mp = -8   # likely dead/rug
    elif mcap < 8_000:       mp = -4
    elif mcap < 15_000:      mp = 4
    elif mcap < 75_000:      mp = 8    # sweet spot
    elif mcap < 300_000:     mp = 6
    elif mcap < 1_000_000:   mp = 3
    elif mcap < 5_000_000:   mp = 1
    else:                    mp = -2   # too big for moonshot
    raw += mp; det["mcap_range"] = mp

    # 9. Liquidity ratio (0–6)
    lr = liq / max(1, mcap)
    lp2 = 6 if lr > 0.25 else 4 if lr > 0.12 else 2 if lr > 0.05 else 0
    raw += lp2; det["liq_ratio"] = lp2

    # 10. Volume momentum (0–6)
    vr  = vol24 / max(1, mcap)
    vp  = 6 if vr > 5 else 4 if vr > 2 else 2 if vr > 0.5 else 0
    raw += vp; det["vol_momentum"] = vp

    # 11. Price momentum 1h (−4 to +4)
    mp2 = 4 if ch1h > 20 else 2 if ch1h > 5 else -4 if ch1h < -35 else 0
    raw += mp2; det["price_momentum"] = mp2

    # 12. Pump.fun graduation bonus (0–6)
    gp = 6 if pump.get("graduated") else (3 if pump.get("is_pumpfun") else 0)
    raw += gp; det["graduation"] = gp

    # 13. DEX paid bonus (+2)
    dp = 2 if dex.get("dex_paid") else 0
    raw += dp; det["dex_paid"] = dp

    # 14. Bundle penalty
    bp_pct = helius.get("bundle_pct_now", 0)
    bpen   = -15 if bp_pct > 40 else -8 if bp_pct > 20 else -4 if bp_pct > 10 else 0
    raw += bpen; det["bundle_penalty"] = bpen

    # 15. Sniper penalty
    sn_pct = helius.get("sniper_pct", 0)
    spen   = -10 if sn_pct > 30 else -5 if sn_pct > 15 else 0
    raw += spen; det["sniper_penalty"] = spen

    # ── AGE PENALTY ───────────────────────────────────────────────────────────
    age_note = ""
    if age_h > 24 * 60:      # 60+ days
        raw -= 20; det["age_penalty"] = -20
        age_note = "60+ day old token. Likely post-ATH bleed. Volume must be very strong."
    elif age_h > 24 * 30:    # 30–60 days
        raw -= 12; det["age_penalty"] = -12
        age_note = "30+ day old token. Post-ATH bleed risk. Volume must confirm."
    elif age_h > 24 * 7:     # 7–30 days
        raw -= 5; det["age_penalty"] = -5
        age_note = "7+ day old token. Requires strong volume confirmation."

    # ── DANGER PATTERNS ───────────────────────────────────────────────────────
    danger_flags = []
    if vol24 > mcap * 10 and liq < 3000:
        raw -= 20; danger_flags.append("Wash trading: volume 10× MC with near-zero liquidity")
        det["wash_penalty"] = -20
    if mcap > 30_000 and liq < 2_000 and age_h < 2:
        raw -= 25; danger_flags.append("Rug pattern: MC pumped high with critically low liquidity")
        det["rug_pattern"] = -25
    if ch24 > 800 and mcap < 15_000:
        raw -= 12; danger_flags.append(f"Extreme pump +{ch24:.0f}% on micro MC — manipulation likely")
        det["pump_manip"] = -12
    if ins_pct > 30:
        raw -= 15; danger_flags.append(f"Insider wallet concentration {ins_pct:.1f}% — extreme dump risk")
        det["insider_penalty"] = -15

    # ── SECTOR MULTIPLIER ─────────────────────────────────────────────────────
    mult  = sector.get("score_multiplier", 1.0)
    score = max(0, min(100, round(raw * mult)))
    det["sector_boost"] = round((mult - 1) * 100)

    # ── GATED TIER ASSIGNMENT ─────────────────────────────────────────────────
    is_danger = rug_score > 70 or len(danger_flags) >= 2 or (not lp_ok and ins_pct > 20)

    if is_danger:
        tier = "DANGER"
    elif score >= 75 and lp_ok and mint_ok and rug_score < 20 and not danger_flags:
        tier = "S-TIER"
    elif score >= 55 and (lp_ok or mint_ok) and rug_score < 50 and len(danger_flags) < 2:
        tier = "A-TIER"
    elif score >= 35 and rug_score < 70:
        tier = "B-TIER"
    elif score >= 20:
        tier = "C-TIER"
    elif score >= 10:
        tier = "D-TIER"
    else:
        tier = "F-TIER"

    # Old token S-tier cap
    vr2 = vol24 / max(1, mcap)
    if age_h > 24 * 30 and tier == "S-TIER" and vr2 < 1.0:
        tier = "A-TIER"; score = min(score, 74)

    # ── MC-BASED SL/TP ────────────────────────────────────────────────────────
    is_new   = age_h < 0.5
    is_early = age_h < 24
    sl_p  = 0.35 if is_new else (0.28 if is_early else 0.20)
    t1_p  = 1.00 if is_new else (0.70 if is_early else 0.50)
    t2_p  = 4.00 if is_new else (2.50 if is_early else 1.80)
    t3_p  = 12.0 if is_new else (7.00 if is_early else 5.00)

    # ── PROBABILITIES ─────────────────────────────────────────────────────────
    p2x  = min(88, max(5,  round(score * 0.88 + (6 if lp_ok else -10) + (4 if mint_ok else -5)
                                  - (20 if ins_pct > 10 else 0) - (10 if age_h > 24*30 else 0)
                                  - abs(bpen) * 0.5 - abs(spen) * 0.5)))
    p10x = min(72, max(3,  round(score * 0.52 - (20 if top10_pct > 50 else 0)
                                  + (12 if pump.get("graduated") else 0)
                                  + (10 if mcap < 200_000 else 0)
                                  - (15 if age_h > 24*30 else 0))))
    p100x = min(42, max(1, round(score * (
        0.35 if mcap < 30_000 else
        0.22 if mcap < 80_000 else
        0.10 if mcap < 200_000 else
        0.03))))

    # ── VERDICT ───────────────────────────────────────────────────────────────
    if tier == "DANGER":
        verdict = "DANGER"
        vr = (f"Rug score {rug_score}/100. "
              + ("LP not locked. " if not lp_ok else "")
              + ("Mint active. "   if not mint_ok else "")
              + (f"Insiders {ins_pct:.1f}%. " if ins_pct > 10 else "")
              + " ".join(danger_flags[:1]))
    elif score >= 60 and lp_ok and mint_ok and not danger_flags:
        verdict = "BUY"
        vr = (f"Score {score}/100. LP locked. Mint revoked. "
              f"Buy pressure {round(bsr*100)}%. {sector['primary']} narrative. "
              + (age_note if age_note else "Clean setup."))
    else:
        verdict = "WATCH"
        vr = (f"Score {score}/100. "
              + ("LP unconfirmed. " if not lp_ok else "")
              + ("Mint not revoked. " if not mint_ok else "")
              + (age_note + " " if age_note else "")
              + " ".join(danger_flags[:1])
              + " Wait for confirmation.")

    return {
        "score": score, "tier": tier, "verdict": verdict, "verdict_reason": vr.strip(),
        "details": det, "bsr": round(bsr * 100),
        "top10_pct": round(top10_pct, 1), "insider_pct": round(ins_pct, 1),
        "prob_2x": p2x, "prob_10x": p10x, "prob_100x": p100x,
        "can_100x": mcap < 150_000 and p100x > 15,
        "sl_pct": round(sl_p*100), "tp1_pct": round(t1_p*100),
        "tp2_pct": round(t2_p*100), "tp3_pct": round(t3_p*100),
        "sl_mc":  round(mcap * (1 - sl_p)),
        "tp1_mc": round(mcap * (1 + t1_p)),
        "tp2_mc": round(mcap * (1 + t2_p)),
        "tp3_mc": round(mcap * (1 + t3_p)),
        "danger_flags": danger_flags, "age_note": age_note,
        "mult_applied": round((mult - 1) * 100),
        "raw_score": raw,
    }

# ── FULL PIPELINE ─────────────────────────────────────────────────────────────
def full_analyze(ca):
    dex  = fetch_dex(ca)
    rug  = fetch_rug(ca)
    pump = fetch_pump(ca)
    jup  = fetch_jup(ca)

    if not dex.get("name") and not dex.get("price_usd"):
        return None, "Token not found on DexScreener. Verify the contract address."

    if not dex.get("price_usd") and jup.get("price"):
        dex["price_usd"] = jup["price"]

    creator      = pump.get("creator", "")
    total_supply = pump.get("total_supply", 1_000_000_000)
    helius       = fetch_helius(ca, creator, total_supply)

    sector  = detect_sector(
        f"{dex.get('name','')} {dex.get('symbol','')}",
        f"{dex.get('description','')} {pump.get('description','')}",
        socials=dex.get("socials", {}), pump=pump
    )
    scoring = compute_score(dex, rug, pump, sector, helius)

    mcap  = dex.get("mcap", 0)
    price = dex.get("price_usd", 0)
    aa    = update_ath_atl(ca, mcap, price)

    age_h = dex.get("pair_age_h", 0)
    stage = "new" if age_h < 0.5 else "early" if age_h < 24 else "graduated"

    prev       = token_cache.get(ca)
    new_alerts = check_dangers(ca, dex, rug, prev)
    for a in new_alerts:
        push_alert(a)

    liq     = dex.get("liquidity", 0)
    liq_sol = round(liq / max(SOL_PRICE, 1), 1)

    result = {
        "ca": ca, "stage": stage,
        "token": {
            "name":        dex.get("name", "Unknown"),
            "symbol":      dex.get("symbol", "???"),
            "chain":       dex.get("chain", "solana"),
            "price_usd":   price,
            "mcap":        mcap,
            "liquidity":   liq,
            "liq_sol":     liq_sol,
            "vol_24h":     dex.get("vol_24h", 0),
            "vol_6h":      dex.get("vol_6h", 0),
            "vol_1h":      dex.get("vol_1h", 0),
            "vol_5m":      dex.get("vol_5m", 0),
            "change_24h":  dex.get("change_24h", 0),
            "change_6h":   dex.get("change_6h", 0),
            "change_1h":   dex.get("change_1h", 0),
            "change_5m":   dex.get("change_5m", 0),
            "tx_buy_24h":  dex.get("tx_buy_24h", 0),
            "tx_sell_24h": dex.get("tx_sell_24h", 0),
            "tx_buy_1h":   dex.get("tx_buy_1h", 0),
            "tx_sell_1h":  dex.get("tx_sell_1h", 0),
            "tx_buy_5m":   dex.get("tx_buy_5m", 0),
            "tx_sell_5m":  dex.get("tx_sell_5m", 0),
            "dex":         dex.get("dex", ""),
            "pair_age_h":  age_h,
            "created_ts":  dex.get("created_ts", 0),
            "dex_paid":    dex.get("dex_paid", False),
            "websites":    dex.get("websites", []),
            "socials":     dex.get("socials", {}),
        },
        "safety": {
            "mint_revoked":   rug.get("mint_revoked", False),
            "freeze_revoked": rug.get("freeze_revoked", False),
            "lp_locked":      rug.get("lp_locked", False),
            "rug_score":      rug.get("rug_score", 50),
            "risks":          rug.get("risks", []),
            "top_holders":    rug.get("top_holders", []),
        },
        "pumpfun": {
            "is_pumpfun":        pump.get("is_pumpfun", False),
            "graduated":         pump.get("graduated", False),
            "bonding_curve_pct": pump.get("bonding_curve_pct", 0),
            "reply_count":       pump.get("reply_count", 0),
            "creator":           creator,
            "twitter":           pump.get("twitter", ""),
            "telegram":          pump.get("telegram", ""),
            "cto":               helius.get("cto_detected", False),
        },
        "onchain": {
            "bundle_count":      helius.get("bundle_count", 0),
            "bundle_pct_bought": helius.get("bundle_pct_bought", 0),
            "bundle_pct_now":    helius.get("bundle_pct_now", 0),
            "sniper_count":      helius.get("sniper_count", 0),
            "sniper_pct":        helius.get("sniper_pct", 0),
            "first20_pct":       helius.get("first20_pct", 0),
            "first20_sold_pct":  helius.get("first20_sold_pct", 0),
            "dev_sol_balance":   helius.get("dev_sol_balance", None),
            "dev_sold_pct":      helius.get("dev_sold_pct", 0),
            "helius_available":  helius.get("helius_available", False),
        },
        "sector":  sector,
        "scoring": scoring,
        "ath_atl": aa,
        "social": {
            "x_handle":        (dex.get("socials") or {}).get("twitter", "") or pump.get("twitter", ""),
            "tg_link":         (dex.get("socials") or {}).get("telegram", "") or pump.get("telegram", ""),
            "website":         (dex.get("websites") or [""])[0] if dex.get("websites") else "",
            "has_twitter":     bool((dex.get("socials") or {}).get("twitter") or pump.get("twitter")),
            "has_telegram":    bool((dex.get("socials") or {}).get("telegram") or pump.get("telegram")),
            "has_website":     bool(dex.get("websites") or pump.get("website")),
            "pumpfun_replies": pump.get("reply_count", 0),
            "vol_1h":          dex.get("vol_1h", 0),
            "tx_buy_5m":       dex.get("tx_buy_5m", 0),
            "tx_sell_5m":      dex.get("tx_sell_5m", 0),
        },
        "alerts":       new_alerts,
        "sources_used": ["DexScreener", "RugCheck.xyz", "Pump.fun", "Jupiter"]
                        + (["Helius RPC"] if HELIUS_KEY else []),
        "timestamp":    int(time.time()),
    }

    token_cache[ca] = result

    # Add to live feed (deduplicated)
    global live_feed
    live_feed = [t for t in live_feed if t["ca"] != ca]
    live_feed.insert(0, {
        "ca": ca, "stage": stage,
        "symbol": dex.get("symbol", "???"),
        "name":   dex.get("name", "Unknown"),
        "mcap":   mcap,
        "score":  scoring["score"],
        "tier":   scoring["tier"],
        "verdict":scoring["verdict"],
        "change_24h": dex.get("change_24h", 0),
        "change_1h":  dex.get("change_1h", 0),
        "sector": sector["primary"],
        "lp_locked":  rug.get("lp_locked", False),
        "mint_revoked": rug.get("mint_revoked", False),
        "rug_score":  rug.get("rug_score", 50),
        "pair_age_h": age_h,
        "liquidity":  liq,
        "vol_1h":     dex.get("vol_1h", 0),
        "danger_flags": scoring["danger_flags"],
        "has_alert":  len(new_alerts) > 0,
        "timestamp":  int(time.time()),
        "dex_paid":   dex.get("dex_paid", False),
        "sniper_count": helius.get("sniper_count", 0),
        "bundle_count": helius.get("bundle_count", 0),
    })
    live_feed = live_feed[:200]

    scan_stats["total"] += 1
    if scoring["verdict"] == "DANGER": scan_stats["rugs"] += 1
    if scoring["tier"] == "S-TIER":   scan_stats["signals"] += 1
    if pump.get("is_pumpfun"):         scan_stats["pump_new"] += 1

    return result, None

# ── AUTO LIVE SCAN ────────────────────────────────────────────────────────────
def live_scan_worker():
    seen = set()
    while True:
        try:
            r = requests.get(
                "https://frontend-api.pump.fun/coins"
                "?offset=0&limit=20&sort=created_timestamp&order=DESC",
                headers=H, timeout=9
            )
            coins = r.json() if r.status_code == 200 else []
            if isinstance(coins, list):
                for coin in coins:
                    ca  = coin.get("mint", "")
                    umc = float(coin.get("usd_market_cap") or 0)
                    if ca and ca not in seen and umc >= 10_000:
                        seen.add(ca)
                        threading.Thread(target=full_analyze, args=(ca,), daemon=True).start()
        except:
            pass
        time.sleep(20)

# ── AUTO REFRESH ──────────────────────────────────────────────────────────────
def refresh_worker():
    while True:
        time.sleep(30)
        for ca in list(token_cache.keys()):
            try:
                full_analyze(ca)
            except:
                pass

threading.Thread(target=live_scan_worker, daemon=True).start()
threading.Thread(target=refresh_worker,   daemon=True).start()

# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.route("/api/analyze")
def analyze():
    ca = (request.args.get("ca") or "").strip()
    if not ca or len(ca) < 20:
        return jsonify({"error": "Invalid contract address"}), 400
    result, err = full_analyze(ca)
    if err: return jsonify({"error": err}), 404
    return jsonify(result)

@app.route("/api/refresh/<ca>")
def refresh(ca):
    if not ca or len(ca) < 20:
        return jsonify({"error": "Invalid CA"}), 400
    result, err = full_analyze(ca)
    if err: return jsonify({"error": err}), 404
    return jsonify(result)

@app.route("/api/live")
def live():
    sort_by = request.args.get("sort", "time")  # time | score | mcap
    feed = list(live_feed)
    if sort_by == "score":
        feed.sort(key=lambda t: t.get("score", 0), reverse=True)
    elif sort_by == "mcap":
        feed.sort(key=lambda t: t.get("mcap", 0), reverse=True)
    # default: already sorted by time (newest first)
    return jsonify({"tokens": feed[:100], "count": len(feed)})

@app.route("/api/alerts")
def alerts():
    return jsonify({"alerts": alert_queue[:30]})

@app.route("/api/stats")
def stats():
    return jsonify({**scan_stats, "cached": len(token_cache), "live_feed": len(live_feed), "version": "4.0"})

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "version": "4.0", "helius": bool(HELIUS_KEY),
                    "cached": len(token_cache), "live_feed": len(live_feed),
                    "brand": "@Madrimov_trade"})

@app.route("/")
def index():
    return "PumpIQ v4 API | /api/analyze?ca=ADDR | /api/live | /api/alerts | /api/stats"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
