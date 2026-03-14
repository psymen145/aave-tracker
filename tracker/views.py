from django.shortcuts import render

from tracker.models import Position


# Health factor thresholds
LIQUIDATING = 1.0   # already at/below liquidation
CRITICAL    = 1.1   # very close
WARNING     = 1.5   # at risk


def _risk_level(hf: float) -> str:
    if hf <= LIQUIDATING:
        return "liquidating"
    if hf < CRITICAL:
        return "critical"
    if hf < WARNING:
        return "warning"
    return "safe"


def _hf_color(risk: str) -> str:
    return {
        "liquidating": "#f85149",
        "critical":    "#ff7b50",
        "warning":     "#d29922",
        "safe":        "#3fb950",
    }[risk]


def _hf_bar_pct(hf: float) -> int:
    """Map health factor to a 0-100 bar width, capped at HF=3 for display."""
    capped = min(hf, 3.0)
    return int((capped / 3.0) * 100)


def dashboard(request):
    positions_qs = (
        Position.objects
        .filter(deleted=False)
        .select_related("wallet", "network")
    )

    position_data = []
    for p in positions_qs:
        try:
            hf = float(p.health_factor)
        except (ValueError, TypeError):
            continue
        if hf <= 0:
            continue  # no debt — skip

        try:
            collateral = float(p.total_usd_collateral)
        except (ValueError, TypeError):
            collateral = 0.0

        risk = _risk_level(hf)
        position_data.append({
            "position":            p,
            "health_factor":       round(hf, 4),
            "total_usd_collateral": collateral,
            "risk":                risk,
            "hf_color":            _hf_color(risk),
            "hf_bar_pct":          _hf_bar_pct(hf),
        })

    # Sort ascending: lowest health factor (most at risk) first
    position_data.sort(key=lambda x: x["health_factor"])

    context = {
        "positions":      position_data,
        "total_count":    len(position_data),
        "critical_count": sum(1 for p in position_data if p["risk"] in ("liquidating", "critical")),
        "warning_count":  sum(1 for p in position_data if p["risk"] == "warning"),
        "safe_count":     sum(1 for p in position_data if p["risk"] == "safe"),
    }
    return render(request, "tracker/dashboard.html", context)
