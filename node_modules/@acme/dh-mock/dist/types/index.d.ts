/**
 * @acme/dh-mock: a Deephaven server that runs in the page, for demos, offline work and tests. An app on it
 * connects exactly as it would to a real server, `connect({ serverUrl: 'mock://app', dh: createMockDh(server) })`,
 * and its panels can't tell the difference.
 *
 *   Engine, RowSource, SeriesSource     an update graph of in-memory tables that tick (engine.ts)
 *   MockServer, createMockDh            published objects, and the part of the JS API the bindings and shell use (dh.ts)
 *   Api, Reply, ApiError, p             the request-API protocol (dh_chart_ext.rpc), server side (rpc.ts)
 *
 * and a mock of dh_chart_ext's chart server over an app's market (BarMarket: instruments and bar tiers):
 *
 *   Tier                                bars of every symbol on one time grid (1s, 1m, 1d)
 *   NodeCache, BarNode                  charts on demand as shared, reference-counted nodes (dh_chart_ext.nodes)
 *   mockChartApi                        chart_api: charts, studies, workspaces, the update graph (dh_chart_ext.api)
 *   annotationTable, alertTable         the annotation and alert tables, with the alert engine (dh_chart_ext.store / alerts)
 *   registerBacktests                   strategies and backtests in a Web Worker (dh_chart_ext.backtests)
 *   MarkerSource, TierTable             graph nodes for non-tables, and every symbol's bars as one table
 *   addMarketTicker                     the market first in each cycle, then the nodes and tables built on its bars
 *
 * Persistent state (workspaces, strategies, annotations, alerts) lives in localStorage under setStoragePrefix().
 */
export * from './engine';
export { FilterCondition, FilterValue, MockColumn, MockServer, MockTable, MockWidget, Sort, createMockDh, type Published } from './dh';
export { Api, ApiError, Ctx, Reply, p, type MethodDef, type ParamSpec, type Stream } from './rpc';
export { Tier, type SeriesArrays } from './tiers';
export type { BarMarket, MockInstrument } from './market';
export { BarNode, NodeCache, RESOLUTIONS, resolutionName, resolutionSecs } from './nodes';
export { MarkerSource, TierTable } from './markers';
export { UserDocs, alertTable, annotationTable, setStoragePrefix, sharedAnnotation } from './stores';
export { EXAMPLE_STRATEGY, checkStrategy, registerBacktests } from './backtests';
export { mockChartApi, type MockChartApiOptions } from './chart-api';
export { addMarketTicker, type MarketTickerOptions } from './clock';
