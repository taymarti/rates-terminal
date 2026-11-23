/**
 * Books & P&L, one pane: pick a book (or the whole desk) and see its P&L today split by source, its risk by
 * key-rate bucket and curve factor, its limit use and VaR, and its positions. A position's row sends its symbol to
 * the linked panels (the Security panel, a chart); buttons open the P&L path, the RFQ blotter and the executions.
 * A P&L source or risk line that stands for a series (a curve factor, a bucket, the book's idio or basis) charts it on click.
 * Hypothetical data from the mock desk (server/desk.ts).
 */
import { useMemo } from 'react';
import { useLink } from '@acme/chart-react';
import { DataGrid, definePanel, instancesOf, useOwnTable, useRows, type PanelProps } from '@acme/app-shell';

interface BooksParams {
  book: string;
}

const COMPONENTS: Array<[string, string]> = [
  ['Carry', 'Carry'], ['Roll', 'Roll-down'], ['Level', 'Curve: level'], ['Slope', 'Curve: slope'], ['Curvature', 'Curve: curvature'],
  ['Policy', 'Curve: front end'], ['Idio', 'Idio (rich/cheap)'], ['Basis', 'Futures basis'], ['NewTrades', 'New trades'], ['Fees', 'Fees'], ['Residual', 'Convexity, other'],
];
const BUCKETS = ['Bills', '2Y', '5Y', '10Y', '30Y'];
const FACTORS: Array<[string, string]> = [['Level', 'Level'], ['Slope', 'Slope'], ['Curvature', 'Curvature'], ['Policy', 'Front end']];
/**
 * P&L sources and risk lines that stand for a series in the universe: a click charts it. Curve factors chart their
 * benchmark (level: the 10-year, slope: 2s10s, curvature: 2s5s10s, front end: the 2-year); buckets their tenor's yield;
 * idio and basis the book's own biggest exposure (its bond's idio series, its future), found from the positions.
 */
const LINKED: Record<string, string> = {
  Level: 'US10Y', Slope: 'T2S10S', Curvature: 'T2S5S10S', Policy: 'US2Y', Carry: 'GCREPO',
  Bills: 'US3M', '2Y': 'US2Y', '5Y': 'US5Y', '10Y': 'US10Y', '30Y': 'US30Y',
};
type Position = Record<string, unknown>;
/** the bond with the most idio risk (|DV01 x idio|) and the future with the most DV01, in the book */
function biggest(positions: Position[], book: string) {
  const mine = positions.filter(r => book === 'ALL' || r.Book === book);
  const by = (rows: Position[], f: (r: Position) => number) => rows.reduce<Position | null>((m, r) => (!m || f(r) > f(m) ? r : m), null);
  const bond = by(mine.filter(r => r.Kind === 'Cash' || r.Kind === 'Bill' || r.Kind === 'TIPS'), r => Math.abs(Number(r.DV01) * Number(r.IdioBp)));
  const future = by(mine.filter(r => r.Kind === 'Future'), r => Math.abs(Number(r.DV01)));
  return { bond: bond ? String(bond.Sym) : null, future: future ? String(future.Sym) : null };
}

const POSITION_COLUMNS = ['Book', 'Sym', 'Security', 'Kind', 'Qty', 'Unit', 'PriceText', 'Yield', 'DV01', 'IdioBp', 'CarryDay', 'PnlToday', 'PnlNew', 'Trades'];

const money = (v: unknown, short = true) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  const a = Math.abs(n);
  const s = !short || a < 10_000 ? Math.round(a).toLocaleString() : a < 1e6 ? `${(a / 1e3).toFixed(1)}k` : `${(a / 1e6).toFixed(2)}M`;
  return `${n < 0 ? '-' : n > 0 ? '+' : ''}$${s}`;
};
const plain = (v: unknown) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  const a = Math.abs(n);
  return `${n < 0 ? '-' : ''}$${a < 1e6 ? `${(a / 1e3).toFixed(1)}k` : `${(a / 1e6).toFixed(2)}M`}`;
};
const sign = (v: unknown) => (Number(v) > 0 ? 'up' : Number(v) < 0 ? 'down' : '');

/** horizontal bars from a centre line, scaled to the largest */
function Bars({ rows, fmt = money }: { rows: Array<{ label: string; value: number; title?: string; onChart?: () => void }>; fmt?: (v: number) => string }) {
  const max = Math.max(1, ...rows.map(r => Math.abs(r.value)));
  return (
    <div className="tm-bars">
      {rows.map(r => (
        <div
          key={r.label}
          className={`tm-bar-row${r.onChart ? ' tm-bar-link' : ''}`}
          title={r.onChart ? `${r.title ? r.title + '. ' : ''}Click to chart it` : r.title}
          onClick={r.onChart}
        >
          <span className="tm-bar-label">{r.label}</span>
          <span className="tm-bar-track">
            <i className={r.value >= 0 ? 'pos' : 'neg'} style={{ width: `${(50 * Math.abs(r.value)) / max}%`, [r.value >= 0 ? 'left' : 'right']: '50%' }} />
          </span>
          <span className={`tm-bar-value ${sign(r.value)}`}>{fmt(r.value)}</span>
        </div>
      ))}
    </div>
  );
}

function Books({ params, setParams, linkGroup, openPanel }: PanelProps<BooksParams>) {
  const [sym, setSym] = useLink<string>('symbol', { group: linkGroup });
  const books = useRows('books', ['Book', 'Name', 'Strategy', 'Trader', 'Positions', 'GrossMM', 'DV01', 'DV01Limit', 'LimitUsePct', 'VaR99', 'PnlToday', 'TradesToday']).value ?? [];
  const pnl = useRows('pnl_attribution', ['Book', 'Total', ...COMPONENTS.map(c => c[0])]).value ?? [];
  const risk = useRows('book_risk', ['Book', 'DV01', ...BUCKETS.map(b => `KRD_${b}`), 'Level', 'Slope', 'Curvature', 'Policy', 'VaR99', 'IdioRisk']).value ?? [];
  const { value: posTable } = useOwnTable('positions');
  const positions = useRows('positions', ['Book', 'Sym', 'Kind', 'DV01', 'IdioBp']).value ?? [];
  const sel = params.book || 'ALL';
  const b = books.find(r => r.Book === sel);
  const p = pnl.find(r => r.Book === sel);
  const r = risk.find(x => x.Book === sel);
  const ordered = useMemo(() => [...books].sort((x, y) => (x.Book === 'ALL' ? -1 : y.Book === 'ALL' ? 1 : 0)), [books]);
  if (!books.length) return <div className="acme-empty">loading the books…</div>;
  const use = Number(b?.LimitUsePct ?? 0);
  const top = biggest(positions, sel);
  // a chart of what a line stands for, in a new tab next to this panel (unlinked: it keeps its symbol)
  const chartOf = (symbol: string, title: string, studies: string[] = ['SMA_20']) =>
    openPanel(
      'tchart',
      { symbol, res: '15m', range: '5D', chartType: 'line', scale: 'normal', compare: [], panel: '', studies: instancesOf(studies), __link: null },
      title,
      { direction: 'right' },
    );
  const linkOf = (key: string): (() => void) | undefined => {
    if (LINKED[key]) return () => chartOf(LINKED[key], `${key}: ${LINKED[key]}`);
    if (key === 'Idio' && top.bond) return () => chartOf(`${top.bond}.IDIO`, `Idio: ${top.bond}`, ['SMA_20', 'ZSCORE_50']);
    if (key === 'Basis' && top.future) return () => chartOf(top.future!, `Basis: ${top.future}`, ['VWAP']);
    return undefined;
  };
  return (
    <div className="tm-books">
      <div className="tm-books-chips">
        {ordered.map(x => (
          <button key={String(x.Book)} className={x.Book === sel ? 'on' : ''} onClick={() => setParams({ book: String(x.Book) })} title={`${x.Name}: ${x.Strategy}${x.Trader ? ` (${x.Trader})` : ''}`}>
            <b>{x.Book === 'ALL' ? 'Desk' : String(x.Book)}</b>
            <span className={sign(x.PnlToday)}>{money(x.PnlToday)}</span>
          </button>
        ))}
      </div>
      <div className="tm-books-kpis">
        <div>
          <small>P&amp;L today</small>
          <b className={sign(b?.PnlToday)}>{money(b?.PnlToday, false)}</b>
          <em>since 07:00 ET</em>
        </div>
        <div>
          <small>DV01</small>
          <b>{plain(b?.DV01)}</b>
          <em>per bp (long: gains as yields fall)</em>
        </div>
        <div>
          <small>VaR 99% 1-day</small>
          <b>{plain(b?.VaR99)}</b>
          <em>idio {plain(r?.IdioRisk)}</em>
        </div>
        <div>
          <small>DV01 limit use</small>
          <b className={use > 80 ? 'down' : ''}>{use}%</b>
          <span className="tm-meter">
            <i style={{ width: `${Math.min(100, use)}%`, background: use > 80 ? '#ef5350' : use > 50 ? '#ff9800' : '#26a69a' }} />
          </span>
        </div>
        <div>
          <small>Positions · trades</small>
          <b>
            {String(b?.Positions ?? '')} · {String(b?.TradesToday ?? '')}
          </b>
          <em>gross ${Number(b?.GrossMM ?? 0).toLocaleString()}mm</em>
        </div>
        <div className="tm-books-actions">
          <button
            onClick={() =>
              openPanel('series', { table: 'pnl_timeseries', split: ['Book'], value: 'Pnl', where: sel === 'ALL' ? {} : { Book: sel }, panes: false, __link: null }, `P&L ${sel === 'ALL' ? 'by book' : sel}`, { direction: 'right' })
            }
          >
            P&amp;L chart
          </button>
          <button onClick={() => openPanel('table', { table: 'rfq_blotter', linkColumn: 'Sym', sorts: [{ column: 'Time', direction: 'desc' }], filters: sel === 'ALL' ? {} : { Book: `=${sel}` } }, 'rfq_blotter', { direction: 'right' })}>
            RFQ blotter
          </button>
          <button onClick={() => openPanel('table', { table: 'desk_trades', linkColumn: 'Sym', sorts: [{ column: 'Time', direction: 'desc' }], filters: sel === 'ALL' ? {} : { Book: `=${sel}` } }, 'desk_trades', { direction: 'right' })}>
            Executions
          </button>
        </div>
      </div>
      <div className="tm-books-mid">
        <section>
          <h4>
            P&amp;L attribution <small>{b?.Name ? `${b.Name}: ${b.Strategy}` : ''}</small>
          </h4>
          {p && <Bars rows={COMPONENTS.map(([k, label]) => ({ label, value: Number(p[k] ?? 0), onChart: linkOf(k) }))} />}
          {p && (
            <div className="tm-bar-total">
              <span>Total</span>
              <b className={sign(p.Total)}>{money(p.Total, false)}</b>
            </div>
          )}
        </section>
        <section>
          <h4>
            DV01 by bucket <small>$ per bp</small>
          </h4>
          {r && <Bars rows={BUCKETS.map(k => ({ label: k, value: Number(r[`KRD_${k}`] ?? 0), onChart: linkOf(k) }))} fmt={plain} />}
          <h4>
            Curve-factor exposure <small>$ per bp of each factor</small>
          </h4>
          {r && <Bars rows={FACTORS.map(([k, label]) => ({ label, value: Number(r[k] ?? 0), title: 'DV01 weighted by each position’s loading on the factor', onChart: linkOf(k) }))} fmt={plain} />}
        </section>
      </div>
      <div className="tm-books-grid">
        {posTable && (
          <DataGrid
            key={sel}
            table={posTable}
            columns={POSITION_COLUMNS}
            initial={{ filters: sel === 'ALL' ? {} : { Book: `=${sel}` }, sorts: [{ column: 'DV01', direction: 'desc', abs: true }] }}
            onRowClick={row => setSym(String(row.Sym))}
            highlight={row => row.Sym === sym}
          />
        )}
      </div>
    </div>
  );
}

export function booksPanel() {
  return definePanel<BooksParams>({
    kind: 'books',
    label: 'Books & P&L',
    title: 'Books & P&L',
    description: 'Positions, risk and P&L attribution by book (hypothetical desk)',
    defaultParams: { book: 'ALL' },
    component: Books,
  });
}
