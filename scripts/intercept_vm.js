const fs = require('fs');

// We have the chunk file 6101.4aa3c89e.chunk.js
// We need to execute it in Node.js, but mock `window` and other globals.
// Since we only care about the secret key, we can use a regex to find the giant base64 string
// Then we just use Playwright to execute the VM and intercept `hssss` instead of TextEncoder.

const code = fs.readFileSync('tests/debank_js_all/6101.4aa3c89e.chunk.js', 'utf8');

// The `T` string is:
const match = code.match(/E=\(T="([A-Za-z0-9+/=]+)"/);
if (match) {
    console.log("Found VM bytecode length:", match[1].length);
    fs.writeFileSync('tests/vm_bytecode.txt', match[1]);
} else {
    console.log("Could not find VM bytecode");
}
