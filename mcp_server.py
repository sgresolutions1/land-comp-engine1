"""
MCP server for the comp engine. Plug it into Claude Desktop, Cursor, or any MCP-capable agent
(including Grok via xAI's remote MCP support) as tools the AI can call.

Local (Claude Desktop / Cursor):   python mcp_server.py
Remote (hosted, for Grok etc.):    python mcp_server.py --http --port 8001   -> endpoint /mcp
"""
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

from comp_engine import check_offer, comp_parcel, comps_from_csv

port = 8001
if "--port" in sys.argv:
    port = int(sys.argv[sys.argv.index("--port") + 1])

mcp = FastMCP(
    "land-comp-engine",
    host="0.0.0.0",
    port=port,
    instructions=(
        "Use comp_land_parcel to value vacant land and get the offer. Value = the cheapest matching lot currently "
        "for sale or pending (never off-market lots); offer = 40-60% of that. Do not compute the offer yourself. "
        "Before texting any offer to a seller, call check_land_offer; if verdict is BLOCK, do not send it; "
        "if REVIEW or decision NEEDS_HUMAN_REVIEW, hand it to a human."
    ),
)

COMP_FIELDS_DOC = (
    "Each comp: {price, acres, status ('sold'|'pending'|'active' or Zillow/LandPortal wording), "
    "date (sold date or list date, YYYY-MM-DD), and either lat/lon or distance_mi; optional address, source, url}. "
    "Include ALL for-sale, pending, and sold comps you found that match the lot. Off-market lots can be included "
    "too; they're ignored for value but help catch mistakes."
)


@mcp.tool(description="Value a vacant land parcel from comps and return market value, recommended offer, "
                      "confidence, decision, and warnings. " + COMP_FIELDS_DOC)
def comp_land_parcel(subject_acres: float, comps: list[dict],
                     subject_lat: Optional[float] = None, subject_lon: Optional[float] = None,
                     settings: Optional[dict] = None) -> dict:
    res = comp_parcel({"acres": subject_acres, "lat": subject_lat, "lon": subject_lon}, comps, settings)
    res["comps"] = [c for c in res["comps"] if c["included"]] + \
                   [{"address": c["address"], "price": c["price"], "acres": c["acres"], "excluded_because": c["reason"]}
                    for c in res["comps"] if not c["included"]]
    return res


@mcp.tool(description="Check an offer BEFORE it is sent to a seller. Returns verdict PASS / REVIEW / BLOCK "
                      "and catches offers priced off listing prices instead of sold prices. " + COMP_FIELDS_DOC)
def check_land_offer(subject_acres: float, comps: list[dict], proposed_offer: float,
                     subject_lat: Optional[float] = None, subject_lon: Optional[float] = None,
                     settings: Optional[dict] = None) -> dict:
    res = check_offer({"acres": subject_acres, "lat": subject_lat, "lon": subject_lon}, comps, proposed_offer, settings)
    res["engine"].pop("comps", None)  # keep the reply short for the bot
    return res


@mcp.tool(description="Value a parcel from raw CSV text exported from Zillow, LandPortal, Acres, etc. "
                      "Pass default_status='sold' if the export only contains sold properties.")
def comp_from_csv_text(subject_acres: float, csv_text: str, subject_lat: Optional[float] = None,
                       subject_lon: Optional[float] = None, default_status: Optional[str] = None,
                       proposed_offer: Optional[float] = None) -> dict:
    comps, skipped = comps_from_csv(csv_text, default_status=default_status)
    subj = {"acres": subject_acres, "lat": subject_lat, "lon": subject_lon}
    res = check_offer(subj, comps, proposed_offer) if proposed_offer else comp_parcel(subj, comps)
    res["csv_rows_skipped"] = skipped
    return res


if __name__ == "__main__":
    mcp.run(transport="streamable-http" if "--http" in sys.argv else "stdio")
