"""
The terminal's own methods on chart_api (the same as the browser mock's, terminal/src/server/api.ts): the dataset
catalog, instrument reference, bond analytics, the curve, a contract's basket, scenarios, correlations, accrued
interest and everything about one security. Charts, bars, workspaces, backtests and the update graph are
dh_chart_ext's own.

Replies are JSON: Api.add makes them JSON-safe (numpy numbers become Python numbers, NaN and infinities null).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Callable

import numpy as np
from dh_chart_ext.data import STUDY_INFO, STUDY_TYPES
from dh_chart_ext.rpc import REQUIRED, ApiError, Reply

from .bonds import DAY_MS, accrued, fmt32, parse32, price_from_yield, risk, yield_from_price
from .catalog import BY_ID, DATASETS
from .market import curve_at
from .universe import BOND_BY_CUSIP, BONDS, BY_SYM, CONTRACT_BY_SYM, CONTRACTS, FUTURES_ROLLS, SESSIONS, SWAP_TENORS, TENORS, meta, otr_for

YEAR_MS = 365.25 * DAY_MS
ROLL_NAMES = {r: f"{r.split('-')[0]} Dec/Mar roll" for r in FUTURES_ROLLS}


def term_meta(sym: str) -> dict:
    """meta() for chart replies, with the rolls' names."""
    out = meta(sym)
    if sym in ROLL_NAMES:
        out = {**out, "name": ROLL_NAMES[sym], "dataset": "futures_rolls"}
    return out


def register_terminal(api, market, live, desk, tables: Callable[[], dict]):
    m = market

    # ---------------------------------------------------------------- the catalog
    def datasets(_c, _a):
        t = tables()
        return [{**d, "symbols": d.get("symbols", []), "rows": t[d["table"]].size if d.get("table") in t else None} for d in DATASETS]

    api.add("datasets", datasets, [], doc="The dataset catalog: chartable and table-only datasets, their symbols and tables.")

    def instruments(_c, a):
        ds = a.get("dataset", "")
        out = [{"sym": i.sym, **meta(i.sym), "session": i.session, "description": i.description or i.expr or ""} for i in m.inst if not ds or i.dataset == ds]
        if not ds or ds == "futures_rolls":
            out += [{"sym": r, **term_meta(r), "session": BY_SYM[r.split("-")[0]].session, "description": f"{r.split('-')[0]} minus {r.split('-')[1]}"}
                    for r in FUTURES_ROLLS]
        return out

    api.add("instruments", instruments, [("dataset", str, "")], doc="Reference data for symbols (all, or one dataset).")

    def dataset(_c, a):
        d = BY_ID.get(a["id"])
        if not d:
            raise ApiError(f"no dataset {a['id']}", "not_found")
        t = tables().get(d.get("table") or "")
        vol = any(BY_SYM[s].has_volume for s in d.get("symbols", []) if s in BY_SYM)
        cols = [{"name": c.name, "type": str(c.data_type)} for c in t.columns] if t is not None else []
        ind = [k for k in STUDY_TYPES if vol or not STUDY_INFO.get(k, ("", False))[1]] if d["kind"] == "chart" else []
        return Reply({**d, "columns": cols, "indicators": ind}, tables=[t] if t is not None else [])

    api.add("dataset", dataset, [("id", str, REQUIRED)], doc="A dataset's description, its table (as a reply table) and the indicators its symbols can use.",
            tables=True)

    # ---------------------------------------------------------------- Treasury analytics
    def bond(_c, a):
        b = BOND_BY_CUSIP.get(a["cusip"]) or otr_for(a["cusip"])
        if not b:
            raise ApiError(f"no bond {a['cusip']}", "not_found")
        now = m.t
        settle = now // DAY_MS * DAY_MS + DAY_MS
        live_y = m.yield_of(b.cusip)
        given = parse32(a["price"]) if a.get("price") else None
        yv = a.get("yield", float("nan"))
        y = yv if isinstance(yv, (int, float)) and math.isfinite(yv) else yield_from_price(b, given, settle) if given is not None else live_y
        r = risk(b, y, settle)
        repo = m.repo()
        face = a.get("face_mm", 10)
        h = a.get("horizon_days", 30)
        tenor = (b.maturity - settle) / YEAR_MS
        carry = (b.coupon - repo * r.dirty / 100) * (h / 365)
        rolled = y + (curve_at(m.F, max(1 / 365, tenor - h / 365)) - curve_at(m.F, tenor))
        roll = price_from_yield(b, rolled, settle + h * DAY_MS) - price_from_yield(b, y, settle + h * DAY_MS)
        scen = []
        for s in (-100, -50, -25, -10, 0, 10, 25, 50, 100):
            px = price_from_yield(b, y + s / 100, settle)
            scen.append({"shift_bp": s, "price": fmt32(px), "yield": round(y + s / 100, 4), "pnl_usd": round((px - r.price) * face * 10_000)})
        hedges = []
        for c in CONTRACTS:
            if not (c.front and c.basket):
                continue
            bk = min(m.basket(c, now), key=lambda x: x["implied"])
            cr = risk(bk["bond"], bk["y"], settle)
            fut_dv01 = cr.dv01 / bk["cf"] * (c.root.multiplier / 1000) * 0.1
            hedges.append({"contract": c.sym, "name": c.root.name, "ctd": bk["bond"].name, "fut_dv01": round(fut_dv01, 2),
                           "contracts": round(r.dv01 * face / fut_dv01), "duration_gap": abs(cr.mod_dur - r.mod_dur)})
        hedges.sort(key=lambda x: x["duration_gap"])
        day = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
        return {
            "cusip": b.cusip, "security": b.name, "type": b.type, "term": b.term, "coupon": b.coupon, "maturity": day(b.maturity), "settle": day(settle),
            "status": "on-the-run" if b.age == 0 else f"{b.age} off the run", "outstanding_bn": b.outstanding, "live_yield": round(live_y, 4), "yield": round(y, 4),
            "price": fmt32(r.price), "price_decimal": round(r.price, 6), "accrued": round(r.accrued, 5), "dirty": round(r.dirty, 5),
            "mod_duration": round(r.mod_dur, 3), "mac_duration": round(r.mac_dur, 3), "dv01_per_mm": round(r.dv01, 2), "convexity": round(r.convexity, 3),
            "position": {"face_mm": face, "dv01_usd": round(r.dv01 * face), "market_value_usd": round(r.dirty * face * 10_000)},
            "carry_roll": {"horizon_days": h, "repo": round(repo, 3), "carry_32nds": round(carry * 32, 2), "roll_32nds": round(roll * 32, 2),
                           "total_bp": round((carry + roll) / (r.mod_dur * r.dirty / 10_000), 2), "rolled_yield": round(rolled, 4)},
            "curve_rich_bp": round((y - curve_at(m.F, tenor)) * 100, 2),
            "scenarios": scen,
            "hedges": hedges[:3],
        }

    api.add("bond", bond, [("cusip", str, REQUIRED), ("price", str, ""), ("yield", float, float("nan")), ("face_mm", float, 10), ("horizon_days", int, 30)], doc="Analytics for a cash bond: price (32nds) and yield (give either to solve the other), risk, carry and roll-down, curve scenarios, and the "
            "futures hedge.")

    def curve(_c, _a):
        now = m.t
        pt = lambda s, ms: round(m.value_at(s, ms), 4)  # noqa: E731
        return {
            "tenors": [{"sym": s, "years": yrs, "label": lab, "cusip": (otr_for(s).cusip if otr_for(s) else None), "now": round(m.value(s), 4),
                        "d1": pt(s, now - DAY_MS), "w1": pt(s, now - 7 * DAY_MS), "m1": pt(s, now - 30 * DAY_MS)} for s, yrs, lab in TENORS],
            "swaps": [{"sym": s, "years": t, "now": round(m.value(s), 4)} for s, t in SWAP_TENORS],
            "factors": m.factors(),
        }

    api.add("curve", curve, [], doc="The on-the-run curve now and 1 day, 1 week and 1 month ago, and the swap curve.")

    def ctd(_c, a):
        c = CONTRACT_BY_SYM.get(a["contract"])
        if not c:
            raise ApiError(f"no contract {a['contract']}", "not_found")
        day = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
        return [{**r, "Maturity": day(r["Maturity"])} for r in live.basket_rows if r["Contract"] == c.sym]

    api.add("ctd", ctd, [("contract", str, REQUIRED)], doc="A contract's deliverable basket: CF, gross and net basis, implied repo, CTD.")

    def scenario(_c, a):
        now = m.t
        settle = now // DAY_MS * DAY_MS + DAY_MS
        shift, twist = a.get("shift_bp", 25), a.get("twist_bp", 0)
        out = []
        for b in BONDS:
            if b.type == "Bill":
                continue
            y = m.yield_of(b.cusip)
            if not math.isfinite(y):
                continue
            tenor = (b.maturity - now) / YEAR_MS
            s = shift + twist * max(-1, min(1, math.log(tenor / 5) / math.log(6)))
            p0 = price_from_yield(b, y, settle)
            p1 = price_from_yield(b, y + s / 100, settle)
            out.append({"cusip": b.cusip, "security": b.name, "shift_bp": round(s, 2), "pnl_per_mm": round((p1 - p0) * 10_000)})
        return out

    api.add("scenario", scenario, [("shift_bp", float, 25), ("twist_bp", float, 0)],
            doc="P&L per $1mm face (bonds) or per contract (futures) for a parallel shift and a twist (bp, pivot at 5Y).")

    def correlation(_c, a):
        days = int(a.get("days", 60))
        syms = []
        for s in a["symbols"]:
            s = str(s).strip()
            if s not in m.index and s.upper() in m.index:
                s = s.upper()
            if s not in m.index:
                raise ApiError(f"unknown symbol {s}", "not_found")
            syms.append(s)
        ch = []
        for s in syms:
            col = np.array([r[m.index[s]] for r in m.daily_c[-(days + 1):]], dtype=float)
            col[~np.isfinite(col)] = np.nan  # a future before its deliverables were issued is +inf in the simulator
            ch.append(np.diff(col))

        def corr(x, y):
            ok = np.isfinite(x) & np.isfinite(y)
            if ok.sum() < 3:
                return 0.0
            x, y = x[ok], y[ok]
            sx, sy = x.std(), y.std()
            return float(((x - x.mean()) * (y - y.mean())).mean() / (sx * sy)) if sx and sy else 0.0

        return {"symbols": syms, "days": days, "matrix": [[round(corr(x, y), 3) for y in ch] for x in ch]}

    api.add("correlation", correlation, [("symbols", list, REQUIRED), ("days", int, 60)], doc="Correlation of daily changes between symbols over a window.")

    def accrued_(_c, a):
        b = BOND_BY_CUSIP.get(a["cusip"])
        if not b:
            raise ApiError(f"no bond {a['cusip']}", "not_found")
        d = int(datetime.fromisoformat(a["date"]).replace(tzinfo=timezone.utc).timestamp() * 1000) if a.get("date") else m.t
        return {"cusip": b.cusip, "date": datetime.fromtimestamp(d / 1000, tz=timezone.utc).strftime("%Y-%m-%d"), "accrued": round(accrued(b, d), 6)}

    api.add("accrued", accrued_, [("cusip", str, REQUIRED), ("date", str, "")], doc="Accrued interest of a bond on a date.")

    def security(_c, a):
        out = desk.security(str(a["sym"]))
        if not out:
            raise ApiError(f"{a['sym']} is not a Treasury cash bond or future", "not_found")
        return out

    api.add("security", security, [("sym", str, REQUIRED)], doc="Everything about one Treasury: amount, SOMA, desk position by book, price, idio, CTD and basis, auctions, RFQ and street volume. "
            "A future gives its CTD.")

    def sessions(_c, _a):
        return SESSIONS

    api.add("session_specs", sessions, [], doc="Every exchange calendar the terminal uses.")
