"""
Land Comp Engine
----------------
Takes a subject parcel + a pile of comps (sold, pending, for-sale) from Zillow, LandPortal, etc.
Returns a market value, a recommended offer, a confidence score, and a plain-English explanation.

Default rule (your method): value = the CHEAPEST matching lot currently on the market (for sale or
pending), size-adjusted to the subject. Off-market lots are never used. Sold comps are a sanity check
only. Offer = 40-60% of that value depending on confidence.
Set valuation_mode="sold_weighted" in config.py to value off sold comps instead.
"""
from __future__ import annotations

import math
from copy import deepcopy
from datetime import date, datetime
from statistics import median
from typing import Any, Optional

from pydantic import BaseModel, Field

from .config import DEFAULTS

# ----------------------------------------------------------------------------- models


class Subject(BaseModel):
    acres: float = Field(..., gt=0, description="Subject parcel size in acres")
    lat: Optional[float] = None
    lon: Optional[float] = None
    county: Optional[str] = None
    state: Optional[str] = None
    apn: Optional[str] = None
    address: Optional[str] = None


class Comp(BaseModel):
    price: float = Field(..., gt=0, description="Sold price for sold comps, list price for pending/active")
    acres: float = Field(..., gt=0)
    status: str = Field(..., description="sold / pending / active (Zillow/LandPortal wording is fine)")
    date: Optional[str] = Field(None, description="Sold date for sold comps, list date for active/pending (YYYY-MM-DD)")
    days_on_market: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    distance_mi: Optional[float] = Field(None, description="Use if you don't have lat/lon")
    address: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None


# ----------------------------------------------------------------------------- helpers

_STATUS_MAP = {
    "sold": "sold", "recently_sold": "sold", "recently sold": "sold", "closed": "sold",
    "sale": "sold", "sold_listing": "sold",
    "pending": "pending", "under contract": "pending", "under_contract": "pending",
    "contingent": "pending", "active under contract": "pending", "sale pending": "pending",
    "active": "active", "for_sale": "active", "for sale": "active", "listed": "active",
    "listing": "active", "coming soon": "active", "coming_soon": "active", "new": "active",
}


def normalize_status(raw: str) -> Optional[str]:
    """Map whatever Zillow/LandPortal calls it to sold/pending/active. None = unusable (e.g. off-market)."""
    s = (raw or "").strip().lower()
    if s in _STATUS_MAP:
        return _STATUS_MAP[s]
    if "sold" in s or "closed" in s:
        return "sold"
    if "pend" in s or "contract" in s or "contingent" in s:
        return "pending"
    if "off" in s or "withdrawn" in s or "expired" in s or "cancel" in s:
        return None  # no price signal we can trust
    if "sale" in s or "active" in s or "list" in s:
        return "active"
    return None


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%b-%d-%Y", "%B-%d-%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s[:19] if "T" in s else s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _haversine_mi(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _weighted_median(values: list[float], weights: list[float]) -> float:
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    run = 0.0
    for v, w in pairs:
        run += w
        if run >= total / 2:
            return v
    return pairs[-1][0]


def _percentile(values: list[float], pct: float) -> float:
    v = sorted(values)
    k = (len(v) - 1) * pct
    f, c = math.floor(k), math.ceil(k)
    return v[f] if f == c else v[f] + (v[c] - v[f]) * (k - f)


def _money(x: float) -> str:
    return f"${x:,.0f}"


# ----------------------------------------------------------------------------- core

CHEAP = "cheapest_on_market"


def comp_parcel(subject: dict | Subject, comps: list[dict | Comp], settings: Optional[dict] = None,
                today: Optional[date] = None) -> dict[str, Any]:
    cfg = deepcopy(DEFAULTS)
    if settings:
        cfg.update(settings)
    today = today or date.today()
    mode = cfg["valuation_mode"]
    subj = subject if isinstance(subject, Subject) else Subject(**subject)
    comp_objs = [c if isinstance(c, Comp) else Comp(**c) for c in comps]

    # ---- 1. normalize every comp and compute the facts we need
    rows = []
    for i, c in enumerate(comp_objs):
        st = normalize_status(c.status)
        d = _parse_date(c.date)
        if c.distance_mi is not None:
            dist = c.distance_mi
        elif None not in (c.lat, c.lon, subj.lat, subj.lon):
            dist = _haversine_mi(subj.lat, subj.lon, c.lat, c.lon)
        else:
            dist = None
        age_days = (today - d).days if d else c.days_on_market
        rows.append({
            "id": i, "address": c.address, "source": c.source, "url": c.url,
            "raw_status": c.status, "status": st, "price": c.price, "acres": c.acres,
            "price_per_acre": c.price / c.acres, "distance_mi": None if dist is None else round(dist, 2),
            "age_days": age_days, "date": c.date,
            "included": False, "reason": None, "is_value_basis": False,
        })

    # ---- 2. hard filters
    for r in rows:
        if r["status"] is None:
            r["reason"] = f"off-market / unusable status '{r['raw_status']}' (not for sale, no real price)"
        elif r["status"] == "sold" and r["age_days"] is not None and r["age_days"] > cfg["max_sold_age_months"] * 30.4:
            r["reason"] = f"sale too old ({r['age_days']} days)"
        elif r["status"] == "active" and r["age_days"] is not None and r["age_days"] > cfg["max_active_age_days"]:
            r["reason"] = f"stale listing ({r['age_days']} days on market = overpriced)"

    candidates = [r for r in rows if r["reason"] is None]

    # ---- 3. widen radius / acreage band until we have enough of the comps this mode needs
    key_statuses = {"active", "pending"} if mode == CHEAP else {"sold"}
    need = cfg["min_on_market_comps"] if mode == CHEAP else cfg["min_sold_comps"]
    chosen_radius, chosen_band, step_penalty = None, None, 0
    pool: list[dict] = []
    done = False
    for bi, band in enumerate(cfg["acreage_bands"]):
        for ri, radius in enumerate(cfg["radius_steps_mi"]):
            pool = [r for r in candidates
                    if band[0] <= r["acres"] / subj.acres <= band[1]
                    and (r["distance_mi"] is None or r["distance_mi"] <= radius)]
            chosen_radius, chosen_band, step_penalty = radius, band, bi + ri
            if sum(1 for r in pool if r["status"] in key_statuses) >= need:
                done = True
                break
        if done:
            break

    pool_ids = {r["id"] for r in pool}
    for r in candidates:
        if r["id"] not in pool_ids:
            ratio = r["acres"] / subj.acres
            if not (chosen_band[0] <= ratio <= chosen_band[1]):
                r["reason"] = f"size too different ({r['acres']} ac vs subject {subj.acres} ac)"
            else:
                r["reason"] = f"too far ({r['distance_mi']} mi > {chosen_radius} mi)"

    # ---- 4. adjust each comp to the subject's size (+ weights for sold mode)
    k = cfg["size_adjust_exponent"]
    for r in pool:
        haircut = 1.0 if mode == CHEAP else cfg["status_price_haircut"][r["status"]]
        r["adj_price_per_acre"] = r["price_per_acre"] * haircut * (r["acres"] / subj.acres) ** k
        w = cfg["status_weight"][r["status"]]
        if r["age_days"] is not None and r["status"] == "sold":
            w *= 0.5 ** (r["age_days"] / 365)
        if r["distance_mi"] is not None:
            w *= 1 / (1 + r["distance_mi"] / 5)
        w *= 1 / (1 + abs(math.log(r["acres"] / subj.acres)))
        r["weight"] = w

    # ---- 5. outliers
    def _flag_outliers(group: list[dict], anchor_vals: list[float]):
        med = median(anchor_vals)
        for r in group:
            if r["adj_price_per_acre"] > med * 2.5 or r["adj_price_per_acre"] < med * 0.4:
                r["reason"] = f"outlier ({_money(r['adj_price_per_acre'])}/ac vs typical {_money(med)}/ac)"

    if mode == CHEAP:
        # on-market and sold are judged separately (listings usually sit above sales)
        for grp in ([r for r in pool if r["status"] in ("active", "pending")],
                    [r for r in pool if r["status"] == "sold"]):
            if len(grp) >= 4:
                _flag_outliers(grp, [r["adj_price_per_acre"] for r in grp])
    elif len(pool) >= 4:
        anchor = [r["adj_price_per_acre"] for r in pool if r["status"] == "sold"]
        if len(anchor) < 3:
            anchor = [r["adj_price_per_acre"] for r in pool]
        _flag_outliers(pool, anchor)
    pool = [r for r in pool if r["reason"] is None]

    for r in pool:
        r["included"] = True
        r["reason"] = "used"

    sold = [r for r in pool if r["status"] == "sold"]
    pend = [r for r in pool if r["status"] == "pending"]
    act = [r for r in pool if r["status"] == "active"]
    onm = act + pend

    warnings: list[str] = []
    result: dict[str, Any] = {
        "subject": subj.model_dump(),
        "valuation_mode": mode,
        "counts": {"for_sale": len(act), "pending": len(pend), "sold": len(sold),
                   "excluded": len(rows) - len(pool), "total_given": len(rows)},
        "search": {"radius_mi": chosen_radius, "acreage_band": f"{chosen_band[0]}x–{chosen_band[1]}x subject size"},
    }

    if not pool:
        result.update({
            "decision": "DO_NOT_OFFER", "confidence": 0, "market_value": None, "offer": None,
            "warnings": ["No usable comps. Find matching for-sale or sold lots before offering."],
            "explanation": "No comps survived the filters, so there is no defensible value.",
            "comps": rows,
        })
        return result

    conf = 100
    force_human = False
    price_to_beat = None

    # ---- 6. value
    if mode == CHEAP and onm:
        ranked = sorted(onm, key=lambda r: r["adj_price_per_acre"])
        pick = ranked[0]
        if len(ranked) >= 2 and ranked[0]["adj_price_per_acre"] < cfg["cheap_listing_suspect_ratio"] * ranked[1]["adj_price_per_acre"]:
            cheap = ranked[0]
            pct_below = 1 - cheap["adj_price_per_acre"] / ranked[1]["adj_price_per_acre"]
            warnings.append(
                f"Cheapest listing ({cheap['address'] or _money(cheap['price'])}) is {pct_below:.0%} below the next one, "
                f"so it may have a problem (no road access, flood, wetlands). Used the next-cheapest instead. Look at it before offering.")
            pick = ranked[1]
            conf -= 10
        pick["is_value_basis"] = True
        skipped_cheap = [r for r in rows if r["status"] in ("active", "pending") and r["reason"]
                         and r["reason"].startswith("outlier") and r["adj_price_per_acre"] < pick["adj_price_per_acre"]]
        for r in skipped_cheap:
            warnings.append(
                f"Skipped a much cheaper listing ({r['address'] or _money(r['price'])}, {_money(r['price'])}) as an outlier. "
                f"It may have a problem (no road access, flood, wetlands). Look at it before offering.")
        market_ppa = pick["adj_price_per_acre"]
        method = "cheapest matching lot on the market"
        price_to_beat = {k2: pick[k2] for k2 in ("address", "price", "acres", "status", "distance_mi", "url", "source")}
        if len(onm) < cfg["min_on_market_comps"]:
            conf -= 20
            warnings.append("Only 1 matching lot on the market. Check Zillow for others before trusting this value.")
        spread_vals = [r["adj_price_per_acre"] for r in onm]

        # sanity check against what land is actually SELLING for
        if len(sold) >= 2:
            sold_ppa = _weighted_median([r["adj_price_per_acre"] for r in sold], [r["weight"] for r in sold])
            result["sold_check_price_per_acre"] = round(sold_ppa, 2)
            if market_ppa > sold_ppa * 1.5:
                conf -= 10
                warnings.append(
                    f"Listings ({_money(market_ppa)}/ac) are well above recent sales ({_money(sold_ppa)}/ac). "
                    f"Sellers are asking more than land is selling for, so stay at the low end of the offer range.")
            elif market_ppa < sold_ppa * 0.7:
                warnings.append(
                    f"Cheapest listing ({_money(market_ppa)}/ac) is well BELOW recent sales ({_money(sold_ppa)}/ac). "
                    f"Check that listing for problems.")
        else:
            warnings.append("Fewer than 2 sold comps, so the listing price could not be double-checked against real sales.")
            conf -= 5

    else:
        if mode == CHEAP:
            # no listings at all -> fall back to sold, but a human must look
            warnings.append("NO matching lots on the market. Value is based on SOLD comps instead. A human must review.")
            force_human = True
            conf -= 25
            method = "sold comps (no matching listings found)"
            lead = sold
        elif sold:
            lead = sold + pend
            method = "sold+pending"
            if len(sold) < cfg["min_sold_comps"]:
                lead = pool
                method = "sold+pending+active (thin sold data)"
                warnings.append(f"Only {len(sold)} sold comp(s). Value leans partly on listings. Verify before offering.")
        else:
            lead = pool
            method = "listings only (NO sold comps)"
            warnings.append("ZERO sold comps. This value is based on asking prices only. A human must verify.")
            force_human = True

        market_ppa = _weighted_median([r["adj_price_per_acre"] for r in lead], [r["weight"] for r in lead])
        for r in lead:
            r["is_value_basis"] = True
        spread_vals = [r["adj_price_per_acre"] for r in lead]
        conf -= max(0, cfg["min_sold_comps"] - len(sold)) * 15

        if mode != CHEAP and len(act) >= 2 and sold:
            ceiling = _percentile([r["adj_price_per_acre"] for r in act], 0.25)
            if ceiling < market_ppa:
                warnings.append(
                    f"Competing listings would sell around {_money(ceiling)}/ac, below the sold-comp value of "
                    f"{_money(market_ppa)}/ac. Value capped to the listings.")
                market_ppa = ceiling
                conf -= 5

    market_value = market_ppa * subj.acres

    # ---- 7. confidence (shared checks)
    spread = max(spread_vals) / min(spread_vals) if len(spread_vals) > 1 else 1
    if spread > cfg["max_ppa_spread_ratio"]:
        conf -= 20
        warnings.append(f"Comps vary {spread:.1f}x in $/acre. Check road access, zoning, and flood.")
    elif spread > 2:
        conf -= 8
    conf -= step_penalty * 5
    if step_penalty:
        warnings.append(f"Search had to widen to {chosen_radius} mi / {chosen_band[0]}x–{chosen_band[1]}x size to find comps.")
    if any(r["distance_mi"] is None for r in pool):
        conf -= 10
        warnings.append("Some comps had no distance or coordinates, so they may not be local.")
    conf = max(0, min(100, conf))
    tier = "high" if conf >= 75 else "medium" if conf >= cfg["min_confidence_to_auto_offer"] else "low"

    # ---- 8. offer
    pct = cfg["offer_pct_by_confidence"][tier]
    lo_pct, hi_pct = cfg["offer_pct_range"]
    offer = math.floor(market_value * pct / 100) * 100
    offer_low = math.floor(market_value * lo_pct / 100) * 100
    offer_high = math.floor(market_value * hi_pct / 100) * 100

    decision = "NEEDS_HUMAN_REVIEW" if force_human or conf < cfg["min_confidence_to_auto_offer"] else "OK_TO_OFFER"

    basis_txt = ""
    if price_to_beat:
        basis_txt = (f" Price to beat: {price_to_beat['address'] or 'listing'} listed at {_money(price_to_beat['price'])} "
                     f"for {price_to_beat['acres']} ac ({price_to_beat['status']}).")
    explanation = (
        f"Valued {subj.acres} ac at {_money(market_ppa)}/ac = {_money(market_value)} using {method}.{basis_txt} "
        f"Comps used: {len(act)} for sale, {len(pend)} pending, {len(sold)} sold within {chosen_radius} mi. "
        f"Recommended offer {_money(offer)} ({pct:.0%} of value; range {_money(offer_low)}–{_money(offer_high)} = "
        f"{lo_pct:.0%}–{hi_pct:.0%}). Confidence {conf}/100 ({tier})."
    )

    result.update({
        "decision": decision,
        "confidence": conf,
        "confidence_tier": tier,
        "valuation_method": method,
        "price_to_beat": price_to_beat,
        "market_price_per_acre": round(market_ppa, 2),
        "market_value": round(market_value, 2),
        "offer": {"recommended": offer, "low": offer_low, "high": offer_high, "pct_of_value": pct},
        "warnings": warnings,
        "explanation": explanation,
        "comps": sorted(rows, key=lambda r: (not r["is_value_basis"], not r["included"], r.get("adj_price_per_acre") or 1e12)),
    })
    for r in result["comps"]:
        for key in ("adj_price_per_acre", "weight", "price_per_acre"):
            if r.get(key) is not None:
                r[key] = round(r[key], 4 if key == "weight" else 2)
    return result


def check_offer(subject: dict | Subject, comps: list[dict | Comp], proposed_offer: float,
                settings: Optional[dict] = None, today: Optional[date] = None) -> dict[str, Any]:
    """Second opinion on an offer a bot (or a person) is about to send. Run this BEFORE texting the seller."""
    cfg = deepcopy(DEFAULTS)
    if settings:
        cfg.update(settings)
    res = comp_parcel(subject, comps, settings, today)
    problems, notes = [], []

    if res["market_value"] is None:
        return {"verdict": "BLOCK", "problems": ["No usable comps, so there's no basis for any offer."],
                "proposed_offer": proposed_offer, "engine": res}

    mv = res["market_value"]
    acres = res["subject"]["acres"]
    pct = proposed_offer / mv
    rec = res["offer"]
    lo_pct, hi_pct = cfg["offer_pct_range"]

    if pct > cfg["max_offer_pct_of_value"]:
        problems.append(f"Offer is {pct:.0%} of value (max {cfg['max_offer_pct_of_value']:.0%}). "
                        f"It was probably priced off the wrong comps.")
    elif pct < lo_pct:
        notes.append(f"Offer is only {pct:.0%} of value, below your {lo_pct:.0%} floor. The seller will likely say no.")

    # Was the bot's number built off the WRONG kind of comp? Compare against each alternative basis.
    mid = (lo_pct + hi_pct) / 2
    alt_bases = {"OFF-MARKET lots": [r for r in res["comps"] if r["status"] is None]}
    if res["valuation_mode"] == CHEAP:
        alt_bases["SOLD lots"] = [r for r in res["comps"] if r["included"] and r["status"] == "sold"]
    else:
        alt_bases["LISTING prices"] = [r for r in res["comps"] if r["included"] and r["status"] == "active"]
    for label, grp in alt_bases.items():
        if not grp:
            continue
        alt_value = median([r["price_per_acre"] for r in grp]) * acres
        if abs(alt_value - mv) / mv < 0.2:
            continue  # basis is close enough to ours that it doesn't matter
        if abs(proposed_offer - alt_value * mid) < abs(proposed_offer - mv * mid):
            problems.append(
                f"Offer lines up with {label} (~{_money(alt_value)} value), not the cheapest matching lot on the "
                f"market ({_money(mv)}). The bot probably comped off the wrong properties."
                if res["valuation_mode"] == CHEAP else
                f"Offer lines up with {label} (~{_money(alt_value)} value), not sold comps ({_money(mv)}).")

    if res["decision"] == "NEEDS_HUMAN_REVIEW":
        notes.append("Engine confidence is too low to auto-send any offer.")

    verdict = "BLOCK" if problems else ("REVIEW" if notes else "PASS")
    return {
        "verdict": verdict,
        "proposed_offer": proposed_offer,
        "proposed_pct_of_value": round(pct, 3),
        "engine_recommended": rec,
        "price_to_beat": res.get("price_to_beat"),
        "problems": problems,
        "notes": notes,
        "engine": res,
    }
