import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// API のエンドポイントはソースに書かない（.env の VITE_SBP_API_URL を読む、不変条件 12）。
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
