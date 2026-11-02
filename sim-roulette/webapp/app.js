/* ============================================================
   SIM Roulette — клиентская логика мини-аппа
   Работает через API бота (bot/web/server.py).
   ============================================================ */
"use strict";

/* ---------------------------------------------------------- данные для отрисовки */
// зеркала серверных констант (bot/game.py) — только для вывода
const RAR = [
  {id:"common",n:"Обычный",c:"#9aa4b2"},
  {id:"unc",   n:"Необычный",c:"#4ade80"},
  {id:"rare",  n:"Редкий",c:"#38bdf8"},
  {id:"epic",  n:"Эпический",c:"#c084fc"},
  {id:"myth",  n:"Мифический",c:"#f472b6"},
  {id:"leg",   n:"Легендарный",c:"#fbbf24"},
  {id:"blat",  n:"Блатной",c:"#f87171"},
  {id:"sec",   n:"Секретный",c:"#22d3ee"},
];
const R_BY_ID={}; RAR.forEach((r,i)=>{R_BY_ID[r.id]=r; r.idx=i;});
const R_IDX=id=>(R_BY_ID[id]?R_BY_ID[id].idx:0);

const CTRY=[
  {k:"RU",name:"Россия",flag:"🇷🇺",cur:"₽",b:100},
  {k:"US",name:"США",flag:"🇺🇸",cur:"$",b:1.5},
  {k:"DE",name:"Германия",flag:"🇩🇪",cur:"€",b:1.5},
  {k:"GB",name:"Великобритания",flag:"🇬🇧",cur:"£",b:1.2},
  {k:"CN",name:"Китай",flag:"🇨🇳",cur:"CN¥",b:10},
  {k:"JP",name:"Япония",flag:"🇯🇵",cur:"JP¥",b:200},
  {k:"KZ",name:"Казахстан",flag:"🇰🇿",cur:"₸",b:500},
  {k:"TR",name:"Турция",flag:"🇹🇷",cur:"₺",b:40},
];
const C_BY_ID={}; CTRY.forEach(c=>C_BY_ID[c.k]=c);
const FLAG=k=>(C_BY_ID[k]?C_BY_ID[k].flag:"🏳");
const CUR=k=>(C_BY_ID[k]?C_BY_ID[k].cur:"");
const BASE=k=>(C_BY_ID[k]?C_BY_ID[k].b:1);
// перевод цены номера в единицы баланса
const toUnits=(p,k)=>Math.round((p/BASE(k))*100)/100;

// операторы — только для красивой точки
const OPS={bel:["Bel","#ffcc00"],mts:["MTS","#e30611"],mgf:["MGF","#00b14f"],tl2:["T2","#111111"],
  att:["ATT","#00a8e0"],vrz:["VRZ","#cd040b"],tmb:["TMB","#e20074"],
  tkm:["TKM","#e20074"],vdf:["VDF","#e60000"],o2:["O2","#0019a5"],epl:["E+","#00a86b"],
  cmp:["CMB","#007b8a"],cuc:["CUC","#c8102e"],ctc:["CTC","#0f4c9f"],
  dcm:["DCM","#e60012"],sbf:["SBF","#a6a6a6"],au:["au","#ff7a00"],
  act:["ACT","#00b14f"],tcl:["TCL","#004a99"],ttk:["TTK","#c8102e"],
  x:["?","#888"]};
const opOf=(k,id)=>OPS[id]||OPS.x;

const FREE_CD=30*60*1000;   // кулдаун бесплатного спина (мс)
const SPIN_X=20;            // базовая стоимость спина
const MULTS=[1,5,10,20];

/* цена прокрута зависит от категории: чем выше — тем дороже
   (зеркало game.CATEGORY_COST на сервере) */
const CAT_COST={common:1,unc:2,rare:4,epic:7,myth:13,leg:25,blat:50,sec:114};
const catMult=c=>CAT_COST[c]||1;
const spinCostX=(cat,count)=>SPIN_X*catMult(cat)*(count||1);

/* ---------------------------------------------------------- состояние */
let S={
  player:null, inv:[], level:0, lvlXp:0, nextXp:0,
  is_admin:false,
  country:"RU", category:null, mult:1,
  busy:false, maintenance:{},
  skus:[],
};
const TG=(window.Telegram&&window.Telegram.WebApp)?window.Telegram.WebApp:null;
let DEV_UID=999999;

/* ---------------------------------------------------------- утилиты */
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=n=>{n=Math.round(Number(n)||0);return n.toLocaleString("ru-RU");};

/* ---------- «реальные деньги» ---------- */
// точка как разделитель тысяч: 100.000 / 10.450
function dotSep(n){
  n=Math.round(Number(n)||0);
  const neg=n<0; n=Math.abs(n);
  return (neg?"-":"")+String(n).replace(/\B(?=(\d{3})+(?!\d))/g,".");
}
// внутренние единицы -> валюта ВЫБРАННОЙ страны (100.000$, 80.000₽)
function money(units){
  const c=C_BY_ID[S.country]||CTRY[0];
  return dotSep((Number(units)||0)*c.b)+c.cur;
}
// цена номера (в валюте его страны) -> валюта выбранной страны
function priceOf(p,k){ return money(toUnits(p,k)); }
// валюта конкретной страны, без пересчёта (для цены в её же деньгах)
function nativePrice(p,k){ const c=C_BY_ID[k]||CTRY[0]; return dotSep(p)+c.cur; }

function toast(msg,ms){
  const t=$("toast"); t.textContent=msg; t.classList.add("show");
  clearTimeout(t._t); t._t=setTimeout(()=>t.classList.remove("show"), ms||2200);
}

/* ---------------------------------------------------------- API */
async function api(path, method, body){
  const payload=Object.assign({}, body||{});
  if(TG && TG.initData){ payload.initData=TG.initData; }
  else {
    payload.uid=DEV_UID;
    // в DEV передаём профиль (аватар/ник) — сервер примет только в DEV_MODE
    payload.profile=Object.assign({first_name:"Игрок",username:"dev_user"}, payload.profile||{});
  }
  method=(method||"POST").toUpperCase();
  let url=path, opts={method, headers:{"Content-Type":"application/json"}};
  if(method==="GET"){
    // fetch() не позволяет body у GET — кладём полезную нагрузку в query.
    // profile не отправляем (сервер получит строку, а не объект).
    delete payload.profile;
    const q=new URLSearchParams();
    for(const k in payload){
      const v=payload[k];
      if(v===undefined||v===null) continue;
      q.set(k, typeof v==="object"?JSON.stringify(v):String(v));
    }
    url=path+(path.includes("?")?"&":"?")+q.toString();
  }else{
    opts.body=JSON.stringify(payload);
  }
  const res=await fetch(url,opts);
  let data={};
  try{ data=await res.json(); }catch(e){}
  if(!res.ok && res.status!==409){
    const err=new Error(data.error||("HTTP "+res.status)); err.data=data; err.status=res.status; throw err;
  }
  return data;
}

/* ---------------------------------------------------------- звук / вибрация / эффекты */
let AC=null;
function beep(freq,dur,type,vol){
  if(!S.player||!S.player.sound_on) return;
  try{
    AC=AC||new (window.AudioContext||window.webkitAudioContext)();
    const o=AC.createOscillator(),g=AC.createGain();
    o.type=type||"sine"; o.frequency.value=freq;
    g.gain.value=vol||0.05; o.connect(g); g.connect(AC.destination);
    o.start(); g.gain.exponentialRampToValueAtTime(0.0001,AC.currentTime+(dur||0.15));
    o.stop(AC.currentTime+(dur||0.15)+0.02);
  }catch(e){}
}
function buzz(pattern){ if(S.player&&S.player.vibration&&navigator.vibrate) try{navigator.vibrate(pattern);}catch(e){} }
function confetti(){
  if(!S.player||!S.player.effects) return;
  const colors=["#ffcc00","#38bdf8","#c084fc","#4ade80","#f472b6"];
  for(let i=0;i<40;i++){
    const c=document.createElement("div"); c.className="confetti";
    c.style.background=colors[i%colors.length];
    c.style.left=(Math.random()*100)+"vw"; c.style.top="-20px";
    c.style.transform=`rotate(${Math.random()*360}deg)`;
    document.body.appendChild(c);
    const dx=(Math.random()*2-1)*140, dur=900+Math.random()*900;
    c.animate([{transform:`translate(0,0) rotate(0)`,opacity:1},
               {transform:`translate(${dx}px,${innerHeight+40}px) rotate(${Math.random()*720}deg)`,opacity:0}],
      {duration:dur,easing:"cubic-bezier(.2,.6,.4,1)"}).onfinish=()=>c.remove();
  }
}

/* ---------------------------------------------------------- темы оформления */
const THEMES={
  classic:{bg1:"#0b1020",bg2:"#0f1730",acc:"#3aa0ff",acc2:"#7b5cff"},
  dark:{bg1:"#050505",bg2:"#141414",acc:"#ff6b6b",acc2:"#ffa502"},
  neon:{bg1:"#0a0018",bg2:"#150030",acc:"#00ffcc",acc2:"#ff00cc"},
  ocean:{bg1:"#001020",bg2:"#002040",acc:"#00b4d8",acc2:"#0077b6"},
  royal:{bg1:"#120024",bg2:"#240048",acc:"#ffd700",acc2:"#b8860b"},
};
function applyTheme(name){
  const t=THEMES[name]||THEMES.classic;
  document.documentElement.style.setProperty("--bg1",t.bg1);
  document.documentElement.style.setProperty("--bg2",t.bg2);
  document.documentElement.style.setProperty("--acc",t.acc);
  document.documentElement.style.setProperty("--acc2",t.acc2);
  document.body.style.background=`radial-gradient(1200px 600px at 20% -10%,${t.bg2},${t.bg1})`;
}

/* ---------------------------------------------------------- BOOT */
async function boot(){
  // Telegram окружение — только если есть реальный initData
  if(TG && TG.initData){
    try{ TG.ready(); TG.expand(); }catch(e){}
    DEV_UID=0;
  }
  // 1) авторизация
  let auth;
  try{
    auth=await api("/api/auth","POST",{});
  }catch(e){
    $("boot").innerHTML='<div class="empty"><div class="ic">⚠️</div>Сервер недоступен.<br>Попробуй позже.</div>';
    return;
  }
  S.player=auth.player; S.is_admin=!!auth.is_admin;
  if(S.player.is_admin) S.is_admin=true;

  // 2) перенос старого сохранения из localStorage (первый вход)
  try{ await migrateOldSave(); }catch(e){}

  // 3) полное состояние
  await loadState();

  // 4) собрать заработанное на рынке (inbox)
  try{ await api("/api/inbox","POST",{}).then(r=>{
    if(r.ok && r.count>0){
      toast(`💰 Получено: +${money(r.earned)}`+(r.items&&r.items.length?` и ${r.items.length} номер(а)`:""));
      S.player.balance=r.balance;   // обновляем отображаемый баланс
    }
  }); }catch(e){}

  // 5) SKU для VIP-магазина
  try{ const s=await api("/api/skus","GET",{}); S.skus=s.skus||[]; }catch(e){}

  $("boot").classList.add("hide");
  $("app").classList.remove("hide");

  applyTheme(S.player.theme||"classic");
  renderHeader();
  renderBanners();
  renderControls();
  updateFreeBtn();

  setupMenu();
  setupSpin();
  setupControls();
  setupMaintenance();
}

/* перенос старого localStorage-сейва */
async function migrateOldSave(){
  const keys=Object.keys(localStorage);
  let found=null;
  for(const key of keys){
    try{
      const v=JSON.parse(localStorage.getItem(key));
      if(v&&typeof v==="object"&&(v.bal!==undefined||v.inv)){
        found=v; localStorage.removeItem(key); break;
      }
    }catch(e){}
  }
  if(!found) return;
  await api("/api/import","POST",{data:found});
  toast("✅ Старое сохранение перенесено!");
}

async function loadState(){
  const st=await api("/api/state","POST",{});
  S.player=st.player; S.inv=st.inventory||[];
  S.level=st.level; S.lvlXp=st.level_xp; S.nextXp=st.next_level_xp;
  S.maintenance=st.maintenance||{};
  if(S.player.is_admin) S.is_admin=true;
}

/* ---------------------------------------------------------- header */
function showEmojiAvatar(name){
  let e=$("avatarEmo");
  if(!e){
    e=document.createElement("div"); e.id="avatarEmo";
    $("userrow").insertBefore(e, $("uname"));
  }
  e.style.display="flex";
  e.textContent=(name||"🙂").trim().charAt(0).toUpperCase();
}
function renderHeader(){
  const p=S.player;
  const name=p.first_name||p.username||"Игрок";
  $("uname").textContent=name;
  // уровень + прогресс
  const span=(S.nextXp-S.lvlXp)||1;
  const prog=Math.max(0,Math.min(100,Math.round(((p.xp-S.lvlXp)/span)*100)));
  $("lvl").textContent=`Ур. ${S.level} · ${fmt(p.xp)} XP`;
  // деньги
  let moneyStr=money(p.balance);
  if(p.donate_balance>0) moneyStr+=` + ${money(p.donate_balance)}`;
  $("moneyVal").textContent=moneyStr;
  // аватар
  const av=$("avatar");
  if(p.photo_url){
    av.className=""; av.src=p.photo_url; av.style.display="";
    av.onerror=()=>{ av.style.display="none"; showEmojiAvatar(name); };
  }else{
    av.removeAttribute("src"); av.style.display="none"; showEmojiAvatar(name);
  }
  // показать/скрыть админ-плитку
  $("tileAdmin").classList.toggle("hide", !S.is_admin);
}

/* ---------------------------------------------------------- баннеры */
// список берётся из banner-config.js (window.BANNERS); здесь — заглушка
const BANNERS=(window.BANNERS&&window.BANNERS.length)?window.BANNERS:[
  {ic:"⭐",tt:"VIP Магазин",ds:"Удача ×2/×4/×10 за Telegram Stars",cta:"Открыть",act:()=>openPanel("vip")},
  {ic:"🎁",tt:"Бонус дня",ds:"Забирай ежедневный подарок",cta:"Забрать",act:()=>openPanel("daily")},
  {ic:"🏪",tt:"Рынок",ds:"Продавай и покупай номера у игроков",cta:"Идти",act:()=>openPanel("market")},
  {ic:"🎟️",tt:"Есть промокод?",ds:"Активируй купон и получи награду",cta:"Ввести",act:()=>openPanel("coupons")},
];
let bIdx=0;
function renderBanners(){
  const track = $("bannerTrack"), dots = $("bannerDots");
  track.innerHTML = ""; dots.innerHTML = "";
  BANNERS.forEach((b,i)=>{
    const d = document.createElement("div"); d.className = "bn";
    const img = b.img
      ? '<img class="bnImg" src="' + esc(b.img) + '" alt="" onerror="this.remove()"><div class="bnScrim"></div>'
      : "";
    const ico = (b.ic && !b.img) ? '<div class="ico">' + b.ic + '</div>' : "";
    let html = img + ico + '<div class="txt"><div class="tt">' + esc(b.tt) + '</div>';
    if (b.ds) html += '<div class="ds">' + esc(b.ds) + '</div>';
    html += '</div>';
    if (b.cta) html += '<button class="cta">' + esc(b.cta) + '</button>';
    d.innerHTML = html;
    if (b.cta) d.querySelector(".cta").onclick = (e)=>{ e.stopPropagation(); (b.act||function(){})(); };
    else if (b.act) d.onclick = ()=>{ b.act(); };
    if (b.act && !b.cta) d.style.cursor = "pointer";
    track.appendChild(d);
    const dot=document.createElement("i"); if(i===0)dot.className="on"; dots.appendChild(dot);
  });
  setInterval(()=>{
    bIdx=(bIdx+1)%BANNERS.length;
    track.style.transform=`translateX(-${bIdx*100}%)`;
    [...dots.children].forEach((d,i)=>d.className=i===bIdx?"on":"");
  },4500);
}

/* ---------------------------------------------------------- контролы (страна/категория/множитель) */
function renderControls(){
  const c=C_BY_ID[S.country];
  $("ctryFlag").textContent=c.flag; $("ctryName").textContent=c.name;
  $("catVal").textContent=S.category?R_BY_ID[S.category].n:"Любая";
  $("catVal").style.color=S.category?R_BY_ID[S.category].c:"";
  $("multVal").textContent="×"+S.mult;
  $("spinCost").textContent=money(spinCostX(S.category,S.mult));
}

function setupControls(){
  $("ctrlCountry").onclick=openCountryPicker;
  $("ctrlCat").onclick=openCatPicker;
  $("ctrlMult").onclick=openMultPicker;
}

function openSheet(html){
  $("sheetBody").innerHTML=html;
  $("sheet").classList.add("show");
}
function closeSheet(){ $("sheet").classList.remove("show"); }
$("sheet").addEventListener("click",e=>{ if(e.target.id==="sheet") closeSheet(); });

function openCountryPicker(){
  const rows=CTRY.map(c=>`
    <button class="row" data-k="${c.k}" style="width:100%;text-align:left">
      <span class="ic">${c.flag}</span>
      <span class="mid"><span class="t">${esc(c.name)}</span><span class="s">${esc(c.cur)} · базовая ставка ${c.b}</span></span>
      ${c.k===S.country?'<span class="end">✅</span>':''}
    </button>`).join("");
  openSheet(`<div class="sheetTitle">🌍 Выбор страны</div>${rows}`);
  $("sheetBody").querySelectorAll("[data-k]").forEach(b=>b.onclick=()=>{
    S.country=b.dataset.k; renderControls(); closeSheet(); beep(600,.08);
  });
}

function openCatPicker(){
  const per=money(spinCostX(S.category,S.mult));
  const rows=[`<button class="row" data-k="" style="width:100%;text-align:left">
      <span class="ic">🎲</span><span class="mid"><span class="t">Любая категория</span><span class="s">все редкости · ${money(spinCostX(null,S.mult))} за прокрут</span></span>
      ${!S.category?'<span class="end">✅</span>':''}</button>`].concat(
    RAR.map(r=>`<button class="row" data-k="${r.id}" style="width:100%;text-align:left">
      <span class="ic" style="color:${r.c}">●</span>
      <span class="mid"><span class="t">${esc(r.n)}</span><span class="s">от ${esc(r.n)} и выше · <b style="color:${r.c}">${money(spinCostX(r.id,S.mult))}</b> за прокрут</span></span>
      ${S.category===r.id?'<span class="end">✅</span>':''}</button>`)).join("");
  openSheet(`<div class="sheetTitle">🎯 Ставка на категорию</div>
    <div class="field"><div class="hint">Номера ниже выбранной категории выпадать не будут.
    Чем выше категория — тем дороже прокрут. Сейчас: <b>${per}</b>.</div></div>${rows}`);
  $("sheetBody").querySelectorAll("[data-k]").forEach(b=>b.onclick=()=>{
    S.category=b.dataset.k||null; renderControls(); closeSheet(); beep(600,.08);
  });
}

function openMultPicker(){
  const rows=MULTS.map(m=>`
    <button class="row" data-m="${m}" style="width:100%;text-align:left">
      <span class="ic">🔁</span>
      <span class="mid"><span class="t">×${m} номеров</span><span class="s">стоимость ${money(spinCostX(S.category,m))}</span></span>
      ${S.mult===m?'<span class="end">✅</span>':''}
    </button>`).join("");
  openSheet(`<div class="sheetTitle">⚡ Количество номеров</div>${rows}`);
  $("sheetBody").querySelectorAll("[data-m]").forEach(b=>b.onclick=()=>{
    S.mult=+b.dataset.m; renderControls(); closeSheet(); beep(600,.08);
  });
}

/* ---------------------------------------------------------- спин */
function setupSpin(){
  $("spinBtn").onclick=doSpin;
  $("btnFree").onclick=doFreeSpin;
  $("btnBonus").onclick=doBonus;
}

function freeReady(){ return !S.player.free_at || (Date.now()-S.player.free_at)>=FREE_CD; }
function updateFreeBtn(){
  const b=$("btnFree"), cd=$("freeCd");
  if(freeReady()){ b.classList.add("on"); cd.textContent=""; }
  else{
    b.classList.remove("on");
    const left=Math.ceil((FREE_CD-(Date.now()-S.player.free_at))/1000);
    cd.textContent=`${String(Math.floor(left/60)).padStart(2,"0")}:${String(left%60).padStart(2,"0")}`;
  }
}
setInterval(()=>{ if(S.player) updateFreeBtn(); },1000);

function spinSpeedMs(){ const s=S.player.spin_speed; return s==="fast"?450:s==="slow"?1100:750; }

function animateReel(items,done){
  const wrap=$("reelWrap"), num=$("reelNum"), sub=$("reelSub");
  const dur=spinSpeedMs();
  wrap.classList.add("spinning"); sub.textContent="Крутим…";
  const fake=["+7 (999) 12-34","+1 (555) 000","+49 111 22","+86 123 45"];
  const t0=performance.now();
  (function tick(){
    const t=performance.now()-t0;
    if(t<dur){
      num.textContent=fake[Math.floor(Math.random()*fake.length)];
      beep(300+Math.random()*200,.03,"square",.03);
      requestAnimationFrame(tick);
    }else{
      wrap.classList.remove("spinning");
      num.textContent=items[0].num;
      sub.textContent=`${items[0].country_flag||FLAG(items[0].k)} ${items[0].rarity_name||R_BY_ID[items[0].r].n} · ${items[0].op_short||opOf(items[0].k,items[0].op)[0]}`;
      done();
    }
  })();
}

async function doSpin(){
  if(S.busy) return;
  if(S.maintenance.started){ toast("🔧 Идёт техперерыв"); return; }
  S.busy=true; const btn=$("spinBtn"); btn.classList.add("busy");
  try{
    const r=await api("/api/spin","POST",{
      country:S.country, category:S.category, count:S.mult,
      useCoins:!!S.player.use_coins,
    });
    S.player.balance=r.balance; S.player.donate_balance=r.donate_balance;
    if(r.coins_used) S.player.coins=Math.max(0,S.player.coins-r.coins_used);
    S.inv=await refreshInv();
    buzz(20);
    if(r.xp_events&&r.xp_events.length) r.xp_events.forEach(e=>{ if(e.type==="level") toast(`🎉 Уровень ${e.level}! +${e.reward}`); confetti(); });

    if(r.items.length===1){
      animateReel(r.items,()=>showSingleResult(r.items[0],r));
    }else{
      showMultiResult(r.items,r);
    }
  }catch(e){
    if(e.data&&e.data.error==="no_funds") toast("💸 Недостаточно средств");
    else if(e.data&&e.data.error==="maintenance") toast("🔧 Идёт техперерыв");
    else toast("Ошибка: "+(e.data&&e.data.error||e.message));
  }finally{
    S.busy=false; btn.classList.remove("busy"); renderHeader();
  }
}

async function doFreeSpin(){
  if(!freeReady()){ toast("⏳ Бесплатный спин ещё не готов"); return; }
  if(S.busy) return;
  S.busy=true; $("spinBtn").classList.add("busy");
  try{
    const r=await api("/api/spin","POST",{country:S.country,category:S.category,count:1,free:true});
    S.player.free_at=Date.now();
    S.player.balance=r.balance; S.inv=await refreshInv();
    buzz(20);
    animateReel(r.items,()=>showSingleResult(r.items[0],r));
  }catch(e){ toast("Ошибка: "+(e.data&&e.data.error||e.message)); }
  finally{ S.busy=false; $("spinBtn").classList.remove("busy"); renderHeader(); updateFreeBtn(); }
}

async function refreshInv(){ const st=await api("/api/state","POST",{}); S.inv=st.inventory||[]; return S.inv; }

/* одиночный результат — оставить / продать */
function showSingleResult(item,r){
  const rar=R_BY_ID[item.r], units=toUnits(item.p,item.k);
  const feats=(item.features||[]).slice(0,4).map(f=>esc(f.t)).join(" · ");
  openSheet(`<div id="resWrap">
    <div class="resRar" style="background:${rar.c}">${esc(item.rarity_name||rar.n)}</div>
    <div class="resNum">${FLAG(item.k)} ${esc(item.num)}</div>
    <div class="resFeat">${esc(item.op_short||opOf(item.k,item.op)[0])}${feats?" · "+feats:""}</div>
    <div class="resPrice">${priceOf(item.p,item.k)}</div>
    <div class="resCur">удача ×${r.luck_mult||1}${item.k!==S.country?" · номинал "+nativePrice(item.p,item.k):""}</div>
    <div class="resActs">
      <button class="btn ghost" id="rSell">Продать +${money(units)}</button>
      <button class="btn good" id="rKeep">Оставить</button>
    </div></div>`);
  if(R_IDX(item.r)>=2){ confetti(); beep(880,.2,"triangle",.08); }
  else beep(520,.12);
  $("rKeep").onclick=async()=>{
    await api("/api/decide","POST",{action:"keep"}); closeSheet(); toast("📱 Номер в коллекции"); renderHeader();
  };
  $("rSell").onclick=async()=>{
    const rr=await api("/api/decide","POST",{action:"sell"});
    closeSheet(); toast(`💰 +${money(rr.earned)}`); S.player.balance=rr.balance; renderHeader(); beep(700,.12);
  };
}

/* мульти-результат */
function showMultiResult(items,r){
  const sold=items.filter(i=>i.sold).length;
  const kept=items.length-sold;
  const best=items.slice().sort((a,b)=>R_IDX(b.r)-R_IDX(a.r))[0];
  const cards=items.map(i=>{
    const rr=R_BY_ID[i.r];
    return `<div class="mc"><div class="mn">${FLAG(i.k)} ${esc(i.num)}</div>
      <span class="mr" style="background:${rr.c}">${esc(rr.n)}</span>
      <div class="mp">${money(toUnits(i.p,i.k))}</div>
      ${i.sold?'<div class="sold">✅ продан</div>':'<div class="sold" style="color:var(--mut)">📱 в коллекции</div>'}</div>`;
  }).join("");
  openSheet(`<div class="sheetTitle">🎉 Результат ×${items.length}</div>
    <div class="msum">
      <div><div class="v">${kept}</div><div class="l">в коллекции</div></div>
      <div><div class="v">${sold}</div><div class="l">продано</div></div>
      <div><div class="v">${money(toUnits(best.p,best.k))}</div><div class="l">лучший</div></div>
    </div>
    <div class="mres">${cards}</div>
    <button class="btn" id="mClose" style="width:100%;margin-top:12px;padding:14px">Готово</button>`);
  if(R_IDX(best.r)>=2){ confetti(); beep(880,.2,"triangle",.08); } else beep(520,.12);
  $("mClose").onclick=closeSheet;
}

/* ---------------------------------------------------------- бонус дня */
async function doBonus(){
  try{
    const r=await api("/api/bonus","POST",{});
    toast(`🎁 +${money(r.reward)} · стрик ${r.streak}🔥`);
    S.player.balance=r.balance; S.player.streak=r.streak; S.player.last_day=new Date().toISOString().slice(0,10);
    confetti(); beep(760,.15);
    renderHeader();
    if($("panel").classList.contains("show")&&$("panelTitle").textContent==="Бонус дня") openPanel("daily");
  }catch(e){
    if(e.data&&e.data.error==="already") toast("⏳ Бонус уже взят сегодня");
    else toast("Ошибка: "+(e.data&&e.data.error||e.message));
  }
}

/* ---------------------------------------------------------- меню */
function setupMenu(){
  $("hamb").onclick=()=>{ $("menu").classList.add("show"); $("menuSearch").value=""; filterMenu(""); };
  $("menuClose").onclick=()=>$("menu").classList.remove("show");
  $("menu").addEventListener("click",e=>{ if(e.target.id==="menu") $("menu").classList.remove("show"); });
  $("menuSearch").addEventListener("input",e=>filterMenu(e.target.value));
  document.querySelectorAll("[data-open]").forEach(b=>b.onclick=()=>{
    $("menu").classList.remove("show"); openPanel(b.dataset.open);
  });
  $("topgear").onclick=()=>{ $("menu").classList.remove("show"); openPanel("settings"); };
}
function filterMenu(q){
  q=(q||"").toLowerCase().trim();
  let any=false;
  document.querySelectorAll("#menuGrid .mtile").forEach(t=>{
    const txt=(t.dataset.kw||"")+" "+t.textContent.toLowerCase();
    const show=!q||txt.includes(q);
    t.style.display=show?"":"none"; if(show)any=true;
  });
  // VIP-промо скрываем при поиске по другому
  $("vipPromo").style.display=q?"none":"";
  $("menuEmpty").classList.toggle("hide",any);
}

/* ---------------------------------------------------------- панели разделов */
function openPanel(name){
  $("panel").classList.add("show");
  const search=$("panelSearch"); search.classList.add("hide"); search.value="";
  const body=$("panelBody"); body.innerHTML='<div class="empty"><div class="ic">⏳</div>Загрузка…</div>';
  const render=RENDER[name];
  if(!render){ $("panelTitle").textContent="—"; body.innerHTML='<div class="empty">Раздел не найден</div>'; return; }
  render(body,search);
}
$("panelBack").onclick=()=>$("panel").classList.remove("show");
$("panel").addEventListener("click",e=>{ if(e.target.id==="panel") $("panel").classList.remove("show"); });

function numCard(n,acts){
  const rar=R_BY_ID[n.r]||RAR[0], op=opOf(n.k,n.op);
  const feats=(n.features||[]).slice(0,2).map(f=>esc(f.t)).join(" · ");
  return `<div class="ncard r${R_IDX(n.r)}">
    <div class="top"><span class="flag">${FLAG(n.k)}</span><span class="rchip">${esc(rar.n)}</span></div>
    <div class="num">${esc(n.num)}</div>
    <div class="op"><span class="dot" style="background:${op[1]}">${esc(op[0].slice(0,2))}</span><span class="onm">${esc(op[0])}</span></div>
    <div class="price">${priceOf(n.p,n.k)}</div>
    ${feats?`<div class="feat">${feats}</div>`:""}
    ${acts?`<div class="acts">${acts}</div>`:""}
  </div>`;
}

function emptyBlock(icon,text){
  return `<div class="empty"><div class="ic">${icon}</div>${text}</div>`;
}

const RENDER={};

/* --- Коллекция --- */
RENDER.inv=(body,search)=>{
  $("panelTitle").textContent="📱 Коллекция";
  const draw=(q)=>{
    const list=S.inv.filter(n=>!q||n.num.includes(q)||(R_BY_ID[n.r]&&R_BY_ID[n.r].n.toLowerCase().includes(q)));
    if(!list.length){ body.innerHTML='<div class="empty"><div class="ic">📱</div>Пока пусто. Крути рулетку!</div>'; return; }
    const byR={}; list.forEach(n=>byR[n.r]=(byR[n.r]||0)+1);
    const stat=Object.keys(byR).sort((a,b)=>R_IDX(b)-R_IDX(a))
      .map(r=>`<span class="rchip" style="background:${R_BY_ID[r].c};color:#0b1020;font-size:10px;font-weight:800;padding:3px 8px;border-radius:10px">${R_BY_ID[r].n} ${byR[r]}</span>`).join(" ");
    body.innerHTML=`<div style="margin-bottom:10px;display:flex;flex-wrap:wrap;gap:6px">${stat}</div>
      <div style="margin-bottom:10px;display:flex;gap:8px">
        <button class="btn sm ghost" id="sellCommon">Продать обычные</button>
        <button class="btn sm ghost" id="sellWorst">Продать худшие</button>
      </div>
      <div class="ngrid">${list.map(n=>numCard(n,
        `<button class="btn sm ghost" data-up="${n.id}">⬆️</button><button class="btn sm bad" data-sell="${n.id}">Продать</button>`
      )).join("")}</div>`;
    body.querySelectorAll("[data-sell]").forEach(b=>b.onclick=()=>sellNum(b.dataset.sell));
    body.querySelectorAll("[data-up]").forEach(b=>b.onclick=()=>openUpgrade(b.dataset.up));
    body.querySelector("#sellCommon").onclick=()=>massSell("common");
    body.querySelector("#sellWorst").onclick=()=>massSell("worst");
  };
  search.oninput=e=>draw(e.target.value.toLowerCase().trim());
  search.classList.remove("hide");
  draw("");
};

async function sellNum(id){
  try{ const r=await api("/api/sell","POST",{number_id:id});
    S.player.balance=r.balance; S.inv=await refreshInv(); renderHeader();
    toast(`💰 +${money(r.earned)}`); beep(700,.1);
    RENDER.inv($("panelBody"),$("panelSearch"));
  }catch(e){ toast("Ошибка продажи"); }
}
async function massSell(mode){
  const label=mode==="common"?"обычные и необычные":mode==="worst"?"кроме 5 лучших":"все номера";
  if(!confirm(`Продать ${label}?`)) return;
  try{ const r=await api("/api/mass_sell","POST",{mode});
    S.player.balance=r.balance; S.inv=await refreshInv(); renderHeader();
    toast(`💰 Продано ${r.count} · +${money(r.earned)}`); confetti();
    RENDER.inv($("panelBody"),$("panelSearch"));
  }catch(e){ toast(e.data&&e.data.error==="nothing"?"Нечего продавать":"Ошибка"); }
}

/* --- Апгрейд (раздел из меню) --- */
RENDER.upg=(body,search)=>{
  $("panelTitle").textContent="⬆️ Апгрейд";
  const draw=(q)=>{
    const list=S.inv.filter(n=>n.r!=="sec"&&(!q||n.num.includes(q)));
    if(!list.length){ body.innerHTML=emptyBlock("⬆️","Нет номеров для улучшения.<br>Секретные уже максимум!"); return; }
    body.innerHTML=`<div class="field"><div class="hint">Выбери номер и попробуй поднять его категорию. При неудаче номер теряется!</div></div>
      <div class="ngrid">${list.map(n=>numCard(n,
        `<button class="btn sm" data-up="${n.id}" style="flex:1">⬆️ Улучшить</button>`
      )).join("")}</div>`;
    body.querySelectorAll("[data-up]").forEach(b=>b.onclick=()=>openUpgrade(b.dataset.up));
  };
  search.oninput=e=>draw(e.target.value.toLowerCase().trim());
  search.classList.remove("hide");
  draw("");
};

/* --- Апгрейд номера (модалка) --- */
function openUpgrade(id){
  const n=S.inv.find(x=>x.id===id); if(!n) return;
  if(n.r==="sec"){ toast("🌌 Секретный номер — максимум"); return; }
  const rar=R_BY_ID[n.r];
  const opts=[[45,"+1 категория"],[30,"+2 категории"],[15,"+3 категории"],[7,"+4 категории"]];
  openSheet(`<div class="sheetTitle">⬆️ Апгрейд номера</div>
    <div class="ncard r${R_IDX(n.r)}" style="margin-bottom:12px">
      <div class="top"><span class="flag">${FLAG(n.k)}</span><span class="rchip" style="background:${rar.c}">${esc(rar.n)}</span></div>
      <div class="num">${esc(n.num)}</div></div>
    <div class="field"><label>Выбери шанс</label>
      <div class="seg" id="upSeg">${opts.map((o,i)=>`<button data-i="${i}" class="${i===0?'on':''}">${o[1]} · ${o[0]}%</button>`).join("")}</div>
      <div class="hint">При неудаче номер теряется безвозвратно!</div></div>
    <button class="btn" id="upGo" style="width:100%;padding:14px">Улучшить</button>`);
  let sel=0;
  $("upSeg").querySelectorAll("button").forEach(b=>b.onclick=()=>{
    $("upSeg").querySelectorAll("button").forEach(x=>x.classList.remove("on"));
    b.classList.add("on"); sel=+b.dataset.i;
  });
  $("upGo").onclick=async()=>{
    const chance=opts[sel][0];
    try{
      const r=await api("/api/upgrade","POST",{number_id:id,chance});
      S.inv=await refreshInv(); renderHeader(); closeSheet();
      if(r.win){
        confetti(); beep(880,.25,"triangle",.09);
        const nr=R_BY_ID[r.item.r];
        openSheet(`<div class="sheetTitle">🎉 Улучшение получено!</div>
          <div class="ncard r${R_IDX(r.item.r)}">
            <div class="top"><span class="flag">${FLAG(r.item.k)}</span><span class="rchip" style="background:${nr.c}">${esc(nr.n)}</span></div>
            <div class="num">${esc(r.item.num)}</div>
            <div class="price">${priceOf(r.item.p,r.item.k)}</div></div>
          <button class="btn good" id="upOk" style="width:100%;margin-top:12px;padding:14px">Отлично!</button>`);
        $("upOk").onclick=closeSheet;
      }else{
        beep(200,.3,"sawtooth",.06);
        openSheet(`<div class="sheetTitle">💔 Неудача</div>
          <div class="empty"><div class="ic">😵</div>Номер ${esc(n.num)} потерян…</div>
          <button class="btn ghost" id="upOk" style="width:100%;padding:14px">Жаль</button>`);
        $("upOk").onclick=closeSheet;
      }
      RENDER.inv&&$("panel").classList.contains("show")&&RENDER.inv($("panelBody"),$("panelSearch"));
    }catch(e){ toast("Ошибка: "+(e.data&&e.data.error||e.message)); }
  };
}

/* --- Рынок (мультиплеер) --- */
RENDER.market=async(body,search)=>{
  $("panelTitle").textContent="🏪 Рынок";
  $("panelSearch").classList.add("hide");
  body.innerHTML='<div class="empty"><div class="ic">⏳</div>Загрузка рынка…</div>';
  let data;
  try{ data=await api("/api/market","GET",{}); }catch(e){ body.innerHTML='<div class="empty">Ошибка загрузки</div>'; return; }
  const listings=data.listings||[], names=data.names||{};
  const draw=(q)=>{
    const me=S.player.user_id;
    const filtered=listings.filter(l=>!q||l.item.num.includes(q));
    if(!filtered.length){ body.innerHTML=emptyBlock("🏪","Пока нет лотов.<br>Выставь свой номер из коллекции!"); bindSell(); return; }
    body.innerHTML=`
      <button class="btn" id="mkSell" style="width:100%;margin-bottom:12px;padding:13px">💰 Выставить свой номер на продажу</button>
      <div class="shead">Лоты игроков (${filtered.length})</div>
      ${filtered.map(l=>{
        const it=l.item, rr=R_BY_ID[it.r], seller=names[String(l.seller)]||"Игрок";
        const own=l.seller===me;
        return `<div class="lcard">
          <div class="lhead"><span>${own?"👤 Твой лот":"👤 "+esc(seller)}</span><span>${l.kind==="swap"?"🔄 обмен":"💰 продажа"}</span></div>
          <div class="lbody">
            <div>
              <div class="lnum">${FLAG(it.k)} ${esc(it.num)}</div>
              <span class="rchip" style="background:${rr.c};color:#0b1020;font-size:9px;font-weight:800;padding:2px 7px;border-radius:8px">${esc(rr.n)}</span>
            </div>
            <div style="text-align:right">
              <div class="lprice">${money(toUnits(l.price,it.k))}</div>
              ${own?`<button class="btn sm ghost" data-un="${l.id}">Снять</button>`
                   :l.kind==="swap"?`<button class="btn sm gold" data-sw="${l.id}">Обменять</button>`
                                   :`<button class="btn sm good" data-buy="${l.id}">Купить</button>`}
            </div>
          </div></div>`;
      }).join("")}`;
    body.querySelectorAll("[data-buy]").forEach(b=>b.onclick=()=>buyLot(b.dataset.buy));
    body.querySelectorAll("[data-un]").forEach(b=>b.onclick=()=>unlist(b.dataset.un));
    body.querySelectorAll("[data-sw]").forEach(b=>b.onclick=()=>swapLot(b.dataset.sw));
    bindSell();
  };
  function bindSell(){ const b=$("mkSell"); if(b) b.onclick=sellFlow; }
  async function sellFlow(){
    await refreshInv();
    if(!S.inv.length){ toast("Нет номеров для продажи"); return; }
    openSheet(`<div class="sheetTitle">💰 Выставить номер</div>
      <div class="ngrid" id="pickGrid">${S.inv.map(n=>numCard(n,`<button class="btn sm" data-pick="${n.id}">Выбрать</button>`)).join("")}</div>`);
    $("pickGrid").querySelectorAll("[data-pick]").forEach(b=>b.onclick=()=>{
      const n=S.inv.find(x=>x.id===b.dataset.pick);
      askPrice(n,"sale");
    });
  }
  search.oninput=e=>draw(e.target.value.toLowerCase().trim());
  search.classList.remove("hide");
  draw("");
};

function askPrice(n,kind){
  const def=Math.round(n.p);   // валюта НОМЕРА (так цену принимает API)
  const cur=(C_BY_ID[n.k]||CTRY[0]).cur;
  openSheet(`<div class="sheetTitle">${kind==="swap"?"🔄 Обмен":"💰 Цена"}</div>
    <div class="ncard r${R_IDX(n.r)}" style="margin-bottom:12px">
      <div class="top"><span class="flag">${FLAG(n.k)}</span><span class="rchip" style="background:${R_BY_ID[n.r].c}">${esc(R_BY_ID[n.r].n)}</span></div>
      <div class="num">${esc(n.num)}</div>
      <div class="price">${nativePrice(def,n.k)}</div></div>
    <div class="field"><label>Цена (${cur} — валюта номера)</label>
      <input id="priceIn" type="number" min="1" value="${def}">
      <div class="hint">≈ ${money(toUnits(def,n.k))} по курсу · комиссия рынка 5% при продаже.</div></div>
    <button class="btn" id="listGo" style="width:100%;padding:14px">Выставить</button>`);
  $("listGo").onclick=async()=>{
    const price=+$("priceIn").value; if(!(price>0)) return toast("Введите цену");
    try{
      await api("/api/market/list","POST",{number_id:n.id,price,kind});
      S.inv=await refreshInv(); closeSheet(); toast("✅ Лот выставлен!");
      RENDER.market($("panelBody"),$("panelSearch"));
    }catch(e){ toast("Ошибка: "+(e.data&&e.data.error||e.message)); }
  };
}

async function buyLot(id){
  try{
    const r=await api("/api/market/buy","POST",{listing_id:id});
    S.player.balance=r.balance; await refreshInv(); renderHeader();
    toast(`✅ Куплено за ${money(r.paid)}`); confetti(); beep(760,.15);
    RENDER.market($("panelBody"),$("panelSearch"));
  }catch(e){
    if(e.data&&e.data.error==="no_funds") toast("💸 Недостаточно средств");
    else if(e.data&&e.data.error==="gone") toast("Лот уже продан");
    else toast("Ошибка: "+(e.data&&e.data.error||e.message));
  }
}
async function unlist(id){
  try{ await api("/api/market/unlist","POST",{listing_id:id}); toast("↩️ Лот снят");
    RENDER.market($("panelBody"),$("panelSearch"));
  }catch(e){ toast("Ошибка"); }
}
async function swapLot(id){
  await refreshInv();
  if(!S.inv.length){ toast("Нет номеров для обмена"); return; }
  openSheet(`<div class="sheetTitle">🔄 Обмен — выбери свой номер</div>
    <div class="ngrid">${S.inv.map(n=>numCard(n,`<button class="btn sm gold" data-sw="${n.id}">Обменять</button>`)).join("")}</div>`);
  $("sheetBody").querySelectorAll("[data-sw]").forEach(b=>b.onclick=async()=>{
    try{
      await api("/api/market/accept_swap","POST",{listing_id:id,number_id:b.dataset.sw});
      await refreshInv(); closeSheet(); toast("🔄 Обмен состоялся!"); confetti();
      RENDER.market($("panelBody"),$("panelSearch"));
    }catch(e){ toast("Ошибка: "+(e.data&&e.data.error||e.message)); }
  });
}

/* --- Рейтинг --- */
RENDER.rating=async(body)=>{
  $("panelTitle").textContent="🏆 Рейтинг"; $("panelSearch").classList.add("hide");
  body.innerHTML='<div class="empty"><div class="ic">⏳</div>Загрузка…</div>';
  let d; try{ d=await api("/api/rating","GET",{}); }catch(e){ body.innerHTML='<div class="empty">Ошибка</div>'; return; }
  const me=S.player.user_id;
  const moneyRows=(d.money||[]).map((m,i)=>`<div class="prow" ${m.user_id===me?'style="border-color:var(--acc)"':''}>
      <div class="rk ${i===0?'g':i===1?'s':i===2?'b':''}">${i+1}</div>
      <div class="nm">${esc(m.name)}${m.user_id===me?" (ты)":""}</div>
      <div class="vl">${money(m.balance)}</div></div>`).join("");
  const nums=(d.numbers||[]).map((x,i)=>`<div class="prow">
      <div class="rk ${i===0?'g':i===1?'s':i===2?'b':''}">${i+1}</div>
      <div class="nm">${FLAG(x.item.k)} ${esc(x.item.num)}<br><span style="font-size:11px;color:var(--mut)">${esc(x.name)}</span></div>
      <div class="vl">${money(toUnits(x.item.p,x.item.k))}</div></div>`).join("");
  body.innerHTML=`<div class="shead">💰 Богатейшие игроки</div><div class="podium">${moneyRows||'<div class="empty">Пусто</div>'}</div>
    <div class="shead">💎 Лучшие номера</div><div class="podium">${nums||'<div class="empty">Пусто</div>'}</div>`;
};

/* --- Задания --- */
RENDER.tasks=async(body)=>{
  $("panelTitle").textContent="🎯 Задания"; $("panelSearch").classList.add("hide");
  body.innerHTML='<div class="empty"><div class="ic">⏳</div>Загрузка…</div>';
  let d; try{ d=await api("/api/tasks","GET",{}); }catch(e){ body.innerHTML='<div class="empty">Ошибка</div>'; return; }
  const tasks=(d.tasks||[]).map(t=>{
    const done=t.v>=t.n;
    const pct=Math.round((t.v/t.n)*100);
    return `<div class="row"><span class="ic">${done?"✅":"🎯"}</span>
      <span class="mid"><span class="t">${esc(t.t)}</span>
        <span class="s">${t.v}/${t.n} · +${money(t.u)}</span>
        <span class="pbar"><i style="width:${pct}%"></i></span></span>
      <span class="end">${t.claimed?'<span style="color:var(--good);font-weight:700">✓</span>':done?`<button class="btn sm good" data-t="${t.id}">Забрать</button>`:""}</span></div>`;
  }).join("");
  const ach=(d.all_achievements||[]).map(a=>{
    const got=(d.achievements||[]).includes(a.id);
    return `<div class="row" style="${got?'border-color:var(--good)':''}">
      <span class="ic">${a.i}</span><span class="mid"><span class="t">${esc(a.t)}</span></span>
      <span class="end">${got?'<span style="color:var(--good)">✓</span>':'<span style="color:var(--mut)">🔒</span>'}</span></div>`;
  }).join("");
  body.innerHTML=`<div class="shead">Задания</div>${tasks||'<div class="empty">Нет заданий</div>'}
    <div class="shead">Достижения</div>${ach}`;
  body.querySelectorAll("[data-t]").forEach(b=>b.onclick=async()=>{
    try{ const r=await api("/api/tasks/claim","POST",{task_id:b.dataset.t});
      S.player.balance=r.balance; renderHeader(); toast(`🎁 +${money(r.reward)}`); beep(700,.1);
      RENDER.tasks(body);
    }catch(e){ toast("Ошибка"); }
  });
};

/* --- Друзья / рефералы --- */
RENDER.friends=async(body)=>{
  $("panelTitle").textContent="👥 Друзья"; $("panelSearch").classList.add("hide");
  body.innerHTML='<div class="empty"><div class="ic">⏳</div>Загрузка…</div>';
  let d; try{ d=await api("/api/friends","GET",{}); }catch(e){ body.innerHTML='<div class="empty">Ошибка</div>'; return; }
  const link=d.ref_link||"";
  const friends=(d.friends||[]);
  body.innerHTML=`<div class="shead">Твоя реферальная ссылка</div>
    <div class="row"><span class="mid"><span class="t" style="word-break:break-all">${esc(link)}</span>
      <span class="s">Друг получит бонус, ты — процент с его игр</span></span></div>
    <button class="btn" id="refCopy" style="width:100%;margin-bottom:14px;padding:13px">📋 Скопировать ссылку</button>
    <button class="btn ghost" id="refShare" style="width:100%;margin-bottom:16px;padding:13px">📨 Поделиться</button>
    <div class="shead">Приглашено: ${d.ref_count||0}</div>
    ${friends.length?friends.map(f=>`<div class="row"><span class="ic">👤</span><span class="mid"><span class="t">${esc(f.name||"Друг")}</span></span></div>`).join(""):'<div class="empty"><div class="ic">👥</div>Пока никого не пригласил</div>'}`;
  $("refCopy").onclick=async()=>{
    try{ await navigator.clipboard.writeText(link); toast("📋 Скопировано!"); }
    catch(e){ toast(link); }
  };
  $("refShare").onclick=()=>{
    if(TG&&TG.share){ try{ TG.share({url:link,text:"Присоединяйся к SIM Roulette!"}); return; }catch(e){} }
    toast("Скопируй ссылку и отправь другу");
  };
};

/* --- Бонус дня --- */
RENDER.daily=(body)=>{
  $("panelTitle").textContent="🎁 Бонус дня"; $("panelSearch").classList.add("hide");
  const p=S.player;
  const today=new Date().toISOString().slice(0,10);
  const taken=p.last_day===today;
  const mult=1+Math.min(0.5,((p.streak||0))*0.1);
  body.innerHTML=`<div class="shead">Ежедневный подарок</div>
    <div class="row"><span class="ic">🔥</span><span class="mid"><span class="t">Текущий стрик: ${p.streak||0} дней</span>
      <span class="s">Каждый день награда растёт на 10% (до +50%)</span></span></div>
    <div class="row"><span class="ic">💰</span><span class="mid"><span class="t">Сегодня: +${money(50*mult)}</span>
      <span class="s">База ${money(50)} × ${mult.toFixed(1)}</span></span></div>
    <button class="btn ${taken?'ghost':'good'}" id="dBonus" style="width:100%;padding:15px;margin-top:8px" ${taken?"disabled":""}>
      ${taken?"✅ Сегодня уже забрал":"🎁 Забрать бонус"}</button>
    <div class="shead">Бесплатный спин</div>
    <div class="row"><span class="ic">⚡</span><span class="mid"><span class="t">Каждые 30 минут</span>
      <span class="s">${freeReady()?"✅ Готов":"⏳ Перезарядка"}</span></span></div>
    <button class="btn ${freeReady()?'':'ghost'}" id="dFree" style="width:100%;padding:14px" ${freeReady()?"":"disabled"}>⚡ Использовать бесплатно</button>`;
  const b=$("dBonus"); if(b) b.onclick=doBonus;
  const f=$("dFree"); if(f) f.onclick=()=>{ $("panel").classList.remove("show"); doFreeSpin(); };
};

/* --- Купоны --- */
RENDER.coupons=(body)=>{
  $("panelTitle").textContent="🎟️ Купоны"; $("panelSearch").classList.add("hide");
  body.innerHTML=`<div class="shead">Активировать промокод</div>
    <div class="field"><input id="cpIn" placeholder="Введите код купона" style="text-transform:uppercase"></div>
    <button class="btn" id="cpGo" style="width:100%;padding:14px;margin-bottom:16px">Активировать</button>
    <div class="shead">Что бывает в купонах</div>
    <div class="row"><span class="ic">💰</span><span class="mid"><span class="t">Деньги</span><span class="s">Пополнение баланса</span></span></div>
    <div class="row"><span class="ic">🍀</span><span class="mid"><span class="t">Удача ×2 / ×4 / ×10</span><span class="s">Повышенный шанс редких номеров</span></span></div>
    <div class="row"><span class="ic">💎</span><span class="mid"><span class="t">Донат-баланс</span><span class="s">Тратится первым при вращении</span></span></div>`;
  $("cpGo").onclick=async()=>{
    const code=$("cpIn").value.trim(); if(!code) return toast("Введи код");
    try{
      const r=await api("/api/coupon","POST",{code});
      S.player.balance=r.balance; S.player.donate_balance=r.donate_balance;
      S.player.luck_until=r.luck_until; S.player.luck_mult=r.luck_mult;
      renderHeader(); confetti(); beep(800,.2);
      let rewardTxt=r.reward;
      if(r.reward_kind==="money") rewardTxt="+"+money(r.reward_amt);
      else if(r.reward_kind==="donate") rewardTxt="+"+money(r.reward_amt)+" доната";
      toast(`🎉 Активировано: ${rewardTxt}`);
      $("cpIn").value="";
    }catch(e){
      const map={not_found:"Купон не найден",locked:"",};
      const er=e.data&&e.data.error;
      if(e.status===400&&er&&er.indexOf("уже")>=0) toast("⏳ Ты уже активировал этот купон");
      else if(er==="not_found") toast("❌ Купон не найден");
      else if(er==="maintenance") toast("🔧 Идёт техперерыв");
      else if(er) toast("❌ "+er);
      else toast("Ошибка активации");
    }
  };
};

/* --- VIP магазин (Telegram Stars) --- */
RENDER.vip=async(body)=>{
  $("panelTitle").textContent="⭐ VIP Магазин"; $("panelSearch").classList.add("hide");
  if(!S.skus.length){ try{ const s=await api("/api/skus","GET",{}); S.skus=s.skus||[]; }catch(e){} }
  const p=S.player;
  const activeLuck=(p.luck_until&&p.luck_until>Date.now())?`удача ×${p.luck_mult} ещё ${Math.ceil((p.luck_until-Date.now())/60000)} мин`:"";
  body.innerHTML=`
    <div class="shead">Твои бонусы</div>
    <div class="row"><span class="ic">🍀</span><span class="mid"><span class="t">Удача</span><span class="s">${esc(activeLuck||"не активна")}</span></span></div>
    <div class="row"><span class="ic">🪙</span><span class="mid"><span class="t">Коины удачи</span><span class="s">${p.coins||0} шт.</span></span></div>
    <div class="row"><span class="ic">💎</span><span class="mid"><span class="t">Донат-баланс</span><span class="s">${money(p.donate_balance||0)}</span></span></div>
    <div class="shead">Товары за ⭐ Telegram Stars</div>
    ${S.skus.map(s=>`<div class="row"><span class="ic">${s.icon}</span>
      <span class="mid"><span class="t">${esc(s.title)}</span><span class="s">${esc(s.desc)}</span></span>
      <span class="end"><button class="btn sm gold" data-sku="${s.sku}">${s.stars} ⭐</button></span></div>`).join("")}
    <div class="field"><div class="hint" style="margin-top:14px">Оплата проходит внутри Telegram через Telegram Stars. Бонус зачисляется сразу после оплаты.</div></div>`;
  body.querySelectorAll("[data-sku]").forEach(b=>b.onclick=()=>buyStars(b.dataset.sku,b));
};

async function buyStars(sku,btn){
  const item=S.skus.find(s=>s.sku===sku);
  if(btn){ btn.textContent="…"; btn.disabled=true; }
  try{
    const r=await api("/api/stars/order","POST",{sku});
    if(r.invoice_url&&TG&&TG.openInvoice){
      TG.openInvoice(r.invoice_url,(status)=>{
        if(status==="paid"){ toast("🎉 Оплачено! Бонус скоро зачислится"); setTimeout(loadState,1500).then(renderHeader); }
        else toast("Оплата не завершена");
        RENDER.vip($("panelBody"));
      });
    }else if(r.invoice_url){
      window.open(r.invoice_url,"_blank");
      toast("Открой оплату в Telegram");
    }else{
      toast("Не удалось создать счёт: "+(r.error||""));
    }
  }catch(e){
    if(e.data&&e.data.error&&e.data.error.indexOf("chat not found")>=0)
      toast("Бот тебя не знает — открой игру через бота /api");
    else toast("Ошибка: "+(e.data&&e.data.error||e.message));
  }finally{ if(btn){ setTimeout(()=>RENDER.vip($("panelBody")),300); } }
}

/* --- Профиль --- */
RENDER.profile=async(body)=>{
  $("panelTitle").textContent="👤 Профиль"; $("panelSearch").classList.add("hide");
  await loadState();
  const p=S.player, st=p.stats||{};
  const span=(S.nextXp-S.lvlXp)||1, prog=Math.max(0,Math.min(100,Math.round(((p.xp-S.lvlXp)/span)*100)));
  const hist=(p.history||[]).slice(0,25).map(h=>{
    const map={spin:["🔁","Спин"],freespin:["⚡","Бесплатный спин"],keep:["📱","Оставлен"],sell:["💰","Продан"],
      bank:["🏦","В банк"],mass_sell:["💰","Массовая продажа"],bonus:["🎁","Бонус"],lvl:["🎉","Уровень"],
      upwin:["⬆️","Апгрейд +"],uplose:["💔","Апгрейд −"],ach:["🏆","Достижение"],task:["✅","Задание"],
      list:["🏪","Выставлен"],unlist:["↩️","Снят с рынка"],buy:["🛒","Куплен"],swap:["🔄","Обмен"],
      inbox:["📥","Перевод"],stars:["⭐","Stars-покупка"],got_item:["🎁","Получен номер"],maintenance:["🔧","Техперерыв"]};
    const m=map[h.k]||["•",h.k];
    const t=new Date(h.ts).toLocaleDateString("ru-RU",{day:"2-digit",month:"2-digit"});
    let amtHTML="";
    if(h.amt){
      if(h.k==="stars") amtHTML=`<b>${dotSep(h.amt)}⭐</b>`;
      else if(h.k==="buy") amtHTML=`<b style="color:var(--bad)">-${money(h.amt)}</b>`;
      else amtHTML=`<b style="color:${h.amt>0?'var(--good)':'var(--bad)'}">${h.amt>0?"+":""}${money(h.amt)}</b>`;
    }
    return `<div class="row"><span class="ic">${m[0]}</span>
      <span class="mid"><span class="t">${m[1]}${h.num?" "+esc(h.num):""}${h.name?" "+esc(h.name):""}</span>
      <span class="s">${t}</span></span>
      <span class="end">${amtHTML}</span></div>`;
  }).join("");
  body.innerHTML=`
    <div class="row"><span class="ic">🏅</span><span class="mid"><span class="t">Уровень ${S.level}</span>
      <span class="s">${fmt(p.xp)} XP · до ${fmt(S.nextXp)}</span><span class="pbar"><i style="width:${prog}%"></i></span></span></div>
    <div class="msum" style="margin-top:12px">
      <div><div class="v">${money(p.balance)}</div><div class="l">баланс</div></div>
      <div><div class="v">${S.inv.length}</div><div class="l">номеров</div></div>
      <div><div class="v">${st.spins||0}</div><div class="l">спинов</div></div>
      <div><div class="v">${st.rare||0}</div><div class="l">редких</div></div>
    </div>
    <div class="shead">Статистика</div>
    <div class="row"><span class="ic">💰</span><span class="mid"><span class="t">Продано номеров</span></span><span class="end">${st.sold||0}</span></div>
    <div class="row"><span class="ic">🛒</span><span class="mid"><span class="t">Куплено на рынке</span></span><span class="end">${st.bought||0}</span></div>
    <div class="row"><span class="ic">⬆️</span><span class="mid"><span class="t">Апгрейдов</span></span><span class="end">${st.upg||0}</span></div>
    <div class="row"><span class="ic">🌍</span><span class="mid"><span class="t">Стран использовано</span></span><span class="end">${(st.ctry||[]).length}</span></div>
    <div class="row"><span class="ic">🔥</span><span class="mid"><span class="t">Стрик</span></span><span class="end">${p.streak||0} дн.</span></div>
    <div class="shead">История</div>${hist||'<div class="empty">Пока пусто</div>'}`;
};

/* --- Настройки --- */
RENDER.settings=(body)=>{
  $("panelTitle").textContent="⚙️ Настройки"; $("panelSearch").classList.add("hide");
  const p=S.player;
  const tog=(id,key,title,sub)=>`<div class="tog" data-tg="${key}"><div><div class="tl">${title}</div>${sub?`<div class="ts">${sub}</div>`:""}</div>
    <div class="switch ${p[key]?"on":""}"><i></i></div></div>`;
  body.innerHTML=`
    <div class="shead">Игра</div>
    ${tog("snd","sound_on","Звук","Эффекты при вращении")}
    ${tog("vib","vibration","Вибрация","Тактильный отклик")}
    ${tog("eff","effects","Спецэффекты","Конфетти при редких номерах")}
    ${tog("coins","use_coins","Тратить коины удачи","+15% к шансу редкости за спин")}
    ${tog("auto","autosell","Автопродажа","В мульти-спине продавать обычные")}
    <div class="shead">Оформление игры</div>
    <div class="seg" id="themeSeg">${Object.keys(THEMES).map(k=>`<button data-th="${k}" class="${(p.theme||"classic")===k?"on":""}">${themeName(k)}</button>`).join("")}</div>
    <div class="shead">Скорость вращения</div>
    <div class="seg" id="spdSeg">${["slow","normal","fast"].map(k=>`<button data-spd="${k}" class="${(p.spin_speed||"slow")===k?"on":""}">${spdName(k)}</button>`).join("")}</div>
    <div class="shead">Аккаунт</div>
    <div class="row"><span class="ic">🆔</span><span class="mid"><span class="t">ID: ${p.user_id}</span>
      <span class="s">@${esc(p.username||"—")}</span></span></div>
    ${S.is_admin?'<button class="btn bad" id="toAdmin" style="width:100%;padding:13px;margin-top:8px">🛠 Открыть админку</button>':""}`;

  body.querySelectorAll("[data-tg]").forEach(r=>r.onclick=async()=>{
    const key=r.dataset.tg, sw=r.querySelector(".switch");
    const val=!sw.classList.contains("on"); sw.classList.toggle("on",val);
    const map={sound_on:"sound_on",vibration:"vibration",effects:"effects",use_coins:"use_coins",autosell:"autosell"};
    S.player[map[key]]=val;
    try{ await api("/api/settings","POST",Object.fromEntries([[map[key]],val])); }catch(e){}
    if(key==="snd"&&val) beep(600,.1);
    if(key==="vib"&&val) buzz(20);
  });
  $("themeSeg").querySelectorAll("button").forEach(b=>b.onclick=async()=>{
    $("themeSeg").querySelectorAll("button").forEach(x=>x.classList.remove("on")); b.classList.add("on");
    const th=b.dataset.th; S.player.theme=th; applyTheme(th);
    try{ await api("/api/settings","POST",{theme:th}); }catch(e){}
  });
  $("spdSeg").querySelectorAll("button").forEach(b=>b.onclick=async()=>{
    $("spdSeg").querySelectorAll("button").forEach(x=>x.classList.remove("on")); b.classList.add("on");
    S.player.spin_speed=b.dataset.spd;
    try{ await api("/api/settings","POST",{spin_speed:b.dataset.spd}); }catch(e){}
  });
  const ta=$("toAdmin"); if(ta) ta.onclick=()=>openPanel("admin");
};
function themeName(k){return {classic:"Классика",dark:"Тёмная",neon:"Неон",ocean:"Океан",royal:"Королевская"}[k]||k;}
function spdName(k){return {slow:"Медленно",normal:"Нормально",fast:"Быстро"}[k]||k;}

/* ---------------------------------------------------------- АДМИНКА */
RENDER.admin=async(body)=>{
  if(!S.is_admin){ body.innerHTML='<div class="empty"><div class="ic">🚫</div>Доступ запрещён</div>'; $("panelTitle").textContent="Админка"; return; }
  $("panelTitle").textContent="🛠 Админка"; $("panelSearch").classList.add("hide");
  body.innerHTML='<div class="empty"><div class="ic">⏳</div>Загрузка…</div>';
  let stats; try{ stats=await api("/api/admin/stats","GET",{}); }catch(e){ body.innerHTML='<div class="empty">Ошибка</div>'; return; }
  const mt=stats.maintenance||{};
  body.innerHTML=`
    <div class="msum">
      <div><div class="v">${stats.players}</div><div class="l">игроков</div></div>
      <div><div class="v">${stats.numbers}</div><div class="l">номеров</div></div>
      <div><div class="v">${stats.market}</div><div class="l">лотов</div></div>
      <div><div class="v">${stats.coupons}</div><div class="l">купонов</div></div>
    </div>

    <div class="shead">🔧 Технический перерыв</div>
    <div class="row"><span class="ic">${mt.started?"🔴":"🟢"}</span>
      <span class="mid"><span class="t">${mt.started?"Игра закрыта":mt.active?"Запланирован":"Перерыв не активен"}</span>
      ${mt.active?`<span class="s">${esc(mt.message||"")}</span>`:""}</span></div>
    <div class="field"><label>Сообщение игрокам</label><input id="mtMsg" value="${esc(mt.message||"Плановые технические работы")}"></div>
    <div class="field"><label>Закрыть через (минуты)</label><input id="mtMin" type="number" min="0" value="5"></div>
    <div class="field"><label>…или к времени МСК (ЧЧ:ММ)</label><input id="mtAt" placeholder="например 23:30"></div>
    <div style="display:flex;gap:8px;margin-bottom:8px">
      <button class="btn bad" id="mtStart" style="flex:1;padding:13px">Закрыть игру</button>
      <button class="btn good" id="mtStop" style="flex:1;padding:13px">Открыть игру</button>
    </div>

    <div class="shead">🎟️ Купоны</div>
    <div class="field"><label>Тип</label>
      <div class="seg" id="cpKind">
        <button data-k="money" class="on">Деньги</button>
        <button data-k="luck">Удача</button>
        <button data-k="donate">Донат-баланс</button>
      </div></div>
    <div class="field"><label id="cpValLab">Сумма (${(C_BY_ID[S.country]||CTRY[0]).cur})</label><input id="cpVal" type="number" min="1" value="1000"></div>
    <div class="field"><label>Код (пусто = сгенерировать)</label><input id="cpCode" placeholder="MYCODE" style="text-transform:uppercase"></div>
    <div class="field"><label>Лимит активаций (0 = без лимита)</label><input id="cpMax" type="number" min="0" value="0"></div>
    <div class="field"><label>Действует до (ЧЧ:ММ, пусто = бессрочно)</label><input id="cpExp" placeholder="23:59"></div>
    <button class="btn" id="cpCreate" style="width:100%;padding:13px;margin-bottom:10px">➕ Создать купон</button>
    <div id="cpList"></div>

    <div class="shead">👑 Админы</div>
    <div class="field"><label>ID пользователя</label><input id="adId" type="number" placeholder="123456789"></div>
    <div style="display:flex;gap:8px;margin-bottom:8px">
      <button class="btn good" id="adAdd" style="flex:1;padding:12px">Добавить</button>
      <button class="btn bad" id="adDel" style="flex:1;padding:12px">Убрать</button>
    </div>
    <div class="shead">Текущие админы</div>
    <div id="adList">${(stats.admins||[]).map(a=>`<div class="row"><span class="ic">👑</span><span class="mid"><span class="t">${a}</span></span></div>`).join("")||'<div class="empty">нет</div>'}</div>

    <div class="shead">🏆 Топ игроков</div>
    ${(stats.top||[]).map((t,i)=>`<div class="row"><span class="ic">${i+1}</span>
      <span class="mid"><span class="t">${esc(t.name)}</span><span class="s">${fmt(t.xp)} XP</span></span>
      <span class="end">${money(t.balance)}</span></div>`).join("")||'<div class="empty">нет игроков</div>'}`;

  // --- техперерыв ---
  $("mtStart").onclick=async()=>{
    const msg=$("mtMsg").value.trim();
    const min=+$("mtMin").value||0;
    const at=$("mtAt").value.trim();
    const body2={action:"start",message:msg};
    if(at){ body2.at=at; }
    else{ body2.delay_sec=min*60; }
    try{ const r=await api("/api/admin/maintenance","POST",body2);
      toast("🔧 Техперерыв запущен"); S.maintenance=r; RENDER.admin(body);
    }catch(e){ toast("Ошибка: "+(e.data&&e.data.error||e.message)); }
  };
  $("mtStop").onclick=async()=>{
    try{ await api("/api/admin/maintenance","POST",{action:"stop"});
      toast("🟢 Игра открыта"); S.maintenance.started=false; S.maintenance.active=false; RENDER.admin(body);
    }catch(e){ toast("Ошибка"); }
  };

  // --- купоны ---
  let kind="money";
  $("cpKind").querySelectorAll("button").forEach(b=>b.onclick=()=>{
    $("cpKind").querySelectorAll("button").forEach(x=>x.classList.remove("on")); b.classList.add("on");
    kind=b.dataset.k;
    const cur=(C_BY_ID[S.country]||CTRY[0]).cur;
    $("cpValLab").textContent = kind==="money"?`Сумма (${cur})`:kind==="luck"?"Множитель удачи (2/4/10)":`Донат-баланс (${cur})`;
    $("cpVal").value = kind==="luck"?2:kind==="donate"?100:1000;
  });
  $("cpCreate").onclick=async()=>{
    const val=+$("cpVal").value;
    if(!(val>0)) return toast("Введите значение");
    const code=$("cpCode").value.trim().toUpperCase();
    const max=+$("cpMax").value||0;
    const exp=$("cpExp").value.trim();
    // деньги/донат вводятся в валюте выбранной страны -> храним внутренние единицы
    let units=val;
    if(kind==="money"||kind==="donate"){
      const base=(C_BY_ID[S.country]||CTRY[0]).b;
      units=Math.round((val/base)*100)/100;
      if(!(units>0)) return toast("Сумма слишком мала");
    }
    const payload={action:"create",kind,value:units,code,max_activations:max};
    if(exp){ const d=new Date(); const [h,m]=exp.split(":").map(Number); d.setHours(h,m,0,0); if(d.getTime()<Date.now()) d.setDate(d.getDate()+1); payload.expires_at=d.getTime(); }
    try{
      const r=await api("/api/admin/coupons","POST",payload);
      toast("✅ Купон создан: "+r.coupon.code); beep(760,.15);
      RENDER.admin(body);
    }catch(e){ toast("Ошибка: "+(e.data&&e.data.error||e.message)); }
  };
  loadCoupons();

  // --- админы ---
  $("adAdd").onclick=()=>adminAction("add");
  $("adDel").onclick=()=>adminAction("remove");
  async function adminAction(action){
    const uid=+$("adId").value; if(!uid) return toast("Введите ID");
    try{ await api("/api/admin/admins","POST",{action,user_id:uid});
      toast(action==="add"?"👑 Админ добавлен":"Админ убран");
      const s2=await api("/api/admin/stats","GET",{});
      $("adList").innerHTML=(s2.admins||[]).map(a=>`<div class="row"><span class="ic">👑</span><span class="mid"><span class="t">${a}</span></span></div>`).join("")||'<div class="empty">нет</div>';
    }catch(e){ toast("Ошибка: "+(e.data&&e.data.error||e.message)); }
  }
};

async function loadCoupons(){
  const el=$("cpList"); if(!el) return;
  let d; try{ d=await api("/api/admin/coupons","POST",{action:"list"}); }catch(e){ el.innerHTML=""; return; }
  const cs=d.coupons||[];
  if(!cs.length){ el.innerHTML='<div class="empty">Купонов пока нет</div>'; return; }
  el.innerHTML=cs.map(c=>{
    const kindLbl={money:"💰",luck:"🍀",donate:"💎"}[c.kind]||"🎟️";
    const val=c.kind==="luck"?`×${c.value}`:money(c.value);
    const exp=c.expires_at?new Date(c.expires_at).toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}):"бессрочно";
    const lim=c.max_activations?`${c.used_count}/${c.max_activations}`:`${c.used_count}`;
    return `<div class="row"><span class="ic">${kindLbl}</span>
      <span class="mid"><span class="t">${esc(c.code)} · ${val}</span>
        <span class="s">${lim} активаций · до ${exp}</span></span>
      <span class="end"><button class="btn sm ${c.active?'bad':'good'}" data-cp="${c.id}" data-on="${c.active?0:1}">${c.active?"Выкл":"Вкл"}</button></span></div>`;
  }).join("");
  el.querySelectorAll("[data-cp]").forEach(b=>b.onclick=async()=>{
    try{ await api("/api/admin/coupons","POST",{action:"toggle",coupon_id:+b.dataset.cp,active:b.dataset.on==="1"});
      loadCoupons();
    }catch(e){ toast("Ошибка"); }
  });
}

/* ---------------------------------------------------------- ТЕХПЕРЕЫВ (игрок) */
let mtTimer=null, newsShown=false;
function setupMaintenance(){
  pollMaintenance();
  setInterval(pollMaintenance,5000);
  $("newsClose").onclick=()=>{ $("newsPop").classList.remove("show"); newsShown=true; updateMtTimer(); };
}

async function pollMaintenance(){
  let m; try{ m=await api("/api/maintenance","GET",{}); }catch(e){ return; }
  S.maintenance=m;

  // блок-экран при активном перерыве
  if(m.started){
    $("maintBlock").classList.remove("hide");
    $("maintMsg").textContent=m.message||"";
    $("maintLink").href=m.channel_url||"#";
    document.getElementById("app").style.display="none";
    return;
  }
  $("maintBlock").classList.add("hide");
  document.getElementById("app").style.display="";

  // всплывающее окно-новость (один раз, можно закрыть)
  if(m.active && m.seconds_left>0 && !newsShown){
    $("newsMsg").textContent=(m.message||"Скоро начнутся технические работы.")+"\nИграть можно до начала перерыва.";
    $("newsPop").classList.add("show");
  }
  updateMtTimer();
}

function updateMtTimer(){
  const m=S.maintenance;
  const bar=$("mtimer");
  if(m && m.active && !m.started && m.seconds_left>0){
    bar.classList.remove("hide");
    const left=m.seconds_left;
    $("mtimerTxt").textContent=`⚠️ Техперерыв через ${String(Math.floor(left/60)).padStart(2,"0")}:${String(left%60).padStart(2,"0")}`;
    if($("newsPop").classList.contains("show")) $("newsCd").textContent=$("mtimerTxt").textContent.replace("⚠️ ","");
  }else{
    bar.classList.add("hide");
  }
}
// тикаем таймер локально между запросами
setInterval(()=>{
  if(S.maintenance&&S.maintenance.active&&!S.maintenance.started&&S.maintenance.seconds_left>0){
    S.maintenance.seconds_left--; updateMtTimer();
  }
},1000);

/* ---------------------------------------------------------- старт */
window.addEventListener("load",boot);
if(TG){ try{ TG.onEvent&&TG.onEvent("viewportChanged",()=>{}); }catch(e){} }
