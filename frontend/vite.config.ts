import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// Build output goes into the Python package so `evaltrack ui` can serve it as
// static files. In dev mode Vite serves from memory and this goes unused.
const distDir = fileURLToPath(new URL("../evaltrack/ui/static", import.meta.url));

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Forward API calls to the FastAPI app so the dev server needs no CORS
      // configuration. Run `evaltrack ui` (default port 8765) in another
      // terminal while developing with `just frontend_dev`.
      "/api": "http://localhost:8765",
    },
  },
  build: {
    outDir: distDir,
    emptyOutDir: true,
  },
});
