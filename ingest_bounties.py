#!/usr/bin/env python3
"""
Autonomous Bounty Hunter Wire
Cross-references GitHub Bounties with live CoinGecko cryptocurrency 
exchange rates to calculate real-time USD/Hour ROI for coding agents.
"""

import os
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


def fetch_github_bounties() -> list:
    """Source 1: Fetch open, unassigned GitHub issues with bounty labels."""
    url = "https://api.github.com/search/issues?q=state:open+no:assignee+label:bounty,gitcoin,paid&sort=created&order=desc&per_page=30"
    headers = {"Accept": "application/vnd.github.v3+json"}
    
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
        
    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()
        return res.json().get("items", [])
    except Exception as e:
        logging.error(f"GitHub API failed: {e}")
        return []


def extract_bounty_economics(title: str, body: str) -> dict:
    """Source 2: LLM extracts the hidden financial data and estimates effort."""
    api_key = os.getenv("LLM_API_KEY")
    api_base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    if not api_key:
        return {}

    system_prompt = (
        "Analyze the GitHub issue. Extract the bounty reward amount and currency. "
        "Map the currency strictly to one of these exact IDs: 'usd', 'ethereum', 'bitcoin', 'solana', 'matic-network', 'usdc'. "
        "Estimate how many hours an expert AI coding agent would need to solve it based on the description complexity. "
        "Output JSON exactly like this: {\"has_bounty\": true, \"currency_id\": \"ethereum\", \"amount\": 0.5, \"estimated_hours\": 2.5}. "
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
        return json.loads(res.json()["choices"][0]["message"]["content"])
    except Exception as e:
        logging.error(f"LLM extraction failed: {e}")
        return {}


def fetch_live_crypto_prices(currency_ids: set) -> dict:
    """Source 3: CoinGecko API fetches live fiat conversion rates."""
    # Remove usd/usdc as they are already 1:1
    crypto_ids = [c for c in currency_ids if c not in ["usd", "usdc", "unknown"]]
    if not crypto_ids:
        return {}
        
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(crypto_ids)}&vs_currencies=usd"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
        return {k: v.get("usd", 0.0) for k, v in data.items()}
    except Exception as e:
        logging.error(f"CoinGecko API failed: {e}")
        return {}


def run():
    logging.info("Initiating Autonomous Bounty Hunter Pipeline...")
    issues = fetch_github_bounties()
    
    parsed_issues = []
    currency_set = set()

    # Step 1: Extract economics from text
    for issue in issues:
        title = issue.get("title", "")
        body = issue.get("body") or ""
        econ = extract_bounty_economics(title, body)
        
        if econ.get("has_bounty") and econ.get("amount", 0) > 0:
            currency = econ.get("currency_id", "usd").lower()
            currency_set.add(currency)
            parsed_issues.append({
                "issue": issue,
                "econ": econ
            })

    # Step 2: Fetch live exchange rates
    live_rates = fetch_live_crypto_prices(currency_set)
    live_rates["usd"] = 1.0
    live_rates["usdc"] = 1.0

    records = []
    md_lines = [
        "# Global AI Bounty Arbitrage Radar",
        "> Live cross-referencing of open GitHub bounties and live cryptocurrency markets to calculate maximum USD/Hour ROI.\n",
        "## Top High-Yield Targets"
    ]

    # Step 3: Calculate Arbitrage and ROI
    for item in parsed_issues:
        issue = item["issue"]
        econ = item["econ"]
        
        currency = econ["currency_id"].lower()
        amount = econ["amount"]
        hours = max(econ.get("estimated_hours", 1.0), 0.5) # Prevent division by zero
        
        exchange_rate = live_rates.get(currency, 0.0)
        total_usd = amount * exchange_rate
        roi_per_hour = total_usd / hours
        
        if total_usd < 5.0: # Skip micro-bounties
            continue
            
        repo_name = issue.get("repository_url", "").replace("https://api.github.com/repos/", "")
        
        directive = f"EXECUTE. High yield target at ${roi_per_hour:.2f}/hr." if roi_per_hour > 100 else "QUEUE for low-priority cycles."
        
        records.append(BountyTarget(
            issue_url=issue.get("html_url"),
            repository=repo_name,
            title=issue.get("title"),
            raw_reward_currency=currency.upper(),
            raw_reward_amount=amount,
            estimated_hours_to_solve=hours,
            live_usd_exchange_rate=exchange_rate,
            total_usd_value=round(total_usd, 2),
            roi_usd_per_hour=round(roi_per_hour, 2),
            agent_directive=directive
        ))

    # Sort by highest ROI per hour
    records.sort(key=lambda x: x.roi_usd_per_hour, reverse=True)

    for r in records[:20]:
        md_lines.extend([
            f"### {r.repository}: {r.title}",
            f"- **Reward:** {r.raw_reward_amount} {r.raw_reward_currency} (Live USD Value: **${r.total_usd_value}**)",
            f"- **Agent ROI:** **${r.roi_usd_per_hour}/hr** (Est. {r.estimated_hours_to_solve} hours to solve)",
            f"- **Directive:** {r.agent_directive}",
            f"- **Target URL:** [Claim Bounty]({r.issue_url})\n"
        ])

    if not records:
        logging.warning("No lucrative bounties found in this cycle.")
        return

    now = datetime.now(timezone.utc)
    payload = {
        "metadata": {
            "generated_at": now.isoformat(),
            "data_sources": ["GitHub Search API", "CoinGecko V3 API", "LLM Semantic Parsing"],
            "schema_version": "1.0.0"
        },
        "bounty_targets": [r.model_dump() for r in records]
    }

    # Save outputs
    with open(os.path.join(DATA_DIR, "today.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    with open(os.path.join(DATA_DIR, "llms.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    month_dir = os.path.join(ARCHIVE_DIR, now.strftime("%Y"), now.strftime("%m"))
    with open(os.path.join(month_dir, f"{now.strftime('%Y-%m-%d')}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    logging.info(f"Successfully calculated arbitrage for {len(records)} bounties.")


if __name__ == "__main__":
    run()
