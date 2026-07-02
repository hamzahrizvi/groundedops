import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend calls "/api/*". In dev, Vite proxies that to the FastAPI
// backend on :8000, stripping the "/api" prefix. This means the backend
// needs NO CORS config and NO changes at all.
//
// For a production build (`npm run build`), either serve the built files
// from FastAPI (StaticFiles) or set API_BASE and add CORS — see README.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.API_TARGET || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
