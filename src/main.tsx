/**
 * The rates terminal, version C: @acme/app-shell with the terminal's panels, on the Deephaven server (server/: the
 * market simulator and every table in Python, charts through dh_chart_ext) when it is up, else on the in-page mock
 * (src/mock, on @acme/dh-mock), which needs no server at all.
 *
 *   ?server=mock    the in-page mock even when the server is up;  ?server=dh  the server only (no fallback)
 *   ?user=alice     whose workspaces, drawings and alerts (default: trader)
 *   ?reset          start from the default layout (the current one is otherwise kept in this browser)
 */
import { StrictMode, useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { DockviewApi } from 'dockview-react';
import '@acme/app-shell/style.css';
import {
  ApiStatus,
  DeephavenRoot,
  Shell,
  SymbolModelProvider,
  ThemeSwitch,
  apiWorkspaceStore,
  chartPanel,
  graphPanel,
  requestPanel,
  seriesPanel,
  strategiesPanel,
  tablePanel,
  useApiClient,
  usePageStores,
  watchlistPanel,
  type AddPanel,
  type PanelKind,
  type WorkspaceStore,
} from '@acme/app-shell';
import { connect, serverAvailable, type DeephavenSession } from '@acme/dh-bindings';
import './styles.css';
import type { Backend } from './mock';
import { INSTRUMENTS } from './model/universe';
import { TERMINAL_SYMBOLS } from './model/symbols';
import { API, setUser } from './panels/common';
import { instancesOf, terminalChartPanel } from './panels/Chart';
import { universePanel } from './panels/Universe';
import { curvePanel } from './panels/Curve';
import { bondPanel } from './panels/Bond';
import { domPanel } from './panels/Dom';
import { heatmapPanel } from './panels/Heatmap';
import { booksPanel } from './panels/Books';
import { securityPanel } from './panels/Security';

const q = new URLSearchParams(location.search);
const USER = q.get('user') ?? 'trader';
const SERVER = q.get('server'); // 'mock' | 'dh' | null (the server if it is up, else the mock)
const WATCHLIST_CORE = ['US2Y', 'US5Y', 'US10Y', 'US30Y', 'T2S10S', 'T5S30S', 'ZT', 'ZF', 'ZN', 'TN', 'ZB', 'UB', 'SR3H7', 'SOFR10Y', 'SS10Y', 'BEI10Y', 'TLT', 'ES', 'GC', '6J', 'MOVE'];
setUser(USER);
const LAYOUT_KEY = 'terminal.shell.current';
if (q.has('reset')) localStorage.removeItem(LAYOUT_KEY);

const THEMES = [
  { key: 'black', label: 'Black', swatch: '#0b0b0c' },
  { key: 'midnight', label: 'Midnight', swatch: 'linear-gradient(#0c1530, #04070f)' },
];

function defaultLayout(api: DockviewApi, add: AddPanel) {
  const chart0 = { range: '1D', chartType: 'candle', scale: 'normal', compare: [], panel: '' };
  const uni = add('universe', {}, { title: 'Universe' })!;
  const chart = add('tchart', { ...chart0, symbol: 'ZN', res: '5m', studies: instancesOf(['EMA_20', 'EMA_50', 'VWAP', 'Volume']) }, { position: { referencePanel: uni, direction: 'right' } })!;
  add('tchart', { ...chart0, symbol: 'US10Y', res: '1m', range: '4h', studies: instancesOf(['BB_20_2', 'RSI_14']), __link: 'blue' }, { position: { referencePanel: chart, direction: 'within' } });
  add('tchart', { ...chart0, symbol: 'T2S10S', res: '1D', range: '1Y', chartType: 'line', studies: instancesOf(['SMA_20', 'ZSCORE_50']), __link: null }, { position: { referencePanel: chart, direction: 'within' } });
  add('tchart', { ...chart0, symbol: 'ZN-ZNH7', res: '15m', range: '5D', chartType: 'line', studies: instancesOf(['SMA_20']), __link: null }, { position: { referencePanel: chart, direction: 'within' } });
  const curve = add('curve', {}, { position: { referencePanel: chart, direction: 'right' } })!;
  const dom = add('dom', {}, { position: { referencePanel: curve, direction: 'below' } })!;
  add('bond', {}, { position: { referencePanel: dom, direction: 'within' } });
  const sec = add('security', {}, { position: { referencePanel: dom, direction: 'within' } })!;
  const books = add('books', { book: 'ALL' }, { position: { referencePanel: chart, direction: 'below' } })!;
  const t1 = add('table', { table: 'futures_contracts', linkColumn: 'Sym' }, { title: 'futures_contracts', position: { referencePanel: books, direction: 'within' } })!;
  add('table', { table: 'ust_cash', linkColumn: 'Cusip' }, { title: 'ust_cash', position: { referencePanel: t1, direction: 'within' } });
  add('table', { table: 'deliverable_basket', linkColumn: 'Cusip' }, { title: 'deliverable_basket', position: { referencePanel: t1, direction: 'within' } });
  add('table', { table: 'rv_monitor', linkColumn: 'Sym' }, { title: 'rv_monitor', position: { referencePanel: t1, direction: 'within' } });
  add('table', { table: 'econ_calendar', __link: null }, { title: 'econ_calendar', position: { referencePanel: t1, direction: 'within' } });
  add('table', { table: 'news', __link: null, sorts: [{ column: 'Timestamp', direction: 'desc' }] }, { title: 'news', position: { referencePanel: t1, direction: 'within' } });
  add('watchlist', { table: 'watchlist_core' }, { title: 'Watchlist', position: { referencePanel: uni, direction: 'below' } });
  // sizes: universe narrow, chart big, right column medium
  const w = api.width;
  const h = api.height;
  try {
    api.getPanel(uni)?.group.api.setSize({ width: Math.round(w * 0.24) });
    api.getPanel(curve)?.group.api.setSize({ width: Math.round(w * 0.27) });
    api.getPanel(chart)?.group.api.setSize({ height: Math.round(h * 0.5) });
    api.getPanel(curve)?.group.api.setSize({ height: Math.round(h * 0.45) });
    api.getPanel(uni)?.group.api.setSize({ height: Math.round(h * 0.62) });
  } catch {
    /* sizes are a nicety */
  }
  api.getPanel(chart)?.api.setActive();
  api.getPanel(books)?.api.setActive();
  api.getPanel(sec)?.api.setActive();
  api.getPanel(uni)?.api.setActive();
}

/** The same terminal and server as the other two kinds of front end: A (deephaven.ui: Deephaven's embedded-widget
 * page, proxied on this origin), B (our shell hosting server widgets: b.html). Only with the server: the mock has no A or B. */
function Versions({ server }: { server: boolean }) {
  if (!server) return null;
  return (
    <span className="tm-versions" title="Front-end groups on the same server: A deephaven.ui, B React shell + server widgets, C React + table data (this page)">
      <a href="./iframe/widget/?name=terminal_a" target="_blank" rel="noreferrer">A</a>
      <a href="./b.html" target="_blank" rel="noreferrer">B</a>
      <b>C</b>
    </span>
  );
}

/** The in-page mock's chip: its update cycle's cost. */
function MockStatus({ backend }: { backend: Backend }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick(n => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const e = backend.engine;
  return (
    <span
      className="acme-status"
      title={`The mock Deephaven server runs in this page: an update cycle every 250 ms.
${backend.server.objects.size} published objects, ${backend.nodes.entries.size} chart nodes, ${backend.market.inst.length} symbols`}
    >
      <b>MOCK</b> {e.cycleMs.toFixed(0)} ms · {new Date().toLocaleTimeString([], { hourCycle: 'h23' })} · {USER}
    </span>
  );
}

function App({ backend }: { backend: Backend | null }) {
  const { value: client, error } = useApiClient(API, `user:${USER}`);
  usePageStores(USER);
  const [kinds, setKinds] = useState<PanelKind[] | null>(null);
  const [store, setStore] = useState<WorkspaceStore | null>(null);
  useEffect(() => {
    if (!client && !error) return;
    const symbols = INSTRUMENTS.map(i => i.sym);
    setKinds([
      universePanel(),
      terminalChartPanel(),
      curvePanel(),
      bondPanel(),
      domPanel(),
      heatmapPanel(),
      booksPanel(),
      securityPanel(),
      watchlistPanel({ table: 'watchlist_core' }),
      tablePanel(),
      { ...chartPanel({ symbols: [...WATCHLIST_CORE, ...symbols.filter(s => !WATCHLIST_CORE.includes(s))], user: USER, api: API, timeZone: 'local' }), label: 'Chart (classic workspace)' },
      seriesPanel(),
      requestPanel({ api: API, user: USER }),
      graphPanel({ api: API, user: USER }),
      strategiesPanel({ api: API, user: USER }),
    ]);
    if (client) setStore(apiWorkspaceStore(client));
  }, [client, error, backend]);
  const dl = useCallback(defaultLayout, []);
  if (!kinds) return <div className="tm-boot">connecting to {backend ? 'the mock server' : 'Deephaven'}…</div>;
  return (
    <SymbolModelProvider model={TERMINAL_SYMBOLS}>
    <Shell
      kinds={kinds}
      store={store}
      defaultLayout={dl}
      title={
        <span className="tm-title">
          <b>RATES</b> TERMINAL <small>C · React + table data</small>
        </span>
      }
      extra={
        <>
          <Versions server={!backend} />
          <ThemeSwitch themes={THEMES} storageKey="terminal.theme" />
          {backend ? (
            <MockStatus backend={backend} />
          ) : (
            <ApiStatus api={API} user={USER} label="DEEPHAVEN" who={USER} title="The Deephaven server (server/): the market simulator, every table and chart_api in Python, through this page's origin (proxied)." />
          )}
        </>
      }
      autosaveKey={LAYOUT_KEY}
      popoutUrl="./popout.html"
      defaultArrangement="free"
    />
    </SymbolModelProvider>
  );
}

// this module owns the page's root and the mock server: when the dev server hot-swaps it, reload the page instead of
// mounting a second app (and a second market) on the same root
const w = window as { __terminalBooted?: boolean };
if (w.__terminalBooted) location.reload();
w.__terminalBooted = true;

const root = createRoot(document.getElementById('root')!);
const mount = (connectFn: () => Promise<DeephavenSession>, backend: Backend | null) =>
  root.render(
    <StrictMode>
      <DeephavenRoot connect={connectFn}>
        <App backend={backend} />
      </DeephavenRoot>
    </StrictMode>,
  );

(async () => {
  root.render(<div className="tm-boot">looking for the Deephaven server…</div>);
  if (SERVER !== 'mock' && (SERVER === 'dh' || (await serverAvailable()))) {
    document.documentElement.dataset.backend = 'dh';
    mount(() => connect({ serverUrl: location.origin }), null);
    return;
  }
  // the mock: build the market after a paint, so the page says what it is doing
  document.documentElement.dataset.backend = 'mock';
  root.render(<div className="tm-boot">no server: generating two years of market history in this page…</div>);
  await new Promise(r => setTimeout(r, 30));
  const { createBackend } = await import('./mock');
  const backend = createBackend();
  mount(backend.connect, backend);
})();
