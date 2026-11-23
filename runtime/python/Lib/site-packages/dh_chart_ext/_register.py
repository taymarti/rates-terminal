"""Registers with the Deephaven server (entry point: deephaven.plugin): the middleware JS plugin, and the
request API object type (rpc.Api, type acme.Api; api.ChartApi is one)."""

from deephaven.plugin import Callback, Registration
from deephaven.plugin.utilities import DheSafeCallbackWrapper, create_js_plugin

PACKAGE_NAMESPACE = "dh_chart_ext"
JS_NAME = "_js"


class ChartExtRegistration(Registration):
    @classmethod
    def register_into(cls, callback: Callback) -> None:
        callback = DheSafeCallbackWrapper(callback)
        callback.register(create_js_plugin(PACKAGE_NAMESPACE, JS_NAME))

        from .rpc import ApiType  # imports deephaven: only inside a worker

        callback.register(ApiType)
