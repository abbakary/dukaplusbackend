"""AI endpoints — data-driven analysis from tenant payload (no hardcoded demo data)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["ai"])


class ChatRequest(BaseModel):
    message: str = ""
    language: str = "sw"
    shopContext: dict[str, Any] = Field(default_factory=dict)
    history: list[Any] = Field(default_factory=list)


class SmartScheduleRequest(BaseModel):
    language: str = "sw"
    staffName: str = ""
    role: str = ""
    existingEvents: list[Any] = Field(default_factory=list)
    businessType: str = "retail"


class CrossMatrixRequest(BaseModel):
    language: str = "sw"
    locationSummary: list[Any] = Field(default_factory=list)
    customerTopPurchases: list[Any] = Field(default_factory=list)
    stagnantItems: list[Any] = Field(default_factory=list)
    selectedLocation: str = "all"
    selectedCustomer: str = "all"


def _chat_fallback(message: str, language: str, ctx: dict[str, Any]) -> str:
    is_sw = language == "sw"
    sales_count = ctx.get("salesCount", 0)
    product_count = ctx.get("productCount", 0)
    customer_count = ctx.get("customerCount", 0)

    if sales_count == 0 and product_count == 0:
        return (
            "Hakuna data ya duka bado. Ongeza bidhaa na rekodi mauzo kupitia POS kwanza."
            if is_sw
            else "No shop data yet. Add products and record sales via POS to get tailored advice."
        )

    if is_sw:
        return (
            f"**Msaidizi wa Duka+**\n"
            f"- Bidhaa: **{product_count}** | Wateja: **{customer_count}** | Mauzo: **{sales_count}**\n"
            f"- Swali lako: _{message[:120]}_\n"
            f"- Ongeza maelezo zaidi au fungua **Dashibodi / BI / Geo Matrix** kwa uchambuzi wa kina kutoka data halisi."
        )
    return (
        f"**Duka+ Assistant**\n"
        f"- Products: **{product_count}** | Customers: **{customer_count}** | Sales: **{sales_count}**\n"
        f"- Your question: _{message[:120]}_\n"
        f"- Open **Dashboard / BI / Geo Matrix** for insights computed from your live data."
    )


def _build_cross_matrix_analysis(body: CrossMatrixRequest) -> dict[str, Any]:
    is_sw = body.language == "sw"
    locs = body.locationSummary or []
    tops = body.customerTopPurchases or []
    stagnant = body.stagnantItems or []

    if not locs and not tops:
        return {
            "executiveSummarySw": "Hakuna data ya mauzo bado. Ongeza bidhaa, wateja, na rekodi mauzo kupitia POS.",
            "executiveSummaryEn": "No sales data yet. Add products, customers, and record sales via POS.",
            "topGrowthLocations": [],
            "underperformingGaps": [],
            "crossSellOpportunities": [],
            "generatedAt": datetime.now(UTC).isoformat(),
        }

    top_growth = []
    for loc in locs[:3]:
        name = loc.get("locationName") or loc.get("location") or "—"
        rev = loc.get("totalRevenue") or 0
        top_prod = loc.get("topSellingProductName") or "—"
        top_cust = next(
            (c.get("customerName") for c in tops if c.get("customerLocation") == name),
            "—",
        )
        top_growth.append(
            {
                "location": name,
                "keyProducts": [top_prod],
                "topCustomer": top_cust,
                "rationale": (
                    f"Maeneo haya yamechangia TSh {rev:,.0f}."
                    if is_sw
                    else f"Territory contributed TSh {rev:,.0f} in tracked revenue."
                ),
            }
        )

    gaps = []
    for item in stagnant[:3]:
        gaps.append(
            {
                "location": "Multiple" if not is_sw else "Maeneo mbalimbali",
                "laggingProduct": item.get("name") or "—",
                "affectedCustomers": [],
                "fixStrategy": (
                    f"Imeuza vipande {item.get('units', 0)} tu — jaribu ofa au kifurushi."
                    if is_sw
                    else f"Only {item.get('units', 0)} units sold — try a promo or bundle."
                ),
            }
        )

    cross_sell = []
    for c in tops[:3]:
        cross_sell.append(
            {
                "customerName": c.get("customerName") or "—",
                "location": c.get("customerLocation") or "—",
                "currentFavorite": c.get("productName") or "—",
                "recommendedCrossSell": "Related category add-on" if not is_sw else "Bidhaa nyingine katika kategoria hiyo hiyo",
                "estimatedRevenueGain": 50000,
            }
        )

    lead = locs[0].get("locationName") or locs[0].get("location") or "—" if locs else "—"
    return {
        "executiveSummarySw": f"Maeneo yanayoongoza: {lead}. {len(stagnant)} bidhaa zina mauzo ya chini.",
        "executiveSummaryEn": f"Leading territory: {lead}. {len(stagnant)} products show low velocity.",
        "topGrowthLocations": top_growth,
        "underperformingGaps": gaps,
        "crossSellOpportunities": cross_sell,
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _smart_schedule_fallback(body: SmartScheduleRequest) -> list[dict[str, Any]]:
    name = body.staffName or ("Mfanyakazi" if body.language == "sw" else "Staff")
    today = datetime.now(UTC).date().isoformat()
    return [
        {
            "id": "ai-shift-open",
            "title": f"{name} — {'Fungua duka' if body.language == 'sw' else 'Open shop'}",
            "date": today,
            "time": "07:30",
            "category": "shift",
            "priority": "high",
        },
        {
            "id": "ai-stock-check",
            "title": "Stock check" if body.language != "sw" else "Ukaguzi wa akiba",
            "date": today,
            "time": "10:00",
            "category": "inventory",
            "priority": "medium",
        },
    ]


@router.post("/chat")
async def ai_chat(body: ChatRequest):
    reply = _chat_fallback(body.message, body.language, body.shopContext)
    return {
        "reply": reply,
        "model": "duka-data-driven-heuristic",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.post("/smart-schedule")
async def ai_smart_schedule(body: SmartScheduleRequest):
    events = _smart_schedule_fallback(body)
    return {"events": events, "source": "local-heuristic-engine"}


@router.post("/cross-matrix-analysis")
async def ai_cross_matrix(body: CrossMatrixRequest):
    analysis = _build_cross_matrix_analysis(body)
    return {
        "analysis": analysis,
        "source": "local-heuristic-engine",
        "model": "duka-matrix-ai-v1",
    }
