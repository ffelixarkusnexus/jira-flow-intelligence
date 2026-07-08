import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// Build into the Forge resource directory so `forge deploy` packages the bundle.
// `base: "./"` is critical — Forge serves the Custom UI from a CDN path, so
// asset URLs in index.html must be relative; absolute "/assets/..." 404s.
export default defineConfig({
  plugins: [react()],
  root: __dirname,
  base: "./",
  build: {
    outDir: resolve(__dirname, "..", "static", "main"),
    emptyOutDir: true,
    sourcemap: false,
  },
});
