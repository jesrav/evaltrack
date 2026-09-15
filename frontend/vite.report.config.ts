import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import { fileURLToPath } from "node:url";

// The static report page: one HTML file with its script and styles inlined,
// written next to the dashboard bundle so the package ships both. The
// dashboard build empties that directory, so `npm run build` runs this second.
const distDir = fileURLToPath(new URL("../evaltrack/ui/static", import.meta.url));

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: {
    outDir: distDir,
    emptyOutDir: false,
    rollupOptions: {
      input: fileURLToPath(new URL("./report.html", import.meta.url)),
    },
  },
});
