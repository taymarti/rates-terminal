/**
 * chart_api on the in-page backend: @acme/dh-mock's chart methods (charts, studies, workspaces, the update graph),
 * plus the terminal's own, the same as rates_terminal.tapi on the server: the dataset catalog, instrument reference,
 * bond analytics, the curve, a contract's basket, scenarios, correlations, accrued interest.
 */
import { fmt32, isExpression, parse32, studiesFor } from '@acme/chart-ext';
import { ApiError, Reply, mockChartApi, p, type Engine, type MockServer, type NodeCache } from '@acme/dh-mock';
import { accrued, priceFromYield, risk, yieldFromPrice } from '../model/bonds';
import { curveAt, type Market } from '../model/market';
import { BONDS, CONTRACTS, TENORS, bondByCusip, otrFor } from '../model/universe';
import { DATASETS } from './catalog';
import type { LiveTables } from './tables';

const DAY = 86400_000;

export function terminalApi(engine: Engine, server: MockServer, market: Market, nodes: NodeCache, live: LiveTables) {
  const meta = (sym: string) => {
    const i = market.instrument(sym)!;
    return { name: i.name, unit: i.unit, tick: i.tick, thirty_seconds: !!i.thirtySeconds, decimals: i.decimals, has_volume: i.hasVolume, kind: i.kind, dataset: i.dataset };
  };
  // a typed spread or ratio (ZN-ZNH7, 100*(US10Y-US2Y)) becomes an instrument of its own, with a session
  const makeExpression = (sym: string) => {
    if (!isExpression(sym)) return null;
    const inst = market.addExpression(sym);
    live.addSessionFor(inst.sym);
    return inst.sym;
  };
  const resolve = (sym: string): string => {
    const s = sym.trim();
    if (market.has(s)) return s;
    if (market.has(s.toUpperCase())) return s.toUpperCase();
    const made = makeExpression(s);
    if (made) return made;
    throw new ApiError(`unknown symbol ${sym}`, 'not_found');
  };
  const api = mockChartApi(engine, server, market, nodes, { meta, resolve: makeExpression });

  // ---------------------------------------------------------------- the catalog
  api.method('datasets', 'The dataset catalog: chartable and table-only datasets, their symbols and tables.', [], () =>
    DATASETS.map(d => ({ ...d, symbols: d.symbols ?? [], rows: d.table ? server.tableSource(d.table)?.size ?? null : null })),
  );
  api.method('instruments', 'Reference data for symbols (all, or one dataset).', [p('dataset', 'string', '')], (_c, a) =>
    market.inst.filter(i => !a.dataset || i.dataset === a.dataset).map(i => ({ sym: i.sym, ...meta(i.sym), session: i.session, description: i.description ?? i.expr ?? '' })),
  );
  api.method('dataset', "A dataset's description, its table (as a reply table) and the indicators its symbols can use.", [p('id', 'string')], (_c, a) => {
    const d = DATASETS.find(x => x.id === a.id);
    if (!d) throw new ApiError(`no dataset ${a.id}`, 'not_found');
    const t = d.table ? server.tableSource(d.table) : undefined;
    const vol = (d.symbols ?? []).some(s => market.instrument(s)?.hasVolume);
    return new Reply({ ...d, columns: t?.columns ?? [], indicators: d.kind === 'chart' ? studiesFor(vol) : [] }, t ? [t] : []);
  }, { tables: true });

  // ---------------------------------------------------------------- Treasury analytics
  api.method(
    'bond',
    'Analytics for a cash bond: price (32nds) and yield (give either to solve the other), risk, carry and roll-down, curve scenarios, and the futures hedge.',
    [p('cusip', 'string'), p('price', 'string', ''), p('yield', 'number', NaN), p('face_mm', 'number', 10), p('horizon_days', 'integer', 30)],
    (_c, a) => {
      const b = bondByCusip.get(a.cusip) ?? otrFor(a.cusip);
      if (!b) throw new ApiError(`no bond ${a.cusip}`, 'not_found');
      const now = Date.now();
      const settle = Math.floor(now / DAY) * DAY + DAY;
      const live = market.yieldOf(b.cusip);
      const given = a.price ? parse32(a.price) : null;
      const y = Number.isFinite(a.yield) ? a.yield : given != null ? yieldFromPrice(b, given, settle) : live;
      const r = risk(b, y, settle);
      const repo = market.repo();
      const h = a.horizon_days;
      // carry: coupon accrual minus financing; roll-down: the same spread to a curve that is h days shorter
      const tenor = (b.maturity - settle) / (365.25 * DAY);
      const carry = (b.coupon - (repo * r.dirty) / 100) * (h / 365);
      const rolledYield = y + (curveAt(market.F, Math.max(1 / 365, tenor - h / 365)) - curveAt(market.F, tenor));
      const roll = priceFromYield(b, rolledYield, settle + h * DAY) - priceFromYield(b, y, settle + h * DAY);
      const scen = [-100, -50, -25, -10, 0, 10, 25, 50, 100].map(s => {
        const px = priceFromYield(b, y + s / 100, settle);
        return { shift_bp: s, price: fmt32(px), yield: +(y + s / 100).toFixed(4), pnl_usd: Math.round((px - r.price) * a.face_mm * 10_000) };
      });
      // hedge with the contract whose CTD is closest in duration
      const candidates = CONTRACTS.filter(c => c.front && c.basket.length);
      const hedges = candidates
        .map(c => {
          const bk = market.basket(c, now).sort((x, z) => x.implied - z.implied)[0];
          const ctdRisk = risk(bk.bond, bk.y, settle);
          const futDv01 = (ctdRisk.dv01 / bk.cf) * (c.root.multiplier / 1000) * 0.1; // $/bp per contract
          const bondDv01 = r.dv01 * a.face_mm; // $/bp
          return { contract: c.sym, name: c.root.name, ctd: bk.bond.name, fut_dv01: +futDv01.toFixed(2), contracts: Math.round(bondDv01 / futDv01), duration_gap: Math.abs(ctdRisk.modDur - r.modDur) };
        })
        .sort((x, z) => x.duration_gap - z.duration_gap);
      return {
        cusip: b.cusip, security: b.name, type: b.type, term: b.term, coupon: b.coupon, maturity: new Date(b.maturity).toISOString().slice(0, 10),
        settle: new Date(settle).toISOString().slice(0, 10), status: b.age === 0 ? 'on-the-run' : `${b.age} off the run`, outstanding_bn: b.outstanding,
        live_yield: +live.toFixed(4), yield: +y.toFixed(4), price: fmt32(r.price), price_decimal: +r.price.toFixed(6), accrued: +r.accrued.toFixed(5), dirty: +r.dirty.toFixed(5),
        mod_duration: +r.modDur.toFixed(3), mac_duration: +r.macDur.toFixed(3), dv01_per_mm: +r.dv01.toFixed(2), convexity: +r.convexity.toFixed(3),
        position: { face_mm: a.face_mm, dv01_usd: Math.round(r.dv01 * a.face_mm), market_value_usd: Math.round(r.dirty * a.face_mm * 10_000) },
        carry_roll: {
          horizon_days: h, repo: +repo.toFixed(3), carry_32nds: +(carry * 32).toFixed(2), roll_32nds: +(roll * 32).toFixed(2),
          total_bp: +((carry + roll) / ((r.modDur * r.dirty) / 10_000)).toFixed(2), rolled_yield: +rolledYield.toFixed(4),
        },
        curve_rich_bp: +((y - curveAt(market.F, tenor)) * 100).toFixed(2),
        scenarios: scen,
        hedges: hedges.slice(0, 3),
      };
    },
  );
  api.method('curve', 'The on-the-run curve now and 1 day, 1 week and 1 month ago, and the swap curve.', [], () => {
    const now = Date.now();
    const pt = (sym: string, ms: number) => +market.valueAt(sym, ms).toFixed(4);
    return {
      tenors: TENORS.map(([sym, years, label]) => ({ sym, years, label, cusip: otrFor(sym)?.cusip ?? null, now: +market.value(sym).toFixed(4), d1: pt(sym, now - DAY), w1: pt(sym, now - 7 * DAY), m1: pt(sym, now - 30 * DAY) })),
      swaps: [['SOFR1Y', 1], ['SOFR2Y', 2], ['SOFR5Y', 5], ['SOFR10Y', 10], ['SOFR30Y', 30]].map(([s, t]) => ({ sym: s, years: t, now: +market.value(s as string).toFixed(4) })),
      factors: market.factors(),
    };
  });
  api.method('ctd', "A contract's deliverable basket: CF, gross and net basis, implied repo, CTD.", [p('contract', 'string')], (_c, a) => {
    const c = CONTRACTS.find(x => x.sym === a.contract);
    if (!c) throw new ApiError(`no contract ${a.contract}`, 'not_found');
    const t = server.tableSource('deliverable_basket')!;
    return Array.from(t.keys())
      .map(k => Object.fromEntries(t.columns.map(col => [col.name, t.get(k, col.name)])))
      .filter(r => r.Contract === c.sym)
      .map(r => ({ ...r, Maturity: String(r.Maturity).slice(0, 10) }));
  });
  api.method('scenario', 'P&L per $1mm face (bonds) or per contract (futures) for a parallel shift and a twist (bp, pivot at 5Y).', [p('shift_bp', 'number', 25), p('twist_bp', 'number', 0)], (_c, a) => {
    const now = Date.now();
    const settle = Math.floor(now / DAY) * DAY + DAY;
    const out = BONDS.filter(b => b.type !== 'Bill').map(b => {
      const y = market.yieldOf(b.cusip);
      const tenor = (b.maturity - now) / (365.25 * DAY);
      const s = a.shift_bp + a.twist_bp * Math.max(-1, Math.min(1, Math.log(tenor / 5) / Math.log(6)));
      const p0 = priceFromYield(b, y, settle);
      const p1 = priceFromYield(b, y + s / 100, settle);
      return { cusip: b.cusip, security: b.name, shift_bp: +s.toFixed(2), pnl_per_mm: Math.round((p1 - p0) * 10_000) };
    });
    return out;
  });
  api.method('correlation', 'Correlation of daily changes between symbols over a window.', [p('symbols', 'array'), p('days', 'integer', 60)], (_c, a) => {
    const syms = (a.symbols as string[]).map(resolve);
    const d1 = market.tiers.d1;
    const ch = syms.map(s => {
      const k = market.index.get(s)!;
      const c = d1.series[k].c;
      const out: number[] = [];
      for (let r = Math.max(1, d1.n - a.days); r < d1.n; r++) out.push(c[r] - c[r - 1]);
      return out;
    });
    const corr = (x: number[], y: number[]) => {
      const n = Math.min(x.length, y.length);
      let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0, k = 0;
      for (let i = 0; i < n; i++) {
        if (!Number.isFinite(x[i]) || !Number.isFinite(y[i])) continue;
        sx += x[i]; sy += y[i]; sxx += x[i] * x[i]; syy += y[i] * y[i]; sxy += x[i] * y[i]; k++;
      }
      const cov = sxy / k - (sx / k) * (sy / k);
      const v = Math.sqrt((sxx / k - (sx / k) ** 2) * (syy / k - (sy / k) ** 2));
      return v ? cov / v : 0;
    };
    return { symbols: syms, days: a.days, matrix: ch.map(x => ch.map(y => +corr(x, y).toFixed(3))) };
  });
  api.method('accrued', 'Accrued interest of a bond on a date.', [p('cusip', 'string'), p('date', 'string', '')], (_c, a) => {
    const b = bondByCusip.get(a.cusip);
    if (!b) throw new ApiError(`no bond ${a.cusip}`, 'not_found');
    const d = a.date ? Date.parse(a.date) : Date.now();
    return { cusip: b.cusip, date: new Date(d).toISOString().slice(0, 10), accrued: +accrued(b, d).toFixed(6) };
  });

  return api;
}
