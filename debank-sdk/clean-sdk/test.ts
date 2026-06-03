import { DeBank } from "./debank";

const WALLET = "0x3ec68709334f64ee4927891627f0b395c6ff6754";

async function main() {
  const db = new DeBank();

  // 1. Portfolio
  console.log("=== PORTFOLIO ===");
  const p = await db.portfolio(WALLET);
  console.log(`Balance: $${p.totalUsd.toFixed(2)}`);
  console.log(`Chains: ${p.chains.join(", ")}`);
  console.log(`Tokens: ${p.tokens.length}`);
  console.log(
    "Top 5:",
    p.tokens
      .sort((a, b) => b.amount * b.price - a.amount * a.price)
      .slice(0, 5)
      .map((t) => `${t.symbol} $${(t.amount * t.price).toFixed(2)}`)
      .join(" | ")
  );

  // 2. Transactions (first page)
  console.log("\n=== TRANSACTIONS (page 1) ===");
  const txs = await db.transactions(WALLET, { count: 5 });
  for (const tx of txs.history_list) {
    const sends = tx.sends.map((s) => `-${s.amount.toFixed(4)}`).join(", ");
    const recvs = tx.receives.map((r) => `+${r.amount.toFixed(4)}`).join(", ");
    console.log(
      `[${new Date(tx.time_at * 1000).toISOString().slice(0, 10)}] ${tx.tx.name || tx.cate_id} | ${tx.chain} | ${sends || "-"} → ${recvs || "-"}`
    );
  }

  console.log("\n✓ SDK works");
}

main().catch((e) => {
  console.error("FAIL:", e.message);
  process.exit(1);
});
