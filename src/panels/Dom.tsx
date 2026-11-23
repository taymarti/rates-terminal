/**
 * Depth of market for the linked symbol: a price ladder (bid size, price, ask size as bars, today's volume at
 * each price split by aggressor), the last trade marked, book imbalance, and the time-and-sales tape. A table and
 * a bar chart in one, from the raw `book`, `volume_profile` and `trades` tables filtered to the symbol on the
 * server. Prices in the instrument's notation (32nds for Treasury futures).
 */
import { useMemo } from 'react';
import { useLink } from '@acme/chart-react';
import { formatPrice } from '@acme/chart-ext';
import { definePanel, useFilteredRows, type PanelProps } from '@acme/app-shell';
import { useInstrument } from './common';

const TRADED = new Set(['future', 'stir', 'equity', 'commodity', 'fx', 'etf']);
// subscribeRows gives Instants as nanoseconds
const TIME = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });

function Dom({ linkGroup }: PanelProps<Record<string, never>>) {
  const [sym] = useLink<string>('symbol', { group: linkGroup });
  const inst = useInstrument(sym);
  const traded = !!inst && inst.has_volume && TRADED.has(inst.kind);
  const book = useFilteredRows(traded ? 'book' : null, { Sym: sym }, ['Level', 'BidPx', 'BidSz', 'BidOrders', 'AskPx', 'AskSz', 'AskOrders']);
  const prof = useFilteredRows(traded ? 'volume_profile' : null, { Sym: sym }, ['Price', 'Volume', 'BuyVolume', 'SellVolume']);
  const tape = useFilteredRows(traded ? 'trades' : null, { Sym: sym }, ['Timestamp', 'Price', 'Size', 'Side']);
  const tick = inst?.tick ?? 0.01;
  const key = (p: number) => Math.round(p / tick);

  const ladder = useMemo(() => {
    if (!book?.length) return null;
    const bids = new Map<number, { sz: number; n: number }>();
    const asks = new Map<number, { sz: number; n: number }>();
    for (const r of book) {
      bids.set(key(Number(r.BidPx)), { sz: Number(r.BidSz), n: Number(r.BidOrders) });
      asks.set(key(Number(r.AskPx)), { sz: Number(r.AskSz), n: Number(r.AskOrders) });
    }
    const vap = new Map<number, { v: number; b: number; s: number }>();
    for (const r of prof ?? []) vap.set(key(Number(r.Price)), { v: Number(r.Volume), b: Number(r.BuyVolume), s: Number(r.SellVolume) });
    const top = Math.max(...asks.keys());
    const bottom = Math.min(...bids.keys());
    const rows: number[] = [];
    for (let k = top; k >= bottom; k--) rows.push(k);
    const maxSz = Math.max(1, ...[...bids.values(), ...asks.values()].map(x => x.sz));
    const maxV = Math.max(1, ...[...vap.values()].map(x => x.v));
    const bidDepth = [...bids.values()].reduce((a, b) => a + b.sz, 0);
    const askDepth = [...asks.values()].reduce((a, b) => a + b.sz, 0);
    return { rows, bids, asks, vap, maxSz, maxV, bidDepth, askDepth, bestBid: Math.max(...bids.keys()), bestAsk: Math.min(...asks.keys()) };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [book, prof, tick]);

  const trades = useMemo(() => (tape ?? []).slice(-60).reverse(), [tape]);
  const lastKey = trades[0] ? key(Number(trades[0].Price)) : null;

  if (!traded)
    return (
      <div className="tm-empty-help">
        <p>
          <b>{sym || 'This symbol'}</b> has no order book here: the DOM is for traded instruments (Treasury, STIR, equity, commodity and FX futures, and ETFs).
        </p>
        <p>Link this panel to a futures chart, or click ZN, ZB or SR3H7 in the universe or the watchlist.</p>
      </div>
    );
  if (!ladder) return <div className="acme-empty">loading the book…</div>;
  const imb = (ladder.bidDepth - ladder.askDepth) / Math.max(1, ladder.bidDepth + ladder.askDepth);
  return (
    <div className="tm-dom">
      <div className="tm-dom-ladder">
        <div className="tm-dom-head">
          <span>Orders</span>
          <span>Bid</span>
          <span>Price</span>
          <span>Ask</span>
          <span>Orders</span>
          <span>Vol @ price</span>
        </div>
        <div className="tm-dom-rows">
          {ladder.rows.map(k => {
            const b = ladder.bids.get(k);
            const a = ladder.asks.get(k);
            const v = ladder.vap.get(k);
            const px = k * tick;
            const cls = [k === lastKey ? 'last' : '', k === ladder.bestBid ? 'bb' : '', k === ladder.bestAsk ? 'ba' : ''].join(' ');
            return (
              <div key={k} className={`tm-dom-row ${cls}`}>
                <span className="tm-num tm-muted">{b?.n ?? ''}</span>
                <span className="tm-dom-bid">
                  {b && <i style={{ width: `${(100 * b.sz) / ladder.maxSz}%` }} />}
                  <em>{b ? b.sz.toLocaleString() : ''}</em>
                </span>
                <span className="tm-dom-px">{formatPrice(inst, px)}</span>
                <span className="tm-dom-ask">
                  {a && <i style={{ width: `${(100 * a.sz) / ladder.maxSz}%` }} />}
                  <em>{a ? a.sz.toLocaleString() : ''}</em>
                </span>
                <span className="tm-num tm-muted">{a?.n ?? ''}</span>
                <span className="tm-dom-vap" title={v ? `buy ${v.b.toLocaleString()} / sell ${v.s.toLocaleString()}` : ''}>
                  {v && (
                    <>
                      <i className="buy" style={{ width: `${(60 * v.b) / ladder.maxV}%` }} />
                      <i className="sell" style={{ width: `${(60 * v.s) / ladder.maxV}%` }} />
                      <em>{v.v.toLocaleString()}</em>
                    </>
                  )}
                </span>
              </div>
            );
          })}
        </div>
        <div className="tm-dom-foot">
          <span>bid depth {ladder.bidDepth.toLocaleString()}</span>
          <span className={imb > 0 ? 'up' : 'down'}>imbalance {(imb * 100).toFixed(0)}%</span>
          <span>ask depth {ladder.askDepth.toLocaleString()}</span>
        </div>
      </div>
      <div className="tm-dom-tape">
        <div className="tm-dom-head">
          <span>Time</span>
          <span>Price</span>
          <span>Size</span>
        </div>
        <div className="tm-dom-rows">
          {trades.map((t, i) => (
            <div key={i} className={`tm-tape-row ${t.Side === 'Buy' ? 'up' : 'down'}`}>
              <span>{TIME.format(Number(t.Timestamp) / 1e6)}</span>
              <span className="tm-num">{formatPrice(inst, t.Price)}</span>
              <span className="tm-num">{Number(t.Size).toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function domPanel() {
  return definePanel<Record<string, never>>({
    kind: 'dom',
    label: 'DOM / time & sales',
    title: 'DOM',
    description: 'Price ladder with depth and volume at price, and the tape',
    defaultParams: {},
    component: Dom,
  });
}
