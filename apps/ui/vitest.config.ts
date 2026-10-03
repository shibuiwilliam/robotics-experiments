import { defineConfig } from "vitest/config";

// 純粋関数だけを検査する（ADR-0008: 表示の正しさより「人の判断が正しく基盤に戻ること」）。
export default defineConfig({
  test: { environment: "node", include: ["test/**/*.test.ts"] },
});
