/**
 * A hypothetical Treasury trading desk on the mock market: books with positions in cash bonds and futures, client
 * RFQs arriving all day (some won, then often hedged with futures), and from those:
 *
 *   books            one row per book: positions, gross, DV01 against its limit, VaR, P&L today
 *   positions        per book and instrument: quantity, price, DV01, idio, carry, P&L today (start-of-day and new trades)
 *   book_risk        per book: DV01 by key-rate bucket, curve-factor exposures (level, slope, curvature, policy), VaR
 *   pnl_attribution  per book: P&L since 07:00 ET split into carry, roll, level, slope, curvature, policy, idio, basis,
 *                    new trades, fees and residual
 *   pnl_timeseries   each book's P&L through the day
 *   rfq_blotter, desk_trades, rfq_flow   client RFQs, the desk's executions, RFQ volume per CUSIP per 5 minutes
 *   security_master, cusip_auctions      per CUSIP: outstanding, SOMA holdings, free float, street volume, desk
 *                    position, idio, deliverability; each issue's auction and reopenings
 *
 * All of it is made up (seeded by the date), shaped like the real thing. Attribution is risk-based: each start-of-day
 * position's yield change is split into the curve factors' moves at its tenor and the rest (idio); futures' rest is
 * basis; P&L on trades done today is its own line.
 */
import { fmt32 } from '@acme/chart-ext';
import { Engine, MarkerSource, RowSource, D, I, L, S, T, B, instant, type MockServer, type Row } from '@acme/dh-mock';
import { accrued, risk, type Bond } from '../model/bonds';
import { hash, loading, makeRng, type Market } from '../model/market';
import { BONDS, CONTRACTS, STIR, bondByCusip, bySym, contractBySym, stirBySym } from '../model/universe';
import { priceText, type LiveTables } from './tables';

const DAY = 86400_000;
const YEAR = 365.25 * DAY;

export interface BookDef {
  id: string;
  name: string;
  strategy: string;
  trader: string;
  dv01Limit: number;
}

export const BOOKS: BookDef[] = [
  { id: 'FRONT', name: 'Front end', strategy: 'Bills, 2s and 3s; client flow', trader: 'AK', dv01Limit: 250_000 },
  { id: 'BELLY', name: 'Belly', strategy: '5s and 7s; client flow', trader: 'MS', dv01Limit: 350_000 },
  { id: 'TENS', name: 'Tens', strategy: '10s and ultras; client flow', trader: 'JL', dv01Limit: 400_000 },
  { id: 'LONG', name: 'Long end', strategy: '20s and 30s; client flow', trader: 'RP', dv01Limit: 300_000 },
  { id: 'TIPS', name: 'Inflation', strategy: 'TIPS against nominals (breakevens)', trader: 'DC', dv01Limit: 150_000 },
  { id: 'BASIS', name: 'Basis', strategy: 'Long CTDs against futures, to delivery', trader: 'NW', dv01Limit: 150_000 },
  { id: 'RV', name: 'Relative value', strategy: '2s5s10s butterfly, off-the-run against on-the-run', trader: 'EH', dv01Limit: 200_000 },
  { id: 'MACRO', name: 'Macro overlay', strategy: 'Directional futures and SOFR strip', trader: 'TB', dv01Limit: 500_000 },
];

const COMPONENTS = ['Carry', 'Roll', 'Level', 'Slope', 'Curvature', 'Policy', 'Idio', 'Basis', 'NewTrades', 'Fees', 'Residual'] as const;
type Component = (typeof COMPONENTS)[number];

/** factor daily vols in bp (from the simulator's annual vols) for VaR */
const FACTOR_DAILY_BP = { level: (0.85 / Math.sqrt(252)) * 100, slope: (0.55 / Math.sqrt(252)) * 100, curvature: (0.35 / Math.sqrt(252)) * 100, policy: (0.45 / Math.sqrt(252)) * 100 };
const IDIO_DAILY_BP = 0.6;
const BUCKETS: Array<[string, number]> = [['Bills', 0.5], ['2Y', 2], ['5Y', 5], ['10Y', 10], ['30Y', 30]];

const CLIENTS = ['Pension-07', 'AssetMgr-12', 'AssetMgr-31', 'HedgeFund-03', 'HedgeFund-19', 'BankTsy-05', 'Insurer-02', 'CentralBank-01', 'MMF-09', 'Corp-14', 'SovWealth-04', 'RIA-22'];
const VENUES = ['D2C-1', 'D2C-1', 'D2C-2', 'Voice', 'Single-dealer'];

interface Pos {
  book: string;
  sym: string;
  qty: number;
  sodQty: number;
  sodPx: number;
  trades: number;
}

interface Trade {
  id: string;
  t: number;
  book: string;
  sym: string;
  qty: number;
  px: number;
  fee: number;
  source: 'RFQ' | 'Hedge';
  ref: string;
  cpty: string;
}

const kindOf = (sym: string) => (bondByCusip.has(sym) ? (bondByCusip.get(sym)!.type === 'Bill' ? 'Bill' : bondByCusip.get(sym)!.type === 'TIPS' ? 'TIPS' : 'Cash') : stirBySym.has(sym) ? 'STIR' : 'Future');
const unitOf = (sym: string) => (bondByCusip.has(sym) ? '$mm' : 'contracts');

export function buildDesk(engine: Engine, server: MockServer, market: Market, live: LiveTables & { addSessionFor(sym: string): void }) {
  const rng = makeRng(hash('desk', Math.floor(market.sodMs / DAY)));
  const node = new MarkerSource(engine, 'desk (positions, RFQs)', 'source', 'the hypothetical desk: books, positions, client RFQs, executions', [live.sim]);

  // ---------------------------------------------------------------- seed positions
  const bond = (term: string, age = 0, type?: Bond['type']) => BONDS.find(b => b.term === term && b.age === age && (!type || b.type === type))!;
  const ctd = (root: string) => {
    const c = CONTRACTS.find(x => x.sym === root)!;
    return [...market.basket(c, Date.now())].sort((a, b) => a.implied - b.implied)[0];
  };
  const seed: Array<[string, string, number]> = [
    ['FRONT', bond('2Y').cusip, 350], ['FRONT', bond('2Y', 1).cusip, -150], ['FRONT', bond('3Y').cusip, 120], ['FRONT', bond('26W').cusip, 500],
    ['FRONT', bond('4W').cusip, -200], ['FRONT', 'ZT', -900],
    ['BELLY', bond('5Y').cusip, 250], ['BELLY', bond('5Y', 8).cusip, -100], ['BELLY', bond('7Y').cusip, 150], ['BELLY', 'ZF', -1200],
    ['TENS', bond('10Y').cusip, 200], ['TENS', bond('10Y', 1).cusip, -80], ['TENS', bond('10Y', 12).cusip, 60], ['TENS', 'ZN', -1100], ['TENS', 'TN', 150],
    ['LONG', bond('30Y').cusip, -60], ['LONG', bond('20Y').cusip, 90], ['LONG', bond('30Y', 18).cusip, 40], ['LONG', 'UB', -40], ['LONG', 'ZB', 30],
    ['TIPS', bond('10Y', 0, 'TIPS').cusip, 100], ['TIPS', bond('10Y').cusip, -95], ['TIPS', bond('5Y', 0, 'TIPS').cusip, 60], ['TIPS', bond('5Y').cusip, -55],
    ['RV', bond('5Y').cusip, 200], ['RV', bond('2Y').cusip, -240], ['RV', bond('10Y').cusip, -56], ['RV', bond('10Y', 2).cusip, 70], ['RV', bond('10Y', 3).cusip, -70],
    ['MACRO', 'ZN', 800], ['MACRO', 'UB', -100], ['MACRO', 'SR3H7', 2500], ['MACRO', 'SR3Z7', -1500], ['MACRO', 'ZQF7', 600],
  ];
  // the basis book: long each CTD, short the contract weighted by its conversion factor (face / $100k x CF)
  for (const root of ['ZN', 'ZF', 'ZB']) {
    const c = ctd(root);
    const face = root === 'ZB' ? 120 : root === 'ZF' ? 300 : 500;
    seed.push(['BASIS', c.bond.cusip, face], ['BASIS', root, -Math.round((face * 10) * c.cf)]);
  }
  const positions = new Map<string, Pos>();
  const key = (book: string, sym: string) => `${book}|${sym}`;
  for (const [book, sym, qty] of seed) {
    const k = key(book, sym);
    const p = positions.get(k) ?? { book, sym, qty: 0, sodQty: 0, sodPx: market.sodValue(sym), trades: 0 };
    p.qty += qty;
    p.sodQty += qty;
    positions.set(k, p);
  }
  const trades: Trade[] = [];

  // ---------------------------------------------------------------- instrument math
  const priceFactor = (sym: string) => (bondByCusip.has(sym) ? 10_000 : bySym.get(sym)?.multiplier ?? 1000); // $ per 1.00 of price per unit
  const tenorOf = (sym: string, now: number): number => {
    const b = bondByCusip.get(sym);
    if (b) return Math.max(1 / 365, (b.maturity - now) / YEAR);
    const s = stirBySym.get(sym);
    if (s) return Math.max(1 / 52, ((s.start + s.end) / 2 - now) / YEAR);
    const c = contractBySym.get(sym);
    if (c) {
      const x = [...market.basket(c, now)].sort((a, z) => a.implied - z.implied)[0];
      return x ? (x.bond.maturity - now) / YEAR : c.root.tenor;
    }
    return 5;
  };
  // risk per unit, refreshed each second: DV01 ($ per bp, positive = gains when yields fall), carry ($ per day), tenor
  let unitRisk = new Map<string, { dv01: number; carry: number; tenor: number; y: number; idio: number }>();
  const refreshRisk = (now: number) => {
    const out = new Map<string, { dv01: number; carry: number; tenor: number; y: number; idio: number }>();
    const settle = Math.floor(now / DAY) * DAY + DAY;
    const repo = market.repo();
    const syms = new Set([...positions.values()].map(p => p.sym));
    for (const sym of syms) {
      const b = bondByCusip.get(sym);
      if (b) {
        const y = market.yieldOf(sym);
        const r = risk(b, y, settle);
        const carry = (b.coupon / 100) * 1e6 / 365 - (repo / 100) * (r.dirty / 100) * 1e6 / 360; // per $mm per day
        out.set(sym, { dv01: r.dv01, carry, tenor: tenorOf(sym, now), y, idio: market.value(`${sym}.IDIO`) });
        continue;
      }
      const s = stirBySym.get(sym);
      if (s) {
        out.set(sym, { dv01: s.kind === 'SR3' ? 25 : 41.67, carry: 0, tenor: tenorOf(sym, now), y: 100 - market.value(sym), idio: NaN });
        continue;
      }
      const c = contractBySym.get(sym);
      if (c) {
        const x = [...market.basket(c, now)].sort((a, z) => a.implied - z.implied)[0];
        const cr = risk(x.bond, x.y, settle);
        out.set(sym, { dv01: ((cr.dv01 / x.cf) * c.root.multiplier) / 10_000, carry: 0, tenor: (x.bond.maturity - now) / YEAR, y: x.y, idio: NaN });
      }
    }
    unitRisk = out;
  };

  // ---------------------------------------------------------------- client RFQs
  const rfqTable = new RowSource(engine, 'rfq_blotter', [
    T('Time'), S('Id'), S('Client'), S('Venue'), S('Sym'), S('Security'), S('ClientSide'), D('SizeMM'), S('Level'), D('Yield'), S('Status'), S('Book'), D('CoverBp'), D('RespondSec'),
  ], { keyCols: ['Id'], kind: 'source', description: 'client RFQs today and how they ended (hypothetical)' });
  rfqTable.parents = [node];
  const tradeTable = new RowSource(engine, 'desk_trades', [
    T('Time'), S('Id'), S('Book'), S('Sym'), S('Security'), S('Side'), D('Qty'), S('Unit'), D('Price'), S('PriceText'), S('Source'), S('Ref'), S('Counterparty'), D('Fee'),
  ], { kind: 'op', description: "the desk's executions today: won RFQs and their futures hedges" });
  tradeTable.parents = [rfqTable];
  const flowTable = new RowSource(engine, 'rfq_flow', [T('Timestamp'), S('Sym'), I('Requests'), D('NotionalMM'), D('DoneMM'), D('ClientBuyMM'), D('ClientSellMM')], {
    keyCols: ['Timestamp', 'Sym'], kind: 'op', description: 'RFQ volume per CUSIP per 5 minutes',
  });
  flowTable.parents = [rfqTable];
  const stats = new Map<string, { n: number; notional: number; done: number; buy: number; sell: number }>();
  const street = new Map<string, number>(); // $bn traded in the market today (estimate)

  const liq = BONDS.map(b => (b.age === 0 ? 6 : b.age <= 2 ? 2 : 1) * (b.type === 'Bill' ? 1.5 : 1));
  const liqSum = liq.reduce((a, b) => a + b, 0);
  const pickBond = () => {
    let u = rng.u() * liqSum;
    for (let i = 0; i < BONDS.length; i++) if ((u -= liq[i]) <= 0) return BONDS[i];
    return BONDS[0];
  };
  const bookFor = (b: Bond) => {
    if (b.type === 'TIPS') return 'TIPS';
    const t = (b.maturity - Date.now()) / YEAR;
    return t < 3.5 ? 'FRONT' : t < 8 ? 'BELLY' : t < 12 ? 'TENS' : 'LONG';
  };
  const hedgeFor = (tenor: number) => (tenor < 3 ? 'ZT' : tenor < 6 ? 'ZF' : tenor < 8.5 ? 'ZN' : tenor < 13 ? 'TN' : tenor < 24 ? 'ZB' : 'UB');

  let nextRfq = 1;
  let nextTrade = 1;
  const book = (t: Trade) => {
    trades.push(t);
    const k = key(t.book, t.sym);
    const p = positions.get(k) ?? { book: t.book, sym: t.sym, qty: 0, sodQty: 0, sodPx: market.sodValue(t.sym), trades: 0 };
    p.qty += t.qty;
    p.trades++;
    positions.set(k, p);
    const i = bySym.get(t.sym);
    tradeTable.put([{
      Time: instant(t.t), Id: t.id, Book: t.book, Sym: t.sym, Security: i?.name ?? t.sym, Side: t.qty > 0 ? 'Buy' : 'Sell', Qty: Math.abs(t.qty), Unit: unitOf(t.sym),
      Price: t.px, PriceText: priceText(i, t.px), Source: t.source, Ref: t.ref, Counterparty: t.cpty, Fee: t.fee,
    }]);
  };

  /** one client RFQ at time t: its outcome, and if the desk wins it, the trade and maybe a futures hedge */
  const rfq = (t: number, historical: boolean) => {
    const b = pickBond();
    const px = historical ? market.valueAt(b.cusip, t) : market.value(b.cusip);
    const y = historical ? market.valueAt(`${b.cusip}.Y`, t) : market.yieldOf(b.cusip);
    if (!Number.isFinite(px)) return;
    const size = Math.max(1, Math.round(Math.exp(2.2 + rng.n() * 0.9) * (b.age === 0 ? 1.6 : 1)));
    const clientBuys = rng.u() < 0.5;
    const u = rng.u();
    const status = u < 0.36 ? 'Done' : u < 0.8 ? 'Traded away' : u < 0.92 ? 'Covered' : 'Passed';
    const id = `R${String(nextRfq++).padStart(5, '0')}`;
    const half = (b.age === 0 ? 1 / 256 : 1 / 128) / 2;
    const level = clientBuys ? px + half : px - half;
    const bk = bookFor(b);
    const cover = status === 'Covered' || status === 'Traded away' ? Math.round(rng.u() * 10) / 10 : null;
    rfqTable.put([{
      Time: instant(t), Id: id, Client: CLIENTS[Math.floor(rng.u() * CLIENTS.length)], Venue: VENUES[Math.floor(rng.u() * VENUES.length)], Sym: b.cusip, Security: b.name,
      ClientSide: clientBuys ? 'Buy' : 'Sell', SizeMM: size, Level: priceText(bySym.get(b.cusip), level), Yield: Number.isFinite(y) ? +y.toFixed(4) : null,
      Status: status, Book: status === 'Done' ? bk : '', CoverBp: cover, RespondSec: Math.round((1.5 + rng.u() * 6) * 10) / 10,
    }]);
    rfqTable.cap(2500);
    const s = stats.get(b.cusip) ?? { n: 0, notional: 0, done: 0, buy: 0, sell: 0 };
    s.n++;
    s.notional += size;
    if (clientBuys) s.buy += size;
    else s.sell += size;
    if (status === 'Done') s.done += size;
    stats.set(b.cusip, s);
    const bucket = Math.floor(t / 300_000) * 300_000;
    const fk = flowTable.find([instant(bucket), b.cusip]);
    const prev = fk != null ? flowTable.row(fk) : undefined;
    flowTable.put([{
      Timestamp: instant(bucket), Sym: b.cusip, Requests: Number(prev?.Requests ?? 0) + 1, NotionalMM: Number(prev?.NotionalMM ?? 0) + size,
      DoneMM: Number(prev?.DoneMM ?? 0) + (status === 'Done' ? size : 0), ClientBuyMM: Number(prev?.ClientBuyMM ?? 0) + (clientBuys ? size : 0),
      ClientSellMM: Number(prev?.ClientSellMM ?? 0) + (clientBuys ? 0 : size),
    }]);
    if (status !== 'Done') return;
    // the desk takes the other side; most of the time it hedges the DV01 with the matching future
    book({ id: `T${String(nextTrade++).padStart(5, '0')}`, t, book: bk, sym: b.cusip, qty: clientBuys ? -size : size, px: level, fee: 0, source: 'RFQ', ref: id, cpty: 'client' });
    if (rng.u() < 0.6 && b.type !== 'Bill') {
      const tenor = (b.maturity - t) / YEAR;
      const fut = hedgeFor(tenor);
      const ur = unitRisk.get(b.cusip);
      const fr = unitRisk.get(fut);
      const bondDv01 = (ur?.dv01 ?? tenor * 90) * size;
      const futDv01 = fr?.dv01 ?? tenor * 9;
      const n = Math.round(bondDv01 / futDv01);
      if (n) {
        const fpx = historical ? market.valueAt(fut, t + 2000) : market.value(fut);
        book({ id: `T${String(nextTrade++).padStart(5, '0')}`, t: t + 2000, book: bk, sym: fut, qty: clientBuys ? n : -n, px: fpx, fee: Math.abs(n) * 1.25, source: 'Hedge', ref: id, cpty: 'CME' });
      }
    }
  };

  // ---------------------------------------------------------------- per-CUSIP reference: SOMA, auctions
  const somaPct = new Map(BONDS.map(b => {
    const g = makeRng(hash('soma', b.cusip));
    return [b.cusip, b.type === 'Bill' ? 4 + g.u() * 8 : b.age === 0 ? 1 + g.u() * 4 : b.age <= 3 ? 8 + g.u() * 10 : 18 + g.u() * 22];
  }));
  const master = new RowSource(engine, 'security_master', [
    S('Cusip'), S('Security'), S('Type'), S('Term'), S('Status'), D('Coupon'), T('Maturity'), T('Issue'), D('OutstandingBn'), D('SomaBn'), D('SomaPct'), D('FreeFloatBn'),
    D('StreetVolBn'), D('DeskPositionMM'), D('IdioBp'), S('Deliverable'), S('CTDFor'), I('RfqCount'), D('RfqNotionalMM'), D('HitRatioPct'),
  ], { keyCols: ['Cusip'], kind: 'op', description: 'per CUSIP: amount outstanding, SOMA holdings, free float, street volume, desk position, idio, deliverability, RFQ flow' });
  master.parents = [rfqTable, live.quotes];
  const auctions = new RowSource(engine, 'cusip_auctions', [
    S('Cusip'), S('Security'), T('Date'), S('Kind'), D('SizeBn'), D('HighYield'), D('WIYield'), D('TailBp'), D('BidToCover'), D('IndirectPct'), D('DirectPct'), D('DealerPct'),
  ], { kind: 'source', description: "each CUSIP's original auction and reopenings (mock)" });
  const aRows: Row[] = [];
  for (const b of BONDS) {
    const reopenings = b.term === '10Y' || b.term === '30Y' || b.term === '20Y' ? 2 : 0;
    const total = b.outstanding;
    for (let k = 0; k <= reopenings; k++) {
      const t = b.issue - 5 * DAY + k * 30 * DAY;
      if (t > Date.now()) continue;
      const g = makeRng(hash('auc', b.cusip, k));
      const tail = g.n() * 0.9;
      const wi = (market.valueAt(`${b.cusip}.Y`, t) || b.coupon) - tail / 100;
      aRows.push({
        Cusip: b.cusip, Security: b.name, Date: instant(t), Kind: k === 0 ? 'Original' : `Reopening ${k}`,
        SizeBn: Math.round((total / (reopenings ? 2.4 : 1)) * (k === 0 ? 1 : 0.7) * 10) / 10,
        HighYield: Number.isFinite(wi) ? +(wi + tail / 100).toFixed(3) : null, WIYield: Number.isFinite(wi) ? +wi.toFixed(3) : null, TailBp: +tail.toFixed(1),
        BidToCover: +(2.55 - tail * 0.12 + g.n() * 0.08).toFixed(2), IndirectPct: +(66 - tail * 4 + g.n() * 3).toFixed(1), DirectPct: +(18 + g.n() * 2.5).toFixed(1),
        DealerPct: +(15 + tail * 3 + g.n() * 1.5).toFixed(1),
      });
    }
  }
  auctions.put(aRows);
  const deliverable = new Map<string, string[]>();
  for (const c of CONTRACTS) for (const x of c.basket) deliverable.set(x.bond.cusip, [...(deliverable.get(x.bond.cusip) ?? []), c.sym]);

  // ---------------------------------------------------------------- the tables computed from positions
  const booksTable = new RowSource(engine, 'books', [
    S('Book'), S('Name'), S('Strategy'), S('Trader'), I('Positions'), D('GrossMM'), D('DV01'), D('DV01Limit'), D('LimitUsePct'), D('VaR99'), D('PnlToday'), I('TradesToday'),
  ], { keyCols: ['Book'], kind: 'op', description: 'each book: size, DV01 against its limit, VaR, P&L today' });
  const posTable = new RowSource(engine, 'positions', [
    S('Book'), S('Sym'), S('Security'), S('Kind'), D('Qty'), S('Unit'), D('Price'), S('PriceText'), D('Yield'), D('DV01'), D('Tenor'), D('IdioBp'), D('CarryDay'),
    D('PnlToday'), D('PnlSod'), D('PnlNew'), D('SodQty'), I('Trades'),
  ], { keyCols: ['Book', 'Sym'], kind: 'op', description: 'positions per book: risk and P&L today' });
  const riskTable = new RowSource(engine, 'book_risk', [
    S('Book'), D('DV01'), ...BUCKETS.map(([b]) => D(`KRD_${b}`)), D('Level'), D('Slope'), D('Curvature'), D('Policy'), D('VaR99'), D('IdioRisk'),
  ], { keyCols: ['Book'], kind: 'op', description: 'per book: DV01 by key-rate bucket, curve-factor exposures ($ per bp of each factor), 1-day 99% VaR' });
  const pnlTable = new RowSource(engine, 'pnl_attribution', [S('Book'), D('Total'), ...COMPONENTS.map(c => D(c))], {
    keyCols: ['Book'], kind: 'op', description: 'P&L since 07:00 ET by source: carry, roll, curve factors, idio, basis, new trades, fees, residual',
  });
  const seriesTable = new RowSource(engine, 'pnl_timeseries', [T('Timestamp'), S('Book'), D('Pnl')], { kind: 'op', description: "each book's P&L through the day" });
  for (const t of [booksTable, posTable, riskTable, pnlTable]) t.parents = [node, tradeTable, live.quotes];
  seriesTable.parents = [pnlTable];
  for (const [name, t] of Object.entries({ books: booksTable, positions: posTable, book_risk: riskTable, pnl_attribution: pnlTable, pnl_timeseries: seriesTable, rfq_blotter: rfqTable, desk_trades: tradeTable, rfq_flow: flowTable, security_master: master, cusip_auctions: auctions }))
    server.publishTable(name, t);

  /** the P&L of every position now, split by source; and the books' risk */
  const compute = (now: number) => {
    const sod = market.sod;
    if (!sod) return;
    refreshRisk(now);
    const elapsedDays = Math.max(0, (now - sod.ms) / DAY);
    const dF = { level: market.F[0] - sod.F[0], slope: market.F[1] - sod.F[1], curvature: market.F[2] - sod.F[2], policy: market.F[3] - sod.F[3] };
    const byBook = new Map<string, Record<Component | 'Total', number>>();
    const riskByBook = new Map<string, Record<string, number>>();
    const idioVar = new Map<string, number>();
    const posRows: Row[] = [];
    const zero = () => Object.fromEntries([...COMPONENTS, 'Total'].map(c => [c, 0])) as Record<Component | 'Total', number>;
    const newPnl = new Map<string, number>();
    const fees = new Map<string, number>();
    for (const t of trades) {
      const k = key(t.book, t.sym);
      const px = market.value(t.sym);
      newPnl.set(k, (newPnl.get(k) ?? 0) + t.qty * (px - t.px) * priceFactor(t.sym));
      fees.set(t.book, (fees.get(t.book) ?? 0) + t.fee);
    }
    for (const p of positions.values()) {
      const ur = unitRisk.get(p.sym);
      const px = market.value(p.sym);
      const f = priceFactor(p.sym);
      const a = byBook.get(p.book) ?? zero();
      byBook.set(p.book, a);
      // start-of-day position: actual P&L, then the curve factors' share at its tenor
      const sodPnl = Number.isFinite(p.sodPx) ? p.sodQty * (px - p.sodPx) * f : 0;
      const dv01Sod = (ur?.dv01 ?? 0) * p.sodQty;
      const w = loading(ur?.tenor ?? 5);
      const parts = { Level: -dv01Sod * dF.level * w[1] * 100, Slope: -dv01Sod * dF.slope * w[2] * 100, Curvature: -dv01Sod * dF.curvature * w[3] * 100, Policy: -dv01Sod * dF.policy * w[4] * 100 };
      const curve = parts.Level + parts.Slope + parts.Curvature + parts.Policy;
      const isBond = bondByCusip.has(p.sym);
      let idio = 0;
      let basis = 0;
      let carry = 0;
      let roll = 0;
      if (isBond) {
        const dy = (market.yieldOf(p.sym) - market.sodYield(p.sym)) * 100; // bp
        carry = (ur?.carry ?? 0) * p.sodQty * elapsedDays;
        // roll: the start-of-day curve, read a little shorter (the bond aged since the open)
        const tenorSod = (ur?.tenor ?? 5) + elapsedDays / 365.25;
        const wSod = loading(tenorSod);
        let dyRoll = 0;
        for (let fct = 0; fct < 5; fct++) dyRoll += (w[fct] - wSod[fct]) * (fct === 0 ? 1 : sod.F[fct - 1]);
        roll = -dv01Sod * dyRoll * 100;
        // idio: the yield change the curve and the roll don't explain
        idio = -dv01Sod * dy - curve - roll;
      } else {
        basis = sodPnl - curve;
      }
      // convexity and pull to par: what the DV01 approximation leaves
      const residual = sodPnl - (curve + idio + basis + roll);
      const nt = newPnl.get(key(p.book, p.sym)) ?? 0;
      a.Level += parts.Level;
      a.Slope += parts.Slope;
      a.Curvature += parts.Curvature;
      a.Policy += parts.Policy;
      a.Idio += idio;
      a.Basis += basis;
      a.Carry += carry;
      a.Roll += roll;
      a.Residual += residual;
      a.NewTrades += nt;
      const total = sodPnl + carry + nt;
      // risk on the current position
      const dv01 = (ur?.dv01 ?? 0) * p.qty;
      const r = riskByBook.get(p.book) ?? { DV01: 0, Level: 0, Slope: 0, Curvature: 0, Policy: 0, Gross: 0, n: 0, ...Object.fromEntries(BUCKETS.map(([b]) => [`KRD_${b}`, 0])) };
      riskByBook.set(p.book, r);
      if (p.qty) r.n++;
      r.DV01 += dv01;
      // $mm face: cash as held; a Treasury future's face is its multiplier x 100 ($100k for ZN), SOFR futures $1mm
      r.Gross += isBond ? Math.abs(p.qty) : stirBySym.has(p.sym) ? Math.abs(p.qty) : Math.abs(p.qty) * (f / 10_000);
      r.Level += dv01 * w[1];
      r.Slope += dv01 * w[2];
      r.Curvature += dv01 * w[3];
      r.Policy += dv01 * w[4];
      const tenor = ur?.tenor ?? 5;
      for (let bi = 0; bi < BUCKETS.length; bi++) {
        const [name, at] = BUCKETS[bi];
        const prev = bi > 0 ? BUCKETS[bi - 1][1] : -Infinity;
        const next = bi < BUCKETS.length - 1 ? BUCKETS[bi + 1][1] : Infinity;
        let wt = 0;
        if (tenor <= at && tenor > prev) wt = prev === -Infinity ? 1 : (tenor - prev) / (at - prev);
        else if (tenor > at && tenor < next) wt = next === Infinity ? 1 : (next - tenor) / (next - at);
        else if (bi === 0 && tenor <= at) wt = 1;
        r[`KRD_${name}`] += dv01 * wt;
      }
      if (isBond) idioVar.set(p.book, (idioVar.get(p.book) ?? 0) + (dv01 * IDIO_DAILY_BP) ** 2);
      const i = bySym.get(p.sym);
      posRows.push({
        Book: p.book, Sym: p.sym, Security: i?.name ?? p.sym, Kind: kindOf(p.sym), Qty: p.qty, Unit: unitOf(p.sym), Price: px, PriceText: priceText(i, px),
        Yield: ur && Number.isFinite(ur.y) ? +ur.y.toFixed(4) : null, DV01: Math.round(dv01), Tenor: +(ur?.tenor ?? 0).toFixed(2),
        IdioBp: ur && Number.isFinite(ur.idio) ? +ur.idio.toFixed(2) : null, CarryDay: Math.round((ur?.carry ?? 0) * p.qty),
        PnlToday: Math.round(total), PnlSod: Math.round(sodPnl + carry), PnlNew: Math.round(nt), SodQty: p.sodQty, Trades: p.trades,
      });
    }
    posTable.put(posRows);
    const all = zero();
    const pnlRows: Row[] = [];
    for (const b of BOOKS) {
      const a = byBook.get(b.id) ?? zero();
      a.Fees = -(fees.get(b.id) ?? 0);
      a.Total = COMPONENTS.reduce((s, c) => s + (c === 'Residual' ? a.Residual : a[c]), 0);
      for (const c of [...COMPONENTS, 'Total'] as const) all[c] += a[c];
      pnlRows.push({ Book: b.id, ...Object.fromEntries(Object.entries(a).map(([k, v]) => [k, Math.round(v)])) });
    }
    pnlRows.push({ Book: 'ALL', ...Object.fromEntries(Object.entries(all).map(([k, v]) => [k, Math.round(v)])) });
    pnlTable.put(pnlRows);
    const riskRows: Row[] = [];
    const bookRows: Row[] = [];
    const tot: Record<string, number> = {};
    let idioAll = 0;
    for (const b of BOOKS) {
      const r: Record<string, number> = riskByBook.get(b.id) ?? {};
      const g = (k: string) => r[k] ?? 0;
      const fv = Math.sqrt(
        (g('Level') * FACTOR_DAILY_BP.level) ** 2 + (g('Slope') * FACTOR_DAILY_BP.slope) ** 2 + (g('Curvature') * FACTOR_DAILY_BP.curvature) ** 2 + (g('Policy') * FACTOR_DAILY_BP.policy) ** 2,
      );
      const iv = Math.sqrt(idioVar.get(b.id) ?? 0);
      idioAll += idioVar.get(b.id) ?? 0;
      const var99 = 2.33 * Math.sqrt(fv * fv + iv * iv);
      riskRows.push({ Book: b.id, DV01: Math.round(r.DV01 ?? 0), ...Object.fromEntries(BUCKETS.map(([n]) => [`KRD_${n}`, Math.round(r[`KRD_${n}`] ?? 0)])), Level: Math.round(r.Level ?? 0), Slope: Math.round(r.Slope ?? 0), Curvature: Math.round(r.Curvature ?? 0), Policy: Math.round(r.Policy ?? 0), VaR99: Math.round(var99), IdioRisk: Math.round(2.33 * iv) });
      for (const k of ['DV01', 'Level', 'Slope', 'Curvature', 'Policy', ...BUCKETS.map(([n]) => `KRD_${n}`)]) tot[k] = (tot[k] ?? 0) + (r[k] ?? 0);
      const a = byBook.get(b.id);
      bookRows.push({
        Book: b.id, Name: b.name, Strategy: b.strategy, Trader: b.trader, Positions: r.n ?? 0, GrossMM: Math.round(r.Gross ?? 0), DV01: Math.round(r.DV01 ?? 0),
        DV01Limit: b.dv01Limit, LimitUsePct: Math.round((100 * Math.abs(r.DV01 ?? 0)) / b.dv01Limit), VaR99: Math.round(var99), PnlToday: Math.round(pnlRows.find(x => x.Book === b.id)!.Total as number),
        TradesToday: [...positions.values()].filter(p => p.book === b.id).reduce((s, p) => s + p.trades, 0),
      });
      void a;
    }
    const fvAll = Math.sqrt((tot.Level * FACTOR_DAILY_BP.level) ** 2 + (tot.Slope * FACTOR_DAILY_BP.slope) ** 2 + (tot.Curvature * FACTOR_DAILY_BP.curvature) ** 2 + (tot.Policy * FACTOR_DAILY_BP.policy) ** 2);
    const varAll = 2.33 * Math.sqrt(fvAll * fvAll + idioAll);
    riskRows.push({ Book: 'ALL', ...Object.fromEntries(Object.entries(tot).map(([k, v]) => [k, Math.round(v)])), VaR99: Math.round(varAll), IdioRisk: Math.round(2.33 * Math.sqrt(idioAll)) });
    bookRows.push({
      Book: 'ALL', Name: 'Desk', Strategy: 'All books', Trader: '', Positions: bookRows.reduce((s, r) => s + Number(r.Positions), 0), GrossMM: bookRows.reduce((s, r) => s + Number(r.GrossMM), 0),
      DV01: Math.round(tot.DV01), DV01Limit: BOOKS.reduce((s, b) => s + b.dv01Limit, 0), LimitUsePct: Math.round((100 * Math.abs(tot.DV01)) / 1_500_000), VaR99: Math.round(varAll),
      PnlToday: Math.round(all.Total), TradesToday: trades.length,
    });
    riskTable.put(riskRows);
    booksTable.put(bookRows);
    // per CUSIP
    const deskPos = new Map<string, number>();
    for (const p of positions.values()) deskPos.set(p.sym, (deskPos.get(p.sym) ?? 0) + p.qty);
    const ctdFor = new Map<string, string[]>();
    for (const c of CONTRACTS) {
      const x = [...market.basket(c, now)].sort((a, z) => a.implied - z.implied)[0];
      if (x) ctdFor.set(x.bond.cusip, [...(ctdFor.get(x.bond.cusip) ?? []), c.sym]);
    }
    master.put(BONDS.filter(b => b.issue <= now).map(b => {
      const s = stats.get(b.cusip);
      const pct = somaPct.get(b.cusip)!;
      return {
        Cusip: b.cusip, Security: b.name, Type: b.type, Term: b.term, Status: b.age === 0 ? 'OTR' : b.age === 1 ? '1st off' : b.age === 2 ? '2nd off' : 'old',
        Coupon: b.coupon, Maturity: instant(b.maturity), Issue: instant(b.issue), OutstandingBn: b.outstanding, SomaBn: +((b.outstanding * pct) / 100).toFixed(1), SomaPct: +pct.toFixed(1),
        FreeFloatBn: +(b.outstanding * (1 - pct / 100)).toFixed(1), StreetVolBn: +(street.get(b.cusip) ?? 0).toFixed(2), DeskPositionMM: deskPos.get(b.cusip) ?? 0,
        IdioBp: +market.value(`${b.cusip}.IDIO`).toFixed(2), Deliverable: (deliverable.get(b.cusip) ?? []).join(', '), CTDFor: (ctdFor.get(b.cusip) ?? []).join(', '),
        RfqCount: s?.n ?? 0, RfqNotionalMM: s?.notional ?? 0, HitRatioPct: s?.n ? Math.round((100 * s.done) / s.notional) : null,
      };
    }));
    return pnlRows;
  };

  // ---------------------------------------------------------------- the day so far, then live
  const seedDay = () => {
    const sod = market.sod;
    if (!sod) return;
    trades.length = 0;
    const now = Date.now();
    refreshRisk(now);
    // street volume so far: $bn per hour by liquidity
    for (let i = 0; i < BONDS.length; i++) street.set(BONDS[i].cusip, (liq[i] * 1.6 * (now - sod.ms)) / 3600_000);
    // RFQs since the open, one every ~25 s on average
    for (let t = sod.ms + Math.floor(rng.u() * 25_000); t < now; t += Math.floor(-Math.log(1 - rng.u()) * 25_000) + 500) rfq(t, true);
    // the P&L path, every 5 minutes, from the positions held then
    const rows: Row[] = [];
    for (let t = sod.ms + 300_000; t < now; t += 300_000) {
      const per = new Map<string, number>();
      for (const p of positions.values()) {
        const px = market.valueAt(p.sym, t);
        if (!Number.isFinite(px) || !Number.isFinite(p.sodPx)) continue;
        per.set(p.book, (per.get(p.book) ?? 0) + p.sodQty * (px - p.sodPx) * priceFactor(p.sym));
      }
      for (const tr of trades) {
        if (tr.t > t) continue;
        const px = market.valueAt(tr.sym, t);
        if (Number.isFinite(px)) per.set(tr.book, (per.get(tr.book) ?? 0) + tr.qty * (px - tr.px) * priceFactor(tr.sym) - tr.fee);
      }
      let all = 0;
      for (const b of BOOKS) {
        const v = per.get(b.id) ?? 0;
        all += v;
        rows.push({ Timestamp: instant(t), Book: b.id, Pnl: Math.round(v) });
      }
      rows.push({ Timestamp: instant(t), Book: 'ALL', Pnl: Math.round(all) });
    }
    seriesTable.put(rows);
  };
  seedDay();
  compute(Date.now());

  let lastCompute = 0;
  let lastSeries = 0;
  let nextAt = Date.now() + 3000;
  engine.addTicker('desk', now => {
    const t0 = performance.now();
    let n = 0;
    while (now >= nextAt) {
      rfq(nextAt, false);
      n++;
      nextAt += Math.floor(-Math.log(1 - rng.u()) * 25_000) + 500;
    }
    // the street trades all day
    for (let i = 0; i < BONDS.length; i++) street.set(BONDS[i].cusip, (street.get(BONDS[i].cusip) ?? 0) + (liq[i] * 1.6 * (0.5 + rng.u())) * (engine.intervalMs / 3600_000));
    if (now - lastCompute >= 1000) {
      lastCompute = now;
      const rows = compute(now);
      if (rows && now - lastSeries >= 30_000) {
        lastSeries = now;
        seriesTable.put(rows.map(r => ({ Timestamp: instant(now), Book: r.Book, Pnl: r.Total })));
      }
    }
    node.tick(performance.now() - t0, n);
  });
  // a new trading day: today's positions become the start of the day
  market.onSod.add(() => {
    for (const p of positions.values()) {
      p.sodQty = p.qty;
      p.sodPx = market.sodValue(p.sym);
      p.trades = 0;
    }
    trades.length = 0;
  });

  // ---------------------------------------------------------------- one security, everything about it
  const security = (sym: string) => {
    const now = Date.now();
    let cusip = sym.replace(/\.(Y|IDIO)$/, '');
    const fut = contractBySym.get(cusip);
    let via: string | null = null;
    if (fut) {
      const x = [...market.basket(fut, now)].sort((a, z) => a.implied - z.implied)[0];
      via = `CTD of ${fut.sym}`;
      cusip = x.bond.cusip;
    }
    const b = bondByCusip.get(cusip);
    if (!b) return null;
    const mk = master.find([cusip]);
    const m = mk != null ? master.row(mk)! : {};
    const ur = unitRisk.get(cusip);
    const y = market.yieldOf(cusip);
    const settle = Math.floor(now / DAY) * DAY + DAY;
    const r = risk(b, y, settle);
    const px = market.value(cusip);
    const sodPx = market.sodValue(cusip);
    const books = [...positions.values()].filter(p => p.sym === cusip && (p.qty || p.sodQty)).map(p => ({
      book: p.book, qty: p.qty, sod_qty: p.sodQty, dv01: Math.round(r.dv01 * p.qty), pnl_today: Math.round(p.sodQty * (px - p.sodPx) * 10_000 + trades.filter(t => t.book === p.book && t.sym === cusip).reduce((s, t) => s + t.qty * (px - t.px) * 10_000, 0)), trades: p.trades,
    }));
    // idio: now, a day ago, and its 20-day z-score (daily bars)
    const d1 = market.tiers.d1;
    const ki = market.index.get(`${cusip}.IDIO`)!;
    const closes: number[] = [];
    for (let i = Math.max(0, d1.n - 21); i < d1.n; i++) if (Number.isFinite(d1.series[ki].c[i])) closes.push(d1.series[ki].c[i]);
    const idio = market.value(`${cusip}.IDIO`);
    const mean = closes.reduce((a, c) => a + c, 0) / Math.max(1, closes.length);
    const sd = Math.sqrt(closes.reduce((a, c) => a + (c - mean) ** 2, 0) / Math.max(1, closes.length));
    const basketRows = server.tableSource('deliverable_basket');
    const ctd: Row[] = [];
    if (basketRows) for (const k of Array.from(basketRows.keys())) if (basketRows.get(k, 'Cusip') === cusip) ctd.push(Object.fromEntries(basketRows.columns.map(c => [c.name, basketRows.get(k, c.name)])));
    const auc = auctions.all().filter(a => a.Cusip === cusip).map(a => ({ ...a, Date: String(a.Date).slice(0, 10) }));
    const blotter = Array.from(rfqTable.keys()).map(k => rfqTable.row(k)!).filter(x => x.Sym === cusip).slice(-15).reverse().map(x => ({ ...x, Time: String(x.Time) }));
    const s = stats.get(cusip);
    const fc = fut ?? CONTRACTS.find(c => c.front && c.basket.some(x => x.bond.cusip === cusip));
    return {
      cusip, security: b.name, type: b.type, term: b.term, coupon: b.coupon, maturity: new Date(b.maturity).toISOString().slice(0, 10), issue: new Date(b.issue).toISOString().slice(0, 10),
      status: m.Status, via, price: priceText(bySym.get(cusip), px), price_decimal: px, yield: +y.toFixed(4), chg_bp: Number.isFinite(sodPx) ? +((y - market.sodYield(cusip)) * 100).toFixed(2) : null,
      dv01_per_mm: +r.dv01.toFixed(2), mod_duration: +r.modDur.toFixed(3), accrued: +accrued(b, settle).toFixed(5),
      amount: { outstanding_bn: b.outstanding, soma_bn: m.SomaBn, soma_pct: m.SomaPct, free_float_bn: m.FreeFloatBn },
      desk: { position_mm: books.reduce((a, x) => a + x.qty, 0), dv01: books.reduce((a, x) => a + x.dv01, 0), pnl_today: books.reduce((a, x) => a + x.pnl_today, 0), books },
      idio: { bp: +idio.toFixed(2), z20: sd ? +((idio - mean) / sd).toFixed(2) : 0, chg_1d_bp: closes.length > 1 ? +(idio - closes[closes.length - 2]).toFixed(2) : null, rich_cheap: idio < -0.5 ? 'rich' : idio > 0.5 ? 'cheap' : 'fair' },
      ctd: ctd.map(x => ({ contract: x.Contract, cf: x.CF, rank: x.Rank, ctd: x.CTD, net_basis_32: x.NetBasis32, gross_basis_32: x.GrossBasis32, implied_repo: x.ImpliedRepo })),
      hedge_future: fc?.sym ?? null,
      auctions: auc,
      rfq: { today: s?.n ?? 0, notional_mm: s?.notional ?? 0, done_mm: s?.done ?? 0, client_buy_mm: s?.buy ?? 0, client_sell_mm: s?.sell ?? 0, hit_ratio_pct: s?.notional ? Math.round((100 * s.done) / s.notional) : null, recent: blotter },
      market: { street_volume_bn: +(street.get(cusip) ?? 0).toFixed(2), listed: fut ? { sym: fut.sym, volume: market.dayBar(fut.sym)?.volume ?? 0 } : null },
      charts: { price: cusip, yield: `${cusip}.Y`, idio: `${cusip}.IDIO` },
      carry_per_mm_day: ur ? Math.round(ur.carry) : null,
    };
  };

  return { security, positions, trades };
}
