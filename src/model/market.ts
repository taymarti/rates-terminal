/**
 * The market simulator. A few factors drive everything, so the instruments move together the way they do:
 *
 *   curve      level, slope, curvature and a front-end policy factor -> every Treasury yield by tenor
 *   bonds      each CUSIP: the curve at its remaining term + its richness + a little noise; price from yield
 *   futures    the cheapest of the deliverable basket's forward prices / conversion factors
 *   STIR       SR3 and ZQ from the curve's implied forwards
 *   swaps      the curve + swap spreads; breakevens; funding fixings
 *   ETFs, cross-asset (ES, NQ, CL, GC, 6E, 6J, Bund, MOVE, VIX) with their usual correlations to rates
 *
 * Volatility follows the New York day; data releases (08:30 / 10:00 ET, FOMC at 14:00) and headlines jump the
 * curve. History is generated at start (seeded by the date, so a reload shows the same past): two years of
 * 3-hour steps into the daily tier (bridged to today's levels), five days of 15-second steps, the last hour of
 * 1-second steps. Then it ticks every engine cycle. The market never closes in the mock: session shading
 * still shows each exchange's calendar.
 */
import { priceFromYield, accrued, type Bond } from './bonds';
import { parseExpr, type Expr } from '@acme/chart-ext';
import { Tier } from '@acme/dh-mock';
import { BONDS, CONTRACTS, INSTRUMENTS, STIR, TENORS, SWAP_TENORS, otrFor, type Contract, type Instrument } from './universe';

// ----------------------------------------------------------------------------- randomness

export function makeRng(seed: number) {
  let a = seed >>> 0;
  const u = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  let spare: number | null = null;
  const n = () => {
    if (spare != null) {
      const s = spare;
      spare = null;
      return s;
    }
    let x = 0;
    let y = 0;
    let r = 0;
    do {
      x = u() * 2 - 1;
      y = u() * 2 - 1;
      r = x * x + y * y;
    } while (r === 0 || r >= 1);
    const f = Math.sqrt((-2 * Math.log(r)) / r);
    spare = y * f;
    return x * f;
  };
  return { u, n };
}
export type Rng = ReturnType<typeof makeRng>;

export function hash(...xs: Array<number | string>): number {
  let h = 2166136261;
  for (const x of xs) {
    const s = String(x);
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
  }
  return h >>> 0;
}

// ----------------------------------------------------------------------------- the curve

const YEAR_S = 365.25 * 86400;
const BASE: Array<[number, number]> = [
  [1 / 12, 3.72], [0.25, 3.68], [0.5, 3.6], [1, 3.5], [2, 3.42], [3, 3.45], [5, 3.62], [7, 3.82], [10, 4.08], [20, 4.62], [30, 4.68],
];
const SWAP_SPREAD: Array<[number, number]> = [[1, -0.08], [2, -0.17], [5, -0.27], [10, -0.44], [30, -0.82]];
const BEI_BASE: Array<[number, number]> = [[2, 2.45], [5, 2.33], [10, 2.28], [30, 2.24]];

function interp(pts: Array<[number, number]>, t: number): number {
  const x = Math.log(Math.max(t, 1 / 365));
  if (t <= pts[0][0]) return pts[0][1];
  for (let i = 1; i < pts.length; i++) {
    if (t <= pts[i][0]) {
      const x0 = Math.log(pts[i - 1][0]);
      const x1 = Math.log(pts[i][0]);
      return pts[i - 1][1] + ((pts[i][1] - pts[i - 1][1]) * (x - x0)) / (x1 - x0);
    }
  }
  return pts[pts.length - 1][1];
}

/** factor indexes */
const L = 0, Sl = 1, C = 2, P = 3, SS = 4, BEI = 5, BUND = 6, ES = 7, NQ = 8, CL = 9, GC = 10, EUR = 11, JPY = 12, MOVE = 13, VIX = 14, FUND = 15;
const NF = 16;
// annual volatility (rates in %, prices in log) and mean reversion per year
const VOL = [0.85, 0.55, 0.35, 0.45, 0.12, 0.3, 0.45, 0.16, 0.1, 0.32, 0.15, 0.07, 0.09, 0.9, 1.4, 0.4];
const KAPPA = [0.6, 0.8, 1.2, 0.8, 2, 1.5, 1.5, 0.05, 0.5, 0.3, 0.1, 0.3, 0.3, 20, 20, 200];

export function curveAt(F: Float64Array, tenor: number): number {
  const t = Math.max(tenor, 1 / 365);
  const lt = Math.log(t / 2) / Math.log(15);
  return (
    interp(BASE, t) +
    F[L] * (1 - 0.8 * Math.exp(-t / 0.35)) +
    F[Sl] * Math.max(-1.2, Math.min(1.2, lt)) +
    F[C] * (Math.exp(-(Math.log(t / 5) ** 2) / 0.8) - 0.3) +
    F[P] * Math.exp(-t / 1.2)
  );
}

/** New York trading-day volatility multiplier (EST, close enough for the mock) */
function season(ms: number): number {
  const h = (((ms / 3600_000 - 5) % 24) + 24) % 24; // ET hour (EST)
  if (h >= 8.33 && h < 15) return 1.45;
  if (h >= 15 && h < 17) return 0.9;
  if (h >= 3 && h < 8.33) return 0.85;
  return 0.5;
}

// ----------------------------------------------------------------------------- events

export interface MarketEvent {
  t: number;
  kind: 'release' | 'fomc' | 'auction' | 'headline';
  title: string;
  /** curve jumps in % (level, slope, policy) */
  jL: number;
  jS: number;
  jP: number;
  importance: 1 | 2 | 3;
  /** releases: consensus, actual, prior, unit */
  consensus?: string;
  actual?: string;
  prior?: string;
}

const RELEASES: Array<{ title: string; when: (d: Date) => boolean; et: number; imp: 1 | 2 | 3; scale: number; unit: string; mean: number; sd: number }> = [
  { title: 'Initial Jobless Claims', when: d => d.getUTCDay() === 4, et: 8.5, imp: 2, scale: 0.02, unit: 'K', mean: 228, sd: 9 },
  { title: 'Nonfarm Payrolls', when: d => d.getUTCDay() === 5 && d.getUTCDate() <= 7, et: 8.5, imp: 3, scale: 0.07, unit: 'K', mean: 135, sd: 45 },
  { title: 'CPI m/m', when: d => d.getUTCDate() >= 10 && d.getUTCDate() <= 14 && d.getUTCDay() === 3, et: 8.5, imp: 3, scale: 0.06, unit: '%', mean: 0.25, sd: 0.08 },
  { title: 'Retail Sales m/m', when: d => d.getUTCDate() >= 15 && d.getUTCDate() <= 17 && d.getUTCDay() >= 2 && d.getUTCDay() <= 4, et: 8.5, imp: 2, scale: 0.035, unit: '%', mean: 0.3, sd: 0.3 },
  { title: 'Core PCE m/m', when: d => d.getUTCDate() >= 26 && d.getUTCDay() === 5, et: 8.5, imp: 3, scale: 0.05, unit: '%', mean: 0.22, sd: 0.06 },
  { title: 'ISM Manufacturing', when: d => d.getUTCDate() <= 3 && d.getUTCDay() === 1, et: 10, imp: 2, scale: 0.03, unit: '', mean: 49.5, sd: 1.2 },
  { title: 'JOLTS Job Openings', when: d => d.getUTCDate() >= 2 && d.getUTCDate() <= 8 && d.getUTCDay() === 2, et: 10, imp: 2, scale: 0.025, unit: 'M', mean: 7.4, sd: 0.3 },
  { title: 'Consumer Confidence', when: d => d.getUTCDate() >= 24 && d.getUTCDay() === 2, et: 10, imp: 1, scale: 0.015, unit: '', mean: 96, sd: 4 },
  { title: 'Durable Goods Orders m/m', when: d => d.getUTCDate() >= 23 && d.getUTCDate() <= 27 && d.getUTCDay() === 3, et: 8.5, imp: 2, scale: 0.025, unit: '%', mean: 0.6, sd: 1.5 },
  { title: 'UMich Sentiment (final)', when: d => d.getUTCDate() >= 22 && d.getUTCDate() <= 28 && d.getUTCDay() === 5, et: 10, imp: 1, scale: 0.012, unit: '', mean: 71, sd: 2.5 },
];

const HEADLINES = [
  'Fed Governor says policy is "well positioned" for incoming data',
  'Treasury refunding: coupon sizes seen unchanged through next year',
  'Dealer survey: year-end balance sheet constraints tighter than last year',
  'Japan 30Y yield touches multi-decade high; US long end follows',
  'Fed regional president flags upside inflation risks from tariffs',
  'Large block trade in TY Mar futures; curve steepener interest reported',
  'Repo: GC trades firm into month-end; SOFR seen higher',
  'Oil slides on OPEC+ supply headlines; breakevens lower',
  'ECB speaker: further easing "not off the table"; Bund rallies',
  'Fed Vice Chair: balance-sheet runoff could end sooner than planned',
  'US 7Y auction preview: WI trading 0.5bp cheap to the curve',
  'Real money buying reported in 20Y sector',
  'Equities wobble on tech earnings; Treasuries catch a bid',
  'CTA models flip to short duration across the curve',
  'Strong demand for SOFR Mar27/Jun27 call spreads',
];

const FOMC_DAYS = [Date.parse('2026-12-09T00:00:00Z'), Date.parse('2027-01-27T00:00:00Z')];
const etAt = (dayUtcMs: number, etHour: number) => dayUtcMs + Math.round((etHour + 5) * 3600_000); // EST

/** Every scheduled event in [from, to): releases, FOMC decisions, auctions, headlines. Deterministic. */
export function eventsBetween(from: number, to: number): MarketEvent[] {
  const out: MarketEvent[] = [];
  const day0 = Math.floor(from / 86400_000) * 86400_000;
  for (let day = day0; day < to + 86400_000; day += 86400_000) {
    const dt = new Date(day);
    const wd = dt.getUTCDay();
    if (wd >= 1 && wd <= 5) {
      for (const r of RELEASES) {
        if (!r.when(dt)) continue;
        const t = etAt(day, r.et);
        if (t < from || t >= to) continue;
        const g = makeRng(hash(r.title, day));
        const z = g.n();
        const dec = r.unit === '%' ? 2 : r.mean < 20 ? 1 : 0;
        const consensus = r.mean + g.n() * r.sd * 0.3;
        const actual = consensus + z * r.sd;
        const prior = r.mean + g.n() * r.sd;
        const sign = r.title.includes('Claims') ? -1 : 1;
        out.push({
          t, kind: 'release', title: r.title, importance: r.imp, jL: sign * z * r.scale, jS: -sign * z * r.scale * 0.35, jP: sign * z * r.scale * 0.6,
          consensus: consensus.toFixed(dec) + r.unit, actual: actual.toFixed(dec) + r.unit, prior: prior.toFixed(dec) + r.unit,
        });
      }
      // auctions at 13:00 ET: 2Y/5Y/7Y late in the month, bills Mon/Tue
      const auction = wd === 1 ? '13W/26W bill auction' : wd === 2 && dt.getUTCDate() >= 22 && dt.getUTCDate() <= 28 ? '2Y note auction' : wd === 3 && dt.getUTCDate() >= 22 && dt.getUTCDate() <= 28 ? '5Y note auction' : wd === 4 && dt.getUTCDate() >= 22 && dt.getUTCDate() <= 28 ? '7Y note auction' : null;
      if (auction) {
        const t = etAt(day, 13);
        if (t >= from && t < to) {
          const g = makeRng(hash(auction, day));
          const tail = g.n() * 0.8;
          out.push({ t, kind: 'auction', title: auction, importance: auction.includes('bill') ? 1 : 2, jL: tail * 0.008, jS: 0, jP: 0, actual: `${tail >= 0 ? '+' : ''}${tail.toFixed(1)}bp tail` });
        }
      }
    }
    for (const f of FOMC_DAYS) {
      if (f === day) {
        const t = etAt(day, 14);
        if (t >= from && t < to) out.push({ t, kind: 'fomc', title: 'FOMC rate decision', importance: 3, jL: 0.02, jS: -0.02, jP: -0.12, consensus: '3.25-3.50%' });
      }
    }
  }
  // headlines: one every 4-9 minutes, around the clock
  const slot = 6.5 * 60_000;
  for (let s = Math.floor(from / slot) * slot; s < to; s += slot) {
    const g = makeRng(hash('news', s));
    const t = s + Math.floor(g.u() * slot);
    if (t < from || t >= to) continue;
    const z = g.n();
    out.push({ t, kind: 'headline', title: HEADLINES[Math.floor(g.u() * HEADLINES.length)], importance: 1, jL: z * 0.0015, jS: g.n() * 0.001, jP: g.n() * 0.0012 });
  }
  return out.sort((a, b) => a.t - b.t);
}

// ----------------------------------------------------------------------------- the market

interface Taylor {
  y0: number;
  p0: number;
  d1: number;
  d2: number;
  day: number;
}

/** a tenor's curve loadings: base, level, slope, curvature, policy */
export function loading(tenor: number): Float64Array {
  const t = Math.max(tenor, 1 / 365);
  const lt = Math.log(t / 2) / Math.log(15);
  return Float64Array.of(
    interp(BASE, t),
    1 - 0.8 * Math.exp(-t / 0.35),
    Math.max(-1.2, Math.min(1.2, lt)),
    Math.exp(-(Math.log(t / 5) ** 2) / 0.8) - 0.3,
    Math.exp(-t / 1.2),
  );
}
const dot = (w: Float64Array, F: Float64Array) => w[0] + F[L] * w[1] + F[Sl] * w[2] + F[C] * w[3] + F[P] * w[4];

/** what valuation needs that only changes once a day: tenors, loadings, accrued, carry terms, fixings */
interface DayCache {
  day: number;
  bonds: Array<{ k: number; ky: number; ki: number; b: Bond; w: Float64Array; rich: number; bei: number; exists: boolean }>;
  otr: Array<{ k: number; bond: number; w: Float64Array }>;
  swaps: Array<{ k: number; w: Float64Array; ss: number; ssw: number }>;
  stir: Array<{ k: number; zq: boolean; t: number; w0: Float64Array; wu: Float64Array; wd: Float64Array }>;
  fut: Array<{ k: number; items: Array<{ bond: number; cf: number; ai: number; days: number; coupon: number }> }>;
  tips: Array<{ k: number; w: Float64Array; bei: number; mult: number }>;
  sofr: number;
  effr: number;
}

export class Market {
  readonly inst: Instrument[] = [];
  readonly index = new Map<string, number>();
  readonly F = new Float64Array(NF);
  idio: Float64Array;
  vals: Float64Array;
  vols: Float64Array;
  /** the time of the last step (UTC ms) */
  t = 0;
  readonly tiers: { s1: Tier; m1: Tier; d1: Tier };
  private exprs: Array<{ i: number; e: Expr; legIdx: number[] }> = [];
  private taylor: Taylor[] = [];
  private bondY: Float64Array;
  private slow = new Float64Array(BONDS.length);
  private slowDue = 0;
  /** while generating history, the daily tier is rebuilt from the minute bars at the end instead of recorded */
  private deferDaily = false;
  /** the start of the trading day (07:00 ET = 12:00 UTC) and the market then: P&L is measured from it */
  sodMs = 0;
  sod: { ms: number; F: Float64Array; vals: Float64Array; bondY: Float64Array } | null = null;
  readonly onSod = new Set<() => void>();
  private bondIdx = new Map<string, number>();
  private dc: DayCache | null = null;
  private z = new Float64Array(NF);
  private rng: Rng = makeRng(1);
  /** traded kinds whose prices sit on their tick */
  private ticked: Array<[k: number, tick: number]> = [];
  private noisy: number[] = [];
  private traded: number[] = [];
  /** events already applied, newest last */
  readonly applied: MarketEvent[] = [];
  /** listeners told about each event as it happens (news tape, econ calendar) */
  readonly onEvent = new Set<(e: MarketEvent) => void>();
  readonly start: number;

  constructor(now = Date.now()) {
    this.start = now;
    const nowS = Math.floor(now / 1000);
    const dayStart = Math.floor(nowS / 86400) * 86400;
    const t1m = dayStart - 4 * 86400;
    const t1s = Math.floor((nowS - 3600) / 60) * 60;
    const tday = dayStart - 730 * 86400;
    this.sodMs = Math.floor((now - 12 * 3600_000) / 86400_000) * 86400_000 + 12 * 3600_000;
    this.tiers = {
      s1: new Tier('1s', 1, t1s, 8192),
      m1: new Tier('1m', 60, t1m, 8192),
      d1: new Tier('1d', 86400, tday, 1024),
    };
    for (const i of INSTRUMENTS) this.addInstrument(i);
    this.idio = new Float64Array(this.inst.length);
    this.vals = new Float64Array(this.inst.length).fill(NaN);
    this.vols = new Float64Array(this.inst.length);
    BONDS.forEach((b, i) => this.bondIdx.set(b.cusip, i));
    this.bondY = new Float64Array(BONDS.length).fill(NaN);
    for (const i of INSTRUMENTS) if (i.expr) this.bindExpr(this.index.get(i.sym)!, i.expr);
    this.indexTicks();
    this.generate(tday, t1m, t1s, now);
  }

  private indexTicks() {
    const useNoise = new Set(['bond', 'swap', 'stir', 'future']);
    this.noisy = this.inst.map((i, k) => (useNoise.has(i.kind) || i.sym.startsWith('TIPS') || (i.kind === 'yield' && !otrFor(i.sym)) ? k : -1)).filter(k => k >= 0);
    this.traded = this.inst.map((i, k) => (i.hasVolume && i.volPerMin ? k : -1)).filter(k => k >= 0);
    this.ticked = [];
    this.inst.forEach((i, k) => {
      if (i.tick && !i.expr && i.kind !== 'yield' && i.kind !== 'swap' && i.kind !== 'spread' && i.kind !== 'rate') this.ticked.push([k, i.tick]);
    });
  }

  private addInstrument(i: Instrument): number {
    const idx = this.inst.length;
    this.inst.push(i);
    this.index.set(i.sym, idx);
    this.tiers.s1.addSymbol();
    this.tiers.m1.addSymbol();
    this.tiers.d1.addSymbol();
    return idx;
  }

  private bindExpr(i: number, text: string) {
    const e = parseExpr(text);
    const legIdx = e.legs.map(l => {
      const k = this.index.get(l);
      if (k == null) throw new Error(`unknown symbol ${l}`);
      return k;
    });
    this.exprs.push({ i, e, legIdx });
  }

  has(sym: string) {
    return this.index.has(sym);
  }

  /**
   * A spread or ratio typed by a user ("ZN-ZNH7", "2*ZF-ZN"): a symbol of its own from now on, with its
   * history computed from the legs' bars in every tier.
   */
  addExpression(text: string): Instrument {
    const e = parseExpr(text);
    const existing = this.inst.find(x => x.expr && parseExpr(x.expr).text === e.text) ?? this.inst.find(x => x.sym === e.text);
    if (existing) return existing;
    const legs = e.legs.map(l => {
      const i = this.inst[this.index.get(l) ?? -1];
      if (!i) throw new Error(`unknown symbol ${l} in ${text}`);
      return i;
    });
    const first = legs[0];
    const ratio = text.includes('/');
    const inst: Instrument = {
      sym: e.text, name: `${ratio ? 'Ratio' : 'Spread'} ${e.text}`, dataset: 'user_spreads', kind: 'spread', unit: first.unit === '%' ? '%' : first.unit,
      tick: null, decimals: ratio ? 5 : first.unit === 'price' ? 4 : Math.max(2, first.decimals), hasVolume: false, session: first.session, expr: e.text,
      thirtySeconds: !ratio && legs.every(l => l.thirtySeconds) && e.legs.length === 2 && /^[A-Z0-9]+-[A-Z0-9]+$/.test(e.text),
    };
    const idx = this.addInstrument(inst);
    const grow = (a: Float64Array, fill: number) => {
      const x = new Float64Array(this.inst.length).fill(fill);
      x.set(a);
      return x;
    };
    this.idio = grow(this.idio, 0);
    this.vals = grow(this.vals, NaN);
    this.vols = grow(this.vols, 0);
    this.bindExpr(idx, e.text);
    const legIdx = e.legs.map(l => this.index.get(l)!);
    // backfill: the expression on the legs' open, high, low and close; its high and low are the extremes of those
    for (const tier of [this.tiers.s1, this.tiers.m1, this.tiers.d1]) {
      const out = tier.series[idx];
      const ls = legIdx.map(k => tier.series[k]);
      for (let r = 0; r < tier.n; r++) {
        const at = (f: 'o' | 'h' | 'l' | 'c') => e.eval(s => ls[e.legs.indexOf(s)][f][r]);
        const o = at('o');
        const c = at('c');
        const a = at('h');
        const b = at('l');
        out.o[r] = o;
        out.c[r] = c;
        out.h[r] = Math.max(o, c, a, b);
        out.l[r] = Math.min(o, c, a, b);
        out.v[r] = 0;
      }
    }
    this.vals[idx] = e.eval(s => this.vals[this.index.get(s)!]);
    return inst;
  }

  // ---------------------------------------------------------------- dynamics

  private stepFactors(dtSec: number, ms: number) {
    const F = this.F;
    const g = this.rng;
    const dt = dtSec / YEAR_S;
    const sq = Math.sqrt(dt) * season(ms);
    const z = this.z;
    const zL = g.n();
    z[L] = zL;
    z[Sl] = -0.3 * zL + 0.954 * g.n();
    z[C] = g.n();
    z[P] = 0.55 * zL + 0.835 * g.n();
    z[SS] = 0.2 * zL + 0.98 * g.n();
    z[BEI] = 0.45 * zL + 0.89 * g.n();
    z[BUND] = g.n();
    z[ES] = -0.3 * zL + 0.954 * g.n();
    z[NQ] = g.n();
    z[CL] = 0.15 * zL + 0.99 * g.n();
    z[GC] = -0.3 * zL + 0.954 * g.n();
    z[EUR] = -0.2 * zL + 0.98 * g.n();
    z[JPY] = -0.5 * zL + 0.866 * g.n();
    z[MOVE] = 0.3 * Math.abs(zL) - 0.24 + 0.95 * g.n();
    z[VIX] = -0.7 * z[ES] + 0.714 * g.n();
    z[FUND] = g.n();
    for (let f = 0; f < NF; f++) F[f] += -KAPPA[f] * F[f] * dt + VOL[f] * sq * z[f];
    // idiosyncratic noise (the instruments that use it): fast mean reversion, ~0.4 bp
    const id = this.idio;
    const kd = 1 - 4000 * dt;
    const sd = 0.25 * sq;
    for (const i of this.noisy) id[i] = id[i] * kd + sd * g.n();
    // each bond's slow idio: mean-reverting over weeks, about 1 bp
    // (stepped once a simulated minute: it moves over weeks, and drawing it every step was a third of the start-up)
    this.slowDue += dtSec;
    if (this.slowDue >= 60) {
      const sdt = this.slowDue / YEAR_S;
      this.slowDue = 0;
      const ks = 1 - 12 * sdt;
      const ss = 0.05 * Math.sqrt(sdt);
      for (let b = 0; b < this.slow.length; b++) this.slow[b] = this.slow[b] * ks + ss * g.n();
    }
  }

  private applyEvent(e: MarketEvent) {
    this.F[L] += e.jL;
    this.F[Sl] += e.jS;
    this.F[P] += e.jP;
    if (e.kind !== 'headline') this.F[MOVE] += Math.min(0.12, Math.abs(e.jL) * 2.5);
    this.applied.push(e);
    if (this.applied.length > 400) this.applied.splice(0, 100);
    for (const l of this.onEvent) l(e);
  }

  /** a bond's price from its yield, by a Taylor expansion around a reference refreshed daily or after big moves */
  private priceAt(bi: number, y: number, ms: number): number {
    const b = BONDS[bi];
    if (b.coupon === 0) return 100 / (1 + (y / 100) * Math.max(0, (b.maturity - ms) / (YEAR_S * 1000)));
    const day = Math.floor(ms / 86400_000);
    let t = this.taylor[bi];
    if (!t || t.day !== day || Math.abs(y - t.y0) > 0.2) {
      const settle = day * 86400_000;
      const p0 = priceFromYield(b, y, settle);
      const up = priceFromYield(b, y + 0.05, settle);
      const dn = priceFromYield(b, y - 0.05, settle);
      t = { y0: y, p0, d1: (up - dn) / 0.1, d2: (up + dn - 2 * p0) / 0.0025, day };
      this.taylor[bi] = t;
    }
    const dy = y - t.y0;
    return t.p0 + t.d1 * dy + 0.5 * t.d2 * dy * dy;
  }

  bondPrice(b: Bond, y: number, ms: number): number {
    return this.priceAt(this.bondIdx.get(b.cusip)!, y, ms);
  }

  /** the curve's yield at a tenor now */
  curve(tenor: number) {
    return curveAt(this.F, tenor);
  }

  yieldOf(cusip: string) {
    const i = this.bondIdx.get(cusip);
    return i == null ? NaN : this.bondY[i];
  }

  /** repo rate for financing (GC) */
  repo() {
    const v = this.vals[this.index.get('GCREPO')!];
    return Number.isFinite(v) ? v : 3.65;
  }

  /** accrued interest per 100 on the day of ms */
  accruedOn(b: Bond, ms: number) {
    return accrued(b, Math.floor(ms / 86400_000) * 86400_000);
  }

  /** a contract's basket at time ms: price, carry to delivery and the forward price over the conversion factor per deliverable */
  basket(c: Contract, ms: number) {
    const r = this.repo();
    const days = Math.max(0, (c.deliveryMonth - ms) / 86400_000);
    return c.basket.map(({ bond, cf }) => {
      const y = this.yieldOf(bond.cusip);
      const price = this.bondPrice(bond, y, ms);
      const ai = this.accruedOn(bond, ms);
      const carry = (bond.coupon - (r * (price + ai)) / 100) * (days / 365);
      return { bond, cf, y, price, ai, carry, implied: (price - carry) / cf };
    });
  }

  private day(ms: number): DayCache {
    const day = Math.floor(ms / 86400_000);
    if (this.dc && this.dc.day === day) return this.dc;
    const yr = (t: number) => (t - ms) / (YEAR_S * 1000);
    const ix = this.index;
    const bonds = BONDS.map(b => {
      const tenor = yr(b.maturity);
      return {
        k: ix.get(b.cusip)!, ky: ix.get(`${b.cusip}.Y`)!, ki: ix.get(`${b.cusip}.IDIO`)!, b, w: loading(tenor), rich: b.richBp / 100,
        bei: b.type === 'TIPS' ? interp(BEI_BASE, tenor) : 0, exists: ms >= b.issue && tenor > 0,
      };
    });
    const otr = TENORS.map(([sym, tenor]) => {
      const o = otrFor(sym);
      return { k: ix.get(sym)!, bond: o ? this.bondIdx.get(o.cusip)! : -1, w: loading(tenor) };
    });
    const swaps = SWAP_TENORS.map(([sym, t]) => ({ k: ix.get(sym)!, w: loading(t), ss: interp(SWAP_SPREAD, t), ssw: 0.5 + 0.5 * Math.min(1, t / 10) }));
    const stir = STIR.map(s => {
      const t = Math.max(1 / 52, yr((s.start + s.end) / 2));
      return { k: ix.get(s.sym)!, zq: s.kind === 'ZQ', t, w0: loading(t), wu: loading(t + 0.05), wd: loading(Math.max(1 / 365, t - 0.05)) };
    });
    const fut = CONTRACTS.filter(c => c.basket.length).map(c => ({
      k: ix.get(c.sym)!,
      items: c.basket.map(({ bond, cf }) => ({ bond: this.bondIdx.get(bond.cusip)!, cf, ai: accrued(bond, day * 86400_000), days: Math.max(0, (c.deliveryMonth - ms) / 86400_000), coupon: bond.coupon })),
    }));
    const tips = [['TIPS5Y', 5, 1.1], ['TIPS10Y', 10, 1]].map(([sym, t, m]) => ({ k: ix.get(sym as string)!, w: loading(t as number), bei: interp(BEI_BASE, t as number), mult: m as number }));
    const g = makeRng(hash('fix', day));
    const d = new Date(day * 86400_000).getUTCDate();
    const monthEnd = d >= 28 || d <= 1 ? 0.06 : 0;
    this.dc = { day, bonds, otr, swaps, stir, fut, tips, sofr: 3.62 + g.n() * 0.015 + monthEnd, effr: 3.58 };
    return this.dc;
  }

  /** compute every instrument's value at time ms from the factors */
  private valuate(ms: number) {
    const F = this.F;
    const V = this.vals;
    const id = this.idio;
    const dc = this.day(ms);
    const ix = this.index;
    // cash bonds first: the on-the-run yields are theirs
    for (let bi = 0; bi < dc.bonds.length; bi++) {
      const x = dc.bonds[bi];
      if (!x.exists) {
        V[x.k] = V[x.ky] = V[x.ki] = NaN;
        this.bondY[bi] = NaN;
        continue;
      }
      // idio: the bond's own richness, a slow component (weeks) and a fast one (minutes), in %
      const idio = x.rich + this.slow[bi] + id[x.k] * 0.8;
      let yv = dot(x.w, F) + idio;
      if (x.bei) yv -= x.bei + F[BEI];
      this.bondY[bi] = yv;
      V[x.k] = this.priceAt(bi, yv, ms);
      V[x.ky] = yv;
      V[x.ki] = idio * 100;
    }
    // a benchmark is its on-the-run issue; before that issue existed, the curve at its tenor (the benchmark rolled)
    for (const o of dc.otr) {
      const y = o.bond >= 0 ? this.bondY[o.bond] : NaN;
      V[o.k] = Number.isFinite(y) ? y : dot(o.w, F) + id[o.k] * 0.8;
    }
    for (const s of dc.swaps) V[s.k] = dot(s.w, F) + s.ss + F[SS] * s.ssw + id[s.k];
    for (const t of dc.tips) V[t.k] = dot(t.w, F) - t.bei - F[BEI] * t.mult + id[t.k];
    const sofr = Math.round((dc.sofr + F[P] * 0.05) * 100) / 100;
    V[ix.get('SOFR')!] = sofr;
    V[ix.get('EFFR')!] = Math.round((dc.effr + F[P] * 0.03) * 100) / 100;
    const repo = sofr + 0.02 + F[FUND] * 0.02;
    V[ix.get('GCREPO')!] = repo;
    // STIR: forwards from the front of the curve
    for (const s of dc.stir) {
      const fwd = dot(s.w0, F) + (s.t * (dot(s.wu, F) - dot(s.wd, F))) / 0.1;
      V[s.k] = 100 - (s.zq ? fwd - 0.07 : fwd + 0.03) + id[s.k] * 0.3;
    }
    // Treasury futures: the cheapest to deliver of the basket (forward price over conversion factor)
    for (const f of dc.fut) {
      let best = Infinity;
      for (const it of f.items) {
        const y = this.bondY[it.bond];
        if (!Number.isFinite(y)) continue;
        const p = this.priceAt(it.bond, y, ms);
        const carry = (it.coupon - (repo * (p + it.ai)) / 100) * (it.days / 365);
        const imp = (p - carry) / it.cf;
        if (imp < best) best = imp;
      }
      V[f.k] = best - 0.04 + id[f.k] * 0.01;
    }
    const y20 = curveAt(F, 20) - 4.62;
    const y8 = curveAt(F, 8) - 3.9;
    const y2 = curveAt(F, 2) - 3.42;
    const set = (sym: string, v: number) => (V[ix.get(sym)!] = v);
    set('TLT', 88.4 * Math.exp((-16.3 * y20) / 100));
    set('IEF', 95.1 * Math.exp((-7.3 * y8) / 100));
    set('SHY', 82.6 * Math.exp((-1.9 * y2) / 100));
    set('ES', 6810 * Math.exp(F[ES]));
    set('NQ', 25150 * Math.exp(1.2 * F[ES] + F[NQ]));
    set('CL', 61.2 * Math.exp(F[CL]));
    set('GC', 4080 * Math.exp(F[GC]));
    set('6E', 1.1585 * Math.exp(F[EUR]));
    set('6J', 0.006555 * Math.exp(F[JPY]));
    const by = (2.7 + 0.55 * F[L] + F[BUND]) / 100;
    const df = Math.pow(1 + by, -8.5);
    set('FGBL', (6 / by) * (1 - df) + 100 * df);
    set('MOVE', 82 * Math.exp(F[MOVE]));
    set('VIX', 16.4 * Math.exp(F[VIX]));
    // traded prices sit on their tick
    for (const [k, tick] of this.ticked) V[k] = Math.round(V[k] / tick) * tick;
    for (const x of this.exprs) V[x.i] = x.e.eval(s => V[x.legIdx[x.e.legs.indexOf(s)]]);
  }

  private volumes(dtSec: number, ms: number) {
    const s = season(ms) * (dtSec / 60);
    for (const k of this.traded) this.vols[k] = Math.max(0, Math.round(this.inst[k].volPerMin! * s * (0.4 + this.rng.u() * 1.2)));
  }

  /** the market at the start of the day, taken as time passes sodMs (07:00 ET); a new day rolls it */
  private checkSod(ms: number) {
    if (this.sod && ms < this.sodMs + 86400_000) return;
    if (this.sod) this.sodMs += 86400_000;
    if (ms < this.sodMs) return;
    this.sod = { ms: this.sodMs, F: Float64Array.from(this.F), vals: Float64Array.from(this.vals), bondY: Float64Array.from(this.bondY) };
    for (const l of this.onSod) l();
  }

  /** a symbol's value at the start of the day (NaN: unknown, e.g. a spread typed later) */
  sodValue(sym: string): number {
    const k = this.index.get(sym);
    return k == null || !this.sod || k >= this.sod.vals.length ? NaN : this.sod.vals[k];
  }

  sodYield(cusip: string): number {
    const i = this.bondIdx.get(cusip);
    return i == null || !this.sod ? NaN : this.sod.bondY[i];
  }

  private recordAll(tSec: number) {
    this.checkSod(tSec * 1000);
    if (!this.deferDaily) this.tiers.d1.record(tSec, this.vals, this.vols);
    this.tiers.m1.record(tSec, this.vals, this.vols);
    this.tiers.s1.record(tSec, this.vals, this.vols);
  }

  // ---------------------------------------------------------------- history

  private generate(tday: number, t1m: number, t1s: number, now: number) {
    const t0 = performance.now();
    // phase A: two years of 3-hour steps, bridged so the factors end at today's levels (0)
    this.rng = makeRng(hash('A', t1m));
    const stepA = 3 * 3600;
    const nA = Math.floor((t1m - tday) / stepA);
    const path = new Float64Array(nA * NF);
    for (let s = 0; s < nA; s++) {
      this.stepFactors(stepA, (tday + s * stepA) * 1000);
      path.set(this.F, s * NF);
    }
    const end = path.slice((nA - 1) * NF, nA * NF);
    for (let s = 0; s < nA; s++) {
      const w = (s + 1) / nA;
      for (let f = 0; f < NF; f++) this.F[f] = path[s * NF + f] - end[f] * w;
      const tSec = tday + (s + 1) * stepA;
      this.valuate(tSec * 1000);
      this.volumes(stepA, tSec * 1000);
      this.tiers.d1.record(tSec, this.vals, this.vols);
    }
    // phase B: 15-second steps up to the last hour (the prefix is the same on every reload today)
    this.F.fill(0);
    this.idio.fill(0);
    this.rng = makeRng(hash('B', t1m));
    const events = eventsBetween(t1m * 1000 - 15_000, now + 1);
    let ev = 0;
    let ms = t1m * 1000;
    this.deferDaily = true;
    for (let t = t1m; t < t1s; t += 20) {
      while (ev < events.length && events[ev].t <= t * 1000) this.applyEvent(events[ev++]);
      this.stepFactors(20, t * 1000);
      this.valuate(t * 1000);
      this.volumes(20, t * 1000);
      this.recordAll(t);
      ms = t * 1000;
    }
    // phase C: the last hour by the second
    this.rng = makeRng(hash('C', now));
    const nowS = Math.floor(now / 1000);
    for (let t = t1s; t <= nowS; t++) {
      while (ev < events.length && events[ev].t <= t * 1000) this.applyEvent(events[ev++]);
      this.stepFactors(1, t * 1000);
      this.valuate(t * 1000);
      this.volumes(1, t * 1000);
      this.recordAll(t);
      ms = t * 1000;
    }
    this.t = Math.max(ms, now);
    // the daily bars of the minute tier's days, from its bars (the same open, high, low, close and volume)
    this.deferDaily = false;
    const m1 = this.tiers.m1;
    const tmp = new Float64Array(this.inst.length);
    const vol = new Float64Array(this.inst.length);
    for (let r = 0; r < m1.n; r++) {
      const t = m1.times[r];
      for (const [f, at] of [['o', t], ['h', t + 15], ['l', t + 30], ['c', t + 59]] as const) {
        for (let k = 0; k < this.inst.length; k++) {
          tmp[k] = m1.series[k][f][r];
          vol[k] = f === 'o' ? m1.series[k].v[r] : 0;
        }
        this.tiers.d1.record(at, tmp, vol);
      }
    }
    for (const tier of Object.values(this.tiers)) tier.takeDirty();
    console.info(`mock market: ${this.inst.length} symbols, history in ${Math.round(performance.now() - t0)} ms`);
  }

  // ---------------------------------------------------------------- live

  /** advance to `now`: one step, events in between, every tier updated */
  live(now: number) {
    const dtMs = Math.max(1, Math.min(5000, now - this.t));
    for (const e of eventsBetween(this.t, now)) this.applyEvent(e);
    this.stepFactors(dtMs / 1000, now);
    this.valuate(now);
    this.volumes(dtMs / 1000, now);
    this.t = now;
    this.recordAll(now / 1000);
    // keep the seconds tier to two hours
    const s1 = this.tiers.s1;
    if (s1.n > 7200) s1.trimHead(s1.n - 3600);
  }

  value(sym: string) {
    const k = this.index.get(sym);
    return k == null ? NaN : this.vals[k];
  }

  instrument(sym: string) {
    const k = this.index.get(sym);
    return k == null ? undefined : this.inst[k];
  }

  /** the day's open, high, low and volume (UTC day) */
  dayBar(sym: string) {
    const k = this.index.get(sym);
    const d = this.tiers.d1;
    if (k == null || d.n === 0) return null;
    const i = d.n - 1;
    const s = d.series[k];
    const prevClose = i > 0 ? s.c[i - 1] : NaN;
    return { open: s.o[i], high: s.h[i], low: s.l[i], close: s.c[i], volume: s.v[i], prevClose };
  }

  /** the value at or before a time (UTC ms), from the finest tier that has it */
  valueAt(sym: string, ms: number): number {
    const k = this.index.get(sym);
    if (k == null) return NaN;
    const tSec = ms / 1000;
    for (const tier of [this.tiers.s1, this.tiers.m1, this.tiers.d1]) {
      if (tier.n && tSec >= tier.times[0]) {
        const i = tier.indexAt(tSec);
        if (i >= 0) return tier.series[k].c[i];
      }
    }
    return NaN;
  }

  factors() {
    return { level: this.F[L], slope: this.F[Sl], curvature: this.F[C], policy: this.F[P] };
  }
}
