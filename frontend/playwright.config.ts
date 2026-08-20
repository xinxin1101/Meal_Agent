import { defineConfig } from "@playwright/test";

const pythonCommand = process.env.MEALPILOT_E2E_PYTHON
  ?? (process.platform === "win32" ? "..\\.venv\\Scripts\\python.exe" : "python");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  use: { baseURL: process.env.MEALPILOT_E2E_BASE_URL ?? "http://127.0.0.1:5174", trace: "retain-on-failure" },
  webServer: [
    { command: "pnpm dev --host 127.0.0.1 --port 5174", url: "http://127.0.0.1:5174", reuseExistingServer: false, env: { VITE_API_PROXY_TARGET: "http://127.0.0.1:8001" } },
    {
      command: `${pythonCommand} -m uvicorn mealpilot.main:app --app-dir ../backend --host 127.0.0.1 --port 8001`,
      url: "http://127.0.0.1:8001/health",
      reuseExistingServer: false,
      env: {
        SILICONFLOW_API_KEY: "",
        SILICONFLOW_MODEL: "",
        MEALPILOT_ALLOWED_HOSTS: "localhost,127.0.0.1,testserver",
        MEALPILOT_RUNTIME_DIR: "../.runtime/e2e",
        MEALPILOT_INCLUDE_SAMPLE_RECIPES: "true",
        MEALPILOT_PUBLISHED_RECIPES_PATH: "../tests/fixtures/no-runtime-recipes.json",
        MEALPILOT_NUTRITION_DATA_PATH: "../data/nutrition/foods.sample.json",
      },
    },
  ],
});
