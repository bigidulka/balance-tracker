const fs = require('fs');

// We have the JS source. Let's look for crypto operations
const content = fs.readFileSync('tests/debank_js/972.js', 'utf8');

// Find variable assignments with 'hmac' or 'sha256'
const hmacRegex = /([a-zA-Z_$][0-9a-zA-Z_$]*)\s*=\s*[^;]*?hmac[^;]*?;/ig;
let match;
console.log("Looking for hmac implementations:");
while ((match = hmacRegex.exec(content)) !== null) {
    console.log(`Found HMAC at index ${match.index}:`);
    console.log(match[0]);
}

const shaRegex = /([a-zA-Z_$][0-9a-zA-Z_$]*)\s*=\s*[^;]*?sha256[^;]*?;/ig;
console.log("\nLooking for sha256 implementations:");
while ((match = shaRegex.exec(content)) !== null) {
    console.log(`Found SHA256 at index ${match.index}:`);
    console.log(match[0]);
}

// Find header assignments
const headerRegex = /['"]x-api-[^'"]+['"]\s*:/ig;
console.log("\nLooking for header constructions:");
while ((match = headerRegex.exec(content)) !== null) {
    const start = Math.max(0, match.index - 50);
    const end = Math.min(content.length, match.index + 200);
    console.log(`Context around index ${match.index}:`);
    console.log(content.substring(start, end));
    console.log('---');
}

// Look for common DeBank key patterns found in other repositories
const possibleKeys = [
    '***REMOVED***', // old api key
    '***REMOVED***', // new api key
];

console.log("\nLooking for specific keys:");
for (const key of possibleKeys) {
    const idx = content.indexOf(key);
    if (idx !== -1) {
        console.log(`Found key ${key} at index ${idx}`);
        const start = Math.max(0, idx - 100);
        const end = Math.min(content.length, idx + 100);
        console.log(`Context: ${content.substring(start, end)}`);
    } else {
        console.log(`Key ${key} NOT found in main.js`);
    }
}
