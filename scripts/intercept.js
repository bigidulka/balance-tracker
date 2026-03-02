
const crypto = require('crypto');

function o(e){return crypto.createHash('sha256').update(e).digest()}
function f(e){return Buffer.from(e, 'utf8')}
function c(e,t,r){
    const n=e.length+t.length;
    const i=new Uint8Array(n);
    i.set(e,0);
    i.set(t,e.length);
    r.set(o(i));
}
const l=32,d=64,h=e=>(e+4294967296).toString(16).slice(-2);

function u(e,t){
    console.log("\n\n========================================");
    console.log("🔥 BINGO! INTERCEPTED HMAC CALL 🔥");
    console.log("Secret (Arg 1):", e);
    console.log("Data (Arg 2):", t);
    console.log("========================================\n\n");
    
    // Original logic just to not break it
    const r=f(e),n=f(t),i=new Uint8Array(d),a=new Uint8Array(d),s=new Uint8Array(d),u=new Uint8Array(l),p=new Uint8Array(l);
    i.fill(0),a.fill(54),s.fill(92);
    r.length>d?i.set(o(r)):i.set(r);
    for(let o=0;o<d;o++)a[o]^=i[o],s[o]^=i[o];
    c(a,n,u),c(s,u,p);
    const m=new Uint8Array(2*p.length);
    for(let o=0;o<p.length;o++){
        const e=h(p[o]);
        m[2*o]=e.charCodeAt(0),m[2*o+1]=e.charCodeAt(1)
    }
    return new TextDecoder("utf8").decode(m);
}

// We need to run the VM.
// But running the VM requires their interpreter code (`A = new(i()())`).
// Instead of extracting the whole VM, let's just use playwright ONE TIME to intercept the JS function
// and get the secret key. Then we put the secret key in our Python script!
