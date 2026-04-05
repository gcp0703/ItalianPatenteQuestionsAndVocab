import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const frontendPort = Number(process.env.VITE_PORT ?? 5183);
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8500";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: frontendPort,
    proxy: {
      "/api": apiProxyTarget,
      "/img_sign": apiProxyTarget
    }
  }
});
