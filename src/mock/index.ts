/**
 * The terminal's in-page backend, for when no Deephaven server is up: the market simulator (model/market) on
 * @acme/dh-mock's engine, every table the panels read (tables.ts, desk.ts), and chart_api with the terminal's own
 * methods (api.ts). The page connects to it exactly as it would to the server:
 *
 *   const backend = createBackend();
 *   <DeephavenRoot connect={backend.connect}>
 */
import { connect } from '@acme/dh-bindings';
import {
  ApiError,
  B,
  Engine,
  L,
  MockServer,
  NodeCache,
  RowSource,
  S,
  addMarketTicker,
  alertTable,
  annotationTable,
  createMockDh,
  p,
  registerBacktests,
  setStoragePrefix,
  sharedAnnotation,
  type MarkerSource,
  type TierTable,
} from '@acme/dh-mock';
import { Market } from '../model/market';
import { terminalApi } from './api';
import { DATASETS, FUTURES_ROLLS } from './catalog';
import { buildDesk } from './desk';
import { buildTables } from './tables';

export function createBackend() {
  setStoragePrefix('terminal.mock.');
  const engine = new Engine(250);
  const server = new MockServer(engine);
  const market = new Market();
  for (const r of FUTURES_ROLLS) {
    const inst = market.addExpression(r);
    inst.dataset = 'futures_rolls';
    inst.name = `${r.split('-')[0]} Dec/Mar roll`;
  }
  const nodes = new NodeCache(engine, market);
  server.publishTable('chart_nodes', nodes.table);

  // the market first in each cycle; the bar tables and the simulator's graph node exist once the tables are built
  let tierTables: TierTable[] = [];
  let sim: MarkerSource | null = null;
  addMarketTicker(engine, market, nodes, now => market.live(now), { tierTables: () => tierTables, marker: () => sim });

  const tables = buildTables(engine, server, market);
  for (const r of FUTURES_ROLLS) tables.addSessionFor(r);
  tierTables = tables.tierTables;
  sim = tables.sim;

  // a few shared annotations from a colleague, so sharing shows up on a fresh page
  const now = Date.now();
  const zn = market.value('ZN');
  annotationTable(engine, server, [
    sharedAnnotation('ZN', 'seed-zn-1', 'price_line', { price: Math.round((zn + 0.25) * 64) / 64 }),
    sharedAnnotation('ZN', 'seed-zn-2', 'price_line', { price: Math.round((zn - 0.3) * 64) / 64 }),
    sharedAnnotation('US10Y', 'seed-10y-1', 'drawing', { type: 'hray', points: [{ time: Math.floor(now / 1000) - 3 * 86400, price: Math.round((market.value('US10Y') + 0.06) * 1000) / 1000 }] }),
    sharedAnnotation('T2S10S', 'seed-2s10s', 'drawing', { type: 'hline', points: [{ time: Math.floor(now / 1000) - 86400, price: Math.round(market.value('T2S10S') + 5) }] }),
  ]);
  alertTable(engine, server, market, nodes, { quotes: tables.quotes });
  const api = terminalApi(engine, server, market, nodes, tables);
  registerBacktests(api, engine, server, market, ['ZN', 'ZB', 'ZF']);
  const desk = buildDesk(engine, server, market, tables);
  api.method(
    'security',
    'Everything about one Treasury: amount, SOMA, desk position by book, price, idio, CTD and basis, auctions, RFQ and street volume. A future gives its CTD.',
    [p('sym', 'string')],
    (_c, a) => {
      const out = desk.security(String(a.sym));
      if (!out) throw new ApiError(`${a.sym} is not a Treasury cash bond or future`, 'not_found');
      return out;
    },
  );
  server.publishApi('chart_api', api);

  const catalog = new RowSource(engine, 'datasets', [S('Id'), S('Title'), S('Group'), S('Kind'), S('Table'), S('Frequency'), B('Live'), L('Symbols'), S('Description')], {
    keyCols: ['Id'], kind: 'source', description: 'the dataset catalog',
  });
  catalog.put(DATASETS.map(d => ({ Id: d.id, Title: d.title, Group: d.group, Kind: d.kind, Table: d.table ?? '', Frequency: d.frequency, Live: d.live, Symbols: d.symbols?.length ?? 0, Description: d.description })));
  server.publishTable('datasets', catalog);

  engine.start();
  const dh = createMockDh(server);
  const backend = {
    engine, server, market, nodes, api, dh,
    connect: () => connect({ serverUrl: 'mock://terminal', dh: dh as never }),
  };
  (globalThis as { mockBackend?: unknown }).mockBackend = backend; // for the console
  return backend;
}

export type Backend = ReturnType<typeof createBackend>;
