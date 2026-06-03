import fs from "fs";
import path from "path";
import https from "https";

type Chain = {
  id: string;
  name: string;
  svg_logo_url?: string;
  white_logo_url?: string;
  logo_url?: string;
};

type ChainListResponse = {
  data?: {
    chains?: Chain[];
  };
};

const OUTPUT_DIR = path.resolve(__dirname, "../assets/chain-icons");
const MANIFEST_PATH = path.join(OUTPUT_DIR, "manifest.json");

function fetchBuffer(url: string): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      if (!res.statusCode || res.statusCode >= 400) {
        reject(new Error(`HTTP ${res.statusCode} for ${url}`));
        return;
      }

      const chunks: Buffer[] = [];
      res.on("data", (chunk: Buffer) => chunks.push(chunk));
      res.on("end", () => resolve(Buffer.concat(chunks)));
    }).on("error", reject);
  });
}

function fetchJson<T>(url: string): Promise<T> {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      if (!res.statusCode || res.statusCode >= 400) {
        reject(new Error(`HTTP ${res.statusCode} for ${url}`));
        return;
      }

      let data = "";
      res.on("data", (chunk: Buffer) => {
        data += chunk.toString();
      });
      res.on("end", () => {
        try {
          resolve(JSON.parse(data) as T);
        } catch (error) {
          reject(error);
        }
      });
    }).on("error", reject);
  });
}

function chooseIcon(chain: Chain): { url: string; ext: string; variant: string } | null {
  if (chain.svg_logo_url) {
    return { url: chain.svg_logo_url, ext: ".svg", variant: "svg_logo_url" };
  }
  if (chain.white_logo_url) {
    return { url: chain.white_logo_url, ext: ".png", variant: "white_logo_url" };
  }
  if (chain.logo_url) {
    return { url: chain.logo_url, ext: ".png", variant: "logo_url" };
  }
  return null;
}

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const response = await fetchJson<ChainListResponse>("https://api.debank.com/chain/list");
  const chains = response.data?.chains ?? [];

  const manifest: Array<{
    id: string;
    name: string;
    file: string | null;
    variant: string | null;
    source_url: string | null;
  }> = [];

  for (const chain of chains) {
    const icon = chooseIcon(chain);
    if (!icon) {
      manifest.push({
        id: chain.id,
        name: chain.name,
        file: null,
        variant: null,
        source_url: null,
      });
      continue;
    }

    const fileName = `${chain.id}${icon.ext}`;
    const filePath = path.join(OUTPUT_DIR, fileName);
    const data = await fetchBuffer(icon.url);
    fs.writeFileSync(filePath, data);

    manifest.push({
      id: chain.id,
      name: chain.name,
      file: fileName,
      variant: icon.variant,
      source_url: icon.url,
    });

    console.log(`Downloaded ${chain.id} -> ${fileName}`);
  }

  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2));
  console.log(`Saved manifest to ${MANIFEST_PATH}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
