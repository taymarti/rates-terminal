"""
L1, chart objects: TvlCharts composed from L0 tables in house style. Used by deephaven.ui (directly or
through the workspace) and by React apps that render Deephaven's chart with WidgetView. Front ends
drawing their own charts from table data don't use this layer.

  price_chart(bars_t, chart_type="candle", studies=[EMA(20), RSI()], scale="log")
  tick_chart(ticks_t)

Studies with pane "overlay" are drawn over the price; every other pane name gets its own pane below,
in first-seen order (volume, rsi, ...). Missing study columns are computed (with_studies).
"""
from __future__ import annotations

from typing import Literal, Optional, Sequence

import deephaven.plot.tradingview_lightweight as tvl
from deephaven.table import Table

from .data import Bollinger, Study, Volume, in_range, with_studies

ChartType = Literal["candle", "bar", "line", "area"]
Scale = Literal["normal", "log", "percent", "indexed"]

THEME = {"up": "#26a69a", "down": "#ef5350", "line": "#2962ff", "grid": "#1e222d"}
STUDY_COLORS = ["#f7a600", "#ab47bc", "#00bcd4", "#8bc34a", "#ff7043", "#90a4ae"]
_SCALE_MODE = {"normal": "normal", "log": "logarithmic", "percent": "percentage", "indexed": "indexed_to_100"}
_LINEAR = tvl.price_scale(mode="normal")


def price_chart(
    bars_t: Table,
    *,
    chart_type: ChartType = "candle",
    studies: Sequence[Study] = (),
    scale: Scale = "normal",
    title: Optional[str] = None,
    auto_bin: bool = False,
    since=None,
    on_press=None,
    colors: Optional[Sequence[Optional[str]]] = None,
    compare: Sequence[tuple[str, Table, str]] = (),
) -> tvl.TvlChart:
    """A price chart from a bars table (L0 `bars`, one symbol), with studies on overlays and panes.

    auto_bin=False keeps the bars at the table's own resolution; True lets the plugin re-bin them
    coarser when zoomed out (fewer bars on screen, but the resolution then depends on zoom).

    since (an Instant) limits the chart to a range; studies are computed on the full table first, so
    they are warmed up at the range start. `scale` applies to the price pane only.

    colors: one color per study (None: the next house color); a study's lines share its color.

    compare: (title, bars table, color) per symbol to overlay as a line of its closes. Comparing
    only makes sense in percent, so the price scale switches to percent unless log was asked for.
    `since` (an Instant or a data.Lookback) applies to them too."""
    if compare and scale == "normal":
        scale = "percent"
    t = in_range(with_studies(bars_t, studies, by=None), since)
    series = [_price_series(t, chart_type, title, auto_bin, scale)]
    for ctitle, ct, ccolor in compare:
        series.append(tvl.line(in_range(ct, since), timestamp="Timestamp", value="Close", title=ctitle, color=ccolor,
                               line_width=2, last_value_visible=True))

    panes: list[str] = []
    color_i = 0
    for si, s in enumerate(studies):
        color = (colors[si] if colors and si < len(colors) else None) or STUDY_COLORS[color_i % len(STUDY_COLORS)]
        if s.pane == "overlay":
            pane = None
        else:
            if s.pane not in panes:
                panes.append(s.pane)
            pane = 1 + panes.index(s.pane)
        if isinstance(s, Volume):
            vol_color = colors[si] if colors and si < len(colors) and colors[si] else "rgba(120, 144, 156, 0.6)"
            series.append(tvl.histogram(t, timestamp="Timestamp", value="Volume", color=vol_color, pane=pane, title="Volume",
                                        price_scale=_LINEAR))
            continue
        for col in s.columns():
            mid = isinstance(s, Bollinger) and col.endswith("_Mid")
            series.append(
                tvl.line(
                    t, timestamp="Timestamp", value=col, title=col, pane=pane,
                    color=color, line_width=1,
                    line_style="dashed" if mid else None, last_value_visible=False, price_lines=_guides(s),
                    # study panes keep a linear scale; otherwise the plugin carries the price pane's mode (log, %) into them
                    price_scale=_LINEAR if pane is not None else None,
                )
            )
        color_i += 1

    return tvl.chart(
        *series,
        pane_stretch_factors=[3.0] + [1.0] * len(panes) if panes else None,
        time_scale=tvl.time_scale(time_visible=True, seconds_visible=True),
        on_press=on_press,
    )


def tick_chart(ticks_t: Table, *, price_col: str = "Price", title: Optional[str] = None, scale: Scale = "normal") -> tvl.TvlChart:
    """Every tick as a line (one symbol). The plugin downsamples long histories on the server."""
    return tvl.chart(
        tvl.line(ticks_t, timestamp="Timestamp", value=price_col, color=THEME["line"], line_width=1, title=title,
                 price_scale=tvl.price_scale(mode=_SCALE_MODE[scale])),
        time_scale=tvl.time_scale(time_visible=True, seconds_visible=True),
    )


def _price_series(t: Table, chart_type: ChartType, title: Optional[str], auto_bin: bool, scale: Scale):
    ps = tvl.price_scale(mode=_SCALE_MODE[scale])  # the price pane's scale only; study panes keep theirs
    if chart_type == "candle":
        return tvl.candlestick(t, up_color=THEME["up"], down_color=THEME["down"], border_visible=False,
                               wick_up_color=THEME["up"], wick_down_color=THEME["down"], auto_bin=auto_bin, title=title, price_scale=ps)
    if chart_type == "bar":
        return tvl.bar(t, up_color=THEME["up"], down_color=THEME["down"], auto_bin=auto_bin, title=title, price_scale=ps)
    if chart_type == "line":
        return tvl.line(t, timestamp="Timestamp", value="Close", color=THEME["line"], line_width=2, title=title, price_scale=ps)
    if chart_type == "area":
        return tvl.area(t, timestamp="Timestamp", value="Close", line_color=THEME["line"],
                        top_color="rgba(41, 98, 255, 0.4)", bottom_color="rgba(41, 98, 255, 0.0)", title=title, price_scale=ps)
    raise ValueError(f"unknown chart_type {chart_type!r}")


def _guides(s: Study):
    """Fixed reference lines for bounded studies (RSI 30/70)."""
    if s.pane == "rsi":
        return [tvl.price_line(price=70, color="rgba(239, 83, 80, 0.5)", line_style="dashed", axis_label_visible=False),
                tvl.price_line(price=30, color="rgba(38, 166, 154, 0.5)", line_style="dashed", axis_label_visible=False)]
    return None
