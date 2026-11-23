/**
 * Version B's page: Deephaven's bootstrap chain (the JS API, the server's JS plugins, the connection), then the
 * terminal (b.tsx), loaded lazily: some of Deephaven's modules need the `dh` global the bootstrap sets.
 */
import React from 'react';
import { createRoot } from 'react-dom/client';
// first: Deephaven's base theme variables and resets
import '@deephaven/components/scss/BaseStyleSheet.scss';
import { DeephavenWidgetsRoot } from '@acme/dh-widgets';

const TerminalB = React.lazy(() => import('./b'));

createRoot(document.getElementById('root')!).render(
  <DeephavenWidgetsRoot apiUrl="./jsapi/dh-core.js" pluginsUrl="./js-plugins/">
    <TerminalB />
  </DeephavenWidgetsRoot>,
);
