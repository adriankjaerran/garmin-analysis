import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

// dev: http://localhost:5173 with /api proxied to the FastAPI server (uv run garmin-app)
export default defineConfig({
  plugins: [preact()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
});
