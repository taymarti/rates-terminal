# @acme/chart-ext

Chart features for TradingView Lightweight Charts, built for Deephaven's TradingView plugin but
usable on any Lightweight Charts chart. From [dh-charts](https://github.com/taymarti/dh-charts).

- **Plain LWC plugins and helpers:** `TimeRangeShading`, `crosshairSync`, `bindPriceLines`
  (persistent price lines around `@tradingview/lwc-plugin-user-price-lines`), `bindDrawings`
  (horizontal ray, trend line).
- **The hook for Deephaven charts:** `attachTvlExtensions(root, extensions)` applies extensions to
  every Deephaven TradingView chart under `root`. It needs the plugin build that fires `tvl-chart`
  events (`@deephaven/js-plugin-tradingview-lightweight` 0.1.0-acme.1, from our fork).
- **Annotation persistence:** `tableAnnotationStore(dh, table, owner)` over the Deephaven
  annotations table (`dh_chart_ext.store.annotation_table`), and the page store
  (`providePageAnnotationStore`).

```ts
import { attachTvlExtensions, defaultExtensions } from '@acme/chart-ext';
attachTvlExtensions(document.getElementById('charts')!, defaultExtensions());
```

An ES module for bundlers (Vite, webpack), with types. Peer dependencies: `lightweight-charts`
5.2.0 (one copy per app) and, for the store's types, `@deephaven/jsapi-types` (optional).
