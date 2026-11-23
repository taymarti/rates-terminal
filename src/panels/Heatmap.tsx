/**
 * Correlation heatmap of daily changes between symbols (chart_api `correlation`), with presets for the curve,
 * futures, swaps and cross-asset, and any symbols typed in. A click on a cell sends the row's symbol to the
 * linked panels; a double-click opens a chart of the row symbol compared with the column symbol.
 */
import { Fragment, useState } from 'react';
import { useLink } from '@acme/chart-react';
import { definePanel, type PanelProps } from '@acme/app-shell';
import { usePolled } from './common';

interface HeatmapParams {
  symbols: string[];
  days: number;
}

const PRESETS: Record<string, string[]> = {
  Curve: ['US3M', 'US2Y', 'US5Y', 'US10Y', 'US30Y', 'T2S10S', 'T5S30S', 'T2S5S10S'],
  Futures: ['ZT', 'ZF', 'ZN', 'TN', 'ZB', 'UB', 'SR3H7', 'SR3Z7'],
  'Swaps & inflation': ['US10Y', 'SOFR10Y', 'SS10Y', 'SS30Y', 'TIPS10Y', 'BEI10Y', 'MOVE'],
  'Cross-asset': ['US10Y', 'US2Y', 'ES', 'NQ', 'GC', 'CL', '6E', '6J', 'FGBL', 'MOVE', 'VIX'],
};

function color(v: number) {
  const a = Math.min(1, Math.abs(v));
  return v >= 0 ? `rgba(41, 98, 255, ${0.15 + 0.75 * a})` : `rgba(239, 83, 80, ${0.15 + 0.75 * a})`;
}

function Heatmap({ params, setParams, linkGroup, openPanel }: PanelProps<HeatmapParams>) {
  const [, setSym] = useLink<string>('symbol', { group: linkGroup });
  const [text, setText] = useState(params.symbols.join(', '));
  const { value, error } = usePolled<{ symbols: string[]; matrix: number[][] }>('correlation', { symbols: params.symbols, days: params.days }, 10_000);
  return (
    <div className="tm-heat">
      <div className="tm-heat-bar">
        {Object.entries(PRESETS).map(([k, v]) => (
          <button key={k} className={params.symbols.join() === v.join() ? 'on' : ''} onClick={() => (setParams({ symbols: v }), setText(v.join(', ')))}>
            {k}
          </button>
        ))}
        <input
          className="tm-input"
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && setParams({ symbols: text.split(/[,\s]+/).map(s => s.trim().toUpperCase()).filter(Boolean) })}
          title="Symbols, comma separated (Enter)"
          spellCheck={false}
        />
        <select value={params.days} onChange={e => setParams({ days: Number(e.target.value) })}>
          {[20, 60, 120, 250].map(d => (
            <option key={d} value={d}>
              {d} days
            </option>
          ))}
        </select>
      </div>
      {error && <div className="acme-error">{error}</div>}
      {value && (
        <div className="tm-heat-grid" style={{ gridTemplateColumns: `90px repeat(${value.symbols.length}, minmax(44px, 1fr))` }}>
          <span />
          {value.symbols.map(s => (
            <span key={s} className="tm-heat-col">
              {s}
            </span>
          ))}
          {value.symbols.map((r, i) => (
            <Fragment key={r}>
              <span className="tm-heat-row" onClick={() => setSym(r)}>
                {r}
              </span>
              {value.matrix[i].map((v, j) => (
                <span
                  key={`${r}-${j}`}
                  className="tm-heat-cell"
                  style={{ background: i === j ? '#2a2e39' : color(v) }}
                  title={`${r} vs ${value.symbols[j]}: ${v.toFixed(2)} (${params.days} days of daily changes)`}
                  onClick={() => setSym(r)}
                  onDoubleClick={() =>
                    i !== j &&
                    openPanel('tchart', { symbol: r, res: '1D', range: '6M', chartType: 'line', scale: 'percent', compare: [value.symbols[j]], panel: '', studies: [], __link: null }, `${r} vs ${value.symbols[j]}`, { direction: 'right' })
                  }
                >
                  {i === j ? '' : v.toFixed(2)}
                </span>
              ))}
            </Fragment>
          ))}
        </div>
      )}
      <p className="tm-muted">Correlation of daily changes (yields and spreads in bp, prices in points). Double-click a cell to chart the pair.</p>
    </div>
  );
}

export function heatmapPanel() {
  return definePanel<HeatmapParams>({
    kind: 'heatmap',
    label: 'Correlation',
    title: 'Correlation',
    description: 'Correlation heatmap of daily changes',
    defaultParams: { symbols: PRESETS['Cross-asset'], days: 60 },
    linkable: true,
    component: Heatmap,
  });
}
