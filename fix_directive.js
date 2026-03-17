const fs = require('fs');
let src = fs.readFileSync('./frontend/src/app/late-swap/page.tsx', 'utf8');
// Add newline after 'use client' directive
src = src.replace("'use client'import", "'use client'\nimport");
fs.writeFileSync('./frontend/src/app/late-swap/page.tsx', src);
console.log('Done, length:', src.length, 'has newline:', src.includes('\n'));
