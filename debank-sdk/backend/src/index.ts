import Fastify from "fastify";
import cors from "@fastify/cors";
import { createRequire } from "node:module";
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { getAddress, isAddress } from "viem";
import { PublicKey } from "@solana/web3.js";
import { z } from "zod";

const require = createRequire(import.meta.url);
const { DeBankClient: LocalDeBankClient } = require("../../dist/index.js") as typeof import("../../dist/index.js");

type WatchlistEntry = {
  telegramUserId: number;
  address: string;
  label: string | null;
  createdAt: string;
};

type ChainManifestItem = {
  id: string;
  name: string;
  file: string;
};

type ChainMeta = {
  id: string;
  name: string;
  isSupportHistory: boolean;
  iconFile: string | null;
};

type WalletKind = "evm" | "solana";

type NormalizedWallet = {
  address: string;
  kind: WalletKind;
};

type SolanaTokenRegistryItem = {
  address: string;
  symbol?: string;
  name?: string;
  logoURI?: string;
  decimals?: number;
};

type SolanaBalanceToken = {
  id: string;
  chain: string;
  chainName: string;
  symbol: string;
  name: string;
  amount: number;
  price: number;
  amountUsd: number;
  isScam: boolean;
};

type SolanaTokenAccount = {
  mint: string;
  amount: number;
  decimals: number;
  isNative: boolean;
  tokenAccount: string;
  owner: string;
};

type SolanaMetadataEntry = {
  symbol: string | null;
  name: string | null;
};

type SolanaSnapshot = {
  nativeAmount: number;
  tokenAccounts: SolanaTokenAccount[];
  tokens: SolanaBalanceToken[];
};

type SummaryResponse = {
  address: string;
  totalUsd: number;
  tracked: boolean;
  label: string | null;
  fetchedAt: number;
  chains: string[];
  chainCount: number;
  tokenCount: number;
  topTokens: Array<{ symbol: string; amountUsd: number }>;
  recentTransactions: number;
  scamTransactions: number;
};

type BalanceTokenView = {
  id: string;
  chain: string;
  chainName: string;
  symbol: string;
  name: string;
  amount: number;
  price: number;
  amountUsd: number;
  isScam: boolean;
};

type BalanceResponse = {
  address: string;
  fetchedAt: number;
  totalUsd: number;
  chains: string[];
  tokenCount: number;
  tokens: BalanceTokenView[];
};

type PortfolioTokenView = {
  id: string;
  chain: string;
  chainName: string;
  symbol: string;
  name: string;
  amount: number;
  price: number;
  amountUsd: number;
  isScam: boolean;
};

type PortfolioChainView = {
  id: string;
  name: string;
  iconFile: string | null;
  totalUsd: number;
  tokenCount: number;
};

type PortfolioResponse = {
  address: string;
  tracked: boolean;
  label: string | null;
  fetchedAt: number;
  totalUsd: number;
  page: number;
  pageSize: number;
  totalTokens: number;
  hasPrevPage: boolean;
  hasNextPage: boolean;
  chains: PortfolioChainView[];
  tokens: PortfolioTokenView[];
};

type TransactionTransferView = {
  symbol: string;
  name: string;
  amount: number;
  usdValue: number;
};

type TransactionView = {
  key: string;
  chain: string;
  chainName: string;
  timeAt: number;
  txName: string;
  status: number;
  otherAddr: string;
  isScam: boolean;
  receivedUsd: number;
  sentUsd: number;
  receives: TransactionTransferView[];
  sends: TransactionTransferView[];
};

type TransactionsPageResponse = {
  address: string;
  fetchedAt: number;
  cursor: number;
  nextCursor: number | null;
  pageSize: number;
  hideScam: boolean;
  items: TransactionView[];
};

const envSchema = z.object({
  PORT: z.coerce.number().default(8080),
  DB_PATH: z.string().default("/data/tracker.db"),
  CACHE_TTL_MS: z.coerce.number().default(30_000),
  CHAIN_CACHE_TTL_MS: z.coerce.number().default(6 * 60 * 60 * 1000),
  SOLANA_RPC_URL: z.string().url().default("https://api.mainnet-beta.solana.com"),
  SOLANA_TX_RPC_URL: z.string().url().default("https://solana-rpc.publicnode.com"),
  SOLANA_TOKENLIST_URL: z
    .string()
    .url()
    .default("https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"),
});

const env = envSchema.parse(process.env);
const app = Fastify({ logger: true });
const db = new DatabaseSync(env.DB_PATH);
const debank = new LocalDeBankClient();

const summaryCache = new Map<string, { expiresAt: number; value: SummaryResponse }>();
const portfolioCache = new Map<string, { expiresAt: number; value: PortfolioResponse }>();
let chainCatalogCache: { expiresAt: number; value: Map<string, ChainMeta> } | null = null;
let solanaTokenRegistryCache: { expiresAt: number; value: Map<string, SolanaTokenRegistryItem> } | null = null;
let solanaMetadataCache: { expiresAt: number; value: Map<string, SolanaMetadataEntry> } | null = null;
const solanaSnapshotCache = new Map<string, { expiresAt: number; value: SolanaSnapshot }>();
const solanaPriceCache = new Map<string, { expiresAt: number; value: number }>();
const solanaCursorStore = new Map<number, string>();
let solanaCursorSequence = 1;

const SOLANA_CHAIN_ID = "sol";
const SOLANA_CHAIN_NAME = "Solana";
const SOLANA_NATIVE_MINT = "So11111111111111111111111111111111111111112";
const SOLANA_DECIMALS = 9;
const SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const SOLANA_ADDRESS_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const SOLANA_SYSTEM_PROGRAM_ID = "11111111111111111111111111111111";
const METAPLEX_TOKEN_METADATA_PROGRAM_ID = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s";

const chainManifestPath = new URL("../../assets/chain-icons/manifest.json", import.meta.url);
const chainManifest = new Map<string, ChainManifestItem>(
  (JSON.parse(readFileSync(chainManifestPath, "utf-8")) as ChainManifestItem[]).map((item) => [item.id, item]),
);

db.exec(`
  CREATE TABLE IF NOT EXISTS watchlist (
    telegram_user_id INTEGER NOT NULL,
    address TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (telegram_user_id, address)
  );
`);

const insertWatchlist = db.prepare(`
  INSERT INTO watchlist (telegram_user_id, address, label)
  VALUES (@telegramUserId, @address, @label)
  ON CONFLICT(telegram_user_id, address)
  DO UPDATE SET label = excluded.label
`);

const deleteWatchlist = db.prepare(`
  DELETE FROM watchlist
  WHERE telegram_user_id = ? AND address = ?
`);

const listWatchlist = db.prepare(`
  SELECT telegram_user_id as telegramUserId, address, label, created_at as createdAt
  FROM watchlist
  WHERE telegram_user_id = ?
  ORDER BY created_at DESC
`);

const getWatchlistEntry = db.prepare(`
  SELECT telegram_user_id as telegramUserId, address, label, created_at as createdAt
  FROM watchlist
  WHERE telegram_user_id = ? AND address = ?
`);

function isSolanaAddress(address: string): boolean {
  return SOLANA_ADDRESS_RE.test(address.trim());
}

function normalizeWallet(address: string): NormalizedWallet {
  const value = address.trim();
  if (isAddress(value, { strict: false })) {
    return { address: getAddress(value), kind: "evm" };
  }
  if (isSolanaAddress(value)) {
    return { address: value, kind: "solana" };
  }
  throw new Error("Invalid wallet address");
}

function normalizeAddress(address: string): string {
  return normalizeWallet(address).address;
}

function trimMetaplexString(value: string): string {
  return value.replace(/\u0000/g, "").trim();
}

function shortAddress(value: string): string {
  return `${value.slice(0, 4)}...${value.slice(-4)}`;
}

function storeSolanaCursor(signature: string): number {
  const cursor = solanaCursorSequence;
  solanaCursorSequence += 1;
  solanaCursorStore.set(cursor, signature);
  return cursor;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  let lastStatus = 0;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const response = await fetch(url, init);
    if (response.ok) {
      return (await response.json()) as T;
    }

    lastStatus = response.status;
    if (response.status !== 429 && response.status < 500) {
      throw new Error(`Request failed: ${response.status}`);
    }

    if (attempt < 2) {
      await sleep(250 * 2 ** attempt);
    }
  }
  throw new Error(`Request failed: ${lastStatus || 429}`);
}

async function fetchSolanaRpc<T>(method: string, params: unknown[]): Promise<T> {
  return await fetchSolanaRpcViaUrl<T>(env.SOLANA_RPC_URL, method, params);
}

async function fetchSolanaRpcViaUrl<T>(url: string, method: string, params: unknown[]): Promise<T> {
  const payload = await fetchJson<{ result?: T; error?: { message?: string } }>(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });

  if (payload.error) {
    throw new Error(payload.error.message || `Solana RPC error for ${method}`);
  }

  if (payload.result === undefined) {
    throw new Error(`Solana RPC returned no result for ${method}`);
  }

  return payload.result;
}

async function getSolanaTokenRegistry(): Promise<Map<string, SolanaTokenRegistryItem>> {
  if (solanaTokenRegistryCache && solanaTokenRegistryCache.expiresAt > Date.now()) {
    return solanaTokenRegistryCache.value;
  }

  try {
    const payload = await fetchJson<{ tokens?: SolanaTokenRegistryItem[] }>(env.SOLANA_TOKENLIST_URL);
    const registry = new Map<string, SolanaTokenRegistryItem>();
    for (const token of payload.tokens ?? []) {
      if (token.address) {
        registry.set(token.address, token);
      }
    }

    solanaTokenRegistryCache = {
      expiresAt: Date.now() + env.CHAIN_CACHE_TTL_MS,
      value: registry,
    };

    return registry;
  } catch {
    return solanaTokenRegistryCache?.value ?? new Map<string, SolanaTokenRegistryItem>();
  }
}

async function getJupiterPrices(mints: string[]): Promise<Map<string, number>> {
  const uniqueMints = [...new Set(mints.filter(Boolean))];
  const prices = new Map<string, number>();
  const missing: string[] = [];

  for (const mint of uniqueMints) {
    const cached = solanaPriceCache.get(mint);
    if (cached && cached.expiresAt > Date.now()) {
      prices.set(mint, cached.value);
      continue;
    }

    if (cached) {
      prices.set(mint, cached.value);
    }
    missing.push(mint);
  }

  for (let index = 0; index < missing.length; index += 50) {
    const chunk = missing.slice(index, index + 50);
    let payload: Record<string, { usdPrice?: number; price?: number | string }>;

    try {
      payload = await fetchJson<Record<string, { usdPrice?: number; price?: number | string }>>(
        `https://lite-api.jup.ag/price/v3?ids=${chunk.join(",")}`,
      );
    } catch {
      continue;
    }

    for (const mint of chunk) {
      const item = payload[mint];
      if (!item) {
        continue;
      }

      const price = Number(item.usdPrice ?? item.price ?? 0);
      if (!Number.isFinite(price) || price <= 0) {
        continue;
      }
      prices.set(mint, price);
      solanaPriceCache.set(mint, {
        expiresAt: Date.now() + env.CACHE_TTL_MS,
        value: price,
      });
    }
  }

  return prices;
}

async function getSolanaNativeBalance(address: string): Promise<number> {
  const result = await fetchSolanaRpc<{ value?: number }>("getBalance", [address, { commitment: "confirmed" }]);
  return Number(result.value ?? 0) / 10 ** SOLANA_DECIMALS;
}

async function getSolanaTokenAccounts(address: string): Promise<SolanaTokenAccount[]> {
  const programIds = [SPL_TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID];
  const merged = new Map<string, SolanaTokenAccount>();

  for (const programId of programIds) {
    const response = await fetchSolanaRpc<{
      value?: Array<{
        pubkey?: string;
        account?: {
          data?: {
            parsed?: {
              info?: {
                isNative?: boolean;
                mint?: string;
                owner?: string;
                tokenAmount?: {
                  uiAmount?: number | null;
                  uiAmountString?: string;
                  decimals?: number;
                };
              };
            };
          };
        };
      }>;
    }>("getTokenAccountsByOwner", [address, { programId }, { encoding: "jsonParsed", commitment: "confirmed" }]);

    for (const item of response.value ?? []) {
      const info = item.account?.data?.parsed?.info;
      const mint = info?.mint;
      if (!mint) {
        continue;
      }

      const amount = Number(info.tokenAmount?.uiAmountString ?? info.tokenAmount?.uiAmount ?? 0);
      if (!Number.isFinite(amount) || amount <= 0) {
        continue;
      }

      const decimals = Number(info.tokenAmount?.decimals ?? 0);
      const tokenAccount = item.pubkey ?? `${mint}:${programId}`;
      const isNative = Boolean(info?.isNative);
      const existing = merged.get(mint);
      merged.set(mint, {
        mint,
        amount: (existing?.amount ?? 0) + amount,
        decimals,
        isNative: Boolean(existing?.isNative) || isNative,
        tokenAccount: existing?.tokenAccount ?? tokenAccount,
        owner: info?.owner ?? address,
      });
    }
  }

  return [...merged.values()];
}

function readMetaplexString(buffer: Buffer, start: number): { value: string; nextOffset: number } {
  const length = buffer.readUInt32LE(start);
  const value = trimMetaplexString(buffer.subarray(start + 4, start + 4 + length).toString("utf8"));
  return { value, nextOffset: start + 4 + length };
}

function decodeMetaplexMetadata(data: string): SolanaMetadataEntry | null {
  const buffer = Buffer.from(data, "base64");
  if (buffer.length < 70) {
    return null;
  }

  try {
    let offset = 1 + 32 + 32;
    const name = readMetaplexString(buffer, offset);
    offset = name.nextOffset;
    const symbol = readMetaplexString(buffer, offset);
    return {
      name: name.value || null,
      symbol: symbol.value || null,
    };
  } catch {
    return null;
  }
}

async function getSolanaMetadata(mints: string[]): Promise<Map<string, SolanaMetadataEntry>> {
  const requested = [...new Set(mints.filter(Boolean))];
  const cached = solanaMetadataCache?.value ?? new Map<string, SolanaMetadataEntry>();
  const missing = requested.filter((mint) => !cached.has(mint));

  if (!missing.length && solanaMetadataCache && solanaMetadataCache.expiresAt > Date.now()) {
    return cached;
  }

  const nextMap = new Map<string, SolanaMetadataEntry>(cached);
  for (let index = 0; index < missing.length; index += 100) {
    const chunk = missing.slice(index, index + 100);
    if (!chunk.length) {
      continue;
    }

    const accounts = chunk.map((mint) => {
      const [pda] = PublicKey.findProgramAddressSync(
        [Buffer.from("metadata"), new PublicKey(METAPLEX_TOKEN_METADATA_PROGRAM_ID).toBuffer(), new PublicKey(mint).toBuffer()],
        new PublicKey(METAPLEX_TOKEN_METADATA_PROGRAM_ID),
      );
      return pda.toBase58();
    });

    const result = await fetchSolanaRpc<{
      value?: Array<
        | {
            data?: [string, string];
          }
        | null
      >;
    }>("getMultipleAccounts", [accounts, { encoding: "base64", commitment: "confirmed" }]);

    for (let itemIndex = 0; itemIndex < chunk.length; itemIndex += 1) {
      const raw = result.value?.[itemIndex];
      const encoded = raw?.data?.[0];
      if (!encoded) {
        continue;
      }
      const decoded = decodeMetaplexMetadata(encoded);
      if (decoded) {
        nextMap.set(chunk[itemIndex], decoded);
      }
    }
  }

  solanaMetadataCache = {
    expiresAt: Date.now() + env.CHAIN_CACHE_TTL_MS,
    value: nextMap,
  };

  return nextMap;
}

async function buildSolanaSnapshot(address: string): Promise<SolanaSnapshot> {
  const cached = solanaSnapshotCache.get(address);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.value;
  }

  const [nativeAmount, tokenAccounts, registry] = await Promise.all([
    getSolanaNativeBalance(address),
    getSolanaTokenAccounts(address),
    getSolanaTokenRegistry(),
  ]);

  const metadataMap = await getSolanaMetadata(tokenAccounts.map((item) => item.mint));
  const priceMap = await getJupiterPrices([SOLANA_NATIVE_MINT, ...tokenAccounts.map((item) => item.mint)]);
  const solPrice = Number(priceMap.get(SOLANA_NATIVE_MINT) ?? 0);

  const tokens: SolanaBalanceToken[] = [];
  if (nativeAmount > 0) {
    tokens.push({
      id: SOLANA_NATIVE_MINT,
      chain: SOLANA_CHAIN_ID,
      chainName: SOLANA_CHAIN_NAME,
      symbol: "SOL",
      name: "Solana",
      amount: nativeAmount,
      price: solPrice,
      amountUsd: nativeAmount * solPrice,
      isScam: false,
    });
  }

  for (const account of tokenAccounts) {
    const registryEntry = registry.get(account.mint);
    const metadataEntry = metadataMap.get(account.mint);
    const price = Number(priceMap.get(account.mint) ?? 0);
    const symbol =
      account.mint === SOLANA_NATIVE_MINT
        ? "WSOL"
        : metadataEntry?.symbol || registryEntry?.symbol || shortAddress(account.mint);
    const name =
      account.mint === SOLANA_NATIVE_MINT ? "Wrapped SOL" : metadataEntry?.name || registryEntry?.name || account.mint;

    tokens.push({
      id: account.mint,
      chain: SOLANA_CHAIN_ID,
      chainName: SOLANA_CHAIN_NAME,
      symbol,
      name,
      amount: account.amount,
      price,
      amountUsd: account.amount * price,
      isScam: false,
    });
  }

  const snapshot: SolanaSnapshot = {
    nativeAmount,
    tokenAccounts,
    tokens: tokens.sort((left, right) => right.amountUsd - left.amountUsd),
  };

  solanaSnapshotCache.set(address, {
    expiresAt: Date.now() + env.CACHE_TTL_MS,
    value: snapshot,
  });

  return snapshot;
}

async function getSolanaTokens(address: string): Promise<SolanaBalanceToken[]> {
  return (await buildSolanaSnapshot(address)).tokens;
}

async function getSolanaSignatures(address: string, cursor: number, pageSize: number) {
  const before = cursor > 0 ? solanaCursorStore.get(cursor) : undefined;
  return await fetchSolanaRpcViaUrl<
    Array<{
      signature: string;
      blockTime?: number | null;
      err?: unknown;
    }>
  >(env.SOLANA_TX_RPC_URL, "getSignaturesForAddress", [address, { limit: pageSize, before }]);
}

function classifySolanaTxName(receives: TransactionTransferView[], sends: TransactionTransferView[]): string {
  if (receives.length && sends.length) {
    return "Swap";
  }
  if (receives.length) {
    return "Получение";
  }
  if (sends.length) {
    return "Отправка";
  }
  return "Транзакция";
}

async function buildSolanaTransactionsPage(
  address: string,
  cursor: number,
  pageSize: number,
  hideScam: boolean,
): Promise<TransactionsPageResponse> {
  const signatures = await getSolanaSignatures(address, cursor, pageSize);
  const currentTokens = await getSolanaTokens(address);
  const currentTokenMap = new Map(currentTokens.map((token) => [token.id, token]));
  const rawTransactions: Array<{
    signature: string;
    blockTime: number;
    err: unknown;
    accountKeys: string[];
    preBalances: number[];
    postBalances: number[];
    fee: number;
    preTokenBalances: Array<{
      owner?: string;
      mint?: string;
      uiTokenAmount?: { uiAmount?: number | null; uiAmountString?: string };
    }>;
    postTokenBalances: Array<{
      owner?: string;
      mint?: string;
      uiTokenAmount?: { uiAmount?: number | null; uiAmountString?: string };
    }>;
  }> = [];
  const txMints = new Set<string>();

  for (const entry of signatures) {
    const tx = await fetchSolanaRpcViaUrl<{
      blockTime?: number | null;
      meta?: {
        err?: unknown;
        preBalances?: number[];
        postBalances?: number[];
        preTokenBalances?: Array<{
          owner?: string;
          mint?: string;
          uiTokenAmount?: { uiAmount?: number | null; uiAmountString?: string };
        }>;
        postTokenBalances?: Array<{
          owner?: string;
          mint?: string;
          uiTokenAmount?: { uiAmount?: number | null; uiAmountString?: string };
        }>;
        fee?: number;
      };
      transaction?: {
        message?: {
          accountKeys?: Array<{ pubkey?: string; signer?: boolean; writable?: boolean } | string>;
        };
      };
    }>(env.SOLANA_TX_RPC_URL, "getTransaction", [entry.signature, { encoding: "jsonParsed", maxSupportedTransactionVersion: 0, commitment: "confirmed" }]);

    const accountKeys = (tx.transaction?.message?.accountKeys ?? []).map((item) =>
      typeof item === "string" ? item : item.pubkey || "",
    );
    const preTokenBalances = tx.meta?.preTokenBalances ?? [];
    const postTokenBalances = tx.meta?.postTokenBalances ?? [];

    for (const item of [...preTokenBalances, ...postTokenBalances]) {
      if (item.owner === address && item.mint) {
        txMints.add(item.mint);
      }
    }

    rawTransactions.push({
      signature: entry.signature,
      blockTime: Number(tx.blockTime ?? entry.blockTime ?? 0),
      err: tx.meta?.err,
      accountKeys,
      preBalances: tx.meta?.preBalances ?? [],
      postBalances: tx.meta?.postBalances ?? [],
      fee: Number(tx.meta?.fee ?? 0),
      preTokenBalances,
      postTokenBalances,
    });
  }

  const [registry, metadataMap] = await Promise.all([getSolanaTokenRegistry(), getSolanaMetadata([...txMints])]);

  const tokenMap = new Map(currentTokenMap);
  for (const mint of txMints) {
    if (tokenMap.has(mint)) {
      continue;
    }
    const metadataEntry = metadataMap.get(mint);
    const registryEntry = registry.get(mint);
    tokenMap.set(mint, {
      id: mint,
      chain: SOLANA_CHAIN_ID,
      chainName: SOLANA_CHAIN_NAME,
      symbol: mint === SOLANA_NATIVE_MINT ? "WSOL" : metadataEntry?.symbol || registryEntry?.symbol || shortAddress(mint),
      name: mint === SOLANA_NATIVE_MINT ? "Wrapped SOL" : metadataEntry?.name || registryEntry?.name || mint,
      amount: 0,
      price: Number(solanaPriceCache.get(mint)?.value ?? 0),
      amountUsd: 0,
      isScam: false,
    });
  }

  const items: TransactionView[] = [];

  for (const tx of rawTransactions) {
    const accountKeys = tx.accountKeys;
    const preBalances = tx.preBalances;
    const postBalances = tx.postBalances;
    const preTokenBalances = tx.preTokenBalances;
    const postTokenBalances = tx.postTokenBalances;

    const receives: TransactionTransferView[] = [];
    const sends: TransactionTransferView[] = [];

    const nativeIndex = accountKeys.findIndex((item) => item === address);
    if (nativeIndex >= 0) {
      const deltaLamports = Number(postBalances[nativeIndex] ?? 0) - Number(preBalances[nativeIndex] ?? 0) + tx.fee;
      const deltaSol = deltaLamports / 10 ** SOLANA_DECIMALS;
      if (deltaSol > 0) {
        receives.push({
          symbol: "SOL",
          name: "Solana",
          amount: deltaSol,
          usdValue: deltaSol * Number(currentTokenMap.get(SOLANA_NATIVE_MINT)?.price ?? solanaPriceCache.get(SOLANA_NATIVE_MINT)?.value ?? 0),
        });
      } else if (deltaSol < 0) {
        sends.push({
          symbol: "SOL",
          name: "Solana",
          amount: Math.abs(deltaSol),
          usdValue: Math.abs(deltaSol) * Number(currentTokenMap.get(SOLANA_NATIVE_MINT)?.price ?? solanaPriceCache.get(SOLANA_NATIVE_MINT)?.value ?? 0),
        });
      }
    }

    const tokenDiffs = new Map<string, number>();
    for (const item of preTokenBalances) {
      if (item.owner !== address || !item.mint) {
        continue;
      }
      const amount = Number(item.uiTokenAmount?.uiAmountString ?? item.uiTokenAmount?.uiAmount ?? 0);
      tokenDiffs.set(item.mint, (tokenDiffs.get(item.mint) ?? 0) - amount);
    }
    for (const item of postTokenBalances) {
      if (item.owner !== address || !item.mint) {
        continue;
      }
      const amount = Number(item.uiTokenAmount?.uiAmountString ?? item.uiTokenAmount?.uiAmount ?? 0);
      tokenDiffs.set(item.mint, (tokenDiffs.get(item.mint) ?? 0) + amount);
    }

    for (const [mint, diff] of tokenDiffs) {
      if (!Number.isFinite(diff) || diff === 0) {
        continue;
      }
      const token = tokenMap.get(mint);
      const transfer = {
        symbol: token?.symbol ?? shortAddress(mint),
        name: token?.name ?? mint,
        amount: Math.abs(diff),
        usdValue: Math.abs(diff) * Number(token?.price ?? 0),
      };

      if (diff > 0) {
        receives.push(transfer);
      } else {
        sends.push(transfer);
      }
    }

    const otherAddr = accountKeys.find(
      (item) => item && item !== address && item !== SOLANA_SYSTEM_PROGRAM_ID && item !== SPL_TOKEN_PROGRAM_ID && item !== TOKEN_2022_PROGRAM_ID,
    ) ?? "";

    items.push({
      key: tx.signature,
      chain: SOLANA_CHAIN_ID,
      chainName: SOLANA_CHAIN_NAME,
      timeAt: tx.blockTime,
      txName: classifySolanaTxName(receives, sends),
      status: tx.err ? -1 : 1,
      otherAddr,
      isScam: false,
      receivedUsd: sumUsd(receives),
      sentUsd: sumUsd(sends),
      receives,
      sends,
    });
  }

  const filtered = hideScam ? items.filter((item) => !item.isScam) : items;
  const lastSignature = signatures[signatures.length - 1]?.signature;
  return {
    address,
    fetchedAt: Date.now(),
    cursor,
    nextCursor: signatures.length >= pageSize && lastSignature ? storeSolanaCursor(lastSignature) : null,
    pageSize,
    hideScam,
    items: filtered,
  };
}
async function getChainCatalog(): Promise<Map<string, ChainMeta>> {
  if (chainCatalogCache && chainCatalogCache.expiresAt > Date.now()) {
    return chainCatalogCache.value;
  }

  const response = await fetch("https://api.debank.com/chain/list");
  if (!response.ok) {
    throw new Error(`Failed to load chain catalog: ${response.status}`);
  }

  const payload = (await response.json()) as {
    data?: {
      chains?: Array<{ id: string; name: string; is_support_history: boolean }>;
    };
  };

  const catalog = new Map<string, ChainMeta>();
  for (const chain of payload.data?.chains ?? []) {
    catalog.set(chain.id, {
      id: chain.id,
      name: chain.name,
      isSupportHistory: Boolean(chain.is_support_history),
      iconFile: chainManifest.get(chain.id)?.file ?? null,
    });
  }

  if (!catalog.has(SOLANA_CHAIN_ID)) {
    catalog.set(SOLANA_CHAIN_ID, {
      id: SOLANA_CHAIN_ID,
      name: SOLANA_CHAIN_NAME,
      isSupportHistory: false,
      iconFile: chainManifest.get(SOLANA_CHAIN_ID)?.file ?? null,
    });
  }

  chainCatalogCache = {
    expiresAt: Date.now() + env.CHAIN_CACHE_TTL_MS,
    value: catalog,
  };

  return catalog;
}

function buildTransfers(
  transfers: Array<{ amount: number; price: number; token_id: string }> | undefined,
  tokenDict: Record<string, { optimized_symbol?: string; symbol?: string; name?: string }> | undefined,
): TransactionTransferView[] {
  return (transfers ?? []).map((item) => ({
    symbol: tokenDict?.[item.token_id]?.optimized_symbol || tokenDict?.[item.token_id]?.symbol || tokenDict?.[item.token_id]?.name || item.token_id,
    name: tokenDict?.[item.token_id]?.name || tokenDict?.[item.token_id]?.optimized_symbol || tokenDict?.[item.token_id]?.symbol || item.token_id,
    amount: Number(item.amount ?? 0),
    usdValue: Number(item.amount ?? 0) * Number(item.price ?? 0),
  }));
}

function sumUsd(transfers: TransactionTransferView[]): number {
  return transfers.reduce((total, item) => total + item.usdValue, 0);
}

function buildChainSet(
  usedChains: string[] | undefined,
  tokens: Array<{ chain?: string }> | undefined,
  transactions: Array<{ chain?: string }> | undefined,
): string[] {
  const chains = new Set<string>();

  for (const chain of usedChains ?? []) {
    if (chain) {
      chains.add(chain);
    }
  }

  for (const token of tokens ?? []) {
    if (token.chain) {
      chains.add(token.chain);
    }
  }

  for (const tx of transactions ?? []) {
    if (tx.chain) {
      chains.add(tx.chain);
    }
  }

  return [...chains];
}

async function buildSummary(telegramUserId: number, rawAddress: string): Promise<SummaryResponse> {
  const wallet = normalizeWallet(rawAddress);
  const address = wallet.address;
  const cacheKey = `${telegramUserId}:${address}`;
  const cached = summaryCache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.value;
  }

  if (wallet.kind === "solana") {
    const trackedEntry = getWatchlistEntry.get(telegramUserId, address) as WatchlistEntry | undefined;
    const tokens = await getSolanaTokens(address);
    const totalUsd = tokens.reduce((sum, token) => sum + token.amountUsd, 0);
    const summary: SummaryResponse = {
      address,
      totalUsd,
      tracked: Boolean(trackedEntry),
      label: trackedEntry?.label ?? null,
      fetchedAt: Date.now(),
      chains: [SOLANA_CHAIN_ID],
      chainCount: 1,
      tokenCount: tokens.length,
      topTokens: tokens.slice(0, 5).map((token) => ({ symbol: token.symbol, amountUsd: token.amountUsd })),
      recentTransactions: (await getSolanaSignatures(address, 0, 20)).length,
      scamTransactions: 0,
    };

    summaryCache.set(cacheKey, {
      expiresAt: Date.now() + env.CACHE_TTL_MS,
      value: summary,
    });

    return summary;
  }

  const [balance, usedChains, tokens, transactions] = await Promise.all([
    debank.http.getWalletBalance(address),
    debank.http.getUsedChains(address),
    debank.http.getTokenCacheBalanceList(address),
    debank.http.getTransactions(address, { page_count: 20 }),
  ]);

  const trackedEntry = getWatchlistEntry.get(telegramUserId, address) as WatchlistEntry | undefined;
  const resolvedChains = buildChainSet(usedChains, tokens, transactions.history_list);
  const topTokens = tokens
    .map((token) => ({
      symbol: token.optimized_symbol || token.symbol || token.name,
      amountUsd: Number(token.amount) * Number(token.price),
    }))
    .sort((left, right) => right.amountUsd - left.amountUsd)
    .slice(0, 5);

  const summary: SummaryResponse = {
    address,
    totalUsd: balance.totalUsd,
    tracked: Boolean(trackedEntry),
    label: trackedEntry?.label ?? null,
    fetchedAt: Date.now(),
    chains: resolvedChains,
    chainCount: resolvedChains.length,
    tokenCount: tokens.length,
    topTokens,
    recentTransactions: transactions.history_list.length,
    scamTransactions: transactions.history_list.filter((item) => item.is_scam).length,
  };

  summaryCache.set(cacheKey, {
    expiresAt: Date.now() + env.CACHE_TTL_MS,
    value: summary,
  });

  return summary;
}

async function buildPortfolio(
  telegramUserId: number,
  rawAddress: string,
  page: number,
  pageSize: number,
): Promise<PortfolioResponse> {
  const wallet = normalizeWallet(rawAddress);
  const address = wallet.address;
  const cacheKey = `${telegramUserId}:${address}:portfolio:${page}:${pageSize}`;
  const cached = portfolioCache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.value;
  }

  if (wallet.kind === "solana") {
    const [summary, tokens] = await Promise.all([buildSummary(telegramUserId, address), getSolanaTokens(address)]);
    const start = page * pageSize;
    const response: PortfolioResponse = {
      address,
      tracked: summary.tracked,
      label: summary.label,
      fetchedAt: Date.now(),
      totalUsd: summary.totalUsd,
      page,
      pageSize,
      totalTokens: tokens.length,
      hasPrevPage: page > 0,
      hasNextPage: start + pageSize < tokens.length,
      chains: [
        {
          id: SOLANA_CHAIN_ID,
          name: SOLANA_CHAIN_NAME,
          iconFile: null,
          totalUsd: summary.totalUsd,
          tokenCount: tokens.length,
        },
      ],
      tokens: tokens.slice(start, start + pageSize),
    };

    portfolioCache.set(cacheKey, {
      expiresAt: Date.now() + env.CACHE_TTL_MS,
      value: response,
    });

    return response;
  }

  const [summary, chainCatalog, tokens] = await Promise.all([
    buildSummary(telegramUserId, address),
    getChainCatalog(),
    debank.http.getTokenCacheBalanceList(address),
  ]);

  const normalizedTokens = tokens
    .map((token) => {
      const chain = chainCatalog.get(token.chain);
      return {
        id: token.id,
        chain: token.chain,
        chainName: chain?.name ?? token.chain,
        symbol: token.optimized_symbol || token.symbol || token.name,
        name: token.name,
        amount: Number(token.amount ?? 0),
        price: Number(token.price ?? 0),
        amountUsd: Number(token.amount ?? 0) * Number(token.price ?? 0),
        isScam: Boolean(token.is_scam),
      } satisfies PortfolioTokenView;
    })
    .sort((left, right) => right.amountUsd - left.amountUsd);

  const chainTotals = new Map<string, PortfolioChainView>();
  for (const token of normalizedTokens) {
    const existing = chainTotals.get(token.chain);
    if (existing) {
      existing.totalUsd += token.amountUsd;
      existing.tokenCount += 1;
      continue;
    }
    chainTotals.set(token.chain, {
      id: token.chain,
      name: token.chainName,
      iconFile: chainCatalog.get(token.chain)?.iconFile ?? null,
      totalUsd: token.amountUsd,
      tokenCount: 1,
    });
  }

  const start = page * pageSize;
  const response: PortfolioResponse = {
    address,
    tracked: summary.tracked,
    label: summary.label,
    fetchedAt: Date.now(),
    totalUsd: summary.totalUsd,
    page,
    pageSize,
    totalTokens: normalizedTokens.length,
    hasPrevPage: page > 0,
    hasNextPage: start + pageSize < normalizedTokens.length,
    chains: Array.from(chainTotals.values()).sort((left, right) => right.totalUsd - left.totalUsd).slice(0, 8),
    tokens: normalizedTokens.slice(start, start + pageSize),
  };

  portfolioCache.set(cacheKey, {
    expiresAt: Date.now() + env.CACHE_TTL_MS,
    value: response,
  });

  return response;
}

async function buildBalance(rawAddress: string): Promise<BalanceResponse> {
  const wallet = normalizeWallet(rawAddress);
  const address = wallet.address;

  if (wallet.kind === "solana") {
    const tokens = await getSolanaTokens(address);
    return {
      address,
      fetchedAt: Date.now(),
      totalUsd: tokens.reduce((sum, token) => sum + token.amountUsd, 0),
      chains: [SOLANA_CHAIN_ID],
      tokenCount: tokens.length,
      tokens,
    };
  }

  const chainCatalog = await getChainCatalog();
  const [balanceResult, usedChainsResult, tokensResult] = await Promise.allSettled([
    debank.http.getWalletBalance(address),
    debank.http.getUsedChains(address),
    debank.http.getTokenCacheBalanceList(address),
  ]);

  if (balanceResult.status === "rejected") {
    throw balanceResult.reason instanceof Error ? balanceResult.reason : new Error(String(balanceResult.reason));
  }
  const balance = balanceResult.value;
  const usedChains: string[] = usedChainsResult.status === "fulfilled" ? usedChainsResult.value : [];
  const tokens: Array<{ id?: string; chain?: string; optimized_symbol?: string; symbol?: string; name?: string; amount?: number; price?: number; amountUsd?: number; is_scam?: boolean }> = tokensResult.status === "fulfilled" ? tokensResult.value : [];

  const normalizedTokens = tokens
    .map((token) => {
      const chain = chainCatalog.get(token.chain ?? "");
      return {
        id: token.id ?? "",
        chain: token.chain ?? "unknown",
        chainName: chain?.name ?? token.chain ?? "unknown",
        symbol: token.optimized_symbol || token.symbol || token.name || "UNKNOWN",
        name: token.name || token.optimized_symbol || token.symbol || "UNKNOWN",
        amount: Number(token.amount ?? 0),
        price: Number(token.price ?? 0),
        amountUsd: Number(token.amount ?? 0) * Number(token.price ?? 0),
        isScam: Boolean(token.is_scam),
      } satisfies BalanceTokenView;
    })
    .filter((token) => token.amount > 0 || token.amountUsd > 0)
    .sort((left, right) => right.amountUsd - left.amountUsd);

  return {
    address,
    fetchedAt: Date.now(),
    totalUsd: Number(balance.totalUsd ?? 0),
    chains: buildChainSet(usedChains, tokens, []),
    tokenCount: normalizedTokens.length,
    tokens: normalizedTokens,
  };
}

async function buildTransactionsPage(
  rawAddress: string,
  cursor: number,
  pageSize: number,
  hideScam: boolean,
): Promise<TransactionsPageResponse> {
  const wallet = normalizeWallet(rawAddress);
  const address = wallet.address;

  if (wallet.kind === "solana") {
    return await buildSolanaTransactionsPage(address, cursor, pageSize, hideScam);
  }

  const chainCatalog = await getChainCatalog();
  const collected: TransactionView[] = [];
  let currentCursor = cursor;
  let nextCursor: number | null = null;
  let iterations = 0;

  while (collected.length < pageSize && iterations < 10) {
    const page = await debank.http.getTransactions(address, {
      start_time: currentCursor,
      page_count: Math.max(pageSize, 20),
    });

    const items = page.history_list ?? [];
    if (!items.length) {
      nextCursor = null;
      break;
    }

    const pageItems = hideScam ? items.filter((item) => !item.is_scam) : items;
    for (const item of pageItems) {
      const receives = buildTransfers(item.receives, page.token_dict);
      const sends = buildTransfers(item.sends, page.token_dict);
      collected.push({
        key: `${item.id}:${item.idx}`,
        chain: item.chain,
        chainName: chainCatalog.get(item.chain)?.name ?? item.chain,
        timeAt: item.time_at,
        txName: item.tx?.name || item.cate_id,
        status: Number(item.tx?.status ?? 0),
        otherAddr: item.other_addr,
        isScam: Boolean(item.is_scam),
        receivedUsd: sumUsd(receives),
        sentUsd: sumUsd(sends),
        receives,
        sends,
      });

      if (collected.length >= pageSize) {
        break;
      }
    }

    const lastItem = items[items.length - 1];
    if (!lastItem || lastItem.time_at === currentCursor || items.length < Math.max(pageSize, 20)) {
      nextCursor = null;
      break;
    }

    nextCursor = lastItem.time_at;
    currentCursor = lastItem.time_at;
    iterations += 1;
  }

  return {
    address,
    fetchedAt: Date.now(),
    cursor,
    nextCursor,
    pageSize,
    hideScam,
    items: collected,
  };
}

await app.register(cors, { origin: true });

app.get("/health", async () => ({ ok: true }));

app.get("/chains", async () => {
  const chainCatalog = await getChainCatalog();
  return { items: Array.from(chainCatalog.values()) };
});

app.get("/wallets/:address/balance", async (request, reply) => {
  try {
    const params = z
      .object({
        address: z.string().min(1),
      })
      .parse(request.params);

    return await buildBalance(params.address);
  } catch (error) {
    reply.code(400);
    return { error: error instanceof Error ? error.message : "Failed to fetch wallet balance" };
  }
});

app.get("/users/:telegramUserId/watchlist", async (request) => {
  const params = z.object({ telegramUserId: z.coerce.number().int() }).parse(request.params);
  const rows = listWatchlist.all(params.telegramUserId) as WatchlistEntry[];
  return { items: rows };
});

app.post("/users/:telegramUserId/watchlist", async (request, reply) => {
  const params = z.object({ telegramUserId: z.coerce.number().int() }).parse(request.params);
  const body = z
    .object({
      address: z.string().min(1),
      label: z.string().trim().min(1).max(64).optional().or(z.literal("")),
    })
    .parse(request.body);

  const address = normalizeAddress(body.address);
  insertWatchlist.run({
    telegramUserId: params.telegramUserId,
    address,
    label: body.label || null,
  });

  for (const key of [...summaryCache.keys(), ...portfolioCache.keys()]) {
    if (key.includes(`${params.telegramUserId}:${address}`)) {
      summaryCache.delete(key);
      portfolioCache.delete(key);
    }
  }

  reply.code(201);
  return { ok: true, address };
});

app.delete("/users/:telegramUserId/watchlist/:address", async (request) => {
  const params = z
    .object({
      telegramUserId: z.coerce.number().int(),
      address: z.string().min(1),
    })
    .parse(request.params);

  const address = normalizeAddress(params.address);
  deleteWatchlist.run(params.telegramUserId, address);

  for (const key of [...summaryCache.keys(), ...portfolioCache.keys()]) {
    if (key.includes(`${params.telegramUserId}:${address}`)) {
      summaryCache.delete(key);
      portfolioCache.delete(key);
    }
  }

  return { ok: true };
});

app.get("/users/:telegramUserId/wallets/:address/summary", async (request, reply) => {
  try {
    const params = z
      .object({
        telegramUserId: z.coerce.number().int(),
        address: z.string().min(1),
      })
      .parse(request.params);

    return await buildSummary(params.telegramUserId, params.address);
  } catch (error) {
    reply.code(400);
    return { error: error instanceof Error ? error.message : "Failed to fetch wallet summary" };
  }
});

app.get("/users/:telegramUserId/wallets/:address/portfolio", async (request, reply) => {
  try {
    const params = z
      .object({
        telegramUserId: z.coerce.number().int(),
        address: z.string().min(1),
      })
      .parse(request.params);
    const query = z
      .object({
        page: z.coerce.number().int().min(0).default(0),
        pageSize: z.coerce.number().int().min(1).max(20).default(8),
      })
      .parse(request.query);

    return await buildPortfolio(params.telegramUserId, params.address, query.page, query.pageSize);
  } catch (error) {
    reply.code(400);
    return { error: error instanceof Error ? error.message : "Failed to fetch portfolio" };
  }
});

app.get("/users/:telegramUserId/wallets/:address/transactions", async (request, reply) => {
  try {
    const params = z
      .object({
        telegramUserId: z.coerce.number().int(),
        address: z.string().min(1),
      })
      .parse(request.params);
    void params.telegramUserId;

    const query = z
      .object({
        cursor: z.coerce.number().int().min(0).default(0),
        pageSize: z.coerce.number().int().min(1).max(20).default(8),
        hideScam: z
          .union([z.boolean(), z.string()])
          .transform((value) => value === true || value === "true")
          .default(false),
      })
      .parse(request.query);

    return await buildTransactionsPage(params.address, query.cursor, query.pageSize, query.hideScam);
  } catch (error) {
    reply.code(400);
    return { error: error instanceof Error ? error.message : "Failed to fetch transactions" };
  }
});

app.setErrorHandler((error, _request, reply) => {
  app.log.error(error);
  reply.code(500).send({ error: "Internal server error" });
});

await app.listen({ host: "0.0.0.0", port: env.PORT });
