import { DeBankClient } from "../src";

const WALLET = "0x3ec68709334f64ee4927891627f0b395c6ff6754";

async function main() {
  const client = new DeBankClient();
  const result = await client.http.getWalletBalance(WALLET);

  console.log("=== HTTP ONLY BALANCE ===");
  console.log(`address: ${result.address}`);
  console.log(`totalUsd: ${result.totalUsd.toFixed(2)}`);
  console.log(`source: ${result.source}`);
  console.log(`signedAt: ${new Date(result.signedAt).toISOString()}`);
  console.log(`signedHeaders: ${Object.keys(result.headers).filter((key) => key.toLowerCase().startsWith("x-api-")).join(", ")}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
