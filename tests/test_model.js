const assert=require('node:assert/strict');const M=require('../web/model.js');
const data={sales:[{id:1,date:'2026-09-01',site:'A',total:10000,cost:3000},{id:2,date:'2026-10-01',site:'A',total:20000,cost:7000},{id:3,date:'2026-10-01',site:'B',total:5000,cost:2000}],payments:[{sale_id:1,date:'2026-10-02',amount:10000},{sale_id:2,date:'2026-10-03',amount:12000}],expenses:[{date:'2026-10-04',site:'A',amount:3000}]};
const m=M.metrics(data,'2026-10','A');assert.equal(m.revenue,20000);assert.equal(m.receipts,22000);assert.equal(m.paid,12000);assert.equal(m.balance,8000);assert.equal(m.profit,10000);
assert.equal(M.metrics(data,'2026-10').revenue,25000);assert.equal(M.metrics(data,'2026-11').revenue,0);assert.equal(M.metrics(data,'2026-11').profit,0);
console.log('OK: utilidad, cobros por fecha, saldo, sedes y período vacío.');
