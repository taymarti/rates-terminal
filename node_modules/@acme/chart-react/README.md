# @acme/chart-react

React components for trading charts, styled like TradingView desktop. From
[dh-charts](https://github.com/taymarti/dh-charts).

- **`ChartToolbar`**: symbol search (optionally accepting spreads typed as expressions), compare, a grouped interval menu, a chart-type menu with icons, an
  "ƒx Indicators" search dialog, range, and % / log scale. Controlled: pass `value` and `options`,
  get changes from `onChange(patch)`. Parts whose options are missing are left out. Styles are
  injected by the component; colors are CSS variables (`--acme-tb-*`) on `.acme-tb`.
- **`ChartLegend`**: the legend over a Lightweight Charts chart: symbol and O H L C at the crosshair
  with the change, then one row per study (value in its color; show/hide, settings dialog for
  parameters and color, remove). Pass the chart and its series; studies match series by title.
- **`AlertsPanel`**: the user's alerts: a form for a new alert (price or a study value, crossing a
  level), the active ones, and the log of fired ones. Data-agnostic: the host supplies rows and
  handles create and cancel.
- **`Watchlist`**: symbol, last, change, change %, colored, flashing on each tick; click to select.

```tsx
import { ChartToolbar } from '@acme/chart-react';

<ChartToolbar
  value={{ symbol: 'ES', resolution: '1m', studies: ['EMA_20'], scale: 'normal' }}
  options={{
    symbols: [{ key: 'ES', label: 'ES' }, { key: 'NQ', label: 'NQ' }],
    resolutions: [{ key: '1m', label: '1m', group: 'Minutes' }, { key: '5m', label: '5m', group: 'Minutes' }],
    studies: [{ key: 'EMA_20', label: 'EMA 20', group: 'Moving averages' }],
    scales: true,
  }}
  onChange={patch => setValue(v => ({ ...v, ...patch }))}
/>
```

An ES module for bundlers, with types. Peer dependencies: `react` 18 or 19; `lightweight-charts` 5
(types only, for `ChartLegend`). In deephaven.ui, the dh-chart-ext server plugin renders these as the
`acme.chart.Toolbar`, `acme.chart.Frame` (chart + legend) and `acme.chart.Watchlist` elements.
