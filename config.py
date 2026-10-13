"""
All the knobs for the comp engine live here.
Change these numbers to match how YOUR business comps and offers.
Every setting can also be overridden per-request by passing `settings` in the API/MCP call.
"""

DEFAULTS = {
    # ---------- How value is set ----------
    # "cheapest_on_market": value = the cheapest matching lot currently FOR SALE (or pending) -- your method.
    #                       Sold comps are only a sanity check.
    # "sold_weighted":      value = weighted median of SOLD comps, listings used as a ceiling.
    "valuation_mode": "cheapest_on_market",

    # ---------- Which comps count ----------
    "radius_steps_mi": [3, 5, 10, 15],      # start tight, widen only if not enough comps
    "acreage_bands": [(0.5, 2.0), (0.33, 3.0), (0.2, 5.0)],  # comp acres must be within X–Y times subject acres
    "max_sold_age_months": 24,              # ignore sales older than this
    "max_active_age_days": 365,             # listings sitting a year+ are overpriced -> ignore
    "min_on_market_comps": 2,               # cheapest_on_market mode: want 2+ matching listings
    "min_sold_comps": 3,                    # sold_weighted mode: want 3+ sold comps

    # If the cheapest listing is under 60% of the next-cheapest, it probably has a problem
    # (landlocked, flood, wetlands). The engine then uses the next-cheapest and warns you.
    "cheap_listing_suspect_ratio": 0.6,

    # ---------- sold_weighted mode only ----------
    "status_weight": {"sold": 1.0, "pending": 0.8, "active": 0.3},
    "status_price_haircut": {"sold": 1.00, "pending": 0.97, "active": 0.85},

    # ---------- Size adjustment ----------
    # Bigger parcels sell for less per acre. 0.3 is a common land rule of thumb.
    "size_adjust_exponent": 0.3,

    # ---------- Offer math ----------
    # Offer = value x offer %. Your range is 40–60%. Lower confidence -> lower %.
    "offer_pct_range": (0.40, 0.60),
    "offer_pct_by_confidence": {"high": 0.55, "medium": 0.50, "low": 0.40},
    "min_confidence_to_auto_offer": 55,     # below this score, a human must review

    # ---------- Guardrails ----------
    "max_offer_pct_of_value": 0.60,         # check_offer blocks anything above this
    "max_ppa_spread_ratio": 4.0,            # if comps' $/acre vary more than 4x, values are unreliable
}
