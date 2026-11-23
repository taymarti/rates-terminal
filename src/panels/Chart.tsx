/**
 * The terminal's chart: @acme/app-shell's chart workspace on chart_api, with the catalog's symbols, each symbol's
 * notation (32nds for Treasuries, % for yields, bp for spreads), the core watchlist, and an instrument card.
 * Panel kind 'tchart' (saved layouts refer to it).
 */
import { useMemo } from 'react';
import { fmt32, formatChange, formatPrice, upDown } from '@acme/chart-ext';
import { chartWorkspacePanel, instancesOf, useRows, type ChartInstrument, type SeriesChoice } from '@acme/app-shell';
import type { ToolbarItem } from '@acme/chart-react';
import { currentUser, useCatalog, useInstrument, type InstrumentInfo } from './common';

/** A bond's own series beside its price: <cusip>.Y (yield) and <cusip>.IDIO (rich/cheap to the curve, bp). */
const SERIES: Array<[suffix: string, key: string, label: string]> = [['.Y', 'yield', 'Yield'], ['.IDIO', 'idio', 'Idio (bp)']];

/** A series symbol's base and series key, or null for a base symbol. */
export function baseOf(sym: string): { base: string; series: string } | null {
  for (const [suffix, key] of SERIES) if (sym.endsWith(suffix)) return { base: sym.slice(0, -suffix.length), series: key };
  return null;
}

/** The symbols to pick from: securities and other base symbols; their series are chosen on the chart. */
function useSymbols(): ToolbarItem[] {
  const cat = useCatalog();
  return useMemo(
    () => (cat ? [...cat.instruments.values()].filter(i => !baseOf(i.sym)).map(i => ({ key: i.sym, label: i.sym, description: i.name, group: i.dataset })) : []),
    [cat],
  );
}

/** What a symbol can chart: its price, and for a bond its yield and idio. */
function useSeries(sym: string): SeriesChoice[] {
  const cat = useCatalog();
  return useMemo(() => {
    const own = SERIES.filter(([suffix]) => cat?.instruments.has(sym + suffix));
    if (!own.length) return [];
    return [{ key: 'price', label: 'Price', sym }, ...own.map(([suffix, key, label]) => ({ key, label, sym: sym + suffix }))];
  }, [cat, sym]);
}

/** Quote, day range and reference data for the chart's symbol. */
function InfoCard({ sym, inst }: { sym: string; inst: InstrumentInfo | null }) {
  const rows = useRows('quotes', ['Sym', 'Last', 'Bid', 'Ask', 'Chg', 'ChgBp', 'Open', 'High', 'Low', 'PrevClose', 'Volume']).value ?? [];
  const q = rows.find(r => r.Sym === sym);
  const ref = useRows('instruments', ['Sym', 'Name', 'Kind', 'Unit', 'Tick', 'Notation', 'Multiplier', 'Session', 'Description']).value?.find(r => r.Sym === sym);
  const line = (k: string, v: unknown) =>
    v == null || v === '' ? null : (
      <div className="tm-kv" key={k}>
        <span>{k}</span>
        <b>{String(v)}</b>
      </div>
    );
  return (
    <div className="tm-info">
      <div className="tm-info-sym">{sym}</div>
      <div className="tm-info-name">{inst?.name ?? (ref?.Name as string) ?? ''}</div>
      {q && (
        <>
          <div className={`tm-info-last ${upDown(q.Chg)}`}>
            {formatPrice(inst, q.Last)} <small>{formatChange(inst, q.Chg)}</small>
          </div>
          {line('Bid / Ask', `${formatPrice(inst, q.Bid)} / ${formatPrice(inst, q.Ask)}`)}
          {line('Open', formatPrice(inst, q.Open))}
          {line('Day range', `${formatPrice(inst, q.Low)} – ${formatPrice(inst, q.High)}`)}
          {line('Prev close', formatPrice(inst, q.PrevClose))}
          {inst?.has_volume && line('Volume', Number(q.Volume).toLocaleString())}
        </>
      )}
      <div className="tm-info-sep" />
      {line('Kind', ref?.Kind)}
      {line('Unit', ref?.Unit)}
      {line('Tick', ref?.Tick != null ? (inst?.thirty_seconds ? `${fmt32(Number(ref.Tick) + 0, Number(ref.Tick)).replace(/^0-/, '')} (1/${Math.round(1 / Number(ref.Tick))})` : String(ref.Tick)) : null)}
      {line('Notation', ref?.Notation)}
      {line('Multiplier', ref?.Multiplier != null ? `$${Number(ref.Multiplier).toLocaleString()} per point` : null)}
      {line('Calendar', ref?.Session)}
      {line('Notes', ref?.Description)}
    </div>
  );
}

export { instancesOf };

export function terminalChartPanel() {
  return chartWorkspacePanel({
    kind: 'tchart',
    user: currentUser(),
    description: 'A chart of any symbol or spread: indicators, drawings, alerts, compare',
    useSymbols,
    useSeries,
    baseOf,
    useInstrument: sym => useInstrument(sym) as ChartInstrument | null,
    watchlist: { table: 'watchlist_core', title: 'Core' },
    panels: [{ key: 'info', label: 'Instrument', render: (sym, inst) => <InfoCard sym={sym} inst={inst as InstrumentInfo | null} /> }],
    statusNote: 'simulated data ticks around the clock',
    defaultParams: { symbol: 'ZN', studies: instancesOf(['EMA_20', 'EMA_50', 'Volume']) },
  });
}
