/**
 * The yield curve: Lightweight Charts' yield-curve chart (tenor on the horizontal axis, in months) with the
 * on-the-run curve now, 1 day, 1 week and 1 month ago, and the SOFR swap curve; below, the tenors with their
 * changes and the main curve spreads. A click on a tenor sends its symbol to the linked panels.
 */
import { useEffect, useRef } from 'react';
import { LineSeries, LineStyle, createYieldCurveChart, type ISeriesApi, type IYieldCurveChartApi } from 'lightweight-charts';
import { followTheme, upDown } from '@acme/chart-ext';
import { useLink } from '@acme/chart-react';
import { definePanel, useRows, type PanelProps } from '@acme/app-shell';

const LINES = [
  { key: 'now', title: 'UST now', color: '#2962ff', width: 3, style: LineStyle.Solid },
  { key: 'd1', title: '1D ago', color: '#90a4ae', width: 1, style: LineStyle.Dashed },
  { key: 'w1', title: '1W ago', color: '#ab47bc', width: 1, style: LineStyle.Dashed },
  { key: 'm1', title: '1M ago', color: '#ff9800', width: 1, style: LineStyle.Dotted },
  { key: 'swap', title: 'SOFR swaps', color: '#26a69a', width: 2, style: LineStyle.Solid },
] as const;

function Curve({ linkGroup }: PanelProps<Record<string, never>>) {
  const [sym, setSym] = useLink<string>('symbol', { group: linkGroup });
  const curve = useRows('ust_curve', ['Tenor', 'Years', 'Sym', 'Yield', 'Chg1D', 'Chg1W', 'Chg1M', 'Chg3M']).value ?? [];
  const swaps = useRows('swap_curve', ['Tenor', 'Years', 'SwapRate', 'SwapSpreadBp', 'ChgBp']).value ?? [];
  const spreads = useRows('quotes', ['Sym', 'Last', 'Chg', 'Name']).value?.filter(r => ['T3M10Y', 'T2S10S', 'T5S30S', 'T2S5S10S', 'T5S10S30S', 'SS10Y', 'BEI10Y'].includes(String(r.Sym))) ?? [];
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IYieldCurveChartApi | null>(null);
  const series = useRef<Record<string, ISeriesApi<'Line', number>>>({});
  const fitted = useRef(false);

  useEffect(() => {
    const c = createYieldCurveChart(el.current!, {
      autoSize: true,
      layout: { attributionLogo: false },
      yieldCurve: { baseResolution: 1, minimumTimeRange: 360, startTimeRange: 0 },
      localization: { priceFormatter: (p: number) => `${p.toFixed(3)}%` },
    });
    chart.current = c;
    const unTheme = followTheme(c, el.current!);
    for (const l of LINES)
      series.current[l.key] = c.addSeries(LineSeries, { color: l.color, lineWidth: l.width as 1, lineStyle: l.style, title: l.title, pointMarkersVisible: l.key === 'now', lastValueVisible: l.key === 'now', priceLineVisible: false }) as ISeriesApi<'Line', number>;
    return () => {
      unTheme();
      c.remove();
      chart.current = null;
      series.current = {};
    };
  }, []);

  useEffect(() => {
    if (!curve.length || !chart.current) return;
    const pts = [...curve].sort((a, b) => Number(a.Years) - Number(b.Years));
    const at = (r: Record<string, unknown>, chg: string | null) => {
      const y = Number(r.Yield);
      return chg ? y - Number(r[chg] ?? 0) / 100 : y;
    };
    const months = (r: Record<string, unknown>) => Math.round(Number(r.Years) * 12);
    const set = (key: string, chg: string | null) => series.current[key]?.setData(pts.map(r => ({ time: months(r), value: at(r, chg) })).filter(p => Number.isFinite(p.value)));
    set('now', null);
    set('d1', 'Chg1D');
    set('w1', 'Chg1W');
    set('m1', 'Chg1M');
    series.current.swap?.setData([...swaps].sort((a, b) => Number(a.Years) - Number(b.Years)).map(r => ({ time: Math.round(Number(r.Years) * 12), value: Number(r.SwapRate) })));
    if (!fitted.current) {
      fitted.current = true;
      chart.current.timeScale().fitContent();
    }
  }, [curve, swaps]);

  const bp = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? `${v > 0 ? '+' : ''}${v.toFixed(1)}` : '');
  const sorted = [...curve].sort((a, b) => Number(a.Years) - Number(b.Years));
  return (
    <div className="tm-curve">
      <div className="tm-curve-chart">
        <div ref={el} style={{ position: 'absolute', inset: 0 }} />
        <div className="tm-curve-key">
          {LINES.map(l => (
            <span key={l.key}>
              <i style={{ background: l.color }} />
              {l.title}
            </span>
          ))}
        </div>
      </div>
      <div className="tm-curve-tables">
        <table className="tm-table">
          <thead>
            <tr>
              <th>Tenor</th>
              <th>Yield</th>
              <th>1D</th>
              <th>1W</th>
              <th>1M</th>
              <th>3M</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(r => (
              <tr key={String(r.Tenor)} className={sym === r.Sym ? 'on' : ''} onClick={() => setSym(String(r.Sym))} title={`${r.Sym}: send to linked panels`}>
                <td>{String(r.Tenor)}</td>
                <td className="tm-num">{Number(r.Yield).toFixed(3)}</td>
                {(['Chg1D', 'Chg1W', 'Chg1M', 'Chg3M'] as const).map(k => (
                  <td key={k} className={`tm-num ${upDown(r[k])}`}>
                    {bp(r[k])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <table className="tm-table">
          <thead>
            <tr>
              <th>Spread</th>
              <th>Level</th>
              <th>Chg</th>
            </tr>
          </thead>
          <tbody>
            {spreads.map(r => {
              const pctUnit = String(r.Sym).startsWith('BEI');
              return (
                <tr key={String(r.Sym)} className={sym === r.Sym ? 'on' : ''} onClick={() => setSym(String(r.Sym))} title={String(r.Name)}>
                  <td>{String(r.Sym)}</td>
                  <td className="tm-num">{pctUnit ? `${Number(r.Last).toFixed(3)}%` : Number(r.Last).toFixed(1)}</td>
                  <td className={`tm-num ${upDown(r.Chg)}`}>{pctUnit ? bp(Number(r.Chg) * 100) : bp(r.Chg)}</td>
                </tr>
              );
            })}
            {swaps.map(r => (
              <tr key={`sw${r.Tenor}`} onClick={() => setSym(`SOFR${r.Tenor}`)}>
                <td>SOFR {String(r.Tenor)}</td>
                <td className="tm-num">{Number(r.SwapRate).toFixed(3)}</td>
                <td className="tm-num" title="swap spread, bp">
                  {bp(r.SwapSpreadBp)} ss
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function curvePanel() {
  return definePanel<Record<string, never>>({
    kind: 'curve',
    label: 'Yield curve',
    title: 'UST curve',
    description: 'The on-the-run curve now and 1D/1W/1M ago, swaps, spreads',
    defaultParams: {},
    component: Curve,
  });
}
