"""
The Deephaven plugins the wheel carries inside it, registered with the server. Deephaven finds plugins by one
`deephaven.plugin` entry point per installed distribution, so the wheel's one entry point registers both:

  dh_chart_ext                                 our chart features (dh-charts), the JS plugin that applies them
  deephaven.plot.tradingview_lightweight       Deephaven's TradingView plugin, acme build (0.1.0+acme.1)

Neither is on PyPI. The repo's runtime/python installs them as packages of their own instead, and doesn't use this.
"""
from deephaven.plugin import Callback, Registration


class PluginsRegistration(Registration):
    @classmethod
    def register_into(cls, callback: Callback) -> None:
        from deephaven.plot.tradingview_lightweight._register import TvlRegistration
        from dh_chart_ext._register import ChartExtRegistration

        TvlRegistration.register_into(callback)
        ChartExtRegistration.register_into(callback)

