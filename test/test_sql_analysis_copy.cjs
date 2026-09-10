// Browserless behavioral checks for the exact helper shipped in the page.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const page = fs.readFileSync(require('node:path').join(__dirname, '../reports/multi-ma/sql-analysis.html'), 'utf8');
new vm.Script(page.match(/<script>([\s\S]*?)<\/script>/)[1]);
const helper = page.slice(page.indexOf('async function copyText('), page.indexOf("$('copyError').onclick="));
async function scenario(secure, clipboardMode, legacySuccess, expected) {
  let removed=0, selected=0, legacy=0, writes=0, shown=0;
  const element = () => ({style:{}, select(){selected++}, setSelectionRange(){}, focus(){},
    remove(){removed++}, classList:{remove(){shown++}}});
  const manual = element();
  const context = vm.createContext({window:{isSecureContext:secure}, navigator:{clipboard:{
    async writeText(){writes++;if(clipboardMode==='reject')throw Error('denied')}
  }}, document:{createElement:element,body:{appendChild(){}},execCommand(){legacy++;return legacySuccess}},
    $:()=>manual});
  vm.runInContext(helper,context);
  assert.equal(await context.copyText('한글 error'), expected);
  if(secure&&clipboardMode==='ok')assert.deepEqual([writes,legacy,removed],[1,0,0]);
  else assert.deepEqual([legacy,removed],[1,1]);
  if(!expected){assert.equal(shown,1);assert.equal(manual.value,'한글 error');assert.ok(selected>=2)}
}
(async()=>{
  await scenario(true,'ok',false,true);
  await scenario(true,'reject',true,true);
  await scenario(false,'ok',true,true);
  await scenario(false,'ok',false,false);
  console.log('copy fallback 4 scenarios + page syntax PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
