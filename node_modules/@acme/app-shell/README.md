# @acme/app-shell

Layer 3: an app shell for desk apps on Deephaven. It takes its Deephaven session from React context,
so the same panels run on our own connection (`DeephavenRoot` + `connect`) and inside an app that
Deephaven's React bootstrap set up (`DeephavenProvider` + `adoptConnection`).

```tsx
import '@acme/app-shell/style.css';
import { DeephavenRoot, Shell, apiWorkspaceStore, chartPanel, tablePanel, useApiClient } from '@acme/app-shell';
import { connect } from '@acme/dh-bindings';

const connectToServer = () => connect({ serverUrl: location.origin });

function App() {
  const { value: api } = useApiClient('chart_api', 'user:alice');
  return (
    <Shell
      kinds={[chartPanel({ symbols: ['ES', 'NQ'], user: 'alice' }), tablePanel()]}
      store={api ? apiWorkspaceStore(api) : null}
      defaultLayout={(_dock, add) => add('chart', { symbol: 'ES' })}
    />
  );
}

<DeephavenRoot connect={connectToServer}><App /></DeephavenRoot>
```

| Export | What |
|---|---|
| `Shell` | dockview layout (tabs, splits, floating groups, pop-out windows, maximize), "+ Panel", workspaces (Save, Save as, Revert, Delete), a link-group chip per panel, the connection status. The current layout is autosaved in the browser and restored on reload |
| `definePanel`, `PanelProps` | a panel kind: a component plus default params; params are kept in the layout (so saved with a workspace), changed with `setParams` |
| `apiWorkspaceStore`, `localWorkspaceStore` | workspaces on the server per user (`dh_chart_ext.workspaces` on any request API) or in this browser; layouts are versioned (`migrateLayout`) |
| `DeephavenRoot`, `DeephavenProvider`, `useDeephaven` | the session; `DeephavenRoot` connects, and when the server drops the session (a restart) connects again and remounts the app |
| `useTableLease`, `useOwnTable`, `useRows`, `useApiClient`, `usePublished`, `useConnectionState` | hooks: a shared or private table handle, live rows of a small table, one API stream per page and name, the server's published objects, connection state |
| `DataGrid` | any table: virtual scrolling (only the visible rows come from the server), server-side sort (shift-click for more columns) and quick filters, column resize, row click and highlight |
| `chartPanel`, `watchlistPanel`, `tablePanel`, `requestPanel`, `graphPanel`, `seriesPanel` | generic panels: a chart with toolbar and legend, a watchlist, any published table, a form for any method of a request API (from `describe`), the app's update graph, line charts of any table with a time column (wide, or long split by columns, a pane per symbol) |
| `strategiesPanel` | a trader's strategies (editor with the server's checks, save, delete), backtest runs with progress and P&L, and each run's results as a tab group (`dh_chart_ext.backtests`) |
| `RawChart`, `RawWorkspace`, `usePageStores` | front-end group C's chart and the page's chart stores (annotations, alerts, sessions) |

Dependencies: `dockview-react` 8.8. Peer dependencies: the other `@acme` packages, `@deephaven/jsapi-types` (types),
`lightweight-charts` 5.2.0, React 18 or 19.

`npm test` runs the unit tests; `npm run build` builds `dist/` (after `ext/`, `react/` and `bindings/`, whose types it
references).
