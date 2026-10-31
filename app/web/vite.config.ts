import { defineConfig, loadEnv } from "vite";
import preact from "@preact/preset-vite";

// dev: http://localhost:5173 with /api proxied to the FastAPI server (uv run garmin-app)
// override: API_PORT=8767 VITE_PORT=5175 npm run dev
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", ["API_PORT", "VITE_PORT"]);
  return {
    plugins: [preact()],
    server: { port: Number(env.VITE_PORT || 5173), proxy: { "/api": `http://127.0.0.1:${env.API_PORT || "8765"}` } },
    build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  };
});
