/* NODE_PATH=/path/to/playwright/node_modules node tests/t_cancellation_ui.cjs
   Local static fixture and fake cancellation API; no real bookings or payments. */
const {chromium}=require('playwright');const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),http=require('node:http'),path=require('node:path');
const root=path.resolve(__dirname,'../site');
const token='11111111-1111-4111-8111-111111111111';
let booking,attempts,failOnce;
function reset(){booking={id:1,status:'confirmed',can_cancel:true,total:500,date:'2026-12-01',start_hour:10,end_hour:11,
 is_demo:true,is_past:false,access_token:token,garage:{id:1,name:'Тестовый бокс'},support:{message:'Поддержка скоро появится'},
 payment:{mode:'demo',status:'paid',token:'22222222-2222-4222-8222-222222222222'}};attempts=0;failOnce=true;}
const server=http.createServer(async(req,res)=>{
 const json=b=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(b));};
 if(req.url==='/api/public/my-bookings'){for await(const x of req){}return json({bookings:[booking]});}
 if(req.url==='/api/public/cancel-booking'){
  let body='';for await(const x of req)body+=x;attempts++;
  assert.equal(req.headers.authorization,'Booking '+token);assert.equal(JSON.parse(body).confirm,true);
  if(failOnce){failOnce=false;res.statusCode=503;return json({error:{code:'unavailable',message:'Тестовая ошибка: попробуйте снова'}});}
  booking={...booking,status:'cancelled',can_cancel:false};return json(booking);
 }
 try{const file=path.resolve(root,'.'+new URL(req.url,'http://x').pathname);if(!file.startsWith(root+'/'))throw Error();
  res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');res.end(await fs.readFile(file));
 }catch{res.statusCode=404;res.end();}
});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch();
try{for(const width of [1440,390]){reset();const p=await browser.newPage({viewport:{width,height:900}});const errors=[];p.on('pageerror',e=>errors.push(e.message));
await p.addInitScript(t=>localStorage.setItem('gh_website_bookings_v1',JSON.stringify([{id:1,token:t}])),token);
await p.goto('http://127.0.0.1:'+server.address().port+'/bookings.html');
const row=p.locator('.my-booking-row');await row.waitFor();await row.click({position:{x:14,y:14}});
await p.locator('.booking-dialog[open]').waitFor();await p.locator('[data-cancel-booking]').click();assert.equal(attempts,0);
await p.locator('[data-cancel-back]').click();assert.equal(attempts,0);assert.equal(booking.status,'confirmed');
await p.locator('[data-cancel-booking]').click();await p.locator('[data-cancel-confirm]').click();
await p.getByText('Тестовая ошибка: попробуйте снова',{exact:true}).waitFor();assert(await p.locator('[data-cancel-confirm]').isEnabled());
await p.locator('[data-cancel-confirm]').click();await p.locator('.booking-cancel-success').waitFor();assert.equal(attempts,2);
assert.equal(await p.locator('[data-cancel-booking]').count(),0);assert((await p.locator('.booking-status').textContent()).includes('Отменена'));
await p.locator('.booking-dialog-close').click();await p.locator('.booking-dialog').waitFor({state:'detached'});
await p.getByRole('heading',{name:'История',exact:true}).waitFor();assert((await row.textContent()).includes('Отменена'));
await row.focus();await p.keyboard.press('Enter');await p.locator('.booking-dialog[open]').waitFor();assert.equal(await p.locator('[data-cancel-booking]').count(),0);
assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
console.log('PASS '+width+'px: whole-row/keyboard popup, confirmation, keep booking, retry, cancelled history');await p.close();}}
finally{await browser.close();server.close()}})().catch(e=>{console.error(e);server.close();process.exit(1)});
