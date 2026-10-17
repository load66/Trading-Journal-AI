"""Privacy-minimized, optional, quota-bounded Groq suggestion cache for Finance.

NEVER pass account identifiers, balance, dates or raw bank credentials to Groq.
Only a sanitized, short description, debit/credit sign and coarse amount bucket.
Call this during an authenticated provider sync, NEVER during a public GET.
"""
import hashlib
import json
import logging
import os
import re
import time
from decimal import Decimal, InvalidOperation

import httpx

from finance_category_rules import CATEGORIES, category_for

MODEL_DEFAULT = "openai/gpt-oss-120b"
CATEGORIES_AI = CATEGORIES - frozenset(("unreviewed", "bonus"))
MAX_DAILY_REQUESTS = 4
MAX_BATCH = 25
MAX_CACHE = 10000
URI = "https://api.groq.com/openai/v1/chat/completions"
LOGGER = logging.getLogger(__name__)
SYSTEM = (
    "Categorize these synthetic-shape banking descriptions conservatively. "
    "Return one JSON object with an items array of objects with i (integer), "
    "category (string), confidence (number 0 to 1). Only use the categories: "
    + ", ".join(sorted(CATEGORIES_AI)) + ". "
    "Never invent bank/account information. If ambiguous, use category 'other' "
    "with confidence below 0.7. No prose."
)


def sanitized_description(value):
    text = str(value or "")[:350]
    text = re.sub(r"https?://\S+|\S+@\S+", " ", text)
    text = re.sub(r"(?<!\w)\d{3,}(?!\w)", " ", text)
    text = re.sub(r"\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:96]


def cache_key(value):
    return hashlib.sha256(sanitized_description(value).casefold().encode()).hexdigest()


def amount_bucket(amount):
    try:
        amount = abs(Decimal(str(amount)))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    if amount < 1:
        return "<$1"
    if amount <= 50:
        return "$1-50"
    if amount <= 250:
        return "$50-250"
    if amount <= 1000:
        return "$250-1000"
    return ">$1000"


def candidates(state):
    if isinstance(state.get("items"), dict):
        for item in state["items"].values():
            for row in item.get("transactions", {}).values():
                yield row
    else:
        for account in state.get("transactions", {}).values():
            if isinstance(account, dict):
                yield from account.values()


def classify_unknown(state, *, post=httpx.post, now=None):
    """Add suggestions only. Network errors fail open to Uncategorized, no retries."""
    now = int(time.time()) if now is None else int(now)
    stats = {"attempted":0,"categorized":0,"failed":0}
    state["financeAILastSync"] = {**stats,"at":now}
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        LOGGER.info("finance_ai attempted=0 categorized=0 failed=0")
        return 0
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    usage = state.setdefault("financeAIUsage", {})
    if usage.get("day") != today:
        usage.update(day=today, calls=0)
    cache = state.setdefault("financeAICache", {})
    if not isinstance(cache, dict) or len(cache) >= MAX_CACHE:
        return 0
    remaining = min(MAX_DAILY_REQUESTS - int(usage.get("calls", 0)), 4)
    if remaining <= 0:
        LOGGER.info("finance_ai attempted=0 categorized=0 failed=0")
        return 0
    unique = {}
    for row in candidates(state):
        if not isinstance(row, dict) or row.get("pending") or row.get("kind") == "possible_reversal":
            continue
        category, _, signals = category_for(row.get("description"), row.get("amount"), pending=False,
                                           kind=row.get("kind"))
        # Never send unresolved suspected financial transfers to Groq: merchant
        # guesses cannot convert bank movements into consumer spending.
        if category != "unreviewed" or "possible_transfer" in signals:
            continue
        desc = sanitized_description(row.get("description"))
        bucket = amount_bucket(row.get("amount"))
        ident = cache_key(desc)
        if (not desc or bucket is None or ident in cache or ident in unique
                or ident in state.get("merchantRules", {})):
            continue
        unique[ident] = {
            "description": desc,
            "direction": "credit" if Decimal(str(row["amount"])) > 0 else "debit",
            "bucket": bucket
        }
        if len(unique) >= MAX_BATCH * remaining:
            break
    if not unique:
        LOGGER.info("finance_ai attempted=0 categorized=0 failed=0")
        return 0
    model = os.getenv("FINANCE_CATEGORY_MODEL") or os.getenv("GROQ_MODEL") or MODEL_DEFAULT
    total = 0
    items = list(unique.items())
    for offset in range(0, len(items), MAX_BATCH):
        if usage["calls"] >= MAX_DAILY_REQUESTS:
            break
        batch = items[offset:offset + MAX_BATCH]
        request = [{**payload, "i": i} for i, (_, payload) in enumerate(batch)]
        usage["calls"] += 1
        stats["attempted"] += len(batch)
        try:
            response = post(URI, timeout=20, headers={"Authorization": "Bearer " + key},
                            json={
                                "model": model, "temperature": 0, "max_completion_tokens": 2400,
                                "response_format": {"type": "json_object"},
                                "messages": [
                                    {"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": json.dumps({"items": request})}
                                ]
                            })
            if response.status_code == 429:
                stats["failed"] += len(batch)
                break
            if response.status_code != 200:
                stats["failed"] += len(batch)
                continue
            response_json = response.json()
            choice = response_json["choices"][0]
            if choice.get("finish_reason") != "stop":
                stats["failed"] += len(batch)
                continue
            parsed = json.loads(choice["message"]["content"])
            if not isinstance(parsed, dict) or set(parsed) != {"items"}:
                stats["failed"] += len(batch)
                continue
            predicted = parsed.get("items")
            if not isinstance(predicted, list) or len(predicted) > len(batch):
                stats["failed"] += len(batch)
                continue
            seen = set()
            validated = {}
            for result in predicted:
                if not isinstance(result, dict) or set(result) != {"i", "category", "confidence"}:
                    validated = {}
                    break
                index, category, confidence = result["i"], result["category"], result["confidence"]
                if (type(index) is not int or index < 0 or index >= len(batch)
                    or index in seen or category not in CATEGORIES_AI
                    or type(confidence) not in (int, float)
                    or not 0 <= confidence <= 1):
                    validated = {}
                    break
                seen.add(index)
                if confidence >= (0.90 if category == "subscription" else 0.7):
                    validated[batch[index][0]] = {"category": category, "confidenceScore": round(confidence, 2)}
            for hashed, result in validated.items():
                if len(cache) < MAX_CACHE:
                    cache.setdefault(hashed, result)
                    total += 1
            stats["categorized"] += len(validated)
            stats["failed"] += len(batch) - len(validated)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            stats["failed"] += len(batch)
            continue
    state["financeAILastSync"] = {**stats,"at":now}
    LOGGER.info("finance_ai attempted=%d categorized=%d failed=%d",
                stats["attempted"],stats["categorized"],stats["failed"])
    return total
