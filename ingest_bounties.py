#!/usr/bin/env python3
"""
Autonomous Bounty Hunter Wire
Cross-references GitHub Bounties with live cryptocurrency conversion rates
to calculate real-time USD/Hour ROI for coding agents.
Works 100% free with heuristic parsing, with optional LLM enrichment.
"""

import os
import re
import json
import logging
from datetime import datetime, timezone
import requests
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DATA_DIR = "data"
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
os.makedirs(ARCHIVE_DIR, exist_ok=True)

class BountyTarget(BaseModel):
    issue_url: str
    repository: str
    title: str
    raw_reward_currency: str
    raw_reward_amount: float
    estimated_hours_to_solve: float
    live_usd_exchange_rate: float
    total_usd_value: float
    roi_usd_per_hour: float
    agent_directive: str


# Baseline crypto exchange rates if CoinGecko throttles GitHub Actions cloud runner IPs
FALLBACK_RATES = {
    "USD": 1.0,
    "USDC": 1.0,
    "USDT": 1.0,
    "ETH": 2600.0,
    "ETHEREUM": 2600.0,
    "SOL": 160.0,
    "SOLANA": 160.0,
    "BTC": 68000.0,
    "BITCOIN": 68000.0,
    "MATIC": 0.40
}


def fetch_github_bounties() -> list:
    """Fetches open unassigned issues tagged with bounty labels or titles."""
    token = os.getenv("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Bounty-Hunter-Wire/1.0"
    }
    if token:
        headers["Authorization"] = f"token {token}"

    # Search queries targeting active bounties (separated to avoid label query syntax errors)
    queries = [
        "is:issue+is:open+no:assignee+label:bounty",
        "is:issue+is:open+no:assignee+bounty+in:title"
    ]

    all_items = []
    seen_ids = set()

    for q in queries:
        url = f"https://api.github.com/search/issues?q={q}&sort=created&order=desc&per_page=20"
        try:
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 200:
                items = res.json().get("items", [])
                for item in items:
                    if item["id"] not in seen_ids:
                        seen_ids.add(item["id"])
                        all_items.append(item)
            else:
                logging.warning(f"GitHub Search query '{q}' returned status {res.status_code}")
        except Exception as e:
            logging.error(f"GitHub search failed for {q}: {e}")

    logging.info(f"Retrieved {len(all_items)} unique bounty issues from GitHub.")
    return all_items


def extract_bounty_heuristics(title: str, body: str) -> dict:
    """Heuristic regex extractor for bounty amounts, currencies, and estimated effort."""
    text = f"{title} {body}"
    amount = 0.0
    currency = "USD"
    has_bounty = False

    # 1. Check for standard dollar amounts: $500, $1,000, etc.
    m_dollar = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)', text, re.IGNORECASE)
    if m_dollar:
        try:
            amount = float(m_dollar.group(1).replace(',', ''))
            currency = "USD"
            has_bounty = True
        except ValueError:
            pass

    # 2. Check for crypto amounts: 0.5 ETH, 5 SOL, 250 USDC
    if not has_bounty:
        m_crypto = re.search(r'([0-9,]+(?:\.[0-9]+)?)\s*(usdc|usdt|eth|ethereum|sol|solana|btc|bitcoin)', text, re.IGNORECASE)
        if m_crypto:
            try:
                amount = float(m_crypto.group(1).replace(',', ''))
                currency = m_crypto.group(2).upper()
                has_bounty = True
            except ValueError:
                pass

    # 3. Check for "bounty: 100" patterns
    if not has_bounty:
        m_bounty = re.search(r'bounty[:\s]+(?:\$)?([0-9,]+(?:\.[0-9]+)?)', text, re.IGNORECASE)
        if m_bounty:
            try:
                amount = float(m_bounty.group(1).replace(',', ''))
                currency = "USD"
                has_bounty = True
            except ValueError:
                pass

    body_len = len(body) if body else 100
    if body_len > 3000:
        estimated_hours = 6.0
    elif body_len > 1000:
        estimated_hours = 3.5
    else:
        estimated_hours = 2.0

    return {
        "has_bounty": has_bounty,
        "currency_id": currency,
        "amount": amount,
        "estimated_hours": estimated_hours
    }


def extract_bounty_economics(title: str, body: str) -> dict:
    """Extracts economics using OpenAI if available; falls back cleanly to heuristics."""
    api_key = os.getenv("LLM_API_KEY")
    api_base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    if not api_key:
        return extract_bounty_heuristics(title, body)

    system_prompt = (
        "Analyze the GitHub issue. Extract the bounty reward amount and currency. "
        "Map currency strictly to: 'USD', 'ETH', 'SOL', 'BTC', 'USDC', 'USDT'. "
        "Estimate hours an expert AI coding agent needs to solve it. "
        "Output JSON only: {\"has_bounty\": bool, \"currency_id\": str, \"amount\": float, \"estimated_hours\": float}. "
        "If no clear reward is mentioned, set has_bounty to false."
    )
    user_prompt = f"Title: {title}\n\nBody: {body[:3000]}"

    try:
        res = requests.post(
            f"{api_base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            },
            timeout=15
        )
        if res.status_code == 200:
            return json.loads(res.json()["choices"][0]["message"]["content"])
        else:
            logging.warning(f"LLM API returned {res.status_code}. Using heuristic extraction.")
            return extract_bounty_heuristics(title, body)
    except Exception as e:
        logging.warning(f"LLM extraction failed ({e}), using heuristics.")
        return extract_bounty_heuristics(title, body)


def fetch_live_crypto_prices(currency_ids: set) -> dict:
    """Fetches fiat rates from CoinGecko, falling back to static rates if rate-limited."""
    rates = dict(FALLBACK_RATES)

    coin_mapping = {
        "ETH": "ethereum",
        "ETHEREUM": "ethereum",
        "SOL": "solana",
        "SOLANA": "solana",
        "BTC": "bitcoin",
        "BITCOIN": "bitcoin",
        "MATIC": "matic-network"
    }

    query_ids = [coin_mapping[c] for c in currency_ids if c in coin_mapping]

    if query_ids:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(set(query_ids))}&vs_currencies=usd"
        try:
            res = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if res.status_code == 200:
                data = res.json()
                for sym, cid in coin_mapping.items():
                    if cid in data and "usd" in data[cid]:
                        rates[sym] = float(data[cid]["usd"])
        except Exception as e:
            logging.warning(f"CoinGecko lookup skipped or throttled ({e}). Using baseline exchange rates.")

    return rates


def run():
    logging.info("Initiating Autonomous Bounty Hunter Pipeline...")
    issues = fetch_github_bounties()
    
    parsed_issues = []
    currency_set = set()

    for issue in issues:
        title = issue.get("title", "")
        body = issue.get("body") or ""
        econ = extract_bounty_economics(title, body)
        
        currency = str(econ.get("currency_id", "USD")).upper()
        amount = float(econ.get("amount", 0.0))
        hours = max(float(econ.get("estimated_hours", 2.0)), 0.5)

        if econ.get("has_bounty") and amount > 0:
            currency_set.add(currency)
            parsed_issues.append({
                "issue": issue,
                "currency": currency,
                "amount": amount,
                "hours": hours,
                "is_verified": True
            })
        else:
            parsed_issues.append({
                "issue": issue,
                "currency": "USD",
                "amount": 50.0,
                "hours": hours,
                "is_verified": False
            })

    live_rates = fetch_live_crypto_prices(currency_set)

    records = []
    md_lines = [
        "# Global AI Bounty Arbitrage Radar",
        "> Live cross-referencing of open GitHub bounties and live cryptocurrency markets to calculate maximum USD/Hour ROI.\n",
        "## Top High-Yield Targets"
    ]

    for item in parsed_issues:
        issue = item["issue"]
        currency = item["currency"]
        amount = item["amount"]
        hours = item["hours"]

        exchange_rate = live_rates.get(currency, 1.0)
        total_usd = amount * exchange_rate
        roi_per_hour = total_usd / hours

        repo_name = issue.get("repository_url", "").replace("https://api.github.com/repos/", "")
        
        if roi_per_hour >= 100.0:
            directive = f"EXECUTE: High-yield target at ${roi_per_hour:.2f}/hr. Prioritize PR submission."
        elif roi_per_hour >= 40.0:
            directive = f"STANDARD: Profitable target at ${roi_per_hour:.2f}/hr. Queue for execution."
        else:
            directive = f"LOW YIELD: Secondary priority at ${roi_per_hour:.2f}/hr."

        records.append(BountyTarget(
            issue_url=issue.get("html_url", ""),
            repository=repo_name,
            title=issue.get("title", ""),
            raw_reward_currency=currency,
            raw_reward_amount=amount,
            estimated_hours_to_solve=hours,
            live_usd_exchange_rate=exchange_rate,
            total_usd_value=round(total_usd, 2),
            roi_usd_per_hour=round(roi_per_hour, 2),
            agent_directive=directive
        ))

    records.sort(key=lambda x: x.roi_usd_per_hour, reverse=True)

    for r in records[:25]:
        md_lines.extend([
            f"### {r.repository}: {r.title}",
            f"- **Reward:** {r.raw_reward_amount} {r.raw_reward_currency} (USD Value: **${r.total_usd_value:,.2f}**)",
            f"- **Agent ROI:** **${r.roi_usd_per_hour:,.2f}/hr** (Est. {r.estimated_hours_to_solve} hrs)",
            f"- **Directive:** `{r.agent_directive}`",
            f"- **Target URL:** [Claim Bounty]({r.issue_url})\n"
        ])

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    now_iso = now.isoformat()

    payload = {
        "metadata": {
            "generated_at": now_iso,
            "data_sources": ["GitHub Search API", "CoinGecko V3 API", "Semantic Extraction"],
            "total_bounties_indexed": len(records),
            "schema_version": "1.0.0"
        },
        "bounty_targets": [r.model_dump() for r in records]
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "today.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    with open(os.path.join(DATA_DIR, "llms.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    month_dir = os.path.join(ARCHIVE_DIR, now.strftime("%Y"), now.strftime("%m"))
    os.makedirs(month_dir, exist_ok=True)
    with open(os.path.join(month_dir, f"{date_str}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    logging.info(f"Published {len(records)} bounty targets to data/today.json and archive.")

if __name__ == "__main__":
    run()
