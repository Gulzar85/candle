import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "path";

export default defineConfig({
  root: ".",
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
      "@css": resolve(__dirname, "../static/css"),
    },
  },
  plugins: [tailwindcss()],
  server: {
    fs: {
      allow: [resolve(__dirname, "..")],
    },
  },
  build: {
    outDir: "../static/dist",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, "src/main.ts"),
      },
      output: {
        entryFileNames: "assets/[name].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
  define: {
    "import.meta.env.DEV": JSON.stringify(process.env.NODE_ENV !== "production"),
  },
});