/**
 * The terminal's symbols and their datasets (@acme/app-shell's SymbolModel). A symbol is the identity of a thing: a
 * Treasury (its CUSIP), a future, a benchmark yield, a swap, a trading book. Its datasets are the views of it, each
 * chartable (a chart API symbol), tabulatable (a table filtered to it), or both:
 *
 *   bond       Price, Yield, Idio (charts) · Auctions, RFQs, Positions, Deliverable into (tables)
 *   future     Price (chart) · Deliverable basket, Time & sales, Order book, Positions (tables)
 *   benchmark  Yield, and its on-the-run bond's Price and Idio (charts)
 *   book       Positions, Executions, RFQs, P&L through the day (tables)
 *   others     their one series (price, rate, level)
 *
 * The catalog still names a bond's yield and idio as series of their own (<cusip>.Y, <cusip>.IDIO); those names are
 * datasets of the bond here, never symbols (resolve maps them back).
 */
import { useMemo } from 'react';
import { useRows, type DatasetDef, type SymbolInfo, type SymbolModel } from '@acme/app-shell';
import { otrFor } from './universe';
import { useCatalog, type InstrumentInfo } from '../panels/common';

const SERIES: Array<[suffix: string, key: string, label: string]> = [['.Y', 'yield', 'Yield'], ['.IDIO', 'idio', 'Idio (bp)']];

/** a series' own name (X.Y, X.IDIO) as its symbol and dataset */
function resolve(name: string): { sym: string; dataset: string } | null {
  for (const [suffix, key] of SERIES) if (name.endsWith(suffix)) return { sym: name.slice(0, -suffix.length), dataset: key };
  return null;
}

const mainLabel = (i: InstrumentInfo | undefined) =>
  !i ? 'Price' : i.unit === '%' ? (i.kind === 'yield' ? 'Yield' : 'Rate') : i.unit === 'bp' ? 'Level (bp)' : i.kind === 'index' ? 'Level' : 'Price';

function useSymbols(): SymbolInfo[] | null {
  const cat = useCatalog();
  const books = useRows('books', ['Book', 'Name']).value;
  return useMemo(() => {
    if (!cat) return null;
    const title = new Map(cat.datasets.map(d => [d.id, d.title]));
    const out: SymbolInfo[] = [...cat.instruments.values()]
      .filter(i => !resolve(i.sym))
      .map(i => ({ sym: i.sym, kind: i.kind, name: i.name, group: title.get(i.dataset) ?? i.dataset }));
    for (const b of books ?? []) out.push({ sym: String(b.Book), kind: 'book', name: b.Book === 'ALL' ? 'The whole desk' : String(b.Name), group: 'Trading books' });
    return out;
  }, [cat, books]);
}

function useDatasets(sym: string): DatasetDef[] {
  const cat = useCatalog();
  const books = useRows('books', ['Book']).value;
  return useMemo(() => {
    if (!sym || !cat) return [];
    if ((books ?? []).some(b => b.Book === sym)) {
      const where: Record<string, string> = sym === 'ALL' ? {} : { Book: sym };
      return [
        { key: 'positions', label: 'Positions', table: { table: 'positions', where, linkColumn: 'Sym' } },
        { key: 'executions', label: 'Executions', table: { table: 'desk_trades', where, linkColumn: 'Sym' } },
        { key: 'rfqs', label: 'RFQs', table: { table: 'rfq_blotter', where, linkColumn: 'Sym' } },
        { key: 'pnl', label: 'P&L through the day', table: { table: 'pnl_timeseries', where } },
      ];
    }
    const inst = cat.instruments.get(sym);
    const out: DatasetDef[] = [{ key: 'main', label: mainLabel(inst), chart: sym }];
    for (const [suffix, key, label] of SERIES) if (cat.instruments.has(sym + suffix)) out.push({ key, label, chart: sym + suffix });
    const otr = inst?.kind === 'yield' ? otrFor(sym) : undefined;
    if (otr) {
      out.push({ key: 'otr_price', label: `On-the-run price (${otr.cusip})`, chart: otr.cusip });
      if (cat.instruments.has(`${otr.cusip}.IDIO`)) out.push({ key: 'otr_idio', label: 'On-the-run idio (bp)', chart: `${otr.cusip}.IDIO` });
    }
    if (inst?.kind === 'bond') {
      out.push(
        { key: 'auctions', label: 'Auctions', table: { table: 'cusip_auctions', where: { Cusip: sym } } },
        { key: 'rfqs', label: 'RFQs', table: { table: 'rfq_blotter', where: { Sym: sym } } },
        { key: 'positions', label: 'Positions', table: { table: 'positions', where: { Sym: sym }, linkColumn: 'Book' } },
        { key: 'deliverable', label: 'Deliverable into', table: { table: 'deliverable_basket', where: { Cusip: sym }, linkColumn: 'Contract' } },
      );
    }
    if (inst?.kind === 'future' && inst.dataset === 'ust_futures') {
      out.push(
        { key: 'basket', label: 'Deliverable basket', table: { table: 'deliverable_basket', where: { Contract: sym }, linkColumn: 'Cusip' } },
        { key: 'tape', label: 'Time & sales', table: { table: 'trades', where: { Sym: sym } } },
        { key: 'book', label: 'Order book', table: { table: 'book', where: { Sym: sym } } },
        { key: 'positions', label: 'Positions', table: { table: 'positions', where: { Sym: sym }, linkColumn: 'Book' } },
      );
    }
    return out;
  }, [sym, cat, books]);
}

export const TERMINAL_SYMBOLS: SymbolModel = { useSymbols, useDatasets, resolve };
