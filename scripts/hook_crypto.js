
const crypto = require('crypto');
const originalCreateHash = crypto.createHash;

// Hook crypto to find the secret
crypto.createHash = function(algo) {
    const hash = originalCreateHash(algo);
    const originalUpdate = hash.update;
    hash.update = function(data) {
        if (data && data.length > 5 && data.length < 100) {
            try {
                const str = data.toString('utf8');
                if (/^[a-zA-Z0-9_\-]+$/.test(str) && str.length >= 10) {
                    console.log("POTENTIAL SECRET IN HMAC:", str);
                }
            } catch(e) {}
        }
        return originalUpdate.call(this, data);
    };
    return hash;
};

// ... we would need to load the chunk, which requires a mini webpack loader
