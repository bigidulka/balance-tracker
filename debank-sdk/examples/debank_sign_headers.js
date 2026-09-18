const { requireSignerModule } = require("../dist/generated/runtime.js");

const API_KEY = "00000000-0000-0000-0000-000000000000";
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36";

function randomId() {
  const alphabet = "abcdef0123456789";
  let result = "";
  for (let i = 0; i < 32; i += 1) {
    result += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return result;
}

function signRequest(path, params) {
  const previous = globalThis.__ggn;
  globalThis.__ggn = () => "debank.com";

  try {
    const signer = requireSignerModule(35653);
    const signed = signer.OK(params, "GET", path, { version: "v2" });
    const account = JSON.stringify({
      random_at: Math.floor(Date.now() / 1000),
      random_id: randomId(),
      user_addr: null,
      connected_addr: null,
    });

    return {
      "X-API-Key": API_KEY,
      "X-API-Time": String(Math.floor(Date.now() / 1000)),
      "x-api-ts": String(signed.ts),
      "x-api-nonce": signed.nonce,
      "x-api-ver": signed.version,
      "x-api-sign": signed.signature,
      source: "web",
      account,
      referer: "https://debank.com/",
      "user-agent": USER_AGENT,
      accept: "application/json",
    };
  } finally {
    globalThis.__ggn = previous;
  }
}

function main() {
  const payload = JSON.parse(process.argv[2] || "{}");
  if (!payload.path || typeof payload.params !== "object") {
    throw new Error("Usage: node debank_sign_headers.js '{\"path\":\"/path\",\"params\":{}}'");
  }
  const headers = signRequest(payload.path, payload.params);
  process.stdout.write(JSON.stringify(headers));
}

main();
