/* Todos los importes internos son céntimos enteros. */
(function(root){
 const sum=(rows,k)=>rows.reduce((n,r)=>n+(r[k]||0),0);
 function metrics(data,month,site=''){
  const scope=r=>!site||r.site===site;
  const sales=data.sales.filter(r=>r.date.startsWith(month)&&scope(r));
  const ids=new Set(sales.map(r=>r.id));
  const paid=sum(data.payments.filter(r=>ids.has(r.sale_id)),'amount');
  const saleMap=new Map(data.sales.map(r=>[r.id,r]));
  const receipts=sum(data.payments.filter(r=>r.date.startsWith(month)&&scope(saleMap.get(r.sale_id)||{})),'amount');
  const revenue=sum(sales,'total'),cost=sum(sales,'cost');
  const expenses=sum(data.expenses.filter(r=>r.date.startsWith(month)&&scope(r)),'amount');
  return {revenue,cost,expenses,profit:revenue-cost-expenses,paid,balance:revenue-paid,receipts,sales};
 }
 function paid(data,id){return sum(data.payments.filter(p=>p.sale_id===id),'amount');}
 const model={metrics,paid,sum};if(typeof module!=='undefined')module.exports=model;root.CRMModel=model;
})(typeof window==='undefined'?globalThis:window);
