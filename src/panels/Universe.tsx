/**
 * The universe browser: every symbol (a Treasury, a future, a benchmark, a swap, a trading book), by group, with
 * search. Pick one to send it to the linked panels and see its datasets: each chartable, tabulatable or both (the
 * terminal's SymbolModel, model/symbols.ts). Chart a dataset with chosen indicators, or open it as a table.
 * Tables that belong to no one symbol (news, the calendar) stay in "+ Panel > Table".
 */
import { useMemo, useState } from 'react';
import { useLink } from '@acme/chart-react';
import { STUDY_TYPES, formatChange, formatPrice, studiesFor, upDown } from '@acme/chart-ext';
import { definePanel, instancesOf, useDatasets, useRows, useSymbolModel, type DatasetDef, type PanelProps } from '@acme/app-shell';
import { useInstrument } from './common';

interface UniverseParams {
  group: string | null;
  symbol: string | null;
  q: string;
}

const DEFAULT_STUDY: Record<string, string> = {
  EMA: 'EMA_20', SMA: 'SMA_20', WMA: 'WMA_20', BB: 'BB_20_2', KC: 'KC_20_1.5', DC: 'DC_20', RSI: 'RSI_14', MACD: 'MACD_12_26_9',
  STOCH: 'STOCH_14_3', ATR: 'ATR_14', ROC: 'ROC_10', RVOL: 'RVOL_20', ZSCORE: 'ZSCORE_50', VWAP: 'VWAP', Volume: 'Volume',
};

function Universe({ params, setParams, linkGroup, openPanel }: PanelProps<UniverseParams>) {
  const all = useSymbolModel().useSymbols();
  const [, setSym] = useLink<string>('symbol', { group: linkGroup });
  const quotes = useRows('quotes', ['Sym', 'Last', 'Chg']).value ?? [];
  const byQuote = useMemo(() => new Map(quotes.map(q => [String(q.Sym), q])), [quotes]);
  const [picked, setPicked] = useState<string[]>([]);
  const sel = params.symbol ?? 'ZN';
  const datasets = useDatasets(sel);
  const inst = useInstrument(sel);
  if (!all) return <div className="acme-empty">loading the symbols…</div>;

  const q = params.q.trim().toLowerCase();
  const shown = all.filter(s => (!params.group || s.group === params.group) && (!q || s.sym.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)));
  const groups = [...new Set(all.map(s => s.group ?? s.kind))];
  const info = all.find(s => s.sym === sel);
  const indicators = studiesFor(inst?.has_volume ?? false);
  const chosen = picked.length ? picked : ['EMA_20'];

  const pick = (sym: string) => {
    setParams({ symbol: sym });
    setPicked([]);
    setSym(sym);
  };
  const chart = (d: DatasetDef) => {
    const keys = chosen.map(k => DEFAULT_STUDY[k] ?? k).filter(k => indicators.includes(k.split('_')[0]));
    openPanel(
      'tchart',
      { symbol: sel, dataset: d.key, res: '5m', range: '1D', chartType: d.key === 'main' ? 'candle' : 'line', scale: 'normal', compare: [], panel: '', studies: instancesOf(keys), __link: linkGroup },
      `${sel} ${d.label}`,
      { direction: 'right' },
    );
  };
  const table = (d: DatasetDef) => {
    setSym(sel);
    openPanel('table', { table: null, bySymbol: true, dataset: d.key, __link: linkGroup }, `${sel} · ${d.label}`, { direction: 'below' });
  };

  // the list, under its group headings
  const rows: Array<{ head: string } | { sym: string; name: string }> = [];
  let last = '';
  for (const s of shown.slice(0, 400)) {
    const g = s.group ?? s.kind;
    if (g !== last) rows.push({ head: (last = g) });
    rows.push({ sym: s.sym, name: s.name });
  }

  return (
    <div className="tm-uni-wrap">
      <div className="tm-uni">
        <div className="tm-uni-nav">
          <input className="tm-input" placeholder="Search symbols" value={params.q} onChange={e => setParams({ q: e.target.value })} spellCheck={false} />
          <select className="tm-input" value={params.group ?? ''} onChange={e => setParams({ group: e.target.value || null })}>
            <option value="">All groups ({all.length} symbols)</option>
            {groups.map(g => (
              <option key={g} value={g}>
                {g} ({all.filter(s => (s.group ?? s.kind) === g).length})
              </option>
            ))}
          </select>
          <div className="tm-uni-list">
            {rows.map((r, i) =>
              'head' in r ? (
                <div key={`h${i}`} className="tm-uni-head">{r.head}</div>
              ) : (
                <div key={r.sym} className={`tm-uni-sym${sel === r.sym ? ' on' : ''}`} onClick={() => pick(r.sym)} title={r.name}>
                  <b>{r.sym}</b>
                  <span className="tm-uni-sym-name">{r.name}</span>
                  <span className={`tm-num ${upDown(byQuote.get(r.sym)?.Chg)}`}>{byQuote.has(r.sym) ? formatChange(null, byQuote.get(r.sym)?.Chg) : ''}</span>
                </div>
              ),
            )}
            {rows.length === 0 && <div className="acme-empty">nothing matches</div>}
          </div>
        </div>
        <div className="tm-uni-detail">
          <h3>
            {sel} <small className="tm-muted">{info?.kind ?? ''}</small>
          </h3>
          <p>
            {info?.name ?? ''}
            {byQuote.get(sel) ? ` · ${formatPrice(inst, byQuote.get(sel)?.Last)}` : ''}
          </p>
          <h4>
            Datasets <small>each chartable, tabulatable, or both</small>
          </h4>
          <div className="tm-uni-datasets">
            {datasets.map(d => (
              <div key={d.key} className="tm-uni-ds" title={d.description}>
                <span>{d.label}</span>
                {d.chart && <button onClick={() => chart(d)}>Chart</button>}
                {d.table && <button onClick={() => table(d)}>Table</button>}
              </div>
            ))}
            {datasets.length === 0 && <p className="tm-muted">No datasets for this symbol.</p>}
          </div>
          {datasets.some(d => d.chart) && (
            <>
              <h4>
                Indicators <small>transforms over a charted dataset{inst?.has_volume ? '' : '; no volume: VWAP and Volume left out'}</small>
              </h4>
              <div className="tm-chips">
                {indicators.map(k => {
                  const on = chosen.some(c => c.split('_')[0] === k);
                  return (
                    <button key={k} className={on ? 'on' : ''} title={STUDY_TYPES[k].description} onClick={() => setPicked(on ? chosen.filter(c => c.split('_')[0] !== k) : [...chosen, k])}>
                      {k}
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function universePanel() {
  return definePanel<UniverseParams>({
    kind: 'universe',
    label: 'Universe',
    title: 'Universe',
    description: 'Every symbol and its datasets: chart or tabulate them, with indicators',
    defaultParams: { group: null, symbol: 'ZN', q: '' },
    component: Universe,
  });
}
