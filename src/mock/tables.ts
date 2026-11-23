/**
 * The published tables of the mock server: live market data (quotes, order books, time and sales, bars),
 * Treasury analytics (cash bonds, futures, deliverable baskets, STIR, the FOMC path, swaps, repo), reference
 * and calendar data, and a relative-value monitor. Each is a node in the update graph with its parents.
 */
import { fmt32 } from '@acme/chart-ext';
import { Engine, MarkerSource, RowSource, Source, TierTable, D, I, L, S, T, B, instant, type ColumnDef, type MockServer, type Row, type Tier } from '@acme/dh-mock';
import { risk, accrued, yieldFromPrice, couponDates } from '../model/bonds';
import { eventsBetween, hash, makeRng, curveAt, type Market, type MarketEvent } from '../model/market';
import { BONDS, CONTRACTS, FOMC, INSTRUMENTS, ROOTS, SESSIONS, STIR, SWAP_TENORS, TENORS, bySym, otrFor, type Instrument } from '../model/universe';

const DAY = 86400_000;

// ----------------------------------------------------------------------------- helpers

const round = (v: number, d: number) => (Number.isFinite(v) ? Math.round(v * 10 ** d) / 10 ** d : null);
const bp = (v: number) => round(v * 100, 2);

/** a display string for a price in the instrument's notation */
export function priceText(i: Instrument | undefined, v: number): string {
  if (!Number.isFinite(v)) return '';
  if (!i) return v.toFixed(4);
  if (i.thirtySeconds) return fmt32(v, i.tick ?? 1 / 256);
  return v.toFixed(i.decimals);
}

function table(engine: Engine, server: MockServer, name: string, cols: ColumnDef[], opts: { keyCols?: string[]; kind?: 'source' | 'op'; description: string; parents?: Source[]; input?: boolean }) {
  const t = new RowSource(engine, name, cols, { keyCols: opts.keyCols, kind: opts.kind ?? 'op', description: opts.description, input: opts.input });
  t.parents = opts.parents ?? [];
  server.publishTable(name, t);
  return t;
}

export interface LiveTables {
  quotes: RowSource;
  sessions: RowSource;
  addSessionFor(sym: string): void;
  sim: MarkerSource;
  tierNodes: Map<Tier, MarkerSource>;
  tierTables: TierTable[];
}

export const WATCHLIST_CORE = ['US2Y', 'US5Y', 'US10Y', 'US30Y', 'T2S10S', 'T5S30S', 'ZT', 'ZF', 'ZN', 'TN', 'ZB', 'UB', 'SR3H7', 'SOFR10Y', 'SS10Y', 'BEI10Y', 'TLT', 'ES', 'GC', '6J', 'MOVE'];

// ----------------------------------------------------------------------------- build everything

export function buildTables(engine: Engine, server: MockServer, market: Market): LiveTables {
  const sim = new MarkerSource(engine, 'market simulator', 'source', 'curve factors, every instrument, events; ticks every cycle');
  const tierNodes = new Map<Tier, MarkerSource>();
  for (const tier of [market.tiers.s1, market.tiers.m1, market.tiers.d1])
    tierNodes.set(tier, new MarkerSource(engine, `${tier.name} bars (all symbols)`, 'op', `the ${tier.name} base tier every chart rolls up from`, [sim]));

  // ---------------------------------------------------------------- bars
  // every instrument but the ones typed as expressions (the universe's own spreads are in)
  const listed = (i: { sym: string; expr?: string }) => !i.expr || bySym.has(i.sym);
  const bars1m = new TierTable(engine, 'bars_1m', market, market.tiers.m1, listed);
  const bars1d = new TierTable(engine, 'bars_1d', market, market.tiers.d1, listed);
  bars1m.parents = [tierNodes.get(market.tiers.m1)!];
  bars1d.parents = [tierNodes.get(market.tiers.d1)!];
  server.publishTable('bars_1m', bars1m);
  server.publishTable('bars_1d', bars1d);

  // ---------------------------------------------------------------- reference
  const instruments = table(engine, server, 'instruments', [S('Sym'), S('Name'), S('Dataset'), S('Kind'), S('Unit'), D('Tick'), S('Notation'), D('Multiplier'), S('Session'), B('HasVolume'), S('Description')], {
    keyCols: ['Sym'], kind: 'source', description: 'reference data for every symbol',
  });
  instruments.put(
    INSTRUMENTS.map(i => ({
      Sym: i.sym, Name: i.name, Dataset: i.dataset, Kind: i.kind, Unit: i.unit, Tick: i.tick, Notation: i.thirtySeconds ? '32nds' : 'decimal',
      Multiplier: i.multiplier ?? null, Session: SESSIONS[i.session]?.name ?? i.session, HasVolume: i.hasVolume, Description: i.description ?? i.expr ?? '',
    })),
  );
  const sessions = table(engine, server, 'sessions', [S('Sym'), S('Spec')], { keyCols: ['Sym'], kind: 'source', description: 'exchange calendar per symbol (JSON)' });
  sessions.put(INSTRUMENTS.map(i => ({ Sym: i.sym, Spec: JSON.stringify(SESSIONS[i.session]) })));
  const addSessionFor = (sym: string) => {
    const i = market.instrument(sym);
    if (i && SESSIONS[i.session]) sessions.put([{ Sym: sym, Spec: JSON.stringify(SESSIONS[i.session]) }]);
  };

  // ---------------------------------------------------------------- quotes, books, tape
  const quoteCols = [S('Sym'), S('Name'), S('Kind'), D('Last'), S('LastText'), D('Bid'), D('Ask'), D('Chg'), D('ChgPct'), D('ChgBp'), D('Open'), D('High'), D('Low'), D('PrevClose'), D('Volume'), T('Updated')];
  const quotes = table(engine, server, 'quotes', quoteCols, { keyCols: ['Sym'], description: 'last, bid/ask and the day’s change for every symbol', parents: [sim] });
  const core = table(engine, server, 'watchlist_core', quoteCols, { keyCols: ['Sym'], description: 'the default watchlist: a rates trader’s core symbols', parents: [quotes] });
  const tradedKinds = new Set(['future', 'stir', 'equity', 'commodity', 'fx', 'etf']);
  const traded = market.inst.filter(i => i.hasVolume && tradedKinds.has(i.kind));
  const LEVELS = 15;
  const book = table(engine, server, 'book', [S('Sym'), I('Level'), D('BidPx'), D('BidSz'), I('BidOrders'), D('AskPx'), D('AskSz'), I('AskOrders')], {
    keyCols: ['Sym', 'Level'], description: `order book depth, ${LEVELS} levels a side, for every traded symbol`, parents: [sim],
  });
  const trades = table(engine, server, 'trades', [T('Timestamp'), S('Sym'), D('Price'), S('PriceText'), D('Size'), S('Side'), S('Venue')], {
    description: 'time and sales: every trade in the traded symbols (last 4000)', parents: [sim],
  });
  const profile = table(engine, server, 'volume_profile', [S('Sym'), D('Price'), D('Volume'), D('BuyVolume'), D('SellVolume')], {
    keyCols: ['Sym', 'Price'], description: "today's volume at each price, buy and sell aggressor", parents: [trades],
  });
  const sizes = new Map<string, { bid: Float64Array; ask: Float64Array }>();
  const rng = makeRng(hash('book', Date.now()));
  const avgLot = (i: Instrument) => Math.max(1, Math.round((i.volPerMin ?? 100) / 25));
  for (const i of traded) {
    const base = avgLot(i) * 6;
    const mk = () => Float64Array.from({ length: LEVELS }, (_, l) => Math.round(base * (0.6 + l * 0.18) * (0.6 + rng.u() * 0.8)));
    sizes.set(i.sym, { bid: mk(), ask: mk() });
  }
  const lastTradeSide = new Map<string, number>();
  let lastQuote = 0;

  const updateMarketTables = (now: number, dtMs: number) => {
    // quotes every cycle
    const rows: Row[] = [];
    for (const i of market.inst) {
      const v = market.value(i.sym);
      const d = market.dayBar(i.sym);
      const prev = d?.prevClose ?? NaN;
      const chg = v - prev;
      const spread = i.kind === 'yield' || i.kind === 'swap' ? 0.001 : i.kind === 'bond' ? (i.bond?.age === 0 ? 1 / 128 : 1 / 64) : i.tick ?? 0;
      const yieldLike = i.unit === '%' || i.unit === 'bp';
      rows.push({
        Sym: i.sym, Name: i.name, Kind: i.kind, Last: v, LastText: priceText(i, v),
        Bid: yieldLike ? v + spread / 2 : v - (i.kind === 'bond' ? spread / 2 : 0), Ask: yieldLike ? v - spread / 2 : v + (i.kind === 'bond' ? spread / 2 : spread),
        Chg: Number.isFinite(chg) ? chg : null, ChgPct: Number.isFinite(chg) && prev ? (100 * chg) / Math.abs(prev) : null,
        ChgBp: Number.isFinite(chg) ? (i.unit === '%' ? chg * 100 : i.unit === 'bp' ? chg : null) : null,
        Open: d?.open ?? null, High: d?.high ?? null, Low: d?.low ?? null, PrevClose: Number.isFinite(prev) ? prev : null, Volume: d?.volume ?? 0, Updated: instant(now),
      });
    }
    quotes.put(rows);
    core.put(rows.filter(r => WATCHLIST_CORE.includes(String(r.Sym))));
    // books and trades
    const bookRows: Row[] = [];
    const tradeRows: Row[] = [];
    for (const i of traded) {
      const tick = i.tick ?? 0.01;
      const last = market.value(i.sym);
      if (!Number.isFinite(last)) continue;
      const sz = sizes.get(i.sym)!;
      // the last trade's side sets where the inside market sits around the last price
      const side = lastTradeSide.get(i.sym) ?? 1;
      const bid0 = side > 0 ? last - tick : last;
      const ask0 = bid0 + tick;
      const base = avgLot(i) * 6;
      for (let l = 0; l < LEVELS; l++) {
        const target = base * (0.6 + l * 0.18);
        for (const a of [sz.bid, sz.ask]) {
          a[l] = Math.max(1, Math.round(a[l] + (target - a[l]) * 0.08 + (rng.u() - 0.5) * target * 0.25));
        }
        bookRows.push({
          Sym: i.sym, Level: l + 1, BidPx: bid0 - l * tick, BidSz: sz.bid[l], BidOrders: Math.max(1, Math.round(sz.bid[l] / avgLot(i) / 2)),
          AskPx: ask0 + l * tick, AskSz: sz.ask[l], AskOrders: Math.max(1, Math.round(sz.ask[l] / avgLot(i) / 2)),
        });
      }
      // trades: Poisson by the instrument's volume
      const perSec = ((i.volPerMin ?? 100) / 60) * (dtMs / 1000);
      const lot = avgLot(i);
      let n = 0;
      const lambda = perSec / lot;
      let p = Math.exp(-Math.min(lambda, 30));
      let u = rng.u();
      while (u > p && n < 12) {
        n++;
        u *= rng.u();
      }
      for (let k = 0; k < n; k++) {
        const buy = rng.u() < 0.5;
        lastTradeSide.set(i.sym, buy ? 1 : -1);
        const px = Math.round((buy ? ask0 : bid0) / tick) * tick; // the same float as every other price on this tick
        const size = Math.max(1, Math.round(-Math.log(1 - rng.u()) * lot));
        tradeRows.push({ Timestamp: instant(now - Math.round(rng.u() * dtMs)), Sym: i.sym, Price: px, PriceText: priceText(i, px), Size: size, Side: buy ? 'Buy' : 'Sell', Venue: i.kind === 'etf' ? 'ARCA' : i.session === 'EUREX' ? 'XEUR' : 'GLBX' });
        const key = profile.find([i.sym, px]);
        const prevRow = key != null ? profile.row(key) : undefined;
        profile.put([{
          Sym: i.sym, Price: px, Volume: Number(prevRow?.Volume ?? 0) + size,
          BuyVolume: Number(prevRow?.BuyVolume ?? 0) + (buy ? size : 0), SellVolume: Number(prevRow?.SellVolume ?? 0) + (buy ? 0 : size),
        }]);
      }
    }
    book.put(bookRows);
    if (tradeRows.length) {
      tradeRows.sort((a, b) => Number(a.Timestamp) - Number(b.Timestamp));
      trades.put(tradeRows);
      trades.cap(4000);
    }
    lastQuote = now;
  };

  // ---------------------------------------------------------------- Treasury analytics (each second)
  const ust = table(engine, server, 'ust_cash', [
    S('Cusip'), S('Security'), S('Type'), S('Term'), S('Status'), D('Coupon'), T('Maturity'), D('Years'), S('BidPx'), S('AskPx'), D('Price'),
    D('Yield'), D('ChgBp'), D('RichBp'), D('ModDur'), D('DV01'), D('Convexity'), D('Accrued'), D('RepoSpecial'), D('OutstandingBn'),
  ], { keyCols: ['Cusip'], description: 'every Treasury cash bond: price (32nds), yield, risk, richness to the fitted curve, repo', parents: [sim] });

  const fut = table(engine, server, 'futures_contracts', [
    S('Sym'), S('Root'), S('Contract'), S('Last'), D('Price'), S('Chg'), D('Volume'), D('OpenInterest'), S('CTD'), D('CF'), D('DV01'),
    D('ImpliedRepo'), D('NetBasis32'), T('FirstNotice'), I('DaysToFND'), S('Roll'),
  ], { keyCols: ['Sym'], description: 'Treasury futures: price, CTD, conversion factor, DV01, implied repo, roll status', parents: [quotes, ust] });

  const basket = table(engine, server, 'deliverable_basket', [
    S('Contract'), S('Cusip'), S('Security'), D('Coupon'), T('Maturity'), D('CF'), S('Price'), D('Yield'), D('GrossBasis32'), D('Carry32'),
    D('NetBasis32'), D('ImpliedRepo'), I('Rank'), B('CTD'),
  ], { keyCols: ['Contract', 'Cusip'], description: 'every deliverable bond of every contract: basis, carry, net basis, implied repo, CTD rank', parents: [ust, fut] });

  const stir = table(engine, server, 'stir_strip', [S('Sym'), S('Contract'), D('Price'), D('ImpliedRate'), D('ChgBp'), D('Volume'), T('Start'), T('End')], {
    keyCols: ['Sym'], description: 'SOFR and fed funds futures: price, implied rate, change', parents: [quotes],
  });
  const fomc = table(engine, server, 'fomc_path', [T('Meeting'), D('ImpliedRate'), D('CumulativeBp'), D('ThisMeetingBp'), D('ProbCut'), S('Priced')], {
    keyCols: ['Meeting'], description: 'the policy path the STIR curve prices: implied rate after each FOMC meeting', parents: [stir],
  });
  const swaps = table(engine, server, 'swap_curve', [S('Tenor'), D('Years'), D('SwapRate'), D('Treasury'), D('SwapSpreadBp'), D('ChgBp')], {
    keyCols: ['Tenor'], description: 'SOFR OIS swap curve and swap spreads to Treasuries', parents: [quotes],
  });
  const curve = table(engine, server, 'ust_curve', [S('Tenor'), D('Years'), S('Sym'), S('Cusip'), D('Yield'), D('Chg1D'), D('Chg1W'), D('Chg1M'), D('Chg3M')], {
    keyCols: ['Tenor'], description: 'the on-the-run curve with changes over 1 day, 1 week, 1 and 3 months (bp)', parents: [quotes],
  });
  const repo = table(engine, server, 'repo_rates', [S('Kind'), S('Term'), S('Cusip'), S('Security'), D('Rate'), D('SpreadToGCbp')], {
    keyCols: ['Kind', 'Term', 'Cusip'], description: 'GC repo by term, and specials for the on-the-run issues', parents: [quotes],
  });

  const specials = new Map(BONDS.filter(b => b.age === 0 && b.type !== 'Bill').map(b => [b.cusip, 0.12 + makeRng(hash('sp', b.cusip)).u() * 0.4]));
  const openInterest = new Map(CONTRACTS.map(c => [c.sym, Math.round(c.root.volPerMin * (c.front ? 900 : 2100) * (0.8 + makeRng(hash('oi', c.sym)).u() * 0.4))]));

  const updateAnalytics = (now: number) => {
    const settle = Math.floor(now / DAY) * DAY + DAY;
    const ustRows: Row[] = [];
    for (const b of BONDS) {
      const y = market.yieldOf(b.cusip);
      if (!Number.isFinite(y)) continue;
      const r = risk(b, y, settle);
      const i = bySym.get(b.cusip)!;
      const d = market.dayBar(b.cusip);
      const prevY = d && Number.isFinite(d.prevClose) ? yieldFromPrice(b, d.prevClose, settle) : NaN;
      const tenor = (b.maturity - now) / (365.25 * DAY);
      const fitted = curveAt(market.F, tenor) - (b.type === 'TIPS' ? curveAt(market.F, tenor) - y : 0);
      const half = b.age === 0 ? 1 / 256 : 1 / 128;
      ustRows.push({
        Cusip: b.cusip, Security: b.name, Type: b.type, Term: b.term, Status: b.age === 0 ? 'OTR' : b.age === 1 ? '1st off' : b.age === 2 ? '2nd off' : 'old',
        Coupon: b.coupon, Maturity: instant(b.maturity), Years: round(tenor, 2), BidPx: priceText(i, r.price - half), AskPx: priceText(i, r.price + half),
        Price: r.price, Yield: round(y, 4), ChgBp: Number.isFinite(prevY) ? round((y - prevY) * 100, 2) : null, RichBp: round((y - fitted) * 100, 2),
        ModDur: round(r.modDur, 3), DV01: round(r.dv01, 2), Convexity: round(r.convexity, 3), Accrued: round(r.accrued, 5),
        RepoSpecial: specials.has(b.cusip) ? round(specials.get(b.cusip)! * 100, 1) : null, OutstandingBn: b.outstanding,
      });
    }
    ust.put(ustRows);
    // futures and baskets
    const futRows: Row[] = [];
    const basketRows: Row[] = [];
    for (const c of CONTRACTS) {
      if (!c.basket.length) continue;
      const F = market.value(c.sym);
      const i = bySym.get(c.sym)!;
      const d = market.dayBar(c.sym);
      const days = Math.max(1, (c.deliveryMonth - now) / DAY);
      const items = market.basket(c, now).map(x => {
        const aiDel = accrued(x.bond, c.deliveryMonth);
        // coupons paid between now and delivery count as income
        const coupons = couponDates(x.bond.maturity, now).filter(t => t <= c.deliveryMonth).length * (x.bond.coupon / 2);
        const gross = x.price - F * x.cf;
        const net = gross - x.carry;
        const irr = ((F * x.cf + aiDel + coupons - (x.price + x.ai)) / (x.price + x.ai)) * (360 / days);
        return { ...x, gross, net, irr: irr * 100 };
      });
      items.sort((a, b) => a.net / a.cf - b.net / b.cf);
      const ctd = items[0];
      items.forEach((x, rank) =>
        basketRows.push({
          Contract: c.sym, Cusip: x.bond.cusip, Security: x.bond.name, Coupon: x.bond.coupon, Maturity: instant(x.bond.maturity), CF: x.cf,
          Price: fmt32(x.price), Yield: round(x.y, 4), GrossBasis32: round(x.gross * 32, 2), Carry32: round(x.carry * 32, 2), NetBasis32: round(x.net * 32, 2),
          ImpliedRepo: round(x.irr, 3), Rank: rank + 1, CTD: rank === 0,
        }),
      );
      const ctdRisk = risk(ctd.bond, ctd.y, settle);
      const contractDv01 = (ctdRisk.dv01 / ctd.cf) * (c.root.multiplier / 10_000); // $ per bp per contract (face = multiplier x 100)
      const fnd = Math.ceil((c.firstNotice - now) / DAY);
      futRows.push({
        Sym: c.sym, Root: c.root.root, Contract: `${c.root.name} ${c.label}`, Last: priceText(i, F), Price: F,
        Chg: d && Number.isFinite(d.prevClose) ? `${F - d.prevClose >= 0 ? '+' : '-'}${fmt32(Math.abs(F - d.prevClose), c.root.tick).replace(/^0-/, '0-')}` : '',
        Volume: d?.volume ?? 0, OpenInterest: openInterest.get(c.sym) ?? 0, CTD: ctd.bond.name, CF: ctd.cf, DV01: round(contractDv01, 2),
        ImpliedRepo: round(ctd.irr, 3), NetBasis32: round(ctd.net * 32, 2), FirstNotice: instant(c.firstNotice), DaysToFND: fnd,
        Roll: c.front ? (fnd <= 10 ? `rolling (${Math.round(c.volumeShare * 100)}% of volume left)` : 'front') : fnd < 120 ? 'next (taking volume)' : 'deferred',
      });
    }
    fut.put(futRows);
    basket.put(basketRows);
    // STIR and the FOMC path
    const stirRows: Row[] = [];
    for (const s of STIR) {
      const v = market.value(s.sym);
      const d = market.dayBar(s.sym);
      stirRows.push({
        Sym: s.sym, Contract: s.label, Price: round(v, 4), ImpliedRate: round(100 - v, 4), ChgBp: d && Number.isFinite(d.prevClose) ? round(-(v - d.prevClose) * 100, 2) : null,
        Volume: d?.volume ?? 0, Start: instant(s.start), End: instant(s.end),
      });
    }
    stir.put(stirRows);
    const effr = market.value('EFFR');
    const fwd = (ms: number) => {
      const t = Math.max(1 / 52, (ms - now) / (365.25 * DAY));
      return curveAt(market.F, t) + (t * (curveAt(market.F, t + 0.05) - curveAt(market.F, Math.max(1 / 365, t - 0.05)))) / 0.1 - 0.07;
    };
    let prevRate = effr;
    const fomcRows: Row[] = [];
    for (const m of FOMC) {
      if (m < now - DAY) continue;
      const rate = fwd(m + 14 * DAY);
      const step = (rate - prevRate) * 100;
      fomcRows.push({
        Meeting: instant(m), ImpliedRate: round(rate, 3), CumulativeBp: round((rate - effr) * 100, 1), ThisMeetingBp: round(step, 1),
        ProbCut: round(Math.max(0, Math.min(100, (-step / 25) * 100)), 0), Priced: step < -20 ? 'cut' : step < -5 ? 'partial cut' : step > 5 ? 'hike risk' : 'hold',
      });
      prevRate = rate;
    }
    fomc.put(fomcRows);
    // swaps and curve
    swaps.put(SWAP_TENORS.map(([sym, t]) => {
      const sw = market.value(sym);
      const tsy = curveAt(market.F, t);
      const d = market.dayBar(sym);
      return { Tenor: `${t}Y`, Years: t, SwapRate: round(sw, 4), Treasury: round(tsy, 4), SwapSpreadBp: bp(sw - tsy), ChgBp: d && Number.isFinite(d.prevClose) ? bp(sw - d.prevClose) : null };
    }));
    curve.put(TENORS.map(([sym, years, label]) => {
      const y = market.value(sym);
      const ch = (days: number) => bp(y - market.valueAt(sym, now - days * DAY));
      return { Tenor: label, Years: years, Sym: sym, Cusip: otrFor(sym)?.cusip ?? '', Yield: round(y, 4), Chg1D: ch(1), Chg1W: ch(7), Chg1M: ch(30), Chg3M: ch(91) };
    }));
    const gc = market.value('GCREPO');
    const terms: Array<[string, number]> = [['O/N', 0], ['T/N', 0.01], ['1W', 0.015], ['2W', 0.02], ['1M', 0.0], ['3M', -0.06]];
    repo.put([
      ...terms.map(([term, adj]) => ({ Kind: 'GC', Term: term, Cusip: '', Security: 'General collateral (Treasuries)', Rate: round(gc + adj, 3), SpreadToGCbp: round(adj * 100, 1) })),
      ...[...specials].map(([cusip, s]) => ({
        Kind: 'Special', Term: 'O/N', Cusip: cusip, Security: bySym.get(cusip)?.name ?? cusip, Rate: round(gc - s, 3), SpreadToGCbp: round(-s * 100, 1),
      })),
    ]);
  };

  // ---------------------------------------------------------------- relative value monitor (every 2 s)
  const rv = table(engine, server, 'rv_monitor', [
    S('Sym'), S('Name'), S('Dataset'), D('Last'), D('Chg1D'), D('Z20'), D('Z60'), D('Pct1Y'), D('RVol20'), D('RSI14'), S('Trend'), S('Signal'),
  ], { keyCols: ['Sym'], description: 'every symbol against its own history: z-scores, 1-year percentile, realized vol, RSI, trend (daily bars)', parents: [tierNodes.get(market.tiers.d1)!, quotes] });
  const updateRv = () => {
    const d1 = market.tiers.d1;
    const rows: Row[] = [];
    for (let k = 0; k < market.inst.length; k++) {
      const i = market.inst[k];
      const s = d1.series[k];
      const closes: number[] = [];
      for (let r = Math.max(0, d1.n - 260); r < d1.n; r++) if (Number.isFinite(s.c[r])) closes.push(s.c[r]);
      if (closes.length < 30) continue;
      const last = closes[closes.length - 1];
      const z = (n: number) => {
        const w = closes.slice(-n);
        const m = w.reduce((a, b) => a + b, 0) / w.length;
        const sd = Math.sqrt(w.reduce((a, b) => a + (b - m) ** 2, 0) / w.length);
        return sd ? (last - m) / sd : 0;
      };
      const pct = (100 * closes.filter(c => c <= last).length) / closes.length;
      const additive = i.unit === '%' || i.unit === 'bp';
      const rets = closes.slice(-21).map((c, j, a) => (j ? (additive ? (c - a[j - 1]) * (i.unit === '%' ? 100 : 1) : Math.log(c / a[j - 1])) : 0)).slice(1);
      const sd = Math.sqrt(rets.reduce((a, b) => a + b * b, 0) / rets.length);
      let g = 0;
      let lo = 0;
      for (let j = 1; j < closes.length; j++) {
        const dlt = closes[j] - closes[j - 1];
        g = (g * 13 + Math.max(0, dlt)) / 14;
        lo = (lo * 13 + Math.max(0, -dlt)) / 14;
      }
      const rsi = lo === 0 ? 100 : 100 - 100 / (1 + g / lo);
      let e20 = closes[0];
      let e50 = closes[0];
      for (const c of closes) {
        e20 = e20 + (2 / 21) * (c - e20);
        e50 = e50 + (2 / 51) * (c - e50);
      }
      const z60 = z(60);
      const trend = e20 > e50 ? (last > e20 ? '▲ strong up' : '▲ up') : last < e20 ? '▼ strong down' : '▼ down';
      const word = i.kind === 'yield' || i.kind === 'swap' ? (z60 > 1.5 ? 'cheap (high yield)' : z60 < -1.5 ? 'rich (low yield)' : '') : z60 > 1.5 ? 'stretched high' : z60 < -1.5 ? 'stretched low' : '';
      rows.push({
        Sym: i.sym, Name: i.name, Dataset: i.dataset, Last: last, Chg1D: closes.length > 1 ? last - closes[closes.length - 2] : null,
        Z20: round(z(20), 2), Z60: round(z60, 2), Pct1Y: round(pct, 0), RVol20: round(additive ? sd * Math.sqrt(252) : 100 * sd * Math.sqrt(252), 2),
        RSI14: round(rsi, 1), Trend: trend, Signal: rsi > 70 ? `overbought${word ? ', ' + word : ''}` : rsi < 30 ? `oversold${word ? ', ' + word : ''}` : word,
      });
    }
    rv.put(rows);
  };

  // ---------------------------------------------------------------- calendar, news, auctions, positioning
  const calendar = table(engine, server, 'econ_calendar', [T('Time'), S('Country'), S('Event'), S('Importance'), S('Consensus'), S('Prior'), S('Actual'), D('CurveMoveBp'), S('Status')], {
    keyCols: ['Time', 'Event'], kind: 'source', description: 'US data releases, auctions and FOMC decisions a week either side; actuals fill in as they print', parents: [sim],
  });
  const now0 = Date.now();
  const stars = (n: number) => '★'.repeat(n) + '☆'.repeat(3 - n);
  const calRow = (e: MarketEvent, now: number): Row => ({
    Time: instant(e.t), Country: 'US', Event: e.title, Importance: stars(e.importance), Consensus: e.consensus ?? '', Prior: e.prior ?? '',
    Actual: e.t <= now ? e.actual ?? '' : '', CurveMoveBp: e.t <= now ? round(e.jL * 100, 1) : null, Status: e.t <= now ? 'released' : 'upcoming',
  });
  calendar.put(eventsBetween(now0 - 7 * DAY, now0 + 7 * DAY).filter(e => e.kind !== 'headline').map(e => calRow(e, now0)));
  const news = table(engine, server, 'news', [T('Timestamp'), S('Source'), S('Headline'), S('Kind'), I('Importance'), D('MoveBp')], {
    kind: 'source', description: 'the headline tape: releases, auctions and market headlines as they cross', parents: [sim],
  });
  news.put(market.applied.slice(-60).map(e => ({ Timestamp: instant(e.t), Source: 'MockWire', Headline: headline(e), Kind: e.kind, Importance: e.importance, MoveBp: round(e.jL * 100, 2) })));
  market.onEvent.add(e => {
    news.put([{ Timestamp: instant(e.t), Source: 'MockWire', Headline: headline(e), Kind: e.kind, Importance: e.importance, MoveBp: round(e.jL * 100, 2) }]);
    news.cap(500);
    if (e.kind !== 'headline') calendar.put([calRow(e, Date.now() + 1000)]);
  });

  const auctions = table(engine, server, 'treasury_auctions', [T('Date'), S('Security'), S('Term'), D('SizeBn'), D('HighYield'), D('WIYield'), D('TailBp'), D('BidToCover'), D('IndirectPct'), D('DirectPct'), D('DealerPct'), S('Status')], {
    kind: 'source', description: 'recent coupon auction results and the upcoming schedule (mock)',
  });
  const aRows: Row[] = [];
  for (let w = -10; w <= 2; w++) {
    for (const [term, size, tenor] of [['2Y', 69, 2], ['5Y', 70, 5], ['7Y', 44, 7], ['10Y', 39, 10], ['30Y', 22, 30]] as Array<[string, number, number]>) {
      if ((term === '10Y' || term === '30Y') && Math.abs(w) % 4 !== 1) continue;
      if ((term === '2Y' || term === '5Y' || term === '7Y') && Math.abs(w) % 4 !== 0) continue;
      const t = now0 + w * 7 * DAY + (term === '2Y' ? 0 : term === '5Y' ? DAY : 2 * DAY);
      const g = makeRng(hash('auc', term, w));
      const wi = curveAt(market.F, tenor) - 0.08 * (w / 10);
      const tail = g.n() * 0.9;
      const upcoming = t > now0;
      aRows.push({
        Date: instant(t), Security: `${term} ${term.endsWith('0Y') && term !== '10Y' ? 'bond' : 'note'}`, Term: term, SizeBn: size,
        HighYield: upcoming ? null : round(wi + tail / 100, 3), WIYield: round(wi, 3), TailBp: upcoming ? null : round(tail, 1),
        BidToCover: upcoming ? null : round(2.55 - tail * 0.12 + g.n() * 0.08, 2), IndirectPct: upcoming ? null : round(66 - tail * 4 + g.n() * 3, 1),
        DirectPct: upcoming ? null : round(18 + g.n() * 2.5, 1), DealerPct: upcoming ? null : round(15 + tail * 3 + g.n() * 1.5, 1), Status: upcoming ? 'Upcoming' : 'Results',
      });
    }
  }
  auctions.put(aRows.sort((a, b) => Number(a.Date) - Number(b.Date)));

  const cot = table(engine, server, 'cftc_cot', [T('Week'), S('Contract'), D('AssetMgrNet'), D('LevFundsNet'), D('DealerNet'), D('OpenInterest'), D('ChgLevFunds')], {
    kind: 'source', description: 'CFTC Traders in Financial Futures: weekly net positioning (contracts), last 26 weeks (mock)',
  });
  const cotRows: Row[] = [];
  for (const r of [...ROOTS.map(x => x.root), 'SR3']) {
    let lev = -(makeRng(hash('lev', r)).u() * 800_000 + 200_000);
    for (let w = 25; w >= 0; w--) {
      const g = makeRng(hash('cot', r, w));
      const week = Math.floor((now0 - w * 7 * DAY) / DAY) * DAY - ((new Date(now0).getUTCDay() + 5) % 7) * DAY; // Tuesdays
      const chg = g.n() * 40_000;
      lev += chg;
      const am = -lev * 0.85 + g.n() * 30_000;
      cotRows.push({ Week: instant(week), Contract: r, AssetMgrNet: Math.round(am), LevFundsNet: Math.round(lev), DealerNet: Math.round(-(am + lev) * 0.7), OpenInterest: Math.round(Math.abs(lev) * 3.4 + 1e6), ChgLevFunds: Math.round(chg) });
    }
  }
  cot.put(cotRows);

  // ---------------------------------------------------------------- first fill: every table has rows before anyone asks
  {
    const now = Date.now();
    updateMarketTables(now, 250);
    updateAnalytics(now);
    updateRv();
    // today's volume at price from the 1-minute bars (each bar's volume at its close, split by its direction)
    const m1 = market.tiers.m1;
    const dayStart = Math.floor(now / DAY) * DAY / 1000;
    const rows = new Map<string, Row>();
    for (const i of traded) {
      const k = market.index.get(i.sym)!;
      const s = m1.series[k];
      const tick = i.tick ?? 0.01;
      for (let r = 0; r < m1.n; r++) {
        if (m1.times[r] < dayStart || !Number.isFinite(s.c[r])) continue;
        const px = Math.round(s.c[r] / tick) * tick;
        const key = `${i.sym}|${px}`;
        const up = s.c[r] >= s.o[r];
        const v = s.v[r] / avgLot(i) / 6; // the profile counts traded lots, a fraction of the bar's volume
        const row = rows.get(key) ?? { Sym: i.sym, Price: px, Volume: 0, BuyVolume: 0, SellVolume: 0 };
        row.Volume = Number(row.Volume) + Math.round(v);
        row.BuyVolume = Number(row.BuyVolume) + Math.round(up ? v * 0.6 : v * 0.4);
        row.SellVolume = Number(row.Volume) - Number(row.BuyVolume);
        rows.set(key, row);
      }
    }
    profile.put([...rows.values()]);
  }

  // ---------------------------------------------------------------- the ticker
  let lastAnalytics = 0;
  let lastRv = 0;
  const tierN = new Map<Tier, { n: number; epoch: number }>();
  for (const tier of [market.tiers.s1, market.tiers.m1, market.tiers.d1]) tierN.set(tier, { n: tier.n, epoch: tier.epoch });
  engine.addTicker('market tables', (now, dt) => {
    const t0 = performance.now();
    updateMarketTables(now, dt);
    if (now - lastAnalytics >= 1000) {
      lastAnalytics = now;
      updateAnalytics(now);
    }
    if (now - lastRv >= 2000) {
      lastRv = now;
      updateRv();
    }
    sim.tick(performance.now() - t0, market.inst.length);
  });

  return {
    quotes, sessions, addSessionFor, sim, tierNodes,
    tierTables: [bars1m, bars1d],
    // the server calls this after each market step with the tiers' changed rows
    ...{ tierN },
  } as LiveTables & { tierN: Map<Tier, { n: number; epoch: number }> };
}

function headline(e: MarketEvent): string {
  if (e.kind === 'release') return `US ${e.title}: ${e.actual} (consensus ${e.consensus}, prior ${e.prior})`;
  if (e.kind === 'auction') return `Treasury ${e.title} results: ${e.actual}`;
  if (e.kind === 'fomc') return `FOMC decision: ${e.consensus}; statement and projections out`;
  return e.title;
}
