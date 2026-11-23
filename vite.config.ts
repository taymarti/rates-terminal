import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// The rates terminal's web app: version C (index.html) and version B (b.html), served on :4070 and proxied to the
// terminal's Deephaven server (server/, :10050; VITE_PROXY_URL to change it), so the browser talks to one origin.
// With no server, C runs on the in-page mock. `npm run build` makes dist/; `npm run preview` serves it, proxied too.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.VITE_PROXY_URL || 'http://localhost:10050';
  const proxy = {
    '/jsapi': { target, changeOrigin: true }, // dh-core.js, the JS API itself
    '/js-plugins': { target, changeOrigin: true }, // the server's JS plugins (B draws its widgets with them)
    '^/arrow\..*': { target, changeOrigin: true, ws: true }, // the JS API's gRPC-web / websocket traffic
    '^/io\.deephaven\..*': { target, changeOrigin: true, ws: true },
  };
  const file = (p: string) => fileURLToPath(new URL(p, import.meta.url));
  return {
    base: './',
    server: { port: 4070, strictPort: true, proxy },
    preview: { port: 4070, strictPort: true, proxy },
    build: { rollupOptions: { input: { index: file('index.html'), b: file('b.html') } }, chunkSizeWarningLimit: 8000 },
    resolve: {
      // one copy of each: the app's (the @acme and @deephaven packages list them as peer dependencies)
      dedupe: ['react', 'react-dom', 'react-redux', 'redux', 'lightweight-charts'],
      alias: [
        // @deephaven/app-utils default-imports this CommonJS package, which ESM interop breaks: @acme/dh-widgets' shim
        { find: /^@paciolan\/remote-module-loader$/, replacement: file('node_modules/@acme/dh-widgets/dist/remote-module-loader.js') },
      ],
    },
    // Deephaven's packages read process.env and global
    define: { 'process.env': {} },
    optimizeDeps: { esbuildOptions: { define: { global: 'globalThis' } } },
    plugins: [react()],
  };
});
