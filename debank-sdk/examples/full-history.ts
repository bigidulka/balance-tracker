import { DeBankClient } from "../src";

const WALLET = "0x3ec68709334f64ee4927891627f0b395c6ff6754";

async function main() {
  const client = new DeBankClient();
  const history = await client.http.getAllTransactions(WALLET, {
    page_count: 20,
    maxPages: 20,
  });
  const scams = history.history_list.filter((item) => item.is_scam);

  console.log("=== FULL HISTORY ===");
  console.log(`wallet: ${WALLET}`);
  console.log(`transactions: ${history.history_list.length}`);
  console.log(`categories: ${Object.keys(history.cate_dict).length}`);
  console.log(`tokens in dict: ${Object.keys(history.token_dict ?? {}).length}`);
  console.log(`projects in dict: ${Object.keys(history.project_dict ?? {}).length}`);
  console.log(`scamTransactions: ${scams.length}`);

  for (const item of scams.slice(0, 5)) {
    console.log(`- ${item.chain} ${item.cate_id} ${item.id} at ${item.time_at}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
