import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api/* é repassado para o FastAPI, assim o front não precisa saber a porta da API
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.EVCO_API_URL || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
