/**
 * The terminal's chart: @acme/app-shell's chart workspace on chart_api, with the catalog's symbols, each symbol's
 * notation (32nds for Treasuries, % for yields, bp for spreads), the core watchlist, and an instrument card.
 * Panel kind 'tchart' (saved layouts refer to it).
 */
import { useMemo } from 'react';
import { fmt32, formatChange, formatPrice, upDown } from '@acme/chart-ext';
import { chartWorkspacePanel, instancesOf, useRows, useSymbolModel, type ChartInstrument } from '@acme/app-shell';
import type { ToolbarItem } from '@acme/chart-react';
import { currentUser, useInstrument, type InstrumentInfo } from './common';

/** The symbols to pick from: securities and other chartable identities (their datasets are picked on the chart, from
 * the terminal's SymbolModel: a bond's price, yield and idio). Trading books have no chart. */
function useSymbols(): ToolbarItem[] {
  const syms = useSymbolModel().useSymbols();
  return useMemo(() => (syms ?? []).filter(s => s.kind !== 'book').map(s => ({ key: s.sym, label: s.sym, description: s.name, group: s.group })), [syms]);
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
    useInstrument: sym => useInstrument(sym) as ChartInstrument | null,
    watchlist: { table: 'watchlist_core', title: 'Core' },
    panels: [{ key: 'info', label: 'Instrument', render: (sym, inst) => <InfoCard sym={sym} inst={inst as InstrumentInfo | null} /> }],
    statusNote: 'simulated data ticks around the clock',
    defaultParams: { symbol: 'ZN', studies: instancesOf(['EMA_20', 'EMA_50', 'Volume']) },
  });
}
