/**
 * The dataset catalog: what a trader can pick from. A dataset is chartable (its symbols draw as bars, any
 * resolution, with indicators) and may also have a table view; table-only datasets are snapshots, reference
 * data, books and analytics that don't make sense as a single price series.
 */
import { INSTRUMENTS } from '../model/universe';

export type Frequency = 'tick' | 'sub-second' | '1s' | 'daily' | 'weekly' | 'event' | 'static' | 'live';

export interface Dataset {
  id: string;
  title: string;
  group: string;
  /** chart: symbols you can chart; table: table only */
  kind: 'chart' | 'table';
  description: string;
  /** published table for the table view */
  table?: string;
  /** chartable symbols (filled from the universe for chart datasets) */
  symbols?: string[];
  frequency: Frequency;
  live: boolean;
  /** what a chart of it starts with */
  defaultRes?: string;
  defaultStudies?: string[];
  defaultType?: 'candle' | 'bar' | 'line' | 'area';
  /** the column a row click puts on the link bus as the symbol */
  linkColumn?: string;
  /** unit of the chartable values */
  unit?: string;
}

export const FUTURES_ROLLS = ['ZT-ZTH7', 'ZF-ZFH7', 'ZN-ZNH7', 'TN-TNH7', 'ZB-ZBH7', 'UB-UBH7'];

const syms = (dataset: string) => INSTRUMENTS.filter(i => i.dataset === dataset).map(i => i.sym);

export const DATASETS: Dataset[] = [
  // ------------------------------------------------ chartable
  { id: 'ust_yields', title: 'Treasury yields (on-the-run)', group: 'Rates: cash', kind: 'chart', table: 'ust_curve', frequency: 'tick', live: true, unit: '%',
    description: 'Benchmark yields 1M to 30Y from the on-the-run bills, notes and bonds.', defaultRes: '5m', defaultStudies: ['EMA_20', 'EMA_50'], defaultType: 'candle', linkColumn: 'Sym' },
  { id: 'curve_spreads', title: 'Curve spreads and butterflies', group: 'Rates: cash', kind: 'chart', frequency: 'tick', live: true, unit: 'bp',
    description: '3M/10Y, 2s10s, 5s30s, 2s5s10s and 5s10s30s in basis points. Type any other spread in the symbol search (e.g. 100*(US7Y-US5Y)).', defaultRes: '15m', defaultStudies: ['SMA_20', 'ZSCORE_50'], defaultType: 'line' },
  { id: 'ust_cash', title: 'Treasury cash bonds (by CUSIP)', group: 'Rates: cash', kind: 'chart', table: 'ust_cash', frequency: 'tick', live: true, unit: 'price (32nds)',
    description: 'Every bill, note, bond and TIPS in the universe. Chart a CUSIP for its price; the table has yield, risk, richness and repo.', defaultRes: '15m', defaultStudies: ['BB_20_2'], linkColumn: 'Cusip' },
  { id: 'ust_futures', title: 'Treasury futures', group: 'Rates: futures', kind: 'chart', table: 'futures_contracts', frequency: 'tick', live: true, unit: 'price (32nds)',
    description: 'ZT, Z3N, ZF, ZN, TN, TWE, ZB, UB: the Dec 2026 front and Mar 2027 deferred contracts. First notice is Nov 30, so the roll is under way.', defaultRes: '5m', defaultStudies: ['VWAP', 'Volume'], linkColumn: 'Sym' },
  { id: 'futures_rolls', title: 'Futures calendar rolls', group: 'Rates: futures', kind: 'chart', frequency: 'tick', live: true, unit: 'price',
    description: 'Front minus deferred for each Treasury future (Dec/Mar). Roll pricing reflects carry and the delivery option.', defaultRes: '15m', defaultStudies: ['SMA_20'], defaultType: 'line' },
  { id: 'stir', title: 'SOFR and fed funds futures', group: 'Rates: money markets', kind: 'chart', table: 'stir_strip', frequency: 'tick', live: true, unit: 'price',
    description: 'The 3-month SOFR strip and 30-day fed funds; 100 minus the implied rate.', defaultRes: '15m', defaultStudies: ['EMA_20'], linkColumn: 'Sym' },
  { id: 'funding', title: 'Funding and reference rates', group: 'Rates: money markets', kind: 'chart', table: 'repo_rates', frequency: 'daily', live: true, unit: '%',
    description: 'SOFR, the effective fed funds rate and overnight GC repo. Fixings change once a day.', defaultRes: '1D', defaultStudies: [], defaultType: 'line' },
  { id: 'swaps', title: 'SOFR swaps and swap spreads', group: 'Rates: swaps & inflation', kind: 'chart', table: 'swap_curve', frequency: 'tick', live: true, unit: '% / bp',
    description: 'SOFR OIS swap rates 1Y to 30Y and swap spreads to Treasuries (bp, negative beyond 2Y).', defaultRes: '15m', defaultStudies: ['EMA_20'] },
  { id: 'inflation', title: 'TIPS and breakevens', group: 'Rates: swaps & inflation', kind: 'chart', frequency: 'tick', live: true, unit: '%',
    description: '5Y and 10Y TIPS real yields and breakeven inflation.', defaultRes: '15m', defaultStudies: ['SMA_20'] },
  { id: 'etfs', title: 'Treasury ETFs', group: 'Rates: cash', kind: 'chart', frequency: 'tick', live: true, unit: 'price',
    description: 'TLT, IEF and SHY: duration exposure on the long, intermediate and short ends.', defaultRes: '5m', defaultStudies: ['VWAP', 'Volume'] },
  { id: 'cross_asset', title: 'Cross-asset', group: 'Cross-asset', kind: 'chart', frequency: 'tick', live: true, unit: 'price',
    description: 'Equity index (ES, NQ), crude, gold, EUR and JPY futures, and the Euro-Bund, for correlation and risk-on/off context.', defaultRes: '5m', defaultStudies: ['EMA_20', 'Volume'] },
  { id: 'volatility', title: 'Volatility indices', group: 'Cross-asset', kind: 'chart', frequency: 'tick', live: true, unit: 'index',
    description: 'MOVE (Treasury implied vol) and VIX.', defaultRes: '15m', defaultStudies: ['SMA_20'], defaultType: 'line' },
  { id: 'ust_cash_yields', title: 'Treasury cash yields (by CUSIP)', group: 'Rates: cash', kind: 'chart', frequency: 'tick', live: true, unit: '%',
    description: 'The yield of every bill, note, bond and TIPS: <CUSIP>.Y.', defaultRes: '15m', defaultStudies: ['EMA_20'] },
  { id: 'ust_idio', title: 'Idio: rich/cheap by CUSIP', group: 'Rates: cash', kind: 'chart', frequency: 'tick', live: true, unit: 'bp',
    description: "Each issue's yield against the fitted curve, in bp (negative: rich). <CUSIP>.IDIO. Mean-reverting over weeks; on-the-runs trade rich.", defaultRes: '1h', defaultStudies: ['SMA_20', 'ZSCORE_50'], defaultType: 'line' },
  // ------------------------------------------------ table only
  { id: 'deliverable_basket', title: 'Deliverable baskets and CTD', group: 'Rates: futures', kind: 'table', table: 'deliverable_basket', frequency: 'live', live: true,
    description: 'Every deliverable bond per contract: conversion factor, gross basis, carry, net basis, implied repo and CTD rank.', linkColumn: 'Cusip' },
  { id: 'fomc_path', title: 'FOMC path (priced)', group: 'Rates: money markets', kind: 'table', table: 'fomc_path', frequency: 'live', live: true,
    description: 'The implied policy rate after each meeting from the STIR curve, cumulative and per meeting, with a cut probability.' },
  { id: 'repo', title: 'Repo: GC and specials', group: 'Rates: money markets', kind: 'table', table: 'repo_rates', frequency: 'live', live: true,
    description: 'GC repo by term and overnight specials for the on-the-run issues.', linkColumn: 'Cusip' },
  { id: 'auctions', title: 'Treasury auctions', group: 'Calendar & news', kind: 'table', table: 'treasury_auctions', frequency: 'event', live: false,
    description: 'Coupon auction results (tail, bid-to-cover, allotment) and the upcoming schedule.' },
  { id: 'econ_calendar', title: 'Economic calendar', group: 'Calendar & news', kind: 'table', table: 'econ_calendar', frequency: 'event', live: true,
    description: 'Releases, auctions and FOMC decisions a week either side of today. Actuals fill in as they print, with the curve move.' },
  { id: 'news', title: 'Headline tape', group: 'Calendar & news', kind: 'table', table: 'news', frequency: 'event', live: true,
    description: 'Releases, auction results and market headlines as they cross, each with its curve move.' },
  { id: 'cftc_cot', title: 'CFTC positioning (TFF)', group: 'Positioning', kind: 'table', table: 'cftc_cot', frequency: 'weekly', live: false,
    description: 'Asset manager, leveraged fund and dealer net positions per contract, weekly for 26 weeks.' },
  { id: 'rv_monitor', title: 'Relative value monitor', group: 'Analytics', kind: 'table', table: 'rv_monitor', frequency: 'live', live: true,
    description: 'Every symbol against its own daily history: 20/60-day z-scores, 1-year percentile, realized vol, RSI and trend. Click a row to chart it.', linkColumn: 'Sym' },
  { id: 'book', title: 'Order books (depth)', group: 'Market data (raw)', kind: 'table', table: 'book', frequency: 'sub-second', live: true,
    description: '15 levels a side for every traded symbol. The DOM panel draws it as a ladder.', linkColumn: 'Sym' },
  { id: 'trades', title: 'Time and sales', group: 'Market data (raw)', kind: 'table', table: 'trades', frequency: 'tick', live: true,
    description: 'Every trade: price, size, aggressor side and venue (last 4000).', linkColumn: 'Sym' },
  { id: 'volume_profile', title: 'Volume at price', group: 'Market data (raw)', kind: 'table', table: 'volume_profile', frequency: 'tick', live: true,
    description: "Today's traded volume at each price level, split by aggressor.", linkColumn: 'Sym' },
  { id: 'quotes', title: 'Quotes (all symbols)', group: 'Market data (raw)', kind: 'table', table: 'quotes', frequency: 'sub-second', live: true,
    description: 'Last, bid/ask, open/high/low and the change from the previous close for every symbol.', linkColumn: 'Sym' },
  { id: 'bars_1m', title: 'Minute bars (all symbols)', group: 'Market data (raw)', kind: 'table', table: 'bars_1m', frequency: 'tick', live: true,
    description: 'The 1-minute base tier: five days of OHLCV for every symbol, one table.', linkColumn: 'Sym' },
  { id: 'bars_1d', title: 'Daily bars (all symbols)', group: 'Market data (raw)', kind: 'table', table: 'bars_1d', frequency: 'daily', live: true,
    description: 'Two years of daily OHLCV for every symbol.', linkColumn: 'Sym' },
  { id: 'books', title: 'Books: risk and P&L today', group: 'Desk (hypothetical)', kind: 'table', table: 'books', frequency: 'live', live: true,
    description: 'Each book: positions, gross, DV01 against its limit, 1-day 99% VaR, P&L since 07:00 ET. The Books panel shows it with the attribution and risk.' },
  { id: 'positions', title: 'Positions', group: 'Desk (hypothetical)', kind: 'table', table: 'positions', frequency: 'live', live: true,
    description: 'Every position by book: quantity, price, DV01, idio, carry per day, P&L today (start-of-day positions and new trades).', linkColumn: 'Sym' },
  { id: 'pnl_attribution', title: 'P&L attribution', group: 'Desk (hypothetical)', kind: 'table', table: 'pnl_attribution', frequency: 'live', live: true,
    description: 'P&L since 07:00 ET by source, per book: carry, roll, level, slope, curvature, policy, idio, basis, new trades, fees, residual.' },
  { id: 'book_risk', title: 'Book risk', group: 'Desk (hypothetical)', kind: 'table', table: 'book_risk', frequency: 'live', live: true,
    description: 'DV01 by key-rate bucket (bills, 2Y, 5Y, 10Y, 30Y), exposure to each curve factor, VaR and idio risk.' },
  { id: 'pnl_timeseries', title: 'P&L through the day', group: 'Desk (hypothetical)', kind: 'table', table: 'pnl_timeseries', frequency: 'live', live: true,
    description: "Each book's P&L every 5 minutes since the open, then every 30 seconds." },
  { id: 'rfq_blotter', title: 'RFQ blotter', group: 'Desk (hypothetical)', kind: 'table', table: 'rfq_blotter', frequency: 'event', live: true,
    description: 'Client RFQs today: client, venue, side, size, level, outcome (done, traded away, covered, passed).', linkColumn: 'Sym' },
  { id: 'desk_trades', title: 'Desk executions', group: 'Desk (hypothetical)', kind: 'table', table: 'desk_trades', frequency: 'event', live: true,
    description: 'Every execution today: won RFQs and the futures hedges after them.', linkColumn: 'Sym' },
  { id: 'rfq_flow', title: 'RFQ flow by CUSIP', group: 'Desk (hypothetical)', kind: 'table', table: 'rfq_flow', frequency: 'live', live: true,
    description: 'Requests, notional, done and client buy/sell per CUSIP per 5 minutes.', linkColumn: 'Sym' },
  { id: 'security_master', title: 'Security master', group: 'Desk (hypothetical)', kind: 'table', table: 'security_master', frequency: 'live', live: true,
    description: 'Per CUSIP: outstanding, SOMA holdings, free float, street volume, desk position, idio, deliverability and CTD, RFQ count and hit ratio.', linkColumn: 'Cusip' },
  { id: 'cusip_auctions', title: 'Auction results by CUSIP', group: 'Desk (hypothetical)', kind: 'table', table: 'cusip_auctions', frequency: 'event', live: false,
    description: "Each issue's original auction and reopenings: size, high and WI yield, tail, bid-to-cover, allotment.", linkColumn: 'Cusip' },
  { id: 'instruments', title: 'Instrument reference', group: 'Reference & system', kind: 'table', table: 'instruments', frequency: 'static', live: false,
    description: 'Every symbol: name, kind, unit, tick, notation, multiplier, exchange calendar.', linkColumn: 'Sym' },
  { id: 'chart_nodes', title: 'Chart node cache', group: 'Reference & system', kind: 'table', table: 'chart_nodes', frequency: 'live', live: true,
    description: 'The shared chart nodes the server holds for open charts: who holds them, rows, build time.' },
  { id: 'annotations', title: 'Saved drawings and price lines', group: 'Reference & system', kind: 'table', table: 'chart_annotations', frequency: 'event', live: true,
    description: 'Every saved price line and drawing (this browser stands in for the server, so they survive reloads).' },
  { id: 'alerts', title: 'Alerts', group: 'Reference & system', kind: 'table', table: 'chart_alerts', frequency: 'event', live: true,
    description: 'Price, line and study alerts, evaluated by the server every cycle.' },
  { id: 'backtest_runs', title: 'Backtest runs', group: 'Reference & system', kind: 'table', table: 'backtest_runs', frequency: 'event', live: true,
    description: 'Every backtest: status, progress, P&L.' },
];

for (const d of DATASETS) if (d.kind === 'chart' && d.id !== 'futures_rolls') d.symbols = syms(d.id);
DATASETS.find(d => d.id === 'futures_rolls')!.symbols = FUTURES_ROLLS;
