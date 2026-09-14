import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Keep application code cacheable independently from dependencies.
          // Splitting individual React ecosystem packages creates circular
          // chunks because router/query dependencies also import React.
          return id.includes("node_modules") ? "vendor" : undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": process.env.E2E_API_TARGET ?? "http://localhost:8001",
      "/openapi.json": process.env.E2E_API_TARGET ?? "http://localhost:8001",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    css: true,
  },
});
