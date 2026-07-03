import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend calls "/api/*". Vite proxies that to the FastAPI backend,
// stripping the "/api" prefix, so the backend needs NO CORS and NO changes.
// For a production build, serve dist/ from FastAPI or add CORS (see README).
let lastWarn = 0;
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.API_TARGET || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
        configure: (proxy) => {
          // Replace Vite's noisy per-request stack traces with one concise,
          // throttled line while the backend isn't reachable yet.
          proxy.on("error", () => {
            const now = Date.now();
            if (now - lastWarn > 3000) {
              lastWarn = now;
              console.log(
                "\x1b[33m[api]\x1b[0m backend not reachable yet on " +
                  (process.env.API_TARGET || "http://localhost:8000") +
                  " — still starting or not running."
              );
            }
          });
        },
      },
    },
  },
});
