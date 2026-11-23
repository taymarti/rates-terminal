/**
 * Bond analytics for the linked symbol: a CUSIP as is, a benchmark yield (US10Y) as its on-the-run issue, a
 * Treasury future as its cheapest to deliver (with the whole basket). Price in 32nds and yield (type either for
 * a what-if; clear it to follow the market), risk, the position's DV01, carry and roll-down, curve scenarios, and
 * the futures hedge. From chart_api's `bond` and `ctd` methods, refreshed every two seconds.
 */
import { useState } from 'react';
import { useLink } from '@acme/chart-react';
import { definePanel, type PanelProps } from '@acme/app-shell';
import { BONDS, bondByCusip, contractBySym, otrFor } from '../model/universe';
import { upDown } from '@acme/chart-ext';
import { usePolled } from './common';

interface BondReply {
  cusip: string;
  security: string;
  type: string;
  term: string;
  coupon: number;
  maturity: string;
  settle: string;
  status: string;
  outstanding_bn: number;
  live_yield: number;
  yield: number;
  price: string;
  price_decimal: number;
  accrued: number;
  dirty: number;
  mod_duration: number;
  mac_duration: number;
  dv01_per_mm: number;
  convexity: number;
  position: { face_mm: number; dv01_usd: number; market_value_usd: number };
  carry_roll: { horizon_days: number; repo: number; carry_32nds: number; roll_32nds: number; total_bp: number; rolled_yield: number };
  curve_rich_bp: number;
  scenarios: Array<{ shift_bp: number; price: string; yield: number; pnl_usd: number }>;
  hedges: Array<{ contract: string; name: string; ctd: string; fut_dv01: number; contracts: number }>;
}

interface BondParams {
  pinned: string | null;
  face: number;
  horizon: number;
}

const money = (v: number) => `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

function resolveCusip(sym: string | undefined): { cusip: string | null; via: string | null; contract: string | null } {
  if (!sym) return { cusip: null, via: null, contract: null };
  if (bondByCusip.has(sym)) return { cusip: sym, via: null, contract: null };
  const otr = otrFor(sym);
  if (otr) return { cusip: otr.cusip, via: `${sym}: the on-the-run issue`, contract: null };
  const c = contractBySym.get(sym);
  if (c) return { cusip: null, via: null, contract: c.sym };
  return { cusip: null, via: null, contract: null };
}

function Bond({ params, setParams, linkGroup }: PanelProps<BondParams>) {
  const [sym] = useLink<string>('symbol', { group: linkGroup });
  const target = params.pinned ? { cusip: params.pinned, via: 'pinned', contract: null } : resolveCusip(sym);
  const basket = usePolled<Array<Record<string, unknown>>>('ctd', target.contract ? { contract: target.contract } : null, 2000).value;
  const ctdCusip = target.contract ? (basket?.find(r => r.CTD)?.Cusip as string | undefined) ?? null : null;
  const cusip = target.cusip ?? ctdCusip;
  const [priceText, setPriceText] = useState('');
  const [yieldText, setYieldText] = useState('');
  const what = yieldText.trim() ? { yield: Number(yieldText) } : priceText.trim() ? { price: priceText.trim() } : {};
  const { value: b, error } = usePolled<BondReply>('bond', cusip ? { cusip, face_mm: params.face, horizon_days: params.horizon, ...what } : null, Object.keys(what).length ? 0 : 2000);

  if (!cusip)
    return (
      <div className="tm-empty-help">
        <p>Bond analytics follow the linked symbol: a CUSIP, a benchmark yield (US2Y … US30Y) or a Treasury future (its CTD).</p>
        <p>Pick one:</p>
        <div className="tm-chips">
          {BONDS.filter(x => x.age === 0 && x.type !== 'Bill').map(x => (
            <button key={x.cusip} onClick={() => setParams({ pinned: x.cusip })}>
              {x.term} {x.type === 'TIPS' ? 'TIPS' : ''}
            </button>
          ))}
        </div>
      </div>
    );
  if (error && !b) return <div className="acme-error">{error}</div>;
  if (!b) return <div className="acme-empty">loading {cusip}…</div>;
  const whatIf = Object.keys(what).length > 0;
  return (
    <div className="tm-bond">
      <div className="tm-bond-head">
        <div>
          <div className="tm-bond-name">{b.security}</div>
          <div className="tm-muted">
            {b.cusip} · {b.term} {b.type} · {b.status} · ${b.outstanding_bn}bn out · settles {b.settle}
            {target.via && target.via !== 'pinned' ? ` · ${target.via}` : ''}
            {target.contract ? ` · CTD of ${target.contract}` : ''}
          </div>
        </div>
        <div className="tm-bond-px">
          <div className="tm-bond-price">{b.price}</div>
          <div className="tm-bond-yield">
            {b.yield.toFixed(3)}% {whatIf && <small className="tm-whatif">what-if (market {b.live_yield.toFixed(3)}%)</small>}
          </div>
        </div>
        <div className="tm-bond-pin">
          {params.pinned ? <button onClick={() => setParams({ pinned: null })}>Unpin</button> : <button onClick={() => setParams({ pinned: b.cusip })} title="Keep this bond when the linked symbol changes">Pin</button>}
        </div>
      </div>
      <div className="tm-bond-grid">
        <section>
          <h4>Calculator</h4>
          <label>
            Price <input className="tm-input" placeholder={b.price} value={priceText} onChange={e => (setPriceText(e.target.value), setYieldText(''))} spellCheck={false} />
          </label>
          <label>
            Yield <input className="tm-input" placeholder={b.yield.toFixed(4)} value={yieldText} onChange={e => (setYieldText(e.target.value), setPriceText(''))} spellCheck={false} />
          </label>
          <div className="tm-kv"><span>Accrued</span><b>{b.accrued.toFixed(5)}</b></div>
          <div className="tm-kv"><span>Dirty</span><b>{b.dirty.toFixed(5)}</b></div>
          <div className="tm-kv"><span>Rich/cheap to curve</span><b className={upDown(-b.curve_rich_bp)}>{b.curve_rich_bp.toFixed(2)}bp</b></div>
        </section>
        <section>
          <h4>Risk</h4>
          <div className="tm-kv"><span>Mod. duration</span><b>{b.mod_duration.toFixed(3)}</b></div>
          <div className="tm-kv"><span>Mac. duration</span><b>{b.mac_duration.toFixed(3)}</b></div>
          <div className="tm-kv"><span>DV01 per $1mm</span><b>${b.dv01_per_mm.toFixed(2)}</b></div>
          <div className="tm-kv"><span>Convexity</span><b>{b.convexity.toFixed(3)}</b></div>
          <label>
            Face ($mm) <input className="tm-input" type="number" value={params.face} min={1} onChange={e => setParams({ face: Math.max(0.1, Number(e.target.value) || 1) })} />
          </label>
          <div className="tm-kv"><span>Position DV01</span><b>{money(b.position.dv01_usd)}/bp</b></div>
          <div className="tm-kv"><span>Market value</span><b>{money(b.position.market_value_usd)}</b></div>
        </section>
        <section>
          <h4>Carry and roll</h4>
          <label>
            Horizon (days) <input className="tm-input" type="number" value={params.horizon} min={1} max={365} onChange={e => setParams({ horizon: Math.min(365, Math.max(1, Number(e.target.value) || 30)) })} />
          </label>
          <div className="tm-kv"><span>Repo (GC)</span><b>{b.carry_roll.repo.toFixed(3)}%</b></div>
          <div className="tm-kv"><span>Carry</span><b className={upDown(b.carry_roll.carry_32nds)}>{b.carry_roll.carry_32nds.toFixed(2)}/32</b></div>
          <div className="tm-kv"><span>Roll-down</span><b className={upDown(b.carry_roll.roll_32nds)}>{b.carry_roll.roll_32nds.toFixed(2)}/32</b></div>
          <div className="tm-kv"><span>Total</span><b className={upDown(b.carry_roll.total_bp)}>{b.carry_roll.total_bp.toFixed(2)}bp</b></div>
          <h4>Futures hedge</h4>
          {b.hedges.map(h => (
            <div className="tm-kv" key={h.contract} title={`CTD ${h.ctd}; $${h.fut_dv01}/bp per contract`}>
              <span>{h.contract}</span>
              <b>{h.contracts > 0 ? 'sell' : 'buy'} {Math.abs(h.contracts)}</b>
            </div>
          ))}
        </section>
        <section>
          <h4>Scenarios ({params.face}mm)</h4>
          <table className="tm-table">
            <thead>
              <tr><th>Shift</th><th>Price</th><th>Yield</th><th>P&amp;L</th></tr>
            </thead>
            <tbody>
              {b.scenarios.map(s => (
                <tr key={s.shift_bp} className={s.shift_bp === 0 ? 'on' : ''}>
                  <td className="tm-num">{s.shift_bp > 0 ? '+' : ''}{s.shift_bp}bp</td>
                  <td className="tm-num">{s.price}</td>
                  <td className="tm-num">{s.yield.toFixed(3)}</td>
                  <td className={`tm-num ${upDown(s.pnl_usd)}`}>{money(s.pnl_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
      {target.contract && basket && (
        <section className="tm-bond-basket">
          <h4>{target.contract} deliverable basket</h4>
          <table className="tm-table">
            <thead>
              <tr><th>#</th><th>Security</th><th>CF</th><th>Price</th><th>Yield</th><th>Gross</th><th>Carry</th><th>Net</th><th>IRR</th></tr>
            </thead>
            <tbody>
              {basket.map(r => (
                <tr key={String(r.Cusip)} className={r.CTD ? 'on' : ''} onClick={() => setParams({ pinned: String(r.Cusip) })} title="Pin this bond">
                  <td>{String(r.Rank)}</td>
                  <td>{String(r.Security)}</td>
                  <td className="tm-num">{Number(r.CF).toFixed(4)}</td>
                  <td className="tm-num">{String(r.Price)}</td>
                  <td className="tm-num">{Number(r.Yield).toFixed(3)}</td>
                  <td className="tm-num">{Number(r.GrossBasis32).toFixed(1)}</td>
                  <td className="tm-num">{Number(r.Carry32).toFixed(1)}</td>
                  <td className="tm-num">{Number(r.NetBasis32).toFixed(1)}</td>
                  <td className="tm-num">{Number(r.ImpliedRepo).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="tm-muted">Basis in 32nds. CTD: the lowest net basis per conversion factor; implied repo assumes delivery on the first day of the month.</p>
        </section>
      )}
    </div>
  );
}

export function bondPanel() {
  return definePanel<BondParams>({
    kind: 'bond',
    label: 'Bond analytics',
    title: 'Bond',
    description: 'Price/yield, risk, carry and roll, scenarios, hedge, CTD basket',
    defaultParams: { pinned: null, face: 10, horizon: 30 },
    component: Bond,
  });
}
