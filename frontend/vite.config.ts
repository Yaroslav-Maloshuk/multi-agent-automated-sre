import { lookup } from "node:dns/promises";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

async function resolveProxyTarget(): Promise<string> {
  if (process.env.VITE_PROXY_TARGET) return process.env.VITE_PROXY_TARGET;
  try {
    await lookup("backend");
    return "http://backend:8000";
  } catch {
    return "http://localhost:8000";
  }
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
