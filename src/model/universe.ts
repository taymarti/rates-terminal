/**
 * The mock universe: every tradable or chartable symbol, the Treasury cash bonds by CUSIP, the futures
 * contracts with their deliverable baskets, and the exchange calendars. Mock data, shaped like the real
 * thing as of late November 2026 (Dec/Mar Treasury futures roll under way, first notice Nov 30).
 */
import type { SessionSpec } from '@acme/chart-ext';
import { conversionFactor, type Bond } from './bonds';

export type Kind = 'yield' | 'spread' | 'rate' | 'future' | 'stir' | 'bond' | 'etf' | 'index' | 'fx' | 'equity' | 'commodity' | 'swap';

export interface Instrument {
  sym: string;
  name: string;
  /** dataset (catalog group) it belongs to */
  dataset: string;
  kind: Kind;
  /** what the price is in */
  unit: '%' | 'bp' | 'price' | 'index';
  /** price increment (null: any) */
  tick: number | null;
  /** 32nds notation (Treasury futures and cash prices) */
  thirtySeconds?: boolean;
  decimals: number;
  hasVolume: boolean;
  session: string;
  /** years, for curve points */
  tenor?: number;
  /** futures: root, contract month code, $ per 1.00 of price */
  root?: string;
  month?: string;
  multiplier?: number;
  /** cash bonds */
  bond?: Bond;
  /** an expression over other symbols (spreads typed in the search, named curve spreads) */
  expr?: string;
  /** typical volume per minute (traded instruments) */
  volPerMin?: number;
  description?: string;
}

// ----------------------------------------------------------------------------- calendars

const cme = (rth: [string, string], name: string): SessionSpec => ({ tz: 'America/Chicago', open: '17:00', close: '16:00', days: [1, 2, 3, 4, 5], rth, name });

export const SESSIONS: Record<string, SessionSpec> = {
  CBOT_UST: cme(['07:20', '14:00'], 'CBOT Globex, Treasury futures'),
  CME_STIR: cme(['07:20', '14:00'], 'CME Globex, SOFR and Fed funds futures'),
  CME_EQ: cme(['08:30', '15:00'], 'CME Globex, equity index'),
  NYMEX: cme(['08:00', '13:30'], 'NYMEX Globex, crude oil'),
  COMEX: cme(['07:20', '12:30'], 'COMEX Globex, gold'),
  CME_FX: cme(['07:20', '14:00'], 'CME Globex, FX'),
  UST_CASH: { tz: 'America/New_York', open: '18:00', close: '17:30', days: [1, 2, 3, 4, 5], rth: ['08:20', '15:00'], name: 'US Treasuries, cash (BrokerTec/eSpeed)' },
  SWAPS: { tz: 'America/New_York', open: '19:00', close: '17:00', days: [1, 2, 3, 4, 5], rth: ['08:00', '16:00'], name: 'SOFR swaps (SEF)' },
  NYSE: { tz: 'America/New_York', open: '04:00', close: '20:00', days: [1, 2, 3, 4, 5], rth: ['09:30', '16:00'], name: 'NYSE Arca' },
  CBOE: { tz: 'America/New_York', open: '03:15', close: '16:15', days: [1, 2, 3, 4, 5], rth: ['09:30', '16:15'], name: 'Cboe index' },
  EUREX: { tz: 'Europe/Berlin', open: '01:10', close: '22:00', days: [1, 2, 3, 4, 5], rth: ['08:00', '17:30'], name: 'Eurex, Euro-Bund' },
  FIXING: { tz: 'America/New_York', open: '08:00', close: '17:00', days: [1, 2, 3, 4, 5], rth: ['08:00', '09:00'], name: 'NY Fed reference rates (published 08:00 ET)' },
};

// ----------------------------------------------------------------------------- cash Treasuries

const d = (s: string) => Date.parse(`${s}T00:00:00Z`);

/** A real-looking CUSIP: prefix + 2 characters + the check digit. */
function cusip(prefix: string, n: number): string {
  const alnum = '0123456789ABCDEFGHJKLMNPQRSTUVWXYZ';
  const body = prefix + alnum[Math.floor(n / alnum.length) % alnum.length] + alnum[n % alnum.length];
  let sum = 0;
  for (let i = 0; i < 8; i++) {
    const c = body[i];
    let v = /\d/.test(c) ? Number(c) : c.charCodeAt(0) - 55;
    if (i % 2 === 1) v *= 2;
    sum += Math.floor(v / 10) + (v % 10);
  }
  return body + String((10 - (sum % 10)) % 10);
}

const frac = (c: number) => {
  const w = Math.floor(c);
  const f = c - w;
  const map: Record<string, string> = { '0': '', '0.125': ' 1/8', '0.25': ' 1/4', '0.375': ' 3/8', '0.5': ' 1/2', '0.625': ' 5/8', '0.75': ' 3/4', '0.875': ' 7/8' };
  return `${w}${map[String(f)] ?? f.toFixed(3).slice(1)}`;
};
const mdY = (ms: number) => {
  const x = new Date(ms);
  return `${String(x.getUTCMonth() + 1).padStart(2, '0')}/${String(x.getUTCDate()).padStart(2, '0')}/${String(x.getUTCFullYear()).slice(2)}`;
};

type BondSeed = [type: Bond['type'], term: string, coupon: number, maturity: string, issue: string, age: number, outstanding: number, richBp: number];

const SEEDS: BondSeed[] = [
  ['Bill', '4W', 0, '2026-12-17', '2026-11-19', 0, 92, 0],
  ['Bill', '8W', 0, '2027-01-19', '2026-11-17', 0, 86, 0],
  ['Bill', '13W', 0, '2027-02-18', '2026-11-19', 0, 81, 0],
  ['Bill', '26W', 0, '2027-05-20', '2026-11-19', 0, 74, 0],
  ['Bill', '52W', 0, '2027-11-04', '2026-11-05', 0, 48, 0],
  ['Note', '2Y', 3.5, '2028-08-31', '2026-08-31', 2, 71, 0.6],
  ['Note', '2Y', 3.5, '2028-09-30', '2026-09-30', 1, 70, 0.3],
  ['Note', '2Y', 3.375, '2028-10-31', '2026-10-31', 0, 69, -0.4],
  ['Note', '3Y', 3.5, '2029-09-15', '2026-09-15', 2, 58, 0.5],
  ['Note', '3Y', 3.375, '2029-10-15', '2026-10-15', 1, 58, 0.2],
  ['Note', '3Y', 3.5, '2029-11-15', '2026-11-15', 0, 58, -0.5],
  ['Note', '5Y', 4.0, '2031-02-28', '2026-02-28', 8, 70, 1.2],
  ['Note', '5Y', 3.75, '2031-05-31', '2026-05-31', 5, 70, 0.9],
  ['Note', '5Y', 3.875, '2031-08-31', '2026-08-31', 2, 70, 0.6],
  ['Note', '5Y', 3.75, '2031-09-30', '2026-09-30', 1, 70, 0.3],
  ['Note', '5Y', 3.625, '2031-10-31', '2026-10-31', 0, 70, -0.6],
  ['Note', '7Y', 3.875, '2033-08-15', '2023-08-15', 12, 110, 2.1],
  ['Note', '7Y', 4.0, '2033-09-30', '2026-09-30', 1, 44, 0.4],
  ['Note', '7Y', 3.875, '2033-10-31', '2026-10-31', 0, 44, -0.5],
  ['Note', '10Y', 4.5, '2033-11-15', '2023-11-15', 12, 120, 2.4],
  ['Note', '10Y', 4.0, '2034-02-15', '2024-02-15', 11, 125, 2.2],
  ['Note', '10Y', 4.375, '2034-05-15', '2024-05-15', 10, 125, 2.0],
  ['Note', '10Y', 3.875, '2034-08-15', '2024-08-15', 9, 126, 1.9],
  ['Note', '10Y', 4.0, '2036-02-15', '2026-02-15', 3, 92, 0.9],
  ['Note', '10Y', 4.375, '2036-05-15', '2026-05-15', 2, 92, 0.7],
  ['Note', '10Y', 4.25, '2036-08-15', '2026-08-15', 1, 92, 0.4],
  ['Note', '10Y', 4.125, '2036-11-15', '2026-11-15', 0, 58, -1.1],
  ['Bond', '30Y', 4.5, '2042-02-15', '2012-02-15', 30, 35, 3.5],
  ['Bond', '30Y', 3.375, '2042-08-15', '2012-08-15', 29, 33, 3.2],
  ['Bond', '30Y', 4.625, '2044-11-15', '2014-11-15', 25, 34, 2.8],
  ['Bond', '30Y', 3.0, '2047-05-15', '2017-05-15', 19, 40, 2.4],
  ['Bond', '20Y', 4.625, '2046-02-15', '2026-02-15', 3, 22, 1.5],
  ['Bond', '20Y', 4.75, '2046-05-15', '2026-05-15', 2, 22, 1.2],
  ['Bond', '20Y', 4.75, '2046-08-15', '2026-08-15', 1, 22, 0.9],
  ['Bond', '20Y', 4.625, '2046-11-15', '2026-11-15', 0, 16, -0.4],
  ['Bond', '30Y', 2.25, '2052-02-15', '2022-02-15', 18, 60, 3.0],
  ['Bond', '30Y', 4.125, '2053-08-15', '2023-08-15', 13, 70, 2.0],
  ['Bond', '30Y', 4.5, '2054-11-15', '2024-11-15', 8, 72, 1.5],
  ['Bond', '30Y', 4.75, '2055-05-15', '2025-05-15', 6, 70, 1.2],
  ['Bond', '30Y', 4.875, '2056-08-15', '2026-08-15', 1, 60, 0.6],
  ['Bond', '30Y', 4.75, '2056-11-15', '2026-11-15', 0, 30, -0.9],
  ['TIPS', '5Y', 1.625, '2031-10-15', '2026-10-15', 0, 48, 0],
  ['TIPS', '10Y', 1.875, '2036-07-15', '2026-07-15', 0, 52, 0],
];

export const BONDS: Bond[] = SEEDS.map(([type, term, coupon, mat, issue, age, outstanding, richBp], i) => {
  const maturity = d(mat);
  const prefix = type === 'Bill' ? '912797' : type === 'Bond' ? '912810' : '91282C';
  const name = type === 'Bill' ? `B 0 ${mdY(maturity)}` : type === 'TIPS' ? `TII ${frac(coupon)} ${mdY(maturity)}` : `T ${frac(coupon)} ${mdY(maturity)}`;
  return { cusip: cusip(prefix, 400 + i * 7), name, type, coupon, maturity, issue: d(issue), term, age, outstanding, richBp };
});

export const bondByCusip = new Map(BONDS.map(b => [b.cusip, b]));

// ----------------------------------------------------------------------------- Treasury futures

export interface FuturesRoot {
  root: string;
  name: string;
  /** deliverable remaining term in years [min, max] at the first day of the delivery month */
  basket: [number, number];
  tick: number;
  multiplier: number;
  /** the tenor whose yield moves it most (for the CTD-free shortcut and the curve panel) */
  tenor: number;
  volPerMin: number;
}

export const ROOTS: FuturesRoot[] = [
  { root: 'ZT', name: '2-Year T-Note', basket: [1.75, 2.0], tick: 1 / 256, multiplier: 2000, tenor: 2, volPerMin: 900 },
  { root: 'Z3N', name: '3-Year T-Note', basket: [2.75, 3.0], tick: 1 / 256, multiplier: 2000, tenor: 3, volPerMin: 40 },
  { root: 'ZF', name: '5-Year T-Note', basket: [4.1667, 5.25], tick: 1 / 128, multiplier: 1000, tenor: 5, volPerMin: 1100 },
  { root: 'ZN', name: '10-Year T-Note', basket: [6.5, 8.0], tick: 1 / 64, multiplier: 1000, tenor: 7, volPerMin: 1700 },
  { root: 'TN', name: 'Ultra 10-Year T-Note', basket: [9.4167, 10.0], tick: 1 / 64, multiplier: 1000, tenor: 10, volPerMin: 320 },
  { root: 'TWE', name: '20-Year T-Bond', basket: [19.1667, 19.9167], tick: 1 / 32, multiplier: 1000, tenor: 20, volPerMin: 45 },
  { root: 'ZB', name: 'U.S. T-Bond', basket: [15, 25], tick: 1 / 32, multiplier: 1000, tenor: 16, volPerMin: 420 },
  { root: 'UB', name: 'Ultra T-Bond', basket: [25, 30], tick: 1 / 32, multiplier: 1000, tenor: 28, volPerMin: 330 },
];

export interface Contract {
  sym: string;
  root: FuturesRoot;
  /** 'Z6' */
  code: string;
  label: string;
  deliveryMonth: number;
  firstNotice: number;
  lastTrade: number;
  /** share of the root's volume while the roll is under way */
  volumeShare: number;
  front: boolean;
  basket: Array<{ bond: Bond; cf: number }>;
}

const MONTHS: Array<[code: string, label: string, delivery: string, fnd: string, ltd: string, share: number]> = [
  ['Z6', 'Dec 2026', '2026-12-01', '2026-11-30', '2026-12-18', 0.32],
  ['H7', 'Mar 2027', '2027-03-01', '2027-02-26', '2027-03-19', 0.68],
];

// one basket per root, judged at the front month (the deferred contract's basket is mostly the same bonds;
// using them keeps the calendar roll a matter of carry), conversion factors per contract month
export const CONTRACTS: Contract[] = ROOTS.flatMap(root =>
  MONTHS.map(([code, label, delivery, fnd, ltd, share], i) => {
    const dm = d(delivery);
    const front = d(MONTHS[0][2]);
    const basket = BONDS.filter(b => b.type !== 'Bill' && b.type !== 'TIPS')
      .filter(b => {
        const yrs = (b.maturity - front) / (365.25 * 86400_000);
        return yrs >= root.basket[0] - 1e-6 && yrs <= root.basket[1] + 1e-6 && b.issue < front;
      })
      .map(bond => ({ bond, cf: conversionFactor(bond, dm) }));
    return {
      sym: i === 0 ? root.root : `${root.root}${code}`,
      root,
      code,
      label,
      deliveryMonth: dm,
      firstNotice: d(fnd),
      lastTrade: d(ltd),
      volumeShare: share,
      front: i === 0,
      basket,
    };
  }),
);

// ----------------------------------------------------------------------------- STIR

export interface StirContract {
  sym: string;
  kind: 'SR3' | 'ZQ';
  label: string;
  /** reference period start and end (UTC ms) */
  start: number;
  end: number;
}

export const STIR: StirContract[] = [
  { sym: 'ZQZ6', kind: 'ZQ', label: '30-Day Fed Funds Dec 2026', start: d('2026-12-01'), end: d('2026-12-31') },
  { sym: 'ZQF7', kind: 'ZQ', label: '30-Day Fed Funds Jan 2027', start: d('2027-01-01'), end: d('2027-01-31') },
  { sym: 'ZQG7', kind: 'ZQ', label: '30-Day Fed Funds Feb 2027', start: d('2027-02-01'), end: d('2027-02-28') },
  { sym: 'SR3Z6', kind: 'SR3', label: '3-Month SOFR Dec 2026', start: d('2026-12-16'), end: d('2027-03-17') },
  { sym: 'SR3H7', kind: 'SR3', label: '3-Month SOFR Mar 2027', start: d('2027-03-17'), end: d('2027-06-16') },
  { sym: 'SR3M7', kind: 'SR3', label: '3-Month SOFR Jun 2027', start: d('2027-06-16'), end: d('2027-09-15') },
  { sym: 'SR3U7', kind: 'SR3', label: '3-Month SOFR Sep 2027', start: d('2027-09-15'), end: d('2027-12-15') },
  { sym: 'SR3Z7', kind: 'SR3', label: '3-Month SOFR Dec 2027', start: d('2027-12-15'), end: d('2028-03-15') },
  { sym: 'SR3H8', kind: 'SR3', label: '3-Month SOFR Mar 2028', start: d('2028-03-15'), end: d('2028-06-21') },
];

/** FOMC meetings ahead (decision days) */
export const FOMC = ['2026-12-09', '2027-01-27', '2027-03-17', '2027-04-28', '2027-06-16', '2027-07-28', '2027-09-22', '2027-10-27', '2027-12-08'].map(d);

// ----------------------------------------------------------------------------- the symbol list

export const TENORS: Array<[sym: string, years: number, label: string]> = [
  ['US1M', 1 / 12, '1M'], ['US3M', 0.25, '3M'], ['US6M', 0.5, '6M'], ['US1Y', 1, '1Y'], ['US2Y', 2, '2Y'], ['US3Y', 3, '3Y'],
  ['US5Y', 5, '5Y'], ['US7Y', 7, '7Y'], ['US10Y', 10, '10Y'], ['US20Y', 20, '20Y'], ['US30Y', 30, '30Y'],
];

export const SWAP_TENORS: Array<[sym: string, years: number]> = [['SOFR1Y', 1], ['SOFR2Y', 2], ['SOFR5Y', 5], ['SOFR10Y', 10], ['SOFR30Y', 30]];

const y = (sym: string, tenor: number, label: string): Instrument => ({
  sym, name: `US Treasury ${label} yield (on-the-run)`, dataset: 'ust_yields', kind: 'yield', unit: '%', tick: 0.001, decimals: 3,
  hasVolume: false, session: 'UST_CASH', tenor,
});

export const INSTRUMENTS: Instrument[] = [
  ...TENORS.map(([s, t, l]) => y(s, t, l)),
  { sym: 'T3M10Y', name: '3M/10Y curve (bp)', dataset: 'curve_spreads', kind: 'spread', unit: 'bp', tick: 0.1, decimals: 1, hasVolume: false, session: 'UST_CASH', expr: '100*(US10Y-US3M)' },
  { sym: 'T2S10S', name: '2s10s curve (bp)', dataset: 'curve_spreads', kind: 'spread', unit: 'bp', tick: 0.1, decimals: 1, hasVolume: false, session: 'UST_CASH', expr: '100*(US10Y-US2Y)' },
  { sym: 'T5S30S', name: '5s30s curve (bp)', dataset: 'curve_spreads', kind: 'spread', unit: 'bp', tick: 0.1, decimals: 1, hasVolume: false, session: 'UST_CASH', expr: '100*(US30Y-US5Y)' },
  { sym: 'T2S5S10S', name: '2s5s10s butterfly (bp)', dataset: 'curve_spreads', kind: 'spread', unit: 'bp', tick: 0.1, decimals: 1, hasVolume: false, session: 'UST_CASH', expr: '100*(2*US5Y-US2Y-US10Y)' },
  { sym: 'T5S10S30S', name: '5s10s30s butterfly (bp)', dataset: 'curve_spreads', kind: 'spread', unit: 'bp', tick: 0.1, decimals: 1, hasVolume: false, session: 'UST_CASH', expr: '100*(2*US10Y-US5Y-US30Y)' },
  ...SWAP_TENORS.map(([s, t]): Instrument => ({
    sym: s, name: `SOFR OIS swap ${t}Y`, dataset: 'swaps', kind: 'swap', unit: '%', tick: 0.001, decimals: 3, hasVolume: false, session: 'SWAPS', tenor: t,
  })),
  { sym: 'SS2Y', name: '2Y swap spread (bp)', dataset: 'swaps', kind: 'spread', unit: 'bp', tick: 0.1, decimals: 1, hasVolume: false, session: 'SWAPS', expr: '100*(SOFR2Y-US2Y)' },
  { sym: 'SS10Y', name: '10Y swap spread (bp)', dataset: 'swaps', kind: 'spread', unit: 'bp', tick: 0.1, decimals: 1, hasVolume: false, session: 'SWAPS', expr: '100*(SOFR10Y-US10Y)' },
  { sym: 'SS30Y', name: '30Y swap spread (bp)', dataset: 'swaps', kind: 'spread', unit: 'bp', tick: 0.1, decimals: 1, hasVolume: false, session: 'SWAPS', expr: '100*(SOFR30Y-US30Y)' },
  { sym: 'TIPS5Y', name: '5Y TIPS real yield', dataset: 'inflation', kind: 'yield', unit: '%', tick: 0.001, decimals: 3, hasVolume: false, session: 'UST_CASH', tenor: 5 },
  { sym: 'TIPS10Y', name: '10Y TIPS real yield', dataset: 'inflation', kind: 'yield', unit: '%', tick: 0.001, decimals: 3, hasVolume: false, session: 'UST_CASH', tenor: 10 },
  { sym: 'BEI5Y', name: '5Y breakeven inflation', dataset: 'inflation', kind: 'spread', unit: '%', tick: 0.001, decimals: 3, hasVolume: false, session: 'UST_CASH', expr: 'US5Y-TIPS5Y' },
  { sym: 'BEI10Y', name: '10Y breakeven inflation', dataset: 'inflation', kind: 'spread', unit: '%', tick: 0.001, decimals: 3, hasVolume: false, session: 'UST_CASH', expr: 'US10Y-TIPS10Y' },
  { sym: 'SOFR', name: 'SOFR (overnight fixing)', dataset: 'funding', kind: 'rate', unit: '%', tick: 0.01, decimals: 2, hasVolume: false, session: 'FIXING' },
  { sym: 'EFFR', name: 'Effective fed funds rate', dataset: 'funding', kind: 'rate', unit: '%', tick: 0.01, decimals: 2, hasVolume: false, session: 'FIXING' },
  { sym: 'GCREPO', name: 'GC repo, overnight (DVP)', dataset: 'funding', kind: 'rate', unit: '%', tick: 0.01, decimals: 2, hasVolume: false, session: 'UST_CASH' },
  ...CONTRACTS.map((c): Instrument => ({
    sym: c.sym, name: `${c.root.name} ${c.label}`, dataset: 'ust_futures', kind: 'future', unit: 'price', tick: c.root.tick, thirtySeconds: true,
    decimals: 6, hasVolume: true, session: 'CBOT_UST', root: c.root.root, month: c.code, multiplier: c.root.multiplier, tenor: c.root.tenor,
    volPerMin: c.root.volPerMin * c.volumeShare, description: `${c.code}: first notice ${new Date(c.firstNotice).toISOString().slice(0, 10)}`,
  })),
  ...STIR.map((s, i): Instrument => ({
    sym: s.sym, name: s.label, dataset: 'stir', kind: 'stir', unit: 'price', tick: s.kind === 'ZQ' ? 0.0025 : i === 3 ? 0.0025 : 0.005, decimals: 4,
    hasVolume: true, session: 'CME_STIR', root: s.kind, multiplier: s.kind === 'ZQ' ? 4167 : 2500, volPerMin: s.kind === 'ZQ' ? 120 : 900 / (1 + (i - 3) * 0.6),
  })),
  { sym: 'TLT', name: 'iShares 20+ Year Treasury Bond ETF', dataset: 'etfs', kind: 'etf', unit: 'price', tick: 0.01, decimals: 2, hasVolume: true, session: 'NYSE', tenor: 20, volPerMin: 52000 },
  { sym: 'IEF', name: 'iShares 7-10 Year Treasury Bond ETF', dataset: 'etfs', kind: 'etf', unit: 'price', tick: 0.01, decimals: 2, hasVolume: true, session: 'NYSE', tenor: 8, volPerMin: 21000 },
  { sym: 'SHY', name: 'iShares 1-3 Year Treasury Bond ETF', dataset: 'etfs', kind: 'etf', unit: 'price', tick: 0.01, decimals: 2, hasVolume: true, session: 'NYSE', tenor: 2, volPerMin: 9000 },
  { sym: 'ES', name: 'E-mini S&P 500 Dec 2026', dataset: 'cross_asset', kind: 'equity', unit: 'price', tick: 0.25, decimals: 2, hasVolume: true, session: 'CME_EQ', volPerMin: 2400 },
  { sym: 'NQ', name: 'E-mini Nasdaq-100 Dec 2026', dataset: 'cross_asset', kind: 'equity', unit: 'price', tick: 0.25, decimals: 2, hasVolume: true, session: 'CME_EQ', volPerMin: 700 },
  { sym: 'CL', name: 'Crude Oil Jan 2027', dataset: 'cross_asset', kind: 'commodity', unit: 'price', tick: 0.01, decimals: 2, hasVolume: true, session: 'NYMEX', volPerMin: 900 },
  { sym: 'GC', name: 'Gold Feb 2027', dataset: 'cross_asset', kind: 'commodity', unit: 'price', tick: 0.1, decimals: 1, hasVolume: true, session: 'COMEX', volPerMin: 320 },
  { sym: '6E', name: 'Euro FX Dec 2026', dataset: 'cross_asset', kind: 'fx', unit: 'price', tick: 0.00005, decimals: 5, hasVolume: true, session: 'CME_FX', volPerMin: 300 },
  { sym: '6J', name: 'Japanese Yen Dec 2026', dataset: 'cross_asset', kind: 'fx', unit: 'price', tick: 0.0000005, decimals: 7, hasVolume: true, session: 'CME_FX', volPerMin: 220 },
  { sym: 'FGBL', name: 'Euro-Bund Dec 2026', dataset: 'cross_asset', kind: 'future', unit: 'price', tick: 0.01, decimals: 2, hasVolume: true, session: 'EUREX', volPerMin: 800 },
  { sym: 'MOVE', name: 'ICE BofA MOVE Index (Treasury implied vol)', dataset: 'volatility', kind: 'index', unit: 'index', tick: 0.01, decimals: 2, hasVolume: false, session: 'UST_CASH' },
  { sym: 'VIX', name: 'Cboe Volatility Index', dataset: 'volatility', kind: 'index', unit: 'index', tick: 0.01, decimals: 2, hasVolume: false, session: 'CBOE' },
  ...BONDS.map((b): Instrument => ({
    sym: b.cusip, name: `${b.name}${b.age === 0 ? ' (OTR)' : ''}`, dataset: 'ust_cash', kind: 'bond', unit: 'price', tick: 1 / 256,
    thirtySeconds: b.type !== 'Bill', decimals: b.type === 'Bill' ? 4 : 6, hasVolume: false, session: 'UST_CASH', bond: b,
    description: `${b.term} ${b.type}${b.age === 0 ? ', on-the-run' : b.age === 1 ? ', 1st off-the-run' : ''}`,
  })),
  // each bond's yield and its idio (rich/cheap to the fitted curve, bp) as series of their own: <cusip>.Y, <cusip>.IDIO
  ...BONDS.map((b): Instrument => ({
    sym: `${b.cusip}.Y`, name: `${b.name} yield`, dataset: 'ust_cash_yields', kind: 'yield', unit: '%', tick: 0.001, decimals: 3,
    hasVolume: false, session: 'UST_CASH', description: `the yield of ${b.cusip}`,
  })),
  ...BONDS.map((b): Instrument => ({
    sym: `${b.cusip}.IDIO`, name: `${b.name} idio (bp)`, dataset: 'ust_idio', kind: 'spread', unit: 'bp', tick: 0.01, decimals: 2,
    hasVolume: false, session: 'UST_CASH', description: `${b.cusip} rich (-) or cheap (+) to the fitted curve, bp`,
  })),
];

export const bySym = new Map(INSTRUMENTS.map(i => [i.sym, i]));
export const contractBySym = new Map(CONTRACTS.map(c => [c.sym, c]));
export const stirBySym = new Map(STIR.map(s => [s.sym, s]));

/** the on-the-run CUSIP for a curve symbol (US10Y -> the OTR 10Y note) */
export function otrFor(sym: string): Bond | undefined {
  const t = TENORS.find(x => x[0] === sym);
  if (!t) return undefined;
  const term = t[2];
  return BONDS.find(b => b.age === 0 && b.type !== 'TIPS' && (b.term === term || (term === '1M' && b.term === '4W') || (term === '3M' && b.term === '13W') || (term === '6M' && b.term === '26W') || (term === '1Y' && b.term === '52W')));
}

export const SESSION_OF = (sym: string): SessionSpec | undefined => {
  const i = bySym.get(sym);
  return i ? SESSIONS[i.session] : undefined;
};
