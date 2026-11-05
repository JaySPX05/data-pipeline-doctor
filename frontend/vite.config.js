import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    host: true,
    // In dev, /api calls go to the FastAPI backend.
    proxy: { "/api": process.env.VITE_API_PROXY || "http://localhost:8000" },
  },
});
