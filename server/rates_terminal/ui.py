"""
The terminal's panels as deephaven.ui components, for front-end groups A and B (C is the React terminal in
terminal/src, which draws its own charts from the same tables).

  A  terminal_a          one deephaven.ui dashboard: every panel, the selected symbol, dataset and book held as
                         state at its root, so a row clicked in one panel drives the others
  B  b_universe, b_chart, b_curve, b_books, b_security, b_dom, b_watchlist, b_data, b_news
                         the same panels one by one, for a React shell to host through WidgetView (web/, ?terminal).
                         Separate widgets share no React state, so they link through the server: a LinkHub
                         (dh_chart_ext.uikit) holds the selection and every open B panel follows it (one selection
                         for every B page)

Each panel takes what it shows as arguments (sym, dataset, book) and callbacks to change them; nothing in here
knows whether it runs in A or B.
"""
from __future__ import annotations

from typing import Callable, Optional

import deephaven.plot.express as dx
from deephaven import merge, ui
from dh_chart_ext.charts import price_chart
from dh_chart_ext.uikit import NEG, POS, LinkHub, dec, linked, row_value, signed, whole
from dh_chart_ext.workspace import chart_workspace

from .catalog import BY_ID, DATASETS
from .desk import BOOKS, COMPONENTS
from .universe import BOND_BY_CUSIP, BY_SYM, CONTRACT_BY_SYM, TENORS, otr_for, price_text

RESOLUTIONS = ["tick", "1s", "5s", "15s", "1m", "2m", "5m", "15m", "30m", "1h", "4h", "1D", "1W"]


def px_text(sym: str, v: float) -> str:
    """Prices in the instrument's notation (32nds for Treasuries), for the tables' text columns."""
    return price_text(BY_SYM.get(sym) or BY_SYM.get(str(sym).split("-")[0]), v)


def build(*, source, tables: dict, desk, market, annotations, alerts):
    """The panels over the app's tables; returns (terminal_a, {name: B element})."""
    T = tables
    nodes = source.nodes
    quotes = T["quotes"]

    # tables every viewer shares: built once
    curve_long = merge([
        T["ust_curve"].view(["Tenor", "Years", "Yield", "Series = `Now`"]),
        T["ust_curve"].view(["Tenor", "Years", "Yield = Yield - Chg1W / 100.0", "Series = `1W ago`"]),
        T["ust_curve"].view(["Tenor", "Years", "Yield = Yield - Chg1M / 100.0", "Series = `1M ago`"]),
    ])
    swaps_now = T["swap_curve"].view(["Tenor", "Years", "Yield = SwapRate", "Series = `SOFR swaps`"])
    # plotly-express 0.20 serves no figure that combines by= with log_x, markers or a title (each alone works)
    curve_plot = dx.line(merge([curve_long, swaps_now]), x="Years", y="Yield", by="Series")
    watch = T["watchlist_core"].view(["Sym", "Name", "Last = LastText", "ChgBp", "ChgPct", "Volume"])
    news_sorted = T["news"].sort_descending("Timestamp")
    calendar_sorted = T["econ_calendar"].sort("Time")
    auctions_sorted = T["treasury_auctions"].sort_descending("Date")

    def cusip_for(sym: str):
        """The Treasury a symbol stands for: itself, its yield or idio series, a future's CTD, a benchmark's OTR."""
        s = sym[:-2] if sym.endswith(".Y") else sym[:-5] if sym.endswith(".IDIO") else sym
        if s in BOND_BY_CUSIP:
            return s, None
        c = CONTRACT_BY_SYM.get(s)
        if c and c.basket:
            return desk._ctd(c, market.t)["bond"].cusip, f"CTD of {c.sym}"
        if any(t[0] == s for t in TENORS):
            o = otr_for(s)
            if o:
                return o.cusip, f"on-the-run {s}"
        return None, None

    # ------------------------------------------------------------------ one chart from a shared node

    @ui.component
    def node_chart(sym: str, res: str, chart_type: str = "line"):
        def acquire():
            key, table, _cols = nodes.chart(sym, res, [])
            return key, table

        key, table = ui.use_memo(acquire, [sym, res])
        ui.use_effect(lambda: (lambda: nodes.release(key)), [str(key)])
        return ui.use_memo(lambda: price_chart(table, chart_type=chart_type, title=sym), [str(key), chart_type])

    # ------------------------------------------------------------------ panels

    @ui.component
    def universe_panel(dataset: str, on_dataset, sym: str, on_sym):
        d = BY_ID.get(dataset) or DATASETS[0]
        groups: dict = {}
        for x in DATASETS:
            groups.setdefault(x["group"], []).append(x)
        picker = ui.picker(
            *[ui.section(*[ui.item(f"{x['title']}{'' if x['kind'] == 'chart' else '  (table)'}", key=x["id"]) for x in xs], title=g)
              for g, xs in groups.items()],
            label="Dataset", selected_key=d["id"], on_selection_change=lambda k: on_dataset(str(k)), width="100%",
        )

        def rows():
            syms = d.get("symbols") or []
            if d["kind"] == "chart" and syms:
                lit = ", ".join(f"`{s}`" for s in syms)
                return quotes.where(f"Sym in {lit}").view(["Sym", "Name", "Last = LastText", "ChgBp", "ChgPct", "Volume"]), "Sym"
            t = T.get(d.get("table") or "")
            return t, d.get("linkColumn")

        table, link = ui.use_memo(rows, [d["id"]])
        badges = ui.flex(
            ui.badge("chart" if d["kind"] == "chart" else "table only", variant="info"),
            ui.badge(d["frequency"], variant="neutral"),
            ui.badge("live" if d["live"] else "static", variant="positive" if d["live"] else "neutral"),
            *([ui.badge(d["unit"], variant="neutral")] if d.get("unit") else []),
            gap="size-100", wrap=True, flex_grow=0, flex_shrink=0, align_items="center",
        )
        body = ui.text("This dataset has no table.") if table is None else ui.table(
            table, density="compact", format_=[*dec(2, "ChgBp", "ChgPct"), *whole("Volume"), *signed("ChgBp", "ChgPct")] if d["kind"] == "chart" else None,
            on_row_press=(lambda r: on_sym(str(row_value(r, link)))) if link else None,
        )
        return ui.flex(picker, ui.text(d["description"]), badges, ui.view(body, flex_grow=1, min_height="size-2400"),
                       direction="column", gap="size-100", height="100%", margin="size-100")

    @ui.component
    def chart_panel(sym: str, on_sym, layout: str):
        return chart_workspace(
            source, symbol=sym, resolution="5m", range_="1D", studies=("EMA_20", "EMA_50", "VWAP", "Volume"),
            annotations=annotations, alerts=alerts, layout=layout, follow=sym, on_symbol=on_sym, resolutions=RESOLUTIONS,
        )

    @ui.component
    def curve_panel(on_sym):
        return ui.flex(
            ui.heading("Treasury curve: now, 1W and 1M ago; SOFR swaps", level=6, margin="size-0"),
            ui.view(curve_plot, flex_grow=1, min_height="size-3000"),
            ui.table(T["ust_curve"], density="compact", format_=signed("Chg1D", "Chg1W", "Chg1M", "Chg3M"),
                     on_row_press=lambda r: on_sym(str(row_value(r, "Sym"))), hidden_columns=["Cusip"]),
            direction="column", gap="size-100", height="100%",
        )

    @ui.component
    def books_panel(book: str, on_book, on_sym):
        def per_book():
            q = f"Book = `{book}`"
            attribution = merge([T["pnl_attribution"].where(q).view([f"Component = `{c}`", f"Pnl = (double) {c}"]) for c in COMPONENTS])
            positions = T["positions"] if book == "ALL" else T["positions"].where(q)
            return {
                "attribution": dx.bar(attribution, x="Component", y="Pnl", title=f"P&L since 07:00 ET by source: {book}"),
                "path": dx.line(T["pnl_timeseries"].where(q), x="Timestamp", y="Pnl", title=f"P&L through the day: {book}"),
                "risk": T["book_risk"].where(q),
                "positions": positions.view(["Book", "Sym", "Security", "Kind", "Qty", "Unit", "Price = PriceText", "Yield", "DV01", "IdioBp",
                                             "CarryDay", "PnlToday", "PnlNew", "Trades"]),
            }

        v = ui.use_memo(per_book, [book])
        books = ui.table(
            T["books"], density="compact", on_row_press=lambda r: on_book(str(row_value(r, "Book"))),
            format_=[*whole("Positions", "GrossMM", "DV01", "DV01Limit", "VaR99", "PnlToday", "TradesToday"), *signed("PnlToday"),
                     ui.TableFormat(cols="LimitUsePct", mode=ui.TableDatabar(min=0, max=100, color="accent")),
                     ui.TableFormat(if_=f"Book = `{book}`", background_color="#1f2a44")],
            hidden_columns=["Strategy"],
        )
        detail = ui.tabs(
            ui.tab(v["attribution"], title="Attribution"),
            ui.tab(v["path"], title="P&L today"),
            ui.tab(ui.table(v["positions"], density="compact", format_=[*whole("Qty", "DV01", "CarryDay", "PnlToday", "PnlNew"), *dec(2, "IdioBp"),
                                                                         *signed("PnlToday", "PnlNew")],
                            on_row_press=lambda r: on_sym(str(row_value(r, "Sym")))), title="Positions"),
            ui.tab(ui.table(v["risk"], density="compact", format_=whole("DV01", "KRD_Bills", "KRD_2Y", "KRD_5Y", "KRD_10Y", "KRD_30Y", "Level", "Slope",
                                                                         "Curvature", "Policy", "VaR99", "IdioRisk")), title="Risk"),
        )
        return ui.flex(ui.view(books, height="size-2400", flex_shrink=0), ui.view(detail, flex_grow=1, min_height=0),
                       direction="column", gap="size-100", height="100%")

    @ui.component
    def security_panel(sym: str, on_sym):
        cusip, via = cusip_for(sym)
        if cusip is None:
            return ui.flex(ui.heading(sym, level=4), ui.text(f"{sym} is not a Treasury, a Treasury future or a benchmark yield: "
                                                            "pick a CUSIP, a future (its CTD) or US2Y..US30Y (the on-the-run)."),
                           direction="column", margin="size-200")

        def filtered():
            q = f"Cusip = `{cusip}`"
            s = f"Sym = `{cusip}`"
            return {
                "master": T["security_master"].where(q),
                "cash": T["ust_cash"].where(q),
                "positions": T["positions"].where(s).view(["Book", "Qty", "Price = PriceText", "DV01", "PnlToday", "PnlNew", "Trades"]),
                "basket": T["deliverable_basket"].where(q).view(["Contract", "CF", "NetBasis32", "GrossBasis32", "Carry32", "ImpliedRepo", "Rank", "CTD"]),
                "auctions": T["cusip_auctions"].where(q).view(["Date", "Kind", "SizeBn", "HighYield", "WIYield", "TailBp", "BidToCover", "IndirectPct",
                                                              "DirectPct", "DealerPct"]),
                "rfqs": T["rfq_blotter"].where(s).sort_descending("Time").view(["Time", "Client", "Venue", "ClientSide", "SizeMM", "Level", "Status",
                                                                                "Book", "CoverBp"]),
                "flow": T["rfq_flow"].where(s),
            }

        v = ui.use_memo(filtered, [cusip])
        m = ui.use_row_data(v["master"]) or {}
        c = ui.use_row_data(v["cash"]) or {}

        def lv(label, value, fmt=None):
            if value is None:
                text = "–"
            elif fmt:
                text = fmt.format(value)
            else:
                text = str(value)
            return ui.labeled_value(text, label=label)

        b = BOND_BY_CUSIP[cusip]
        header = ui.flex(
            ui.heading(f"{b.name}", level=4, margin="size-0"),
            ui.text(f"{cusip} · {b.type} · {b.term} · {m.get('Status', '')}{' · ' + via if via else ''}"),
            direction="column", gap="size-50",
        )
        facts = ui.flex(
            lv("Bid / ask", f"{c.get('BidPx', '')} / {c.get('AskPx', '')}" if c else None),
            lv("Yield", c.get("Yield"), "{:.4f}"), lv("Chg (bp)", c.get("ChgBp"), "{:+.2f}"), lv("Rich/cheap (bp)", c.get("RichBp"), "{:+.2f}"),
            lv("DV01 per $1mm", c.get("DV01"), "{:,.0f}"), lv("Mod. duration", c.get("ModDur"), "{:.2f}"),
            lv("Outstanding ($bn)", m.get("OutstandingBn"), "{:,.1f}"), lv("SOMA ($bn)", m.get("SomaBn"), "{:,.1f}"),
            lv("Free float ($bn)", m.get("FreeFloatBn"), "{:,.1f}"), lv("Desk position ($mm)", m.get("DeskPositionMM"), "{:,.0f}"),
            lv("Idio (bp)", m.get("IdioBp"), "{:+.2f}"), lv("Street volume ($bn)", m.get("StreetVolBn"), "{:,.2f}"),
            lv("RFQs today", m.get("RfqCount")), lv("Hit ratio (%)", m.get("HitRatioPct")), lv("CTD for", m.get("CTDFor") or "–"),
            gap="size-300", wrap=True,
        )
        charts = ui.action_group(ui.item("Price", key=cusip), ui.item("Yield", key=f"{cusip}.Y"), ui.item("Idio", key=f"{cusip}.IDIO"),
                                 on_action=lambda k: on_sym(str(k)), density="compact")
        detail = ui.tabs(
            ui.tab(node_chart(f"{cusip}.IDIO", "1h"), title="Idio (1h)"),
            ui.tab(ui.table(v["positions"], density="compact", format_=[*whole("Qty", "DV01", "PnlToday", "PnlNew"), *signed("PnlToday", "PnlNew")]),
                   title="Desk"),
            ui.tab(ui.table(v["basket"], density="compact"), title="CTD & basis"),
            ui.tab(ui.table(v["auctions"], density="compact"), title="Auctions"),
            ui.tab(ui.table(v["rfqs"], density="compact"), title="RFQs"),
            ui.tab(dx.bar(v["flow"], x="Timestamp", y=["ClientBuyMM", "ClientSellMM"], title="RFQ flow per 5 minutes ($mm)"), title="Flow"),
        )
        return ui.flex(header, facts, ui.flex(ui.text("Chart in the linked panels:"), charts, gap="size-100", align_items="center"),
                       ui.view(detail, flex_grow=1, min_height="size-3000"), direction="column", gap="size-150", height="100%", margin="size-100")

    @ui.component
    def dom_panel(sym: str):
        def filtered():
            q = f"Sym = `{sym}`"
            ladder = T["book"].where(q).sort("Level").update_view(["Bid = px_text(Sym, BidPx)", "Ask = px_text(Sym, AskPx)"]) \
                .view(["BidOrders", "BidSz", "Bid", "Ask", "AskSz", "AskOrders"])
            tape = T["trades"].where(q).sort_descending("Timestamp").view(["Timestamp", "Price = PriceText", "Size", "Side", "Venue"])
            return ladder, tape

        ladder, tape = ui.use_memo(filtered, [sym])
        traded = sym in BY_SYM and BY_SYM[sym].has_volume and BY_SYM[sym].kind != "bond"
        if not traded:
            return ui.flex(ui.heading(sym, level=4), ui.text(f"No order book for {sym}: books are kept for listed futures, SOFR and fed funds "
                                                             "futures, ETFs and cross-asset contracts."), direction="column", margin="size-200")
        return ui.flex(
            ui.view(ui.table(ladder, density="compact", format_=[
                *whole("BidSz", "AskSz"), ui.TableFormat(cols="BidSz", mode=ui.TableDatabar(color=POS)), ui.TableFormat(cols="AskSz", mode=ui.TableDatabar(color=NEG)),
                ui.TableFormat(cols="Bid", color=POS), ui.TableFormat(cols="Ask", color=NEG)]), flex_grow=1, min_width=0),
            ui.view(ui.table(tape, density="compact", format_=[ui.TableFormat(if_="Side = `Buy`", color=POS),
                                                               ui.TableFormat(if_="Side = `Sell`", color=NEG)]), flex_grow=1, min_width=0),
            direction="row", gap="size-100", height="100%",
        )

    @ui.component
    def watchlist_panel(on_sym):
        return ui.table(watch, density="compact", format_=[*dec(2, "ChgBp", "ChgPct"), *whole("Volume"), *signed("ChgBp", "ChgPct")], on_row_press=lambda r: on_sym(str(row_value(r, "Sym"))))

    @ui.component
    def data_panel(dataset: str, on_sym):
        d = BY_ID.get(dataset) or DATASETS[0]
        t = T.get(d.get("table") or "")
        if t is None:
            return ui.text(f"{d['title']} has no table: it is chart-only. Chart its symbols, or pick another dataset.", margin="size-200")
        link = d.get("linkColumn")
        return ui.table(t, density="compact", on_row_press=(lambda r: on_sym(str(row_value(r, link)))) if link else None, show_quick_filters=True)

    @ui.component
    def news_panel():
        return ui.tabs(
            ui.tab(ui.table(news_sorted, density="compact", format_=signed("MoveBp")), title="Headlines"),
            ui.tab(ui.table(calendar_sorted, density="compact", format_=signed("CurveMoveBp")), title="Calendar"),
            ui.tab(ui.table(auctions_sorted, density="compact"), title="Auctions"),
            ui.tab(ui.table(T["cftc_cot"], density="compact", format_=signed("ChgLevFunds")), title="Positioning"),
        )

    # ------------------------------------------------------------------ A: one dashboard, state at its root

    @ui.component
    def terminal_layout():
        sym, set_sym = ui.use_state("ZN")
        dataset, set_dataset = ui.use_state("ust_futures")
        book, set_book = ui.use_state("ALL")

        def pick_dataset(k):
            set_dataset(k)
            syms = (BY_ID.get(k) or {}).get("symbols") or []
            if syms and sym not in syms:
                set_sym(syms[0])

        return ui.row(
            ui.column(
                ui.panel(universe_panel(dataset, pick_dataset, sym, set_sym), title="Universe"),
                ui.panel(watchlist_panel(set_sym), title="Watchlist"),
                width=20,
            ),
            ui.column(
                ui.stack(ui.panel(chart_panel(sym, set_sym, "terminal-a"), title="Chart"),
                         ui.panel(data_panel(dataset, set_sym), title="Data"), height=58, active_item_index=0),
                ui.stack(ui.panel(books_panel(book, set_book, set_sym), title="Books & P&L"),
                         ui.panel(news_panel(), title="News & calendar"), active_item_index=0),
                width=52,
            ),
            ui.column(
                ui.panel(curve_panel(set_sym), title="UST curve"),
                ui.stack(ui.panel(security_panel(sym, set_sym), title="Security"), ui.panel(dom_panel(sym), title="DOM / T&S"), height=58, active_item_index=0),
                width=28,
            ),
        )

    terminal_a = ui.dashboard(terminal_layout())

    # ------------------------------------------------------------------ B: one widget per panel, linked on the server

    hub = LinkHub(sym="ZN", dataset="ust_futures", book="ALL")

    def pick_dataset_b(k):
        hub.set("dataset", k)
        syms = (BY_ID.get(k) or {}).get("symbols") or []
        if syms and hub.snapshot()["sym"] not in syms:
            hub.set("sym", syms[0])

    b = {
        "b_universe": linked(hub, lambda v: universe_panel(v["dataset"], pick_dataset_b, v["sym"], hub.setter("sym"))),
        "b_chart": linked(hub, lambda v: chart_panel(v["sym"], hub.setter("sym"), "terminal-b")),
        "b_curve": linked(hub, lambda v: curve_panel(hub.setter("sym"))),
        "b_books": linked(hub, lambda v: books_panel(v["book"], hub.setter("book"), hub.setter("sym"))),
        "b_security": linked(hub, lambda v: security_panel(v["sym"], hub.setter("sym"))),
        "b_dom": linked(hub, lambda v: dom_panel(v["sym"])),
        "b_watchlist": linked(hub, lambda v: watchlist_panel(hub.setter("sym"))),
        "b_data": linked(hub, lambda v: data_panel(v["dataset"], hub.setter("sym"))),
        "b_news": news_panel(),
    }
    return terminal_a, b, hub
