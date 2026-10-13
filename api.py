"""
REST API for the comp engine. Any bot that can make an HTTP request (Grok bot, Fusion/GHL webhooks,
Zapier, Make, Cursor scripts) can use this.

Run locally:   uvicorn api:app --host 0.0.0.0 --port 8000
Docs page:     http://localhost:8000/docs
Auth:          set COMP_API_KEY env var; callers send header  x-api-key: <key>
"""
import os
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from comp_engine import Comp, Subject, check_offer, comp_parcel, comps_from_csv

app = FastAPI(title="Land Comp Engine", version="1.0")
API_KEY = os.getenv("COMP_API_KEY")


def auth(x_api_key: Optional[str] = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "bad or missing x-api-key")


class CompRequest(BaseModel):
    subject: Subject
    comps: list[Comp]
    settings: Optional[dict] = None


class CheckRequest(CompRequest):
    proposed_offer: float


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/comp", dependencies=[Depends(auth)])
def comp(req: CompRequest):
    """Value a parcel and get a recommended offer."""
    return comp_parcel(req.subject, req.comps, req.settings)


@app.post("/check-offer", dependencies=[Depends(auth)])
def check(req: CheckRequest):
    """Second opinion on an offer BEFORE it gets texted. verdict = PASS / REVIEW / BLOCK."""
    return check_offer(req.subject, req.comps, req.proposed_offer, req.settings)


@app.post("/comp/csv", dependencies=[Depends(auth)])
async def comp_csv(
    acres: float = Form(...),
    lat: Optional[float] = Form(None),
    lon: Optional[float] = Form(None),
    proposed_offer: Optional[float] = Form(None),
    default_status: Optional[str] = Form(None, description="e.g. 'sold' if the file is a sold-only export"),
    files: list[UploadFile] = File(..., description="One or more CSV exports (Zillow, LandPortal, etc.)"),
):
    """Upload CSV exports straight from Zillow/LandPortal and get a value back."""
    all_comps, skipped = [], []
    for f in files:
        text = (await f.read()).decode("utf-8", errors="ignore")
        c, s = comps_from_csv(text, source=f.filename, default_status=default_status)
        all_comps += c
        skipped += [f"{f.filename} {x}" for x in s]
    subject = {"acres": acres, "lat": lat, "lon": lon}
    out = (check_offer(subject, all_comps, proposed_offer) if proposed_offer
           else comp_parcel(subject, all_comps))
    out["csv_rows_skipped"] = skipped
    return out
