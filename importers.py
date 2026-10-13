"""
Turn a CSV export (Zillow, LandPortal, Acres, Land id, a scraper, whatever) into comps the engine understands.
Column names are matched loosely, so "Sale Price", "sold_price", "SoldPrice" all work.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Optional

SQFT_PER_ACRE = 43560

# first match wins, in this order
COLUMN_ALIASES = {
    "status": ["status", "homestatus", "listingstatus", "salestatus", "propertystatus", "listing_status"],
    "sold_price": ["soldprice", "saleprice", "lastsoldprice", "closeprice", "closedprice", "salesprice", "lastsaleprice"],
    "list_price": ["listprice", "askingprice", "listingprice", "price", "unformattedprice", "currentprice"],
    "acres": ["acres", "acreage", "lotacres", "lotsizeacres", "calcacres", "gisacres", "totalacres", "deededacres",
              "lotareaacres"],
    "lot_sqft": ["lotsizesqft", "lotsqft", "lotsize", "lotarea", "lotareavalue", "lotsquarefeet"],
    "lot_unit": ["lotareaunit", "lotsizeunit", "lotunit"],
    "sold_date": ["solddate", "saledate", "datesold", "closedate", "lastsolddate", "lastsaledate", "recordingdate"],
    "list_date": ["listdate", "listingdate", "datelisted", "listeddate", "date"],
    "dom": ["daysonmarket", "dom", "daysonzillow", "cdom"],
    "lat": ["latitude", "lat"],
    "lon": ["longitude", "lon", "lng", "long"],
    "distance": ["distance", "distancemi", "distancemiles", "miles"],
    "address": ["address", "streetaddress", "fulladdress", "propertyaddress", "situsaddress", "street"],
    "url": ["url", "detailurl", "link", "listingurl", "zillowurl"],
}


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _num(v) -> Optional[float]:
    if v is None:
        return None
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _map_columns(headers: list[str]) -> dict[str, str]:
    norm = {_key(h): h for h in headers}
    out = {}
    for field, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in norm and norm[a] not in out.values():
                out[field] = norm[a]
                break
    return out


def comps_from_csv(text: str, source: str = "csv", default_status: Optional[str] = None) -> tuple[list[dict], list[str]]:
    """Returns (comps, skipped_row_notes). Pass default_status='sold' if the export is a sold-only list."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    cols = _map_columns(reader.fieldnames or [])
    comps, skipped = [], []
    for n, row in enumerate(reader, start=2):
        g = lambda f: row.get(cols[f]) if f in cols else None
        status = (g("status") or default_status or "").strip()
        if not status:
            # no status column: a sold date means it sold
            status = "sold" if g("sold_date") else ""
        sold_price, list_price = _num(g("sold_price")), _num(g("list_price"))
        st_low = status.lower()
        is_sold = "sold" in st_low or "closed" in st_low
        price = (sold_price or list_price) if is_sold else (list_price or sold_price)

        acres = _num(g("acres"))
        if acres is None:
            lot = _num(g("lot_sqft"))
            unit = (g("lot_unit") or "").lower()
            if lot is not None:
                acres = lot if "acre" in unit else lot / SQFT_PER_ACRE

        if not price or not acres or not status:
            skipped.append(f"row {n}: missing {'price' if not price else 'acres' if not acres else 'status'}")
            continue
        comps.append({
            "price": price, "acres": acres, "status": status,
            "date": g("sold_date") if is_sold else g("list_date"),
            "days_on_market": int(_num(g("dom"))) if _num(g("dom")) is not None else None,
            "lat": _num(g("lat")), "lon": _num(g("lon")), "distance_mi": _num(g("distance")),
            "address": g("address"), "url": g("url"), "source": source,
        })
    return comps, skipped
