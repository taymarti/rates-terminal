/**
 * The rates terminal, version B: our dockable shell (@acme/app-shell: tabs, splits, floating, pop-out, saved
 * workspaces), where every panel is a server-side deephaven.ui widget (server/rates_terminal/ui.py, published as
 * b_universe, b_chart, ...), drawn by Deephaven's own plugins (@acme/dh-widgets). Layout, menus and workspaces are
 * this page's; everything inside a panel is the server's.
 *
 * The widgets share no React state, so they link through the server: a row clicked in one sets the selection there
 * (dh_chart_ext.uikit.LinkHub), and every open B panel follows.
 */
import { useLayoutEffect, useMemo, useRef, type ReactNode } from 'react';
import type { DockviewApi } from 'dockview-react';
import '@acme/app-shell/style.css';
import '@acme/dh-widgets/style.css';
import { attachTvlExtensions, browserOwner, defaultExtensions } from '@acme/chart-ext';
import { Shell, apiWorkspaceStore, useApiClient, usePageStores, type AddPanel } from '@acme/app-shell';
import { ShellSession, serverWidgetPanel } from '@acme/dh-widgets/widgets';
import './styles.css';

const q = new URLSearchParams(location.search);
const USER = q.get('user') ?? browserOwner();
const DH_PORT = import.meta.env.VITE_DH_PORT || '10050';

const KINDS = [
  ['b_universe', 'Universe', 'Datasets: pick one, see its symbols live or its table; click a row to select it'],
  ['b_chart', 'Chart', "dh_chart_ext's workspace: toolbar, studies, layouts, drawings, alerts (Deephaven's TradingView plugin)"],
  ['b_curve', 'UST curve', 'The curve now, 1W and 1M ago, and SOFR swaps (plotly express), with the curve table'],
  ['b_books', 'Books & P&L', 'Each book; attribution, P&L path, positions and risk for the selected one'],
  ['b_security', 'Security', 'The selected Treasury (a future: its CTD): amount, SOMA, desk, idio, basis, auctions, RFQs'],
  ['b_dom', 'DOM / T&S', 'Order book and time and sales of the selected listed contract'],
  ['b_watchlist', 'Watchlist', 'The core watchlist; click to select'],
  ['b_data', 'Data', "The selected dataset's table"],
  ['b_news', 'News & calendar', 'Headlines, economic calendar, auctions, CFTC positioning'],
].map(([name, title, description]) => serverWidgetPanel({ name, title, description, dashboardId: 'terminal-b' }));

function defaultLayout(api: DockviewApi, add: AddPanel) {
  const uni = add('b_universe')!;
  const chart = add('b_chart', {}, { position: { referencePanel: uni, direction: 'right' } })!;
  add('b_data', {}, { position: { referencePanel: chart, direction: 'within' } });
  const curve = add('b_curve', {}, { position: { referencePanel: chart, direction: 'right' } })!;
  const sec = add('b_security', {}, { position: { referencePanel: curve, direction: 'below' } })!;
  add('b_dom', {}, { position: { referencePanel: sec, direction: 'within' } });
  const books = add('b_books', {}, { position: { referencePanel: chart, direction: 'below' } })!;
  add('b_news', {}, { position: { referencePanel: books, direction: 'within' } });
  add('b_watchlist', {}, { position: { referencePanel: uni, direction: 'below' } });
  const w = api.width;
  const h = api.height;
  try {
    api.getPanel(uni)?.group.api.setSize({ width: Math.round(w * 0.2) });
    api.getPanel(curve)?.group.api.setSize({ width: Math.round(w * 0.28) });
    api.getPanel(chart)?.group.api.setSize({ height: Math.round(h * 0.58) });
    api.getPanel(curve)?.group.api.setSize({ height: Math.round(h * 0.42) });
  } catch {
    /* sizes are a nicety */
  }
  api.getPanel(chart)?.api.setActive();
  api.getPanel(books)?.api.setActive();
  api.getPanel(sec)?.api.setActive();
}

/** Links to the other two versions on the same server. */
function Versions() {
  return (
    <span className="tm-versions" title="The same terminal and server, three kinds of front end">
      <a href={`http://${location.hostname}:${DH_PORT}/iframe/widget/?name=terminal_a`} target="_blank" rel="noreferrer">A</a>
      <b>B</b>
      <a href="./" target="_blank" rel="noreferrer">C</a>
    </span>
  );
}

function Terminal(): ReactNode {
  const client = useApiClient('chart_api', `user:${USER}`).value;
  const store = useMemo(() => (client ? apiWorkspaceStore(client) : null), [client]);
  // our chart extensions (drawings, price lines, sessions, sync, alerts) on every TradingView chart in the page
  const root = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => attachTvlExtensions(root.current!, defaultExtensions(['sync', 'lines', 'sessions', 'draw', 'alerts'])), []);
  usePageStores(USER, { annotations: 'chart_annotations', alerts: 'chart_alerts' });
  return (
    <div ref={root} className="terminal-b">
      <Shell
        kinds={KINDS}
        store={store}
        defaultLayout={defaultLayout}
        title={
          <span className="tm-title">
            <b>RATES</b> TERMINAL <small>B · React shell + deephaven.ui widgets</small>
          </span>
        }
        extra={<Versions />}
        autosaveKey="terminal-b.shell.current"
      />
    </div>
  );
}

export default function TerminalB() {
  return (
    <ShellSession>
      <Terminal />
    </ShellSession>
  );
}
