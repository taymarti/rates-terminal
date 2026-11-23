/**
 * @acme/dh-widgets: Deephaven's own widgets (deephaven.ui, plotly express, the TradingView plugin: anything a
 * server plugin renders) in a page of ours and in @acme/app-shell panels, outside Deephaven's IDE: front-end
 * group B.
 *
 *   @acme/dh-widgets           DeephavenWidgetsRoot: Deephaven's bootstrap chain (JS API, plugins, connection)
 *   @acme/dh-widgets/widgets   ShellSession, PluginWidget, ServerWidget, serverWidgetPanel (import from code loaded
 *                              lazily under the root: Deephaven's modules need the `dh` global it sets)
 *   @acme/dh-widgets/remote-module-loader
 *                              alias @paciolan/remote-module-loader to this in the app's bundler: @deephaven/app-utils
 *                              imports that CommonJS package's default export, which ESM interop breaks
 *
 * The app's Vite config also needs `define: { 'process.env': {} }` and esbuild's `global: 'globalThis'`, and
 * `dedupe` for react, react-dom, react-redux and redux (Deephaven's packages must share the app's copies).
 */
export { DeephavenWidgetsRoot, type DeephavenWidgetsRootProps } from './root';
