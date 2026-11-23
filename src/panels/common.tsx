/**
 * What the terminal's panels share: the user, the page's chart_api client, and the dataset catalog and instrument
 * reference from it (fetched once per page). Formatting by notation (32nds, %, bp) is @acme/chart-ext's
 * formatPrice / formatChange; live filtered rows and polling are @acme/app-shell's hooks.
 */
import { useEffect, useState } from 'react';
import type { ApiClient } from '@acme/dh-bindings';
import { requestOnce, usePolled as usePolledWith, useApiClient } from '@acme/app-shell';

export const API = 'chart_api';

export interface DatasetInfo {
  id: string;
  title: string;
  group: string;
  kind: 'chart' | 'table';
  description: string;
  table?: string;
  symbols: string[];
  frequency: string;
  live: boolean;
  defaultRes?: string;
  defaultStudies?: string[];
  defaultType?: string;
  linkColumn?: string;
  unit?: string;
  rows: number | null;
}

export interface InstrumentInfo {
  sym: string;
  name: string;
  unit: string;
  tick: number | null;
  thirty_seconds: boolean;
  decimals: number;
  has_volume: boolean;
  kind: string;
  dataset: string;
  session: string;
  description: string;
}

let user = 'trader';
export const setUser = (u: string) => (user = u);
export const currentUser = () => user;

/** the page's chart_api client (one stream per page) */
export function useApi(): ApiClient | null {
  return useApiClient(API, `user:${user}`).value;
}

/** request a chart_api method every `ms` (and when params change); the latest result */
export function usePolled<T>(method: string, params: Record<string, unknown> | null, ms: number) {
  return usePolledWith<T>(useApi(), method, params, ms);
}

export function useCatalog(): { datasets: DatasetInfo[]; instruments: Map<string, InstrumentInfo> } | null {
  const client = useApi();
  const [out, setOut] = useState<{ datasets: DatasetInfo[]; instruments: Map<string, InstrumentInfo> } | null>(null);
  useEffect(() => {
    if (!client) return;
    let done = false;
    Promise.all([requestOnce<DatasetInfo[]>(client, 'datasets'), requestOnce<InstrumentInfo[]>(client, 'instruments')]).then(([d, i]) => {
      if (!done) setOut({ datasets: d, instruments: new Map(i.map(x => [x.sym, x])) });
    });
    return () => {
      done = true;
    };
  }, [client]);
  return out;
}

/** a symbol's metadata, fetched again for symbols the server learned later (typed spreads) */
export function useInstrument(sym: string): InstrumentInfo | null {
  const cat = useCatalog();
  const client = useApi();
  const [extra, setExtra] = useState<InstrumentInfo | null>(null);
  const known = cat?.instruments.get(sym) ?? null;
  useEffect(() => {
    setExtra(null);
    if (!client || known || !cat) return;
    client.request<InstrumentInfo[]>('instruments', { dataset: 'user_spreads' }).then(r => setExtra(r.result.find(i => i.sym === sym) ?? null), () => {});
  }, [client, sym, known, cat]);
  return known ?? extra;
}
