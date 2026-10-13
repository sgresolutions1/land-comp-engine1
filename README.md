# Land Comp Engine

Comps vacant land the way your team does it, with no shortcuts and no math mistakes.

**Your method:** find the **cheapest matching lot currently on the market** (for sale or pending) near the
parcel, then offer **40–60%** of that. **Never** use off-market lots.

Your bots send the parcel and every lot they found on Zillow (and Redfin or LandPortal). The engine returns:

- **Price to beat**: the cheapest matching lot on the market, with its address, price, acres, and link
- **Value** and **$/acre**, size-adjusted to your lot
- **Recommended offer** plus the 40–60% range
- **Confidence** (0–100) and a **decision**: `OK_TO_OFFER`, `NEEDS_HUMAN_REVIEW`, or `DO_NOT_OFFER`
- **Warnings** in plain English, plus every comp marked as used or excluded, with the reason

It also has an **offer checker**. Before a bot texts a seller, it sends its number here and gets back `PASS`,
`REVIEW`, or `BLOCK`. It blocks offers that line up with **off-market lots** (or sold lots) instead of the
cheapest on-market lot, and offers above 60%.

## How it values a lot

1. **Throw out bad comps.** Off-market, withdrawn, and expired lots are ignored. So are listings sitting 365+ days, which are overpriced.
2. **Match the lot.** Comps must be within 3 miles and 0.5x–2x the acreage. If fewer than 2 lots on the market match, the search widens step by step: 5, 10, then 15 miles, and a wider size range. Confidence drops each time it widens.
3. **Size-adjust.** A 10-acre lot listed at $6k/acre means a 5-acre lot is worth about $7.4k/acre, because smaller lots sell for more per acre.
4. **Pick the cheapest.** The cheapest matching for-sale or pending lot sets the value. If it's 40%+ below the next cheapest, it probably has a problem (landlocked, flood, wetlands). The engine skips it, uses the next one, and warns you.
5. **Sanity-check against sold lots.** If listings are 50%+ above what land actually sold for, it warns you to stay at the low end of the range.
6. **Offer.** High confidence: 55%. Medium: 50%. Low: 40%. The range is always 40–60%.
7. **No matching lots on the market?** It falls back to sold comps and marks the deal `NEEDS_HUMAN_REVIEW`.

Change any of this in `comp_engine/config.py`. To value off sold comps instead, set `"valuation_mode": "sold_weighted"`.

## API or MCP? Use both

They share the same engine.

- **API (`api.py`)** is for your **Grok bot and Fusion/GoHighLevel**. Anything that can send a web request can use it, including GHL webhooks, Zapier, and Make. **Start here.**
- **MCP (`mcp_server.py`)** is for **Claude, Cursor, and any agent that speaks MCP**. If your Grok setup supports remote MCP servers, point it at the hosted `/mcp` URL.

## Setup

Open Terminal, then:
```bash
cd ~/Desktop/land-comp-engine
pip3 install -r requirements.txt
python3 -m pytest tests          # should say 15 passed
python3 -m uvicorn api:app --port 8000
```
Then open http://localhost:8000/docs in your browser to try it. Or open the folder in Cursor and ask Cursor to run it.

To put it online so your bots can reach it 24/7, deploy to Railway or Render:
- Start command: `uvicorn api:app --host 0.0.0.0 --port $PORT`
- Set env var `COMP_API_KEY` to a long random password. Bots send it as header `x-api-key`.

**MCP in Cursor:** open this folder in Cursor. `.cursor/mcp.json` is already set up.
**MCP in Claude Desktop:** add this to `claude_desktop_config.json`:
```json
{ "mcpServers": { "land-comp-engine": { "command": "python3", "args": ["/Users/sergio/Desktop/land-comp-engine/mcp_server.py"] } } }
```
**Hosted MCP (Grok or remote agents):** `python3 mcp_server.py --http --port 8001`. The endpoint is `/mcp`.

## Calling the API

`POST /comp`
```json
{
  "subject": { "acres": 5, "lat": 35.0, "lon": -82.0, "apn": "123-45-678" },
  "comps": [
    { "price": 59000, "acres": 5,   "status": "FOR_SALE",   "date": "2026-07-01", "lat": 35.01, "lon": -82.0, "url": "https://zillow.com/..." },
    { "price": 45000, "acres": 5,   "status": "PENDING",    "date": "2026-08-01", "distance_mi": 1.5 },
    { "price": 40000, "acres": 5.2, "status": "SOLD",       "date": "2026-05-01", "distance_mi": 1.2 },
    { "price": 30000, "acres": 5,   "status": "OFF_MARKET", "distance_mi": 0.5 }
  ]
}
```
Status can use Zillow, Redfin, or LandPortal wording. Give either `lat`/`lon` or `distance_mi`. `date` is the list date for listings and the sold date for sales.

`POST /check-offer` takes the same body plus `"proposed_offer": 22000`.

`POST /comp/csv` lets you upload exports directly (Redfin's "Download All" CSV works), as form fields `acres`, `lat`, `lon` plus one or more `files`.

## Paste this into your Grok bot's instructions

> When comping a lot, search Zillow for vacant land near the parcel and collect EVERY lot that is For Sale or
> Pending and roughly the same size (half to double the acres). Also grab recently sold lots. For each, record:
> price, acres, status, list/sold date, and lat/lon (or distance in miles), plus the link.
> Do NOT use off-market lots for value, and do NOT calculate the value or offer yourself.
> Send everything to the comp engine `/comp` and use `offer.recommended`.
> Before texting any offer, call `/check-offer`. If verdict is BLOCK, do not send it.
> If REVIEW or decision is NEEDS_HUMAN_REVIEW, tag the lead for a human.

## Tuning

Run 10–20 of your past deals through it and compare the numbers with what you actually offered. Then adjust
`offer_pct_by_confidence` and `cheap_listing_suspect_ratio` in `config.py`. You can also override any setting
per call: `"settings": {"offer_pct_by_confidence": {"high": 0.6, "medium": 0.5, "low": 0.4}}`.

## What it does NOT do (yet)

- **It doesn't search Zillow or Redfin by itself.** Neither has a public API, and scrapers break and go against their terms. Your Grok bot does the looking, and the engine does the math and the checking. A LandPortal API key would let it pull comps itself.
- **It can't see road access, flood zone, wetlands, slope, or zoning.** It warns you when a listing looks suspiciously cheap or comps vary a lot, which is usually one of these.
