const fs=require('fs'), vm=require('vm');
const ALL=[]; let FAILED=0, NOW=1000;
const ok=(c,m,x='')=>{console.log((c?'  PASS  ':'  FAIL  ')+m+(x?'  ('+x+')':''));if(!c)FAILED++;};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

class FakeAudio{
  constructor(){this._t=0;this.paused=true;this.readyState=0;this._l={};this.loop=false;
                this.playbackRate=1;ALL.push(this);}
  get currentTime(){return this._t;} set currentTime(v){if(this.readyState<1)throw new Error('not ready');this._t=v;}
  set src(v){this._src=v;} get src(){return this._src;}
  play(){if(this.readyState<2)return Promise.reject(new Error('not ready'));this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;} load(){} removeAttribute(){this._src=undefined;}
  get buffered(){return {length:0};}
  addEventListener(k,f,o){(this._l[k]=this._l[k]||[]).push({f,once:o&&o.once});}
  removeEventListener(k,f){this._l[k]=(this._l[k]||[]).filter(e=>e.f!==f);}
  fire(k){const ls=(this._l[k]||[]).slice();this._l[k]=(this._l[k]||[]).filter(e=>!e.once);ls.forEach(e=>e.f());}
}
const G=()=>({gain:{value:0,cancelScheduledValues(){},setValueAtTime(){},
                    linearRampToValueAtTime(v){this.value=v;}},connect(x){return x;},disconnect(){}});
class FakeCtx{constructor(){this.currentTime=0;this.destination={};}
  createGain(){return G();} createMediaElementSource(){return{connect(x){return x;},disconnect(){}};} resume(){}}
function el(){const o={textContent:'',title:'',value:0,max:0,innerHTML:'',_kids:[],
  classList:{toggle(){},contains(){return false}},appendChild(c){o._kids.push(c);},
  scrollIntoView(){},focus(){},remove(){},querySelector(){return el();},dataset:{},style:{}};
  Object.defineProperty(o,'children',{get:()=>o._kids});return o;}
const NODES={},timers=[];
const manifest=JSON.parse(fs.readFileSync(__dirname+'/fixtures/STARWARS/manifest.json','utf8'));
let keyHandler=null;
const sandbox={console,Promise,Math,String,Number,Array,Object,JSON,isFinite,parseFloat,
  setTimeout,Error,Audio:FakeAudio,AudioContext:FakeCtx,Map,
  performance:{now:()=>NOW},
  window:{AudioContext:FakeCtx},
  document:{activeElement:null,querySelector(s){return NODES[s]||(NODES[s]=el());},createElement(){return el();}},
  requestAnimationFrame(){},setInterval(f){timers.push(f);},
  addEventListener(k,f){if(k==='keydown')keyHandler=f;},
  location:{search:''},URLSearchParams,encodeURIComponent,
  fetch(u){const body=u==='/api/songs'?['STARWARS']:manifest;
           return Promise.resolve({json:()=>Promise.resolve(body)});}};
sandbox.globalThis=sandbox; vm.createContext(sandbox);
const HTML=fs.readFileSync(__dirname+'/../web/index.html','utf8');
let code=HTML.split('<script>')[1].split('</script>')[0];
code+="\nglobalThis.__p=()=>({idx,sel,seq,playing,switches,slots,TRACKS,NSLOT});"
    +"\nglobalThis.__act=activate;globalThis.__play=play;globalThis.__assign=assign;"
    +"\nglobalThis.__slotOf=slotOf;globalThis.__status=()=>document.querySelector('#iStat').textContent;";
vm.runInContext(code,sandbox);

const ready=(s,t)=>{s.el.readyState=4;s.el._t=t;s.el.fire('loadedmetadata');s.el.fire('canplay');};

(async()=>{
  await sleep(30);
  const p=()=>sandbox.__p(), slotOf=sandbox.__slotOf;

  console.log('\n--- bounded pool (the leak that helped exhaust connections) ---');
  ok(ALL.length===4,'exactly NSLOT=4 media elements created at boot','created '+ALL.length);
  for(const s of p().slots) if(s.track!==null) ready(s,0);
  sandbox.__play(); await sleep(10);

  const T=137.482;
  for(const s of p().slots) if(s.track!==null) s.el._t=T;

  console.log('\n--- HOT switch keeps position ---');
  sandbox.__act(1,true); await sleep(10);
  ok(p().idx===1,'audible track switched immediately');
  ok(Math.abs(slotOf(1).el.currentTime-T)<0.001,'resumed at the same position',
     'delta '+Math.abs(slotOf(1).el.currentTime-T).toFixed(6)+'s');
  ok(slotOf(1).gain.gain.value===1 && slotOf(0)?.gain.gain.value===0,'gains crossfaded');

  console.log('\n--- THE REPORTED BUG: a track that never loads must not kill audio ---');
  const audible=p().idx, oldSlot=slotOf(audible);
  sandbox.__act(40,true);                      // far jump -> cold slot, never becomes ready
  await sleep(250);
  ok(p().idx===audible,'audible track UNCHANGED while the new one is stuck');
  ok(oldSlot.gain.gain.value===1,'old track still at FULL GAIN (music keeps playing)');
  ok(/loading/.test(sandbox.__status()),'UI reports loading','status: "'+sandbox.__status()+'"');

  NOW+=9000;                                   // push past the 8s deadline
  await sleep(150);
  ok(p().idx===audible,'after giving up, still playing the old track (NOT silence)');
  ok(oldSlot.gain.gain.value===1,'gain still 1 after timeout — this was the silent-player bug');
  ok(/won't load/.test(sandbox.__status()),'UI explains the failure','status: "'+sandbox.__status()+'"');

  console.log('\n--- recovery: the same track works once it loads ---');
  const s40=slotOf(40);
  if(s40){ ready(s40,p().slots.find(x=>x.track===audible).el.currentTime);
           sandbox.__act(40,true); await sleep(50);
           ok(p().idx===40,'switches successfully once ready'); }
  else ok(false,'slot for 40 exists');

  console.log('\n--- element reuse: browsing must not allocate more elements ---');
  const before=ALL.length;
  for(let i=0;i<25;i++){ sandbox.__act(i,false); await sleep(2);
                         const s=slotOf(i); if(s) ready(s,0); }
  await sleep(50);
  ok(ALL.length===before,'no new media elements after 25 switches','still '+ALL.length);
  let live=0; for(const s of p().slots) if(s.track!==null) live++;
  ok(live<=4,'at most 4 concurrent streams (browser cap is 6)','live='+live);

  console.log('\n--- assign() never evicts the audible slot ---');
  sandbox.__assign();
  ok(!!slotOf(p().idx),'audible track still bound after assign()');

  console.log('\n--- first run on a fresh clone: no renders in out/ yet ---');
  // a second page instance whose /api/songs answers [] must explain itself, not throw
  const before2=ALL.length, N2={}; let gateRemoved=false, threw=null;
  const sb2={...sandbox, location:{search:''},
    document:{activeElement:null,querySelector(s){if(!N2[s]){N2[s]=el();if(s==='#gate')N2[s].remove=()=>{gateRemoved=true;};}return N2[s];},createElement(){return el();}},
    fetch(u){return u==='/api/songs'?Promise.resolve({json:()=>Promise.resolve([])})
                                    :Promise.reject(new Error('unexpected fetch '+u));}};
  sb2.globalThis=sb2; vm.createContext(sb2);
  try{vm.runInContext(HTML.split('<script>')[1].split('</script>')[0],sb2);}catch(e){threw=e;}
  await sleep(30);
  ok(!threw,'page boots without throwing',threw?String(threw):'');
  ok(/no renders/.test(N2['#src'].textContent),'header tells the user to run fmdump',JSON.stringify(N2['#src'].textContent));
  ok(gateRemoved,'click-to-start gate removed (nothing to play)');
  ok(ALL.length===before2,'no media elements created when there is nothing to play','+'+(ALL.length-before2));

  console.log('\n'+(FAILED?FAILED+' CHECK(S) FAILED':'ALL CHECKS PASSED'));
  process.exit(FAILED?1:0);
})();
