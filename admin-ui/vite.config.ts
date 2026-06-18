import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/admin-api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/admin-api/, ""),
      },
      "/admin-ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
        rewrite: (path) => path.replace(/^\/admin-ws/, "/ws"),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
