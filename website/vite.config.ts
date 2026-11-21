import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE || "/",
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-react": ["react", "react-dom", "react-router-dom"],
          "vendor-marked": ["marked"],
          "data-docs": ["./src/generated/docs.json"],
          "data-catalogs": ["./src/generated/catalogs.json"],
          "data-layers": ["./src/generated/layers.json"],
          "data-releases": ["./src/generated/releases.json"],
          "data-search": ["./src/generated/search-index.json"],
        },
      },
    },
  },
});
