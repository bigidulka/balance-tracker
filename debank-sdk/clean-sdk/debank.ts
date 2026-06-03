/**
 * DeBank SDK — clean edition
 *
 * Reverse-engineered request signing + portfolio & transaction endpoints.
 * No dependencies beyond Node.js stdlib.
 *
 * Usage:
 *   const db = new DeBank();
 *   const portfolio = await db.portfolio("0x...");
 *   const txs = await db.transactions("0x...");
 *   const all = await db.allTransactions("0x...");
 */

import https from "node:https";
import path from "node:path";
import fs from "node:fs";

// ─── Signer runtime ────────────────────────────────────────────────

type ModuleFactory = (
  module: { exports: unknown },
  exports: Record<string, unknown>,
  require: Require
) => void;

type Require = {
  (id: number): unknown;
  d: (
    exports: Record<string, unknown>,
    defs: Record<string, () => unknown>
  ) => void;
  n: (m: unknown) => (() => unknown) & { a: () => unknown };
  g: typeof globalThis;
};

interface Signer {
  OK: (
    params: Record<string, string | number>,
    method: string,
    path: string,
    opts?: { version?: string }
  ) => { signature: string; nonce: string; ts: number; version: string };
}

function loadSigner(): Signer {
  const raw = fs.readFileSync(
    path.join(__dirname, "debank-signer-modules.json"),
    "utf-8"
  );
  const sources: Record<string, string> = JSON.parse(raw);

  const cache = new Map<number, { exports: unknown }>();
  const factories = new Map<number, ModuleFactory>();
  for (const [id, src] of Object.entries(sources)) {
    factories.set(
      Number(id),
      new Function(
        "module",
        "exports",
        "require",
        `return (${src})(module, exports, require);`
      ) as ModuleFactory
    );
  }

  const req = ((id: number) => {
    if (cache.has(id)) return cache.get(id)!.exports;
    const factory = factories.get(id);
    if (!factory) throw new Error(`signer module ${id} not found`);
    const mod = { exports: {} as Record<string, unknown> };
    cache.set(id, mod);
    factory(mod, mod.exports as Record<string, unknown>, req);
    return mod.exports;
  }) as Require;

  req.d = (exports, defs) => {
    for (const key of Object.keys(defs)) {
      if (!Object.prototype.hasOwnProperty.call(exports, key)) {
        Object.defineProperty(exports, key, {
          enumerable: true,
          get: defs[key],
        });
      }
    }
  };
  req.n = (m) => {
    const g = (() => m) as (() => unknown) & { a: () => unknown };
    g.a = g;
    return g;
  };
  req.g = globalThis;

  return req(35653) as Signer;
}

// ─── Types ──────────────────────────────────────────────────────────

export interface Token {
  id: string;
  chain: string;
  name: string;
  symbol: string;
  display_symbol: string | null;
  optimized_symbol: string;
  decimals: number;
  logo_url: string | null;
  price: number;
  price_24h_change: number | null;
  amount: number;
  is_core: boolean | null;
  is_verified: boolean | null;
  is_scam: boolean;
  is_suspicious: boolean;
}

export interface Portfolio {
  totalUsd: number;
  chains: string[];
  tokens: Token[];
}

export interface TxTransfer {
  amount: number;
  price: number;
  to_addr: string;
  token_id: string;
}

export interface TxItem {
  id: string;
  chain: string;
  cate_id: string;
  time_at: number;
  is_scam: boolean;
  other_addr: string;
  sends: TxTransfer[];
  receives: TxTransfer[];
  tx: {
    name: string;
    from_addr: string;
    to_addr: string;
    status: number;
    value: number;
    eth_gas_fee?: number;
    usd_gas_fee?: number;
  };
}

export interface TxPage {
  history_list: TxItem[];
  cate_dict: Record<string, { id: string; name: string }>;
  token_dict: Record<string, unknown>;
  project_dict: Record<string, unknown>;
}

// ─── SDK ────────────────────────────────────────────────────────────

const BASE = "https://api.debank.com";
const API_KEY = "***REMOVED***";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36";

export class DeBank {
  private signer: Signer;

  constructor() {
    this.signer = loadSigner();
  }

  // ── Public API ──────────────────────────────────────────────────

  /** Total USD balance from net-curve endpoint */
  async balance(addr: string): Promise<number> {
    const data = await this.get<{
      data?: { usd_value_list?: [number, number][] };
    }>("/asset/total_net_curve", { user_addr: addr, days: 1 });
    const pts = data.data?.usd_value_list ?? [];
    if (!pts.length) throw new Error("no balance data");
    return pts[pts.length - 1][1];
  }

  /** Full portfolio: balance + chains + all tokens */
  async portfolio(addr: string): Promise<Portfolio> {
    const [bal, chains, tokens] = await Promise.all([
      this.balance(addr),
      this.usedChains(addr),
      this.tokenBalances(addr),
    ]);
    return { totalUsd: bal, chains, tokens };
  }

  /** Single page of transactions */
  async transactions(
    addr: string,
    opts: { chain?: string; startTime?: number; count?: number } = {}
  ): Promise<TxPage> {
    const res = await this.get<{ data: TxPage }>("/history/list", {
      user_addr: addr,
      chain: opts.chain ?? "",
      start_time: opts.startTime ?? 0,
      page_count: opts.count ?? 20,
    });
    return res.data;
  }

  /** Paginate through all transactions (up to maxPages) */
  async allTransactions(
    addr: string,
    opts: { chain?: string; count?: number; maxPages?: number } = {}
  ): Promise<TxItem[]> {
    const count = opts.count ?? 20;
    const max = opts.maxPages ?? 50;
    const all: TxItem[] = [];
    const seen = new Set<string>();
    let cursor = 0;

    for (let i = 0; i < max; i++) {
      const page = await this.transactions(addr, {
        chain: opts.chain,
        startTime: cursor,
        count,
      });
      if (!page.history_list?.length) break;

      for (const tx of page.history_list) {
        const key = `${tx.id}:${tx.tx?.from_addr}`;
        if (!seen.has(key)) {
          seen.add(key);
          all.push(tx);
        }
      }

      if (page.history_list.length < count) break;
      const last = page.history_list[page.history_list.length - 1];
      if (last.time_at === cursor) break;
      cursor = last.time_at;
    }

    return all;
  }

  // ── Internal endpoints ──────────────────────────────────────────

  private async usedChains(addr: string): Promise<string[]> {
    const res = await this.get<{ data: { chains: string[] } }>(
      "/user/used_chains",
      { id: addr }
    );
    return res.data.chains;
  }

  private async tokenBalances(addr: string): Promise<Token[]> {
    const res = await this.get<{ data: Token[] }>(
      "/token/cache_balance_list",
      { user_addr: addr }
    );
    return res.data;
  }

  // ── HTTP + signing ─────────────────────────────────────────────

  private sign(
    apiPath: string,
    params: Record<string, string | number>
  ): Record<string, string> {
    const prev = (globalThis as any).__ggn;
    (globalThis as any).__ggn = () => "debank.com";
    try {
      const s = this.signer.OK(params, "GET", apiPath, { version: "v2" });
      return {
        "X-API-Key": API_KEY,
        "X-API-Time": String(Math.floor(Date.now() / 1000)),
        "x-api-ts": String(s.ts),
        "x-api-nonce": s.nonce,
        "x-api-ver": s.version,
        "x-api-sign": s.signature,
        source: "web",
        account: JSON.stringify({
          random_at: Math.floor(Date.now() / 1000),
          random_id: this.rid(),
          user_addr: null,
          connected_addr: null,
        }),
        referer: "https://debank.com/",
        "user-agent": UA,
        accept: "application/json",
      };
    } finally {
      (globalThis as any).__ggn = prev;
    }
  }

  private get<T>(
    apiPath: string,
    params: Record<string, string | number>
  ): Promise<T> {
    const headers = this.sign(apiPath, params);
    const url = new URL(`${BASE}${apiPath}`);
    for (const [k, v] of Object.entries(params))
      url.searchParams.set(k, String(v));

    return new Promise((resolve, reject) => {
      const req = https.get(url.toString(), { headers, timeout: 30_000 }, (res) => {
        let buf = "";
        res.on("data", (c: Buffer) => (buf += c));
        res.on("end", () => {
          try {
            resolve(JSON.parse(buf));
          } catch {
            reject(new Error(`bad json: ${buf.slice(0, 200)}`));
          }
        });
      });
      req.on("error", reject);
      req.on("timeout", () => {
        req.destroy();
        reject(new Error("request timeout"));
      });
    });
  }

  private rid(): string {
    const hex = "abcdef0123456789";
    let r = "";
    for (let i = 0; i < 32; i++) r += hex[Math.floor(Math.random() * 16)];
    return r;
  }
}
