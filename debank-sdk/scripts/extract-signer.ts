import fs from "fs";
import path from "path";
import { chromium } from "playwright-core";

const OUTPUT_DIR = path.resolve(__dirname, "../src/generated");
const OUTPUT_FILE = path.join(OUTPUT_DIR, "debank-signer-modules.json");
const TARGET_URL = "https://debank.com/profile/0x3ec68709334f64ee4927891627f0b395c6ff6754/history";
const ROOT_MODULE_ID = 35653;
const SEED_MODULE_IDS = [63335, 89465, 80792, 75066, 49320, 21030] as const;

async function main() {
  const browser = await chromium.launch({ headless: true });

  try {
    const context = await browser.newContext({
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
      viewport: { width: 1440, height: 1100 },
    });

    const page = await context.newPage();
    await page.goto(TARGET_URL, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForTimeout(5000);

    const modules = await page.evaluate(({ rootModuleId, seedModuleIds }) => {
      const browserWindow = globalThis as unknown as {
        webpackChunkdefi_insight_react: unknown[];
      };

      let req: (((id: number) => unknown) & { m?: Record<string, Function> }) | undefined;
      browserWindow.webpackChunkdefi_insight_react.push([
        [Math.random()],
        {},
        function (runtimeRequire: typeof req) {
          req = runtimeRequire;
        },
      ]);

      if (!req || !req.m) {
        throw new Error("Не удалось получить webpack runtime");
      }

      for (const seedId of seedModuleIds) {
        try {
          req(seedId);
        } catch {
          // some modules may still be unavailable on this route
        }
      }

      const availableIds = new Set(Object.keys(req.m).map((key) => Number(key)));
      const result: Record<string, string> = {};
      const queue: number[] = [rootModuleId, ...seedModuleIds];
      const visited = new Set<number>();

      while (queue.length > 0) {
        const id = queue.shift();
        if (typeof id !== "number" || visited.has(id)) {
          continue;
        }

        visited.add(id);
        const factory = req.m[String(id)] ?? req.m[id as unknown as string];
        if (!factory) {
          throw new Error(`Модуль ${id} не найден`);
        }

        const source = factory.toString();
        result[String(id)] = source;

        const headerMatch = source.match(/^\(([^,]+),([^,]+),([^\)]+)\)=>/);
        const requireName = headerMatch?.[3]?.trim();
        if (!requireName) {
          continue;
        }

        const escapedRequireName = requireName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const regex = new RegExp(`${escapedRequireName}\\((\\d+)\\)`, "g");
        let match: RegExpExecArray | null;
        while ((match = regex.exec(source)) !== null) {
          const depId = Number(match[1]);
          if (availableIds.has(depId)) {
            queue.push(depId);
          }
        }
      }

      return result;
    }, { rootModuleId: ROOT_MODULE_ID, seedModuleIds: [...SEED_MODULE_IDS] });

    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    fs.writeFileSync(OUTPUT_FILE, JSON.stringify(modules, null, 2));
    await context.close();

    console.log(`Signer modules saved to ${OUTPUT_FILE}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
