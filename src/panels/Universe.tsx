/**
 * The universe browser: every dataset the server has, chartable and table-only, by group, with search. Pick one
 * to see what it is, its symbols (live), its table's columns and the indicators its symbols can take; then
 * chart a symbol with chosen indicators, open the table, or send a symbol to the linked panels.
 */
import { useMemo, useState } from 'react';
import { useLink } from '@acme/chart-react';
import { STUDY_TYPES, formatChange, formatPrice, studiesFor, upDown } from '@acme/chart-ext';
import { definePanel, instancesOf, useRows, type PanelProps } from '@acme/app-shell';
import { useCatalog, type DatasetInfo } from './common';

interface UniverseParams {
  group: string | null;
  dataset: string | null;
  kind: 'all' | 'chart' | 'table';
  q: string;
}

const FREQ_LABEL: Record<string, string> = {
  tick: 'tick', 'sub-second': '< 1s', '1s': '1s', daily: 'daily', weekly: 'weekly', event: 'events', static: 'static', live: 'live',
};

function Universe({ params, setParams, linkGroup, openPanel }: PanelProps<UniverseParams>) {
  const cat = useCatalog();
  const [, setSym] = useLink<string>('symbol', { group: linkGroup });
  const quotes = useRows('quotes', ['Sym', 'Last', 'Chg', 'ChgBp']).value ?? [];
  const byQuote = useMemo(() => new Map(quotes.map(q => [String(q.Sym), q])), [quotes]);
  const [picked, setPicked] = useState<string[]>([]);
  const [focusSym, setFocusSym] = useState<string | null>(null);
  if (!cat) return <div className="acme-empty">loading the catalog…</div>;

  const q = params.q.trim().toLowerCase();
  const matches = (d: DatasetInfo) =>
    (params.kind === 'all' || d.kind === params.kind) &&
    (!q ||
      d.title.toLowerCase().includes(q) ||
      d.description.toLowerCase().includes(q) ||
      d.id.includes(q) ||
      d.symbols.some(s => s.toLowerCase().includes(q) || cat.instruments.get(s)?.name.toLowerCase().includes(q)));
  const shown = cat.datasets.filter(matches);
  const groups = [...new Set(cat.datasets.map(d => d.group))];
  const inGroup = shown.filter(d => !params.group || d.group === params.group);
  const ds = cat.datasets.find(d => d.id === params.dataset) ?? inGroup[0] ?? null;
  const hasVol = ds ? ds.symbols.some(s => cat.instruments.get(s)?.has_volume) : false;
  const indicators = ds?.kind === 'chart' ? studiesFor(hasVol) : [];
  const chosen = picked.length ? picked : ds?.defaultStudies ?? [];

  const chart = (sym: string, where: 'right' | 'within' = 'right') => {
    const defaults = { EMA: 'EMA_20', SMA: 'SMA_20', WMA: 'WMA_20', BB: 'BB_20_2', KC: 'KC_20_1.5', DC: 'DC_20', RSI: 'RSI_14', MACD: 'MACD_12_26_9', STOCH: 'STOCH_14_3', ATR: 'ATR_14', ROC: 'ROC_10', RVOL: 'RVOL_20', ZSCORE: 'ZSCORE_50', VWAP: 'VWAP', Volume: 'Volume' } as Record<string, string>;
    const keys = chosen.map(k => (k.includes('_') || k === 'VWAP' || k === 'Volume' ? k : defaults[k] ?? k)).filter(k => indicators.includes(k.split('_')[0]));
    openPanel(
      'tchart',
      { symbol: sym, res: ds?.defaultRes ?? '5m', range: (ds?.defaultRes ?? '5m').endsWith('D') ? '1Y' : '1D', chartType: ds?.defaultType ?? 'candle', scale: 'normal', compare: [], panel: '', studies: instancesOf(keys), __link: linkGroup },
      `${sym} ${ds?.defaultRes ?? '5m'}`,
      { direction: where },
    );
  };
  const openTable = (d: DatasetInfo) =>
    d.table && openPanel('table', { table: d.table, linkColumn: d.linkColumn ?? undefined, __link: linkGroup }, d.table, { direction: 'below' });

  return (
    <div className="tm-uni-wrap">
    <div className="tm-uni">
      <div className="tm-uni-nav">
        <input className="tm-input" placeholder="Search datasets and symbols" value={params.q} onChange={e => setParams({ q: e.target.value })} spellCheck={false} />
        <div className="tm-seg">
          {(['all', 'chart', 'table'] as const).map(k => (
            <button key={k} className={params.kind === k ? 'on' : ''} onClick={() => setParams({ kind: k })}>
              {k === 'all' ? `All ${cat.datasets.length}` : k === 'chart' ? 'Chartable' : 'Table only'}
            </button>
          ))}
        </div>
        <select className="tm-input" value={params.group ?? ''} onChange={e => setParams({ group: e.target.value || null, dataset: null })}>
          <option value="">All groups ({shown.length})</option>
          {groups.map(g => (
            <option key={g} value={g}>
              {g} ({shown.filter(d => d.group === g).length})
            </option>
          ))}
        </select>
        <div className="tm-uni-list">
          {inGroup.map(d => (
            <div
              key={d.id}
              className={`tm-uni-card${ds?.id === d.id ? ' on' : ''}`}
              onClick={() => {
                setParams({ dataset: d.id });
                setPicked([]);
                setFocusSym(null);
              }}
              onDoubleClick={() => (d.kind === 'chart' && d.symbols[0] ? chart(d.symbols[0]) : openTable(d))}
              title={d.description}
            >
              <div className="tm-uni-title">
                <span className={`tm-badge ${d.kind}`}>{d.kind === 'chart' ? 'CHART' : 'TABLE'}</span>
                {d.title}
              </div>
              <div className="tm-uni-meta">
                {d.live && <i className="tm-live" title="live" />}
                {d.group} · {FREQ_LABEL[d.frequency] ?? d.frequency}
                {d.symbols.length > 0 && <span> · {d.symbols.length} symbols</span>}
              </div>
            </div>
          ))}
          {inGroup.length === 0 && <div className="acme-empty">nothing matches</div>}
        </div>
      </div>
      {ds && (
        <div className="tm-uni-detail">
          <h3>
            <span className={`tm-badge ${ds.kind}`}>{ds.kind === 'chart' ? 'CHART' : 'TABLE'}</span> {ds.title}
          </h3>
          <p>{ds.description}</p>
          <div className="tm-uni-actions">
            {ds.kind === 'chart' && ds.symbols[0] && <button onClick={() => chart(focusSym ?? ds.symbols[0])}>Chart {focusSym ?? ds.symbols[0]}</button>}
            {ds.table && <button onClick={() => openTable(ds)}>Open table</button>}
            {ds.kind === 'chart' && ds.symbols.length > 1 && (
              <button
                title="One chart of the first symbol with the others compared on a % scale"
                onClick={() =>
                  openPanel('tchart', { symbol: ds.symbols[0], res: ds.defaultRes ?? '5m', range: '5D', chartType: 'line', scale: 'percent', compare: ds.symbols.slice(1, 4), panel: '', studies: [], __link: null }, `${ds.symbols[0]} vs`, { direction: 'right' })
                }
              >
                Compare
              </button>
            )}
            {ds.kind === 'chart' && ds.symbols.length > 1 && (
              <button
                title="The correlation of daily changes between this dataset's symbols"
                onClick={() => openPanel('heatmap', { symbols: ds.symbols.slice(0, 12), days: 60 }, `Correlation: ${ds.title}`, { direction: 'right' })}
              >
                Correlation
              </button>
            )}
          </div>
          {ds.kind === 'chart' && (
            <>
              <h4>
                Indicators <small>{hasVol ? 'all apply' : 'no volume: VWAP and Volume left out'}</small>
              </h4>
              <div className="tm-chips">
                {indicators.map(k => {
                  const on = chosen.some(c => c.split('_')[0] === k);
                  return (
                    <button
                      key={k}
                      className={on ? 'on' : ''}
                      title={STUDY_TYPES[k].description}
                      onClick={() => setPicked(on ? chosen.filter(c => c.split('_')[0] !== k) : [...chosen, k])}
                    >
                      {k}
                    </button>
                  );
                })}
              </div>
              <h4>
                Symbols <small>click: send to linked panels · double-click: new chart</small>
              </h4>
              <div className="tm-uni-syms">
                {ds.symbols.map(s => {
                  const i = cat.instruments.get(s);
                  const qq = byQuote.get(s);
                  return (
                    <div
                      key={s}
                      className={`tm-uni-sym${focusSym === s ? ' on' : ''}`}
                      onClick={() => {
                        setFocusSym(s);
                        setSym(s);
                      }}
                      onDoubleClick={() => chart(s)}
                    >
                      <b>{s}</b>
                      <span className="tm-uni-sym-name">{i?.name ?? ''}</span>
                      <span className="tm-num">{formatPrice(i, qq?.Last)}</span>
                      <span className={`tm-num ${upDown(qq?.Chg)}`}>{formatChange(i, qq?.Chg)}</span>
                    </div>
                  );
                })}
              </div>
            </>
          )}
          {ds.rows != null && (
            <p className="tm-muted">
              {ds.table}: {ds.rows.toLocaleString()} rows when the catalog loaded{ds.linkColumn ? `; a row click sends ${ds.linkColumn} to linked panels` : ''}
            </p>
          )}
        </div>
      )}
    </div>
    </div>
  );
}

export function universePanel() {
  return definePanel<UniverseParams>({
    kind: 'universe',
    label: 'Universe',
    title: 'Universe',
    description: 'Every dataset: chartable and table-only, with indicators',
    defaultParams: { group: null, dataset: 'ust_futures', kind: 'all', q: '' },
    component: Universe,
  });
}
