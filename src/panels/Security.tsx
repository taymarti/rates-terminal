/**
 * Security: everything about the linked security on one pane. A CUSIP as is, a benchmark (US10Y) as its on-the-run
 * issue; a future as itself (quote, contract, CTD, deliverable basket), with a toggle to its underlier's screen (the CTD):
 *   amount outstanding, SOMA holdings and free float; the desk's position by book, its DV01 and P&L; price, yield,
 *   idio (rich/cheap to the curve, z-score); deliverability and basis; auction results; client RFQs and hit ratio;
 *   street volume (and the future's exchange volume).
 * Each series opens as a chart (price, yield, idio, gross basis against its future, RFQ flow, the future's volume).
 * From chart_api's `security` method (server/desk.ts), refreshed every two seconds.
 */
import { useLink } from '@acme/chart-react';
import { upDown } from '@acme/chart-ext';
import { definePanel, instancesOf, useFilteredRows, useRows, type PanelProps } from '@acme/app-shell';
import { otrFor } from '../model/universe';
import { useInstrument, usePolled } from './common';

interface SecurityParams {
  /** for a derivative: show its underlier's security screen (a Treasury future's CTD) instead of the derivative */
  underlier?: boolean;
}

interface SecurityReply {
  cusip: string;
  security: string;
  type: string;
  term: string;
  coupon: number;
  maturity: string;
  issue: string;
  status: string;
  via: string | null;
  price: string;
  yield: number;
  chg_bp: number | null;
  dv01_per_mm: number;
  mod_duration: number;
  accrued: number;
  amount: { outstanding_bn: number; soma_bn: number; soma_pct: number; free_float_bn: number };
  desk: { position_mm: number; dv01: number; pnl_today: number; books: Array<{ book: string; qty: number; sod_qty: number; dv01: number; pnl_today: number; trades: number }> };
  idio: { bp: number; z20: number; chg_1d_bp: number | null; rich_cheap: string };
  ctd: Array<{ contract: string; cf: number; rank: number; ctd: boolean; net_basis_32: number; gross_basis_32: number; implied_repo: number }>;
  hedge_future: string | null;
  auctions: Array<Record<string, unknown>>;
  rfq: { today: number; notional_mm: number; done_mm: number; client_buy_mm: number; client_sell_mm: number; hit_ratio_pct: number | null; recent: Array<Record<string, unknown>> };
  market: { street_volume_bn: number; listed: { sym: string; volume: number } | null };
  charts: { price: string; yield: string; idio: string };
  carry_per_mm_day: number | null;
}

const usd = (v: number) => `${v < 0 ? '-' : ''}$${Math.abs(v) >= 1e6 ? `${(Math.abs(v) / 1e6).toFixed(2)}M` : Math.abs(v) >= 1e3 ? `${(Math.abs(v) / 1e3).toFixed(1)}k` : Math.round(Math.abs(v))}`;
const ChartIcon = () => (
  <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M2 13l4-5 3 3 5-7" />
  </svg>
);

const FUTURE_COLUMNS = ['Sym', 'Root', 'Contract', 'Last', 'Price', 'Chg', 'Volume', 'OpenInterest', 'CTD', 'CF', 'DV01', 'ImpliedRepo', 'NetBasis32', 'FirstNotice', 'DaysToFND', 'Roll'];
const BASKET_COLUMNS = ['Contract', 'Cusip', 'Security', 'CF', 'Price', 'Yield', 'GrossBasis32', 'Carry32', 'NetBasis32', 'ImpliedRepo', 'Rank', 'CTD'];
type ChartFn = (symbol: string, title: string, opts?: { res?: string; range?: string; type?: string; studies?: string[] }) => void;

/** A derivative on its own screen: the linked future's quote, contract, CTD and deliverable basket; charts on click. */
function FutureView({ sym, chart, onUnderlier }: { sym: string; chart: ChartFn; onUnderlier: (() => void) | null }) {
  const inst = useInstrument(sym);
  const f = useFilteredRows('futures_contracts', { Sym: sym }, FUTURE_COLUMNS)?.[0];
  const basket = useFilteredRows('deliverable_basket', { Contract: sym }, BASKET_COLUMNS) ?? [];
  const q = (useRows('quotes', ['Sym', 'Bid', 'Ask', 'High', 'Low']).value ?? []).find(r => r.Sym === sym);
  const ordered = [...basket].sort((a, b) => Number(a.Rank) - Number(b.Rank));
  const kv = (k: string, v: string) => (
    <div className="tm-kv">
      <span>{k}</span>
      <b>{v}</b>
    </div>
  );
  return (
    <div className="tm-sec">
      <div className="tm-sec-head">
        <div>
          <div className="tm-sec-name">{inst?.name ?? sym}</div>
          <div className="tm-muted">
            {sym} · {inst?.kind ?? 'future'}
            {f?.Root ? ` · ${String(f.Root)}` : ''}
            {f?.DaysToFND != null ? ` · first notice in ${String(f.DaysToFND)} days` : ''}
          </div>
        </div>
        <div className="tm-sec-px">
          <div className="tm-sec-price">{f ? String(f.Last) : ''}</div>
          <div>{f ? String(f.Chg) : ''}</div>
        </div>
      </div>
      <div className="tm-sec-charts">
        <button onClick={() => chart(sym, `${sym} price`, { res: '5m', range: '1D', studies: ['VWAP', 'Volume'] })}>
          <ChartIcon /> Price
        </button>
        {f?.CTD ? (
          <button
            title={`Gross basis in points: CTD price - CF x ${sym}`}
            onClick={() => chart(`${String(f.CTD)}-${Number(f.CF)}*${sym}`, `${String(f.CTD)} basis vs ${sym}`, { type: 'line', studies: ['SMA_20'] })}
          >
            <ChartIcon /> CTD basis
          </button>
        ) : null}
        {onUnderlier && (
          <button className="tm-sec-switch" onClick={onUnderlier} title="Show the underlying security's screen instead">
            Underlier: {f?.CTD ? String(f.CTD) : 'CTD'} {'\u2192'}
          </button>
        )}
      </div>
      <div className="tm-sec-grid">
        <section>
          <h4>Contract</h4>
          {q && kv('Bid / ask', `${String(q.Bid ?? '')} / ${String(q.Ask ?? '')}`)}
          {q && kv('Day range', `${String(q.Low ?? '')} - ${String(q.High ?? '')}`)}
          {f && kv('Volume / open interest', `${Number(f.Volume).toLocaleString()} / ${Number(f.OpenInterest).toLocaleString()}`)}
          {f && kv('DV01 per contract', `$${Number(f.DV01).toFixed(2)}`)}
          {f?.FirstNotice != null && kv('First notice', new Date(f.FirstNotice as Date).toLocaleDateString())}
          {f?.Roll ? kv('Roll', String(f.Roll)) : null}
          {inst?.description && <p className="tm-muted">{inst.description}</p>}
        </section>
        <section>
          <h4>Cheapest to deliver</h4>
          {f?.CTD ? (
            <>
              {kv('CTD / conversion factor', `${String(f.CTD)} / ${Number(f.CF).toFixed(4)}`)}
              {kv('Net basis (32nds) / implied repo', `${Number(f.NetBasis32).toFixed(1)} / ${Number(f.ImpliedRepo).toFixed(2)}%`)}
            </>
          ) : (
            <p className="tm-muted">No deliverable basket: cash-settled, or not a Treasury future.</p>
          )}
        </section>
      </div>
      {ordered.length > 0 && (
        <section className="tm-sec-auctions">
          <h4>Deliverable basket</h4>
          <table className="tm-table">
            <thead>
              <tr>
                <th>Rank</th><th>CUSIP</th><th>Security</th><th>CF</th><th>Price</th><th>Yield</th><th>Gross</th><th>Carry</th><th>Net</th><th>IRR</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map(b => (
                <tr key={String(b.Cusip)} className={b.CTD ? 'on' : ''}>
                  <td className="tm-num">{String(b.Rank)}{b.CTD ? ' (CTD)' : ''}</td>
                  <td>{String(b.Cusip)}</td>
                  <td>{String(b.Security)}</td>
                  <td className="tm-num">{Number(b.CF).toFixed(4)}</td>
                  <td className="tm-num">{String(b.Price)}</td>
                  <td className="tm-num">{Number(b.Yield).toFixed(3)}</td>
                  <td className="tm-num">{Number(b.GrossBasis32).toFixed(1)}</td>
                  <td className="tm-num">{Number(b.Carry32).toFixed(1)}</td>
                  <td className="tm-num">{Number(b.NetBasis32).toFixed(1)}</td>
                  <td className="tm-num">{Number(b.ImpliedRepo).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

/** Derivatives get a screen of their own; a Treasury future also has an underlier with a security screen (its CTD). */
const isDerivative = (kind: string | undefined) => kind === 'future' || kind === 'stir';

function Security({ params, setParams, linkGroup, openPanel }: PanelProps<SecurityParams>) {
  const [sym] = useLink<string>('symbol', { group: linkGroup });
  const inst = useInstrument(sym ?? '');
  const derivative = !!sym && isDerivative(inst?.kind);
  const treasuryFuture = derivative && inst?.dataset === 'ust_futures';
  const showFuture = derivative && !(treasuryFuture && params.underlier);
  const target = sym && !showFuture ? otrFor(sym)?.cusip ?? sym : null;
  const { value: s, error } = usePolled<SecurityReply>('security', target ? { sym: target } : null, 2000);

  const chart = (symbol: string, title: string, opts: { res?: string; range?: string; type?: string; studies?: string[] } = {}) =>
    openPanel(
      'tchart',
      { symbol, res: opts.res ?? '15m', range: opts.range ?? '5D', chartType: opts.type ?? 'candle', scale: 'normal', compare: [], panel: '', studies: instancesOf(opts.studies ?? ['EMA_20']), __link: null },
      title,
      { direction: 'right' },
    );

  if (showFuture) return <FutureView sym={sym} chart={chart} onUnderlier={treasuryFuture ? () => setParams({ underlier: true }) : null} />;
  if (!target)
    return <div className="tm-empty-help">Link this panel to a CUSIP, a benchmark yield (US10Y) or a Treasury future: click a position, a bond in ust_cash, or a symbol in the universe.</div>;
  if (error && (!s || error.includes('not a Treasury')))
    return (
      <div className="tm-empty-help">
        <p>
          <b>{sym}</b> is not a Treasury cash bond or future, so there is no security view for it.
        </p>
        <p>Click a CUSIP in ust_cash or the positions, a benchmark yield (US2Y … US30Y) or a Treasury future (ZN, ZB …).</p>
      </div>
    );
  if (!s) return <div className="acme-empty">loading {target}…</div>;
  const ctdRow = s.ctd.find(c => c.ctd) ?? s.ctd[0];
  const buy = s.rfq.client_buy_mm;
  const sell = s.rfq.client_sell_mm;
  return (
    <div className="tm-sec">
      <div className="tm-sec-head">
        <div>
          <div className="tm-sec-name">{s.security}</div>
          <div className="tm-muted">
            {s.cusip} · {s.term} {s.type} · {s.status === 'OTR' ? 'on-the-run' : s.status} · issued {s.issue}
            {s.via ? ` · ${s.via}` : ''}
          </div>
          {treasuryFuture && (
            <button className="tm-sec-switch" onClick={() => setParams({ underlier: false })} title="Back to the future's own screen">
              {'\u2190'} {sym} (the future)
            </button>
          )}
        </div>
        <div className="tm-sec-px">
          <div className="tm-sec-price">{s.price}</div>
          <div>
            {s.yield.toFixed(3)}% <span className={upDown(s.chg_bp)}>{s.chg_bp != null ? `${s.chg_bp > 0 ? '+' : ''}${s.chg_bp.toFixed(1)}bp` : ''}</span>
          </div>
        </div>
      </div>
      <div className="tm-sec-charts">
        <button onClick={() => chart(s.charts.price, `${s.cusip} price`)}>
          <ChartIcon /> Price
        </button>
        <button onClick={() => chart(s.charts.yield, `${s.cusip} yield`, { studies: ['EMA_20', 'BB_20_2'] })}>
          <ChartIcon /> Yield
        </button>
        <button onClick={() => chart(s.charts.idio, `${s.cusip} idio`, { res: '1h', range: '1M', type: 'line', studies: ['SMA_20', 'ZSCORE_50'] })}>
          <ChartIcon /> Idio
        </button>
        {ctdRow && (
          <button title={`Gross basis in points: price - CF x ${ctdRow.contract}`} onClick={() => chart(`${s.cusip}-${ctdRow.cf}*${ctdRow.contract}`, `${s.cusip} basis vs ${ctdRow.contract}`, { type: 'line', studies: ['SMA_20'] })}>
            <ChartIcon /> Basis vs {ctdRow.contract}
          </button>
        )}
        <button
          onClick={() => openPanel('series', { table: 'rfq_flow', columns: ['NotionalMM', 'DoneMM'], where: { Sym: s.cusip }, panes: false, __link: null }, `${s.cusip} RFQ flow`, { direction: 'right' })}
        >
          <ChartIcon /> RFQ flow
        </button>
        {s.hedge_future && (
          <button onClick={() => chart(s.hedge_future!, `${s.hedge_future} volume`, { res: '5m', range: '1D', studies: ['VWAP', 'Volume'] })}>
            <ChartIcon /> {s.hedge_future} volume
          </button>
        )}
      </div>
      <div className="tm-sec-grid">
        <section>
          <h4>Amount</h4>
          <div className="tm-kv"><span>Outstanding</span><b>${s.amount.outstanding_bn.toFixed(1)}bn</b></div>
          <div className="tm-kv"><span>SOMA (Fed) holdings</span><b>${Number(s.amount.soma_bn).toFixed(1)}bn · {Number(s.amount.soma_pct).toFixed(1)}%</b></div>
          <div className="tm-kv"><span>Free float</span><b>${Number(s.amount.free_float_bn).toFixed(1)}bn</b></div>
          <div className="tm-kv"><span>Street volume today (est.)</span><b>${s.market.street_volume_bn.toFixed(1)}bn</b></div>
          {s.market.listed && <div className="tm-kv"><span>{s.market.listed.sym} volume today</span><b>{s.market.listed.volume.toLocaleString()}</b></div>}
          <div className="tm-kv"><span>Coupon · maturity</span><b>{s.coupon}% · {s.maturity}</b></div>
          <div className="tm-kv"><span>Mod. duration · DV01/mm</span><b>{s.mod_duration.toFixed(2)} · ${s.dv01_per_mm.toFixed(0)}</b></div>
        </section>
        <section>
          <h4>Desk position</h4>
          <div className="tm-kv"><span>Net</span><b className={upDown(s.desk.position_mm)}>{s.desk.position_mm > 0 ? '+' : ''}{s.desk.position_mm.toLocaleString()}mm</b></div>
          <div className="tm-kv"><span>DV01</span><b>{usd(s.desk.dv01)}/bp</b></div>
          <div className="tm-kv"><span>P&amp;L today</span><b className={upDown(s.desk.pnl_today)}>{usd(s.desk.pnl_today)}</b></div>
          {s.carry_per_mm_day != null && <div className="tm-kv"><span>Carry per $mm per day</span><b className={upDown(s.carry_per_mm_day)}>{usd(s.carry_per_mm_day)}</b></div>}
          {s.desk.books.length > 0 && (
            <table className="tm-table">
              <thead>
                <tr><th>Book</th><th>Qty</th><th>SOD</th><th>DV01</th><th>P&amp;L</th><th>Trades</th></tr>
              </thead>
              <tbody>
                {s.desk.books.map(b => (
                  <tr key={b.book}>
                    <td>{b.book}</td>
                    <td className="tm-num">{b.qty}</td>
                    <td className="tm-num tm-muted">{b.sod_qty}</td>
                    <td className="tm-num">{usd(b.dv01)}</td>
                    <td className={`tm-num ${upDown(b.pnl_today)}`}>{usd(b.pnl_today)}</td>
                    <td className="tm-num">{b.trades}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {s.desk.books.length === 0 && <p className="tm-muted">The desk has no position.</p>}
        </section>
        <section>
          <h4>Idio</h4>
          <div className="tm-sec-big">
            <b className={s.idio.bp > 0 ? 'up' : s.idio.bp < 0 ? 'down' : ''}>{s.idio.bp > 0 ? '+' : ''}{s.idio.bp.toFixed(2)}bp</b>
            <span>{s.idio.rich_cheap} to the curve</span>
          </div>
          <div className="tm-kv"><span>20-day z-score</span><b>{s.idio.z20.toFixed(2)}</b></div>
          <div className="tm-kv"><span>1-day change</span><b>{s.idio.chg_1d_bp != null ? `${s.idio.chg_1d_bp > 0 ? '+' : ''}${s.idio.chg_1d_bp.toFixed(2)}bp` : ''}</b></div>
          <h4>Deliverable into</h4>
          {s.ctd.length ? (
            <table className="tm-table">
              <thead>
                <tr><th>Contract</th><th>CF</th><th>Rank</th><th>Net (32nds)</th><th>IRR</th></tr>
              </thead>
              <tbody>
                {s.ctd.map(c => (
                  <tr key={c.contract} className={c.ctd ? 'on' : ''}>
                    <td>{c.contract}{c.ctd ? ' (CTD)' : ''}</td>
                    <td className="tm-num">{c.cf.toFixed(4)}</td>
                    <td className="tm-num">{c.rank}</td>
                    <td className="tm-num">{c.net_basis_32.toFixed(1)}</td>
                    <td className="tm-num">{c.implied_repo.toFixed(2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="tm-muted">Not deliverable into any contract.</p>
          )}
        </section>
        <section>
          <h4>Client RFQs today</h4>
          <div className="tm-kv"><span>Requests · notional</span><b>{s.rfq.today} · ${s.rfq.notional_mm.toLocaleString()}mm</b></div>
          <div className="tm-kv"><span>Done · hit ratio</span><b>${s.rfq.done_mm.toLocaleString()}mm · {s.rfq.hit_ratio_pct ?? 0}%</b></div>
          <div className="tm-kv"><span>Clients bought / sold</span><b>${buy.toLocaleString()}mm / ${sell.toLocaleString()}mm</b></div>
          <span className="tm-split" title={`clients bought $${buy}mm, sold $${sell}mm`}>
            <i className="buy" style={{ width: `${(100 * buy) / Math.max(1, buy + sell)}%` }} />
            <i className="sell" style={{ width: `${(100 * sell) / Math.max(1, buy + sell)}%` }} />
          </span>
          {s.rfq.recent.length > 0 && (
            <table className="tm-table">
              <thead>
                <tr><th>Time</th><th>Client</th><th>Side</th><th>Size</th><th>Status</th></tr>
              </thead>
              <tbody>
                {s.rfq.recent.slice(0, 8).map(x => (
                  <tr key={String(x.Id)}>
                    <td>{new Date(String(x.Time)).toLocaleTimeString([], { hourCycle: 'h23' })}</td>
                    <td>{String(x.Client)}</td>
                    <td className={x.ClientSide === 'Buy' ? 'up' : 'down'}>{String(x.ClientSide)}</td>
                    <td className="tm-num">{String(x.SizeMM)}mm</td>
                    <td className={x.Status === 'Done' ? 'up' : 'tm-muted'}>{String(x.Status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
      {s.auctions.length > 0 && (
        <section className="tm-sec-auctions">
          <h4>Auctions</h4>
          <table className="tm-table">
            <thead>
              <tr><th>Date</th><th>Kind</th><th>Size</th><th>High yield</th><th>WI</th><th>Tail</th><th>Bid/cover</th><th>Indirect</th><th>Direct</th><th>Dealer</th></tr>
            </thead>
            <tbody>
              {s.auctions.map((a, i) => (
                <tr key={i}>
                  <td>{String(a.Date)}</td>
                  <td>{String(a.Kind)}</td>
                  <td className="tm-num">${String(a.SizeBn)}bn</td>
                  <td className="tm-num">{a.HighYield != null ? Number(a.HighYield).toFixed(3) : ''}</td>
                  <td className="tm-num">{a.WIYield != null ? Number(a.WIYield).toFixed(3) : ''}</td>
                  <td className={`tm-num ${Number(a.TailBp) > 0 ? 'down' : 'up'}`}>{Number(a.TailBp) > 0 ? '+' : ''}{Number(a.TailBp).toFixed(1)}bp</td>
                  <td className="tm-num">{Number(a.BidToCover).toFixed(2)}</td>
                  <td className="tm-num">{Number(a.IndirectPct).toFixed(1)}%</td>
                  <td className="tm-num">{Number(a.DirectPct).toFixed(1)}%</td>
                  <td className="tm-num">{Number(a.DealerPct).toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

export function securityPanel() {
  return definePanel<SecurityParams>({
    kind: 'security',
    label: 'Security',
    title: 'Security',
    description: 'One security: a Treasury (amount, SOMA, position, idio, CTD, auctions, RFQs) or a future (contract, CTD, basket), its underlier a click away',
    defaultParams: { underlier: false },
    component: Security,
  });
}
