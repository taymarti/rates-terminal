/**
 * Treasury math for the mock: prices and yields of fixed-coupon bonds (semiannual, actual/actual approximated
 * by 182.5-day half-years), accrued interest, risk (duration, DV01, convexity), 32nds formatting, and the
 * CBOT conversion factor. Accurate enough to make the numbers behave like the real ones; not for settlement.
 */

const DAY = 86400_000;
const YEAR_MS = 365.25 * DAY;

export interface Bond {
  cusip: string;
  /** "T 4 1/8 11/15/36" */
  name: string;
  type: 'Bill' | 'Note' | 'Bond' | 'TIPS';
  /** annual coupon in percent (bills: 0) */
  coupon: number;
  /** UTC ms */
  maturity: number;
  issue: number;
  /** original term label: 2Y, 10Y, 30Y ... */
  term: string;
  /** on-the-run rank: 0 = OTR, 1 = first off-the-run ... */
  age: number;
  /** amount outstanding, $bn */
  outstanding: number;
  /** richness vs the curve, bp (negative: rich) */
  richBp: number;
}

export const yearsBetween = (a: number, b: number) => (b - a) / YEAR_MS;

/**
 * Every semiannual coupon date of a maturity, ascending (stepping back from maturity, 40 years), computed once per
 * maturity: pricing runs millions of times (history generation, risk every second) and must not rebuild dates.
 */
const schedules = new Map<number, Float64Array>();
function schedule(maturity: number): Float64Array {
  let s = schedules.get(maturity);
  if (!s) {
    const m = new Date(maturity);
    const out: number[] = [];
    for (let k = 0; k <= 80; k++) out.push(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() - 6 * k, m.getUTCDate()));
    s = Float64Array.from(out.reverse());
    schedules.set(maturity, s);
  }
  return s;
}

/** index of the first coupon date after settle */
function nextIndex(s: Float64Array, settle: number): number {
  let lo = 0;
  let hi = s.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (s[mid] <= settle) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Coupon dates after `settle` up to maturity. */
export function couponDates(maturity: number, settle: number): number[] {
  const s = schedule(maturity);
  return Array.from(s.subarray(nextIndex(s, settle)));
}

export function previousCoupon(maturity: number, settle: number): number {
  const s = schedule(maturity);
  const i = nextIndex(s, settle) - 1;
  return i >= 0 ? s[i] : settle;
}

/** Accrued interest per 100 face. */
export function accrued(b: Bond, settle: number): number {
  if (b.coupon === 0) return 0;
  const s = schedule(b.maturity);
  const i = nextIndex(s, settle);
  const next = i < s.length ? s[i] : b.maturity;
  const prev = i > 0 ? s[i - 1] : settle;
  return (b.coupon / 2) * ((settle - prev) / Math.max(DAY, next - prev));
}

/** Clean price per 100 from a yield (percent), semiannual compounding. Bills: discount-yield-free money-market price. */
export function priceFromYield(b: Bond, y: number, settle: number): number {
  const t = yearsBetween(settle, b.maturity);
  if (t <= 0) return 100;
  if (b.coupon === 0) return 100 / (1 + (y / 100) * t);
  const s = schedule(b.maturity);
  const i0 = nextIndex(s, settle);
  const n = s.length - i0;
  if (n <= 0) return 100;
  const next = s[i0];
  const prev = i0 > 0 ? s[i0 - 1] : next - 182.5 * DAY;
  const w = (next - settle) / Math.max(DAY, next - prev); // fraction of the period to the next coupon
  const v = 1 / (1 + y / 200);
  const c = b.coupon / 2;
  // coupons c at periods w, w+1, ..., w+n-1, and 100 at the last: a geometric sum
  const vw = Math.pow(v, w);
  const annuity = Math.abs(1 - v) < 1e-12 ? n : (1 - Math.pow(v, n)) / (1 - v);
  const pv = c * vw * annuity + 100 * vw * Math.pow(v, n - 1);
  const ai = (b.coupon / 2) * ((settle - prev) / Math.max(DAY, next - prev));
  return pv - ai;
}

/** Yield (percent) from a clean price, by Newton steps. */
export function yieldFromPrice(b: Bond, price: number, settle: number): number {
  let y = b.coupon || 4;
  for (let i = 0; i < 40; i++) {
    const p = priceFromYield(b, y, settle);
    const d = (priceFromYield(b, y + 0.0005, settle) - priceFromYield(b, y - 0.0005, settle)) / 0.001;
    if (!Number.isFinite(d) || d === 0) break;
    const step = (p - price) / d;
    y -= step;
    if (Math.abs(step) < 1e-10) break;
  }
  return y;
}

export interface Risk {
  price: number;
  dirty: number;
  accrued: number;
  /** modified duration, years */
  modDur: number;
  macDur: number;
  /** dollar value of a basis point per 1mm face */
  dv01: number;
  convexity: number;
}

export function risk(b: Bond, y: number, settle: number): Risk {
  const p = priceFromYield(b, y, settle);
  const ai = accrued(b, settle);
  const up = priceFromYield(b, y + 0.01, settle);
  const dn = priceFromYield(b, y - 0.01, settle);
  const dirty = p + ai;
  const dPdy = (dn - up) / 0.02; // price points per 1% of yield
  const modDur = (dPdy * 100) / dirty; // years
  const convexity = (up + dn - 2 * p) / (dirty * 0.0001 * 0.0001) / 100; // as quoted (years^2 / 100)
  return { price: p, dirty, accrued: ai, modDur, macDur: modDur * (1 + y / 200), dv01: (dPdy / 100) * 10_000, convexity };
}

// ----------------------------------------------------------------------------- futures

/** CBOT conversion factor: the price at a 6% yield of the bond with its term rounded down to whole quarters (notes) or quarters (bonds), / 100. */
export function conversionFactor(b: Bond, deliveryMonthStart: number): number {
  const months = Math.floor((yearsBetween(deliveryMonthStart, b.maturity) * 12) / 3) * 3;
  const synthetic: Bond = { ...b, maturity: Date.UTC(new Date(deliveryMonthStart).getUTCFullYear(), new Date(deliveryMonthStart).getUTCMonth() + months, 1) };
  const p = priceFromYield(synthetic, 6, deliveryMonthStart); // clean, as the CBOT formula
  return Math.round((p / 100) * 10_000) / 10_000;
}

