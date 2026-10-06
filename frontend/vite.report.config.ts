import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import { fileURLToPath } from "node:url";

// The static report page: one HTML file with its script and styles inlined,
// written into the report package, which ships it and needs no dashboard.
const distDir = fileURLToPath(
  new URL("../evaltrack/report/static", import.meta.url),
);

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  // The page is one file. Nothing from public/ belongs beside it.
  publicDir: false,
  build: {
    outDir: distDir,
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL("./report.html", import.meta.url)),
    },
  },
});
