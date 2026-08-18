import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend calls "/api/*". Vite proxies that to the FastAPI backend,
// stripping the "/api" prefix, so the backend needs NO CORS and NO changes.
// For a production build, serve dist/ from FastAPI or add CORS (see README).
//
// The default target is the NATIVE one. It used to be "http://backend:8000" —
// the Docker Compose service name, which resolves only inside the Compose
// network — so the documented native workflow (`npm run dev`, or `npm install`
// + `npm run dev` from a clone) proxied every API call to a host that does not
// exist and the UI just showed errors. Docker sets API_TARGET explicitly in
// docker-compose.override.yml instead, so both paths work from their own
// defaults rather than one silently borrowing the other's.
const API_TARGET = process.env.API_TARGET || "http://127.0.0.1:8000";

let lastWarn = 0;
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API_TARGET,
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
                  API_TARGET +
                  " — still starting or not running."
              );
            }
          });
        },
      },
    },
  },
});
