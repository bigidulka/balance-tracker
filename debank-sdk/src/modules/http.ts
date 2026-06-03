import https from "https";
import { requireSignerModule } from "../generated/runtime";

const API_BASE_URL = "https://api.debank.com";
const API_KEY = "cd387377-c201-4209-a1a0-0b969a4fecd2";
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36";

interface SignerModule {
  OK: (
    params: Record<string, string | number>,
    method: string,
    canonicalPath: string,
    options?: { version?: string; nonce?: string; timestamp?: string }
  ) => {
    signature: string;
    nonce: string;
    ts: number;
    version: string;
  };
}

interface CurveResponse {
  data?: {
    usd_value_list?: Array<[number, number]>;
  };
  error_code?: number;
  error_msg?: string;
}

interface ApiEnvelope<T> {
  _cache_seconds: number;
  _seconds: number;
  _use_cache: boolean;
  data: T;
  error_code: number;
  error_msg?: string;
}

export interface TokenBalanceItem {
  amount: number;
  balance?: number;
  chain: string;
  credit_score: number;
  decimals: number;
  display_symbol: string | null;
  id: string;
  is_core: boolean | null;
  is_custom?: boolean;
  is_scam: boolean;
  is_suspicious: boolean;
  is_verified: boolean | null;
  is_wallet: boolean | null;
  logo_url: string | null;
  name: string;
  optimized_symbol: string;
  price: number;
  price_24h_change: number | null;
  protocol_id: string;
  raw_amount?: number;
  raw_amount_hex_str: string;
  raw_amount_str?: string;
  symbol: string;
  time_at: number | null;
  total_supply: number;
}

export interface PortfolioApp {
  id: string;
  name: string;
  [key: string]: unknown;
}

export interface PortfolioAppList {
  apps: PortfolioApp[];
  error_apps: unknown[];
}

export type PortfolioProject = Record<string, unknown>;

export interface UsedChainsResponse {
  chains: string[];
}

export interface HistoryTokenTransfer {
  amount: number;
  price: number;
  to_addr: string;
  token_id: string;
}

export interface HistoryTx {
  from_addr: string;
  id: string;
  idx: number;
  message: string | null;
  name: string;
  params: unknown[];
  selector: string | null;
  status: number;
  to_addr: string;
  value: number;
  eth_gas_fee?: number;
  usd_gas_fee?: number;
}

export interface HistoryItem {
  cate_id: string;
  cex_id: string | null;
  chain: string;
  id: string;
  idx: number;
  is_scam: boolean;
  other_addr: string;
  project_id: string | null;
  receives: HistoryTokenTransfer[];
  sends: HistoryTokenTransfer[];
  time_at: number;
  token_approve: unknown | null;
  tx: HistoryTx;
}

export interface HistoryCategory {
  id: string;
  name: string;
}

export interface HistoryTokenMeta {
  chain: string;
  credit_score: number;
  decimals: number;
  display_symbol: string | null;
  id: string;
  is_core: boolean | null;
  is_scam: boolean;
  is_suspicious: boolean;
  is_verified: boolean | null;
  is_wallet: boolean | null;
  logo_url: string | null;
  name: string;
  optimized_symbol: string;
  price: number;
  price_24h_change: number | null;
  protocol_id: string;
  symbol: string;
  time_at: number | null;
  total_supply: number;
}

export interface HistoryListResponse {
  cate_dict: Record<string, HistoryCategory>;
  cex_dict: Record<string, unknown>;
  history_list: HistoryItem[];
  is_big_addr?: boolean;
  memo_dict?: Record<string, unknown>;
  project_dict?: Record<string, unknown>;
  token_dict?: Record<string, HistoryTokenMeta>;
}

export interface HistoryListParams {
  chain?: string;
  start_time?: number;
  page_count?: number;
}

export interface GetAllTransactionsParams {
  chain?: string;
  page_count?: number;
  maxPages?: number;
}

export interface PortfolioSnapshot {
  totalUsd: number;
  usedChains: string[];
  tokens: TokenBalanceItem[];
  apps: PortfolioApp[];
  errorApps: unknown[];
  projects: PortfolioProject[];
}

export interface WalletSnapshot {
  address: string;
  portfolio: PortfolioSnapshot;
  transactions: HistoryListResponse;
  scamTransactions: HistoryItem[];
  fetchedAt: number;
}

function mergeHistoryResponses(pages: HistoryListResponse[]): HistoryListResponse {
  return {
    cate_dict: Object.assign({}, ...pages.map((page) => page.cate_dict ?? {})),
    cex_dict: Object.assign({}, ...pages.map((page) => page.cex_dict ?? {})),
    history_list: pages.flatMap((page) => page.history_list ?? []),
    is_big_addr: pages.some((page) => Boolean(page.is_big_addr)),
    memo_dict: Object.assign({}, ...pages.map((page) => page.memo_dict ?? {})),
    project_dict: Object.assign({}, ...pages.map((page) => page.project_dict ?? {})),
    token_dict: Object.assign({}, ...pages.map((page) => page.token_dict ?? {})),
  };
}

export interface HttpWalletBalanceResult {
  address: string;
  totalUsd: number;
  source: "http-only.asset.total_net_curve" | "http-fallback.token_cache_balance_list";
  headers: Record<string, string>;
  signedAt: number;
}

export class HttpModule {
  async getWalletBalance(address: string): Promise<HttpWalletBalanceResult> {
    const path = "/asset/total_net_curve";
    const params = { user_addr: address, days: 1 };
    const signed = this.signRequest(path, params);
    const url = new URL(`${API_BASE_URL}${path}`);

    for (const [key, value] of Object.entries(params)) {
      url.searchParams.set(key, String(value));
    }

    try {
      const body = await this.requestJson<CurveResponse>(url.toString(), signed.headers);

      if (body.error_code && body.error_code !== 0) {
        throw new Error(`DeBank API error ${body.error_code}: ${body.error_msg ?? "unknown error"}`);
      }

      const points = body.data?.usd_value_list ?? [];
      if (!points.length) {
        throw new Error("DeBank не вернул точки кривой баланса");
      }

      return {
        address,
        totalUsd: points[points.length - 1][1],
        source: "http-only.asset.total_net_curve",
        headers: signed.headers,
        signedAt: Date.now(),
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (!/(429|too fast|rate limit)/i.test(message)) {
        throw error;
      }

      const tokens = await this.getTokenCacheBalanceList(address);
      const totalUsd = tokens.reduce(
        (sum, token) => sum + Number(token.amount ?? 0) * Number(token.price ?? 0),
        0,
      );

      return {
        address,
        totalUsd,
        source: "http-fallback.token_cache_balance_list",
        headers: signed.headers,
        signedAt: Date.now(),
      };
    }
  }

  async getUsedChains(address: string): Promise<string[]> {
    try {
      const body = await this.getJson<ApiEnvelope<UsedChainsResponse>>("/user/used_chains", { id: address });
      return body?.data?.chains ?? [];
    } catch {
      return [];
    }
  }

  async getTokenCacheBalanceList(address: string): Promise<TokenBalanceItem[]> {
    try {
      const body = await this.getJson<ApiEnvelope<TokenBalanceItem[]>>("/token/cache_balance_list", {
        user_addr: address,
      });
      return body?.data ?? [];
    } catch {
      return [];
    }
  }

  async getTokenBalanceList(address: string, chain: string): Promise<TokenBalanceItem[]> {
    const body = await this.getJson<ApiEnvelope<TokenBalanceItem[]>>("/token/balance_list", {
      user_addr: address,
      chain,
    });
    return body.data;
  }

  async getPortfolioApps(address: string): Promise<PortfolioAppList> {
    const body = await this.getJson<ApiEnvelope<PortfolioAppList>>("/portfolio/app_list", {
      user_id: address,
    });
    return body.data;
  }

  async getPortfolioProjects(address: string): Promise<PortfolioProject[]> {
    const body = await this.getJson<ApiEnvelope<PortfolioProject[]>>("/portfolio/project_list", {
      user_addr: address,
    });
    return body.data;
  }

  async getPortfolio(address: string): Promise<PortfolioSnapshot> {
    const [balance, usedChains, tokens, appList, projects] = await Promise.all([
      this.getWalletBalance(address),
      this.getUsedChains(address),
      this.getTokenCacheBalanceList(address),
      this.getPortfolioApps(address),
      this.getPortfolioProjects(address),
    ]);

    return {
      totalUsd: balance.totalUsd,
      usedChains,
      tokens,
      apps: appList.apps,
      errorApps: appList.error_apps,
      projects,
    };
  }

  async getTransactions(address: string, params: HistoryListParams = {}): Promise<HistoryListResponse> {
    const body = await this.getJson<ApiEnvelope<HistoryListResponse>>("/history/list", {
      user_addr: address,
      chain: params.chain ?? "",
      start_time: params.start_time ?? 0,
      page_count: params.page_count ?? 20,
    });
    return body.data;
  }

  async getScamTransactions(address: string, params: HistoryListParams = {}): Promise<HistoryItem[]> {
    const history = await this.getTransactions(address, params);
    return history.history_list.filter((item) => item.is_scam);
  }

  async getAllTransactions(address: string, params: GetAllTransactionsParams = {}): Promise<HistoryListResponse> {
    const pageCount = params.page_count ?? 20;
    const maxPages = params.maxPages ?? 50;
    const pages: HistoryListResponse[] = [];
    const seenIds = new Set<string>();
    let startTime = 0;

    for (let page = 0; page < maxPages; page += 1) {
      const current = await this.getTransactions(address, {
        chain: params.chain,
        start_time: startTime,
        page_count: pageCount,
      });

      if (!current || !Array.isArray(current.history_list)) {
        break;
      }

      if (!current.history_list.length) {
        break;
      }

      const filteredItems = current.history_list.filter((item) => {
        const key = `${item.id}:${item.idx}`;
        if (seenIds.has(key)) {
          return false;
        }
        seenIds.add(key);
        return true;
      });

      pages.push({
        ...current,
        history_list: filteredItems,
      });

      if (current.history_list.length < pageCount) {
        break;
      }

      const lastItem = current.history_list[current.history_list.length - 1];
      if (!lastItem || lastItem.time_at === startTime) {
        break;
      }

      startTime = lastItem.time_at;
    }

    return mergeHistoryResponses(pages);
  }

  async getAllScamTransactions(address: string, params: GetAllTransactionsParams = {}): Promise<HistoryItem[]> {
    const history = await this.getAllTransactions(address, params);
    return history.history_list.filter((item) => item.is_scam);
  }

  async getWalletSnapshot(address: string, params: HistoryListParams = {}): Promise<WalletSnapshot> {
    const [portfolio, transactions] = await Promise.all([
      this.getPortfolio(address),
      this.getTransactions(address, params),
    ]);

    return {
      address,
      portfolio,
      scamTransactions: transactions.history_list.filter((item) => item.is_scam),
      transactions,
      fetchedAt: Date.now(),
    };
  }

  private signRequest(path: string, params: Record<string, string | number>) {
    const previous = (globalThis as Record<string, unknown>).__ggn;
    (globalThis as Record<string, unknown>).__ggn = () => "debank.com";

    try {
      const signer = requireSignerModule<SignerModule>(35653);
      const signed = signer.OK(params, "GET", path, { version: "v2" });
      const account = JSON.stringify({
        random_at: Math.floor(Date.now() / 1000),
        random_id: this.randomId(),
        user_addr: null,
        connected_addr: null,
      });

      return {
        headers: {
          "X-API-Key": API_KEY,
          "X-API-Time": String(Math.floor(Date.now() / 1000)),
          "x-api-ts": String(signed.ts),
          "x-api-nonce": signed.nonce,
          "x-api-ver": signed.version,
          "x-api-sign": signed.signature,
          "source": "web",
          "account": account,
          "referer": "https://debank.com/",
          "user-agent": USER_AGENT,
          "accept": "application/json",
        },
      };
    } finally {
      (globalThis as Record<string, unknown>).__ggn = previous;
    }
  }

  private requestJson<T>(url: string, headers: Record<string, string>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const request = https.get(
        url,
        {
          headers,
          timeout: 30000,
        },
        (response) => {
          let data = "";

          response.on("data", (chunk: Buffer) => {
            data += chunk.toString();
          });

          response.on("end", () => {
            try {
              resolve(JSON.parse(data) as T);
            } catch {
              reject(new Error(`Не удалось разобрать JSON: ${data.slice(0, 200)}`));
            }
          });
        }
      );

      request.on("error", reject);
      request.on("timeout", () => {
        request.destroy();
        reject(new Error("Таймаут HTTP запроса к DeBank"));
      });
    });
  }

  private async getJson<T>(path: string, params: Record<string, string | number>): Promise<T> {
    const signed = this.signRequest(path, params);
    const url = new URL(`${API_BASE_URL}${path}`);

    for (const [key, value] of Object.entries(params)) {
      url.searchParams.set(key, String(value));
    }

    return this.requestJson<T>(url.toString(), signed.headers);
  }

  private randomId(): string {
    const alphabet = "abcdef0123456789";
    let result = "";
    for (let i = 0; i < 32; i += 1) {
      result += alphabet[Math.floor(Math.random() * alphabet.length)];
    }
    return result;
  }
}
