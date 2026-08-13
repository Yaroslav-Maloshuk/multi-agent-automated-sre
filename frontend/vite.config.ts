import { lookup } from "node:dns/promises";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

async function resolveProxyTarget(): Promise<string> {
  if (process.env.VITE_PROXY_TARGET) return process.env.VITE_PROXY_TARGET;
  // `gateway` is the compose service name; `backend` was the legacy one.
  for (const host of ["gateway", "backend"]) {
    try {
      await lookup(host);
      return `http://${host}:8000`;
    } catch {
      // not resolvable on this network — try the next candidate
    }
  }
  return "http://localhost:8000";
}

export default defineConfig(async () => {
  const target = await resolveProxyTarget();
  return {
    plugins: [react()],
    server: {
      host: true,
      port: 5173,
      proxy: {
        "/api": { target, changeOrigin: true },
        "/ws": { target: target.replace(/^http/, "ws"), ws: true },
      },
    },
  };
});
