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
  {k:"RU",name:"Россия",flag:"🇷🇺",cur:"₽",code:"RUB",b:100},
  {k:"US",name:"США",flag:"🇺🇸",cur:"$",code:"USD",b:1.5},
  {k:"DE",name:"Германия",flag:"🇩🇪",cur:"€",code:"EUR",b:1.5},
  {k:"GB",name:"Великобритания",flag:"🇬🇧",cur:"£",code:"GBP",b:1.2},
  {k:"CN",name:"Китай",flag:"🇨🇳",cur:"CN¥",code:"CNY",b:10},
  {k:"JP",name:"Япония",flag:"🇯🇵",cur:"JP¥",code:"JPY",b:200},
  {k:"KZ",name:"Казахстан",flag:"🇰🇿",cur:"₸",code:"KZT",b:500},
  {k:"TR",name:"Турция",flag:"🇹🇷",cur:"₺",code:"TRY",b:40},
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
/* Стоимость категорий и гейты приходят с сервера ( `limits` ), чтобы
   клиент и сервер никогда не расходились. Значения ниже — фолбэк. */
const DEFAULT_CAT_COST={common:1,unc:2,rare:4,epic:8,myth:16,leg:33,blat:69,sec:113};
const DEFAULT_CAT_LVL={leg:4,blat:7,sec:10};
const catCostTable=()=>Object.assign({},DEFAULT_CAT_COST,(S.limits&&S.limits.category_cost)||{});
const catLevelTable=()=>Object.assign({},DEFAULT_CAT_LVL,(S.limits&&S.limits.category_level)||{});
const catMult=c=>catCostTable()[c]||1;
const spinCostX=(cat,count)=>((S.limits&&S.limits.spin_x)||SPIN_X)*catMult(cat)*(count||1);
const catLocked=id=>{ const n=catLevelTable()[id]||0; return n>0 && S.level<n; };
const catNeed=id=>catLevelTable()[id]||0;

/* ---------------------------------------------------------- состояние */
let S={
  player:null, inv:[], level:0, lvlXp:0, nextXp:0,
  is_admin:false,
  country:"RU", category:null, mult:1,
  busy:false, maintenance:{}, limits:{},
  skus:[],
};
const TG=(window.Telegram&&window.Telegram.WebApp)?window.Telegram.WebApp:null;
let DEV_UID=999999;
// Удобно для локального превью: ?uid=111333 — посмотреть глазами игрока.
// В Telegram используется initData, этот параметр ни на что не влияет.
try{
  const dev=new URLSearchParams(location.search).get("uid");
  if(dev&&/^\d+$/.test(dev)) DEV_UID=+dev;
}catch(e){}

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
function walletMoney(country=S.country){
  const c=C_BY_ID[country]||CTRY[0];
  const amount=S.player&&S.player.wallets?S.player.wallets[c.code]:null;
  return amount==null?money(S.player&&S.player.balance||0):nativePrice(amount,country);
}
// Стоимость номера всегда показана в валюте страны номера.
function priceOf(p,k){ return nativePrice(p,k); }
// валюта конкретной страны, без пересчёта (для цены в её же деньгах)
function nativePrice(p,k){
  const c=C_BY_ID[k]||CTRY[0], n=Number(p)||0;
  return n.toLocaleString("ru-RU",{minimumFractionDigits:0,maximumFractionDigits:2})+c.cur;
}

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
    if(data.error==="subscription_required"&&path!=="/api/auth") renderSubscriptionGate(data);
    const err=new Error(data.error||("HTTP "+res.status)); err.data=data; err.status=res.status; throw err;
  }
  const selfResponse=!data.user_id||!S.player||Number(data.user_id)===Number(S.player.user_id);
  if(S.player&&selfResponse&&data.wallets) S.player.wallets=data.wallets;
  if(S.player&&selfResponse&&Number.isFinite(Number(data.balance))){
    const prior=Number(S.player.balance)||0, next=Number(data.balance);
    if(!data.wallets&&Math.abs(next-prior)>=0.005){
      S.player.wallets=S.player.wallets||{};
      S.player.wallets.RUB=Math.max(0,(Number(S.player.wallets.RUB)||0)+Math.round((next-prior)*10000)/100);
    }
    S.player.balance=next;
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
  classic:{bg1:"#060a17",bg2:"#0c1330",bg3:"#101a3d",acc:"#4da3ff",acc2:"#8b5cf6",gold:"#ffd166",
           a1:"#2b6cff",a2:"#8b5cf6",a3:"#00c2b8"},
  dark:{bg1:"#050505",bg2:"#111116",bg3:"#1c1c22",acc:"#ff7a7a",acc2:"#ffa502",gold:"#ffd166",
        a1:"#8a2b2b",a2:"#a05a10",a3:"#444"},
  neon:{bg1:"#0a0018",bg2:"#16002e",bg3:"#25004d",acc:"#00ffcc",acc2:"#ff00cc",gold:"#ffe600",
        a1:"#00b3a4",a2:"#c400c4",a3:"#5a00ff"},
  ocean:{bg1:"#00101d",bg2:"#002840",bg3:"#003b5c",acc:"#00d4ff",acc2:"#0077b6",gold:"#7ff0d4",
         a1:"#0066aa",a2:"#004e88",a3:"#00a896"},
  royal:{bg1:"#120024",bg2:"#240048",bg3:"#3a0066",acc:"#ffd700",acc2:"#b8860b",gold:"#ffe27a",
         a1:"#6b3fa0",a2:"#9a6b00",a3:"#3a1f7a"},
};
function applyTheme(name){
  const t=THEMES[name]||THEMES.classic;
  const r=document.documentElement.style;
  r.setProperty("--bg1",t.bg1); r.setProperty("--bg2",t.bg2); r.setProperty("--bg3",t.bg3);
  r.setProperty("--acc",t.acc); r.setProperty("--acc2",t.acc2); r.setProperty("--gold",t.gold);
  document.body.style.background=
    `radial-gradient(1100px 700px at 15% -15%,${t.bg3},transparent 60%),`+
    `radial-gradient(900px 600px at 110% 10%,${t.a2}33,transparent 55%),`+
    `linear-gradient(180deg,${t.bg2},${t.bg1})`;
  const blobs=document.querySelectorAll("#aurora i");
  const cols=[t.a1,t.a2,t.a3];
  blobs.forEach((b,i)=>{ b.style.background=cols[i%cols.length]; });
}

/* ---------------------------------------------------------- BOOT */
function renderSubscriptionGate(info={}){
  $("app").classList.add("hide");
  $("boot").classList.remove("hide");
  $("boot").classList.add("subscription-mode");
  const channel=info.channel||"@sim_roulette";
  const channelUrl=info.channel_url||"https://t.me/sim_roulette";
  $("boot").innerHTML=`<div class="bootLogo">SIM ROULETTE</div>
    <section id="subscriptionGate">
      <div class="subGateIcon">📣</div>
      <h1>Подпишись на канал</h1>
      <p>Чтобы пользоваться мини-приложением, подпишись на новостной канал <b>${esc(channel)}</b>.</p>
      <a class="btn ghost" id="subscriptionChannel" href="${esc(channelUrl)}" target="_blank" rel="noopener">📢 Открыть канал</a>
      <button class="btn good" id="subscriptionCheck">✅ Подписался!</button>
      <div id="subscriptionStatus" role="status">После подписки нажми «Подписался!»</div>
    </section>`;
  const link=$("subscriptionChannel");
  link.onclick=(event)=>{
    if(TG&&typeof TG.openTelegramLink==="function"){
      event.preventDefault(); TG.openTelegramLink(channelUrl);
    }
  };
  $("subscriptionCheck").onclick=async()=>{
    const button=$("subscriptionCheck"), status=$("subscriptionStatus");
    button.disabled=true; status.textContent="Проверяем подписку…";
    try{
      const result=await api("/api/subscription/check","POST",{});
      if(result.subscribed){
        status.textContent="Подписка подтверждена — загружаем игру…";
        setTimeout(()=>location.reload(),350);
      }else{
        status.textContent="Подписку пока не видим. Подпишись на канал и попробуй ещё раз.";
      }
    }catch(e){
      status.textContent="Не удалось проверить подписку. Проверь соединение и попробуй ещё раз.";
    }finally{
      if($("subscriptionCheck")) $("subscriptionCheck").disabled=false;
    }
  };
}

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
    if(e.data&&e.data.error==="subscription_required"){
      renderSubscriptionGate(e.data);
      return;
    }
    $("boot").innerHTML='<div class="empty"><div class="ic">⚠️</div>Сервер недоступен.<br>Попробуй позже.</div>';
    return;
  }
  S.player=auth.player; S.is_admin=!!auth.is_admin;
  if(S.player.is_admin) S.is_admin=true;
  if(auth.maintenance) S.maintenance=auth.maintenance;
  if(auth.limits) S.limits=auth.limits;

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
  try{ if(localStorage.getItem("simroulette_calm")==="1") document.body.classList.add("calm"); }catch(e){}
  renderHeader();
  renderBanners();
  renderControls();
  updateFreeBtn();

  setupMenu();
  setupSpin();
  setupControls();
  setupTabs();
  setupMaintenance();
  maybeOnboard();
}

/* ---------------------------------------------------------- нижняя навигация */
function setupTabs(){
  document.querySelectorAll("#tabbar .tab").forEach(t=>{
    t.addEventListener("click",()=>{
      if(t.dataset.tab==="home"){
        document.querySelectorAll("#tabbar .tab").forEach(x=>x.classList.remove("on"));
        t.classList.add("on");
        ["menu","sheet","panel"].forEach(id=>$(id).classList.remove("show"));
      }else{
        document.querySelectorAll("#tabbar .tab").forEach(x=>x.classList.remove("on"));
        t.classList.add("on");
      }
    });
  });
  // Возврат на главную из панели сбрасывает активный таб
  $("panelBack").addEventListener("click",()=>{
    document.querySelectorAll("#tabbar .tab").forEach(x=>x.classList.remove("on"));
    const home=document.querySelector('#tabbar .tab[data-tab="home"]');
    if(home) home.classList.add("on");
  });
}

/* ---------------------------------------------------------- онбординг */
const OB_SLIDES=[
  {art:"🎰",t:"Добро пожаловать!",p:"SIM Roulette — коллекционная игра с редкими телефонными номерами со всего мира. Крути рулетку и собирай свою коллекцию."},
  {art:"💎",t:"Лови редкость",p:"Выбери страну и категорию. Чем выше категория — тем дороже прокрут, но и шанс поймать легендарный номер выше."},
  {art:"🏪",t:"Торгуй и расти",p:"Продавай номера на рынке, улучшай их, выполняй задания и поднимайся в рейтинге игроков."},
];
function maybeOnboard(){
  if(S.maintenance&&S.maintenance.blocked) return;
  try{ if(localStorage.getItem("simroulette_onboard_v6")) return; }catch(e){ return; }
  let i=0;
  const draw=()=>{
    const s=OB_SLIDES[i];
    $("obArt").textContent=s.art; $("obTitle").textContent=s.t; $("obText").textContent=s.p;
    $("obDots").innerHTML=OB_SLIDES.map((_,k)=>`<i class="${k===i?'on':''}"></i>`).join("");
    $("obNext").textContent=i===OB_SLIDES.length-1?"Начать игру 🚀":"Далее";
  };
  const finish=()=>{
    try{ localStorage.setItem("simroulette_onboard_v6","1"); }catch(e){}
    $("onboard").classList.remove("show");
  };
  $("obNext").onclick=()=>{ if(i<OB_SLIDES.length-1){ i++; draw(); beep(620,.07); } else finish(); };
  $("obSkip").onclick=finish;
  draw();
  setTimeout(()=>$("onboard").classList.add("show"),650);
}

/* ---------------------------------------------------------- кастомный confirm */
function askConfirm(text,title){
  return new Promise(resolve=>{
    $("confirmText").textContent=text;
    $("confirmTitle").textContent=title||"Подтвердите действие";
    $("confirmPop").classList.add("show");
    const done=(v)=>{
      $("confirmPop").classList.remove("show");
      $("confirmYes").onclick=null; $("confirmNo").onclick=null;
      resolve(v);
    };
    $("confirmYes").onclick=()=>done(true);
    $("confirmNo").onclick=()=>done(false);
  });
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
  S.limits=st.limits||S.limits;
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
  let moneyStr=walletMoney(S.country);
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
  $("catVal").textContent=S.category?(catLocked(S.category)?"🔒 ":"")+R_BY_ID[S.category].n:"Любая";
  $("catVal").style.color=S.category?R_BY_ID[S.category].c:"";
  $("multVal").textContent="×"+S.mult;
  $("spinCost").textContent=money(spinCostX(S.category,S.mult));
  if(S.player) renderHeader();
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
  const lvl=S.level||0;
  const rows=[`<button class="row" data-k="" style="width:100%;text-align:left">
      <span class="ic">🎲</span><span class="mid"><span class="t">Любая категория</span><span class="s">все редкости · ${money(spinCostX(null,S.mult))} за прокрут</span></span>
      ${!S.category?'<span class="end">✅</span>':''}</button>`].concat(
    RAR.map(r=>{
      const need=catNeed(r.id), locked=catLocked(r.id);
      return `<button class="row ${locked?'locked':''}" data-k="${r.id}" data-locked="${locked?1:0}" style="width:100%;text-align:left">
      <span class="ic" style="color:${r.c}">${locked?'🔒':'●'}</span>
      <span class="mid"><span class="t">${esc(r.n)} ${locked?`<span class="lvlchip">с ур. ${need}</span>`:''}</span>
      <span class="s">${locked?`Твой уровень ${lvl} · откроется на ур. ${need}`:`от ${esc(r.n)} и выше · <b style="color:${r.c}">${money(spinCostX(r.id,S.mult))}</b> за прокрут`}</span></span>
      ${S.category===r.id?'<span class="end">✅</span>':''}</button>`;
    })).join("");
  openSheet(`<div class="sheetTitle">🎯 Ставка на категорию</div>
    <div class="field"><div class="hint">Номера ниже выбранной категории выпадать не будут.
    Чем выше категория — тем дороже прокрут. Сейчас: <b>${per}</b>. Твой уровень: <b>${lvl}</b>.</div></div>${rows}`);
  $("sheetBody").querySelectorAll("[data-k]").forEach(b=>b.onclick=()=>{
    if(b.dataset.locked==="1"){ toast(`🔒 Нужно достичь ур. ${catNeed(b.dataset.k)}`); buzz(40); return; }
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
  const wrap=$("reelWrap"), strip=$("reelStrip");
  const win=items[0];
  const rar=R_BY_ID[win.r]||RAR[0];
  const flag=win.country_flag||FLAG(win.k);
  const digits=Array.from(String(win.num||""));
  const digitPositions=digits.map((ch,i)=>/\d/.test(ch)?i:-1).filter(i=>i>=0);
  const speed=S.player.spin_speed||"normal";
  const tickMs=speed==="fast"?55:speed==="slow"?105:78;
  let cursor=0, timer=null, finished=false;

  wrap.classList.add("spinning");
  wrap.classList.toggle("rare-glow",R_IDX(win.r)>=5);
  strip.innerHTML=`<div class="reelBuildWrap">
      <span class="reelBuildFlag">${esc(flag)}</span>
      <div class="reelBuildLine" id="reelBuildLine" aria-label="Номер собирается"></div>
    </div><div id="reelSub">Номер собирается…</div>`;
  const line=$("reelBuildLine");
  digits.forEach(ch=>{
    if(/\d/.test(ch)) line.insertAdjacentHTML("beforeend",'<span class="buildDigit pending">·</span>');
    else line.insertAdjacentHTML("beforeend",`<span class="buildSep">${esc(ch)}</span>`);
  });
  const cells=Array.from(line.children);

  function finish(){
    if(finished) return;
    finished=true;
    if(timer) clearTimeout(timer);
    wrap.classList.remove("spinning");
    strip.innerHTML=`<div id="reelNum" class="reelAssembled" style="${R_IDX(win.r)>=5?`color:${rar.c}`:""}">${esc(flag)} ${esc(win.num)}</div>
      <div id="reelSub">${esc(rar.n)} · ${esc(win.op_short||opOf(win.k,win.op)[0])} · ${esc(win.country_name||"")}</div>`;
    done();
  }
  function revealNext(){
    if(finished) return;
    if(cursor>=digitPositions.length){
      timer=setTimeout(finish,160);
      return;
    }
    const pos=digitPositions[cursor], cell=cells[pos];
    cell.textContent=digits[pos];
    cell.classList.remove("pending");
    cell.classList.add("locked");
    cell.style.setProperty("--lock-i",cursor);
    beep(330+Math.min(cursor,8)*24,.035,"square",.025);
    cursor++;
    timer=setTimeout(revealNext,tickMs);
  }
  // Небольшая пауза, затем фиксируем цифры по очереди слева направо.
  timer=setTimeout(revealNext,150);
}

async function doSpin(){
  if(S.busy) return;
  if(S.maintenance.blocked){ toast("🔧 Идёт техперерыв"); return; }
  S.busy=true; const btn=$("spinBtn"); btn.classList.add("busy");
  try{
    const r=await api("/api/spin","POST",{
      country:S.country, category:S.category, count:S.mult,
      useCoins:!!S.player.use_coins, speed:S.player.spin_speed||"normal",
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
    else if(e.data&&e.data.error==="level_locked") toast(`🔒 Категория откроется на ур. ${e.data.need}`);
    else if(e.data&&e.data.error==="instant_ban") toast("⛔ Мгновенная скорость временно заблокирована");
    else toast("Ошибка: "+(e.data&&e.data.error||e.message));
  }finally{
    S.busy=false; btn.classList.remove("busy"); renderHeader();
  }
}

async function doFreeSpin(){
  if(!freeReady()){ toast("⏳ Бесплатный спин ещё не готов"); return; }
  if(S.maintenance.blocked){ toast("🔧 Идёт техперерыв"); return; }
  if(S.busy) return;
  S.busy=true; $("spinBtn").classList.add("busy");
  try{
    const r=await api("/api/spin","POST",{country:S.country,category:null,count:1,free:true,
      speed:S.player.spin_speed||"normal"});
    S.player.free_at=Date.now();
    S.player.balance=r.balance; S.inv=await refreshInv();
    buzz(20);
    animateReel(r.items,()=>showSingleResult(r.items[0],r));
  }catch(e){
    if(e.data&&e.data.error==="maintenance") toast("🔧 Идёт техперерыв");
    else if(e.data&&e.data.error==="free_cd"){
      toast(`⏳ Бесплатный спин через ${fmtTime(e.data.retry_in)}`);
      try{ await loadState(); }catch(_){}
    }
    else toast("Ошибка: "+(e.data&&e.data.error||e.message));
  }
  finally{ S.busy=false; $("spinBtn").classList.remove("busy"); renderHeader(); updateFreeBtn(); }
}

async function refreshInv(){ const st=await api("/api/state","POST",{}); S.inv=st.inventory||[]; return S.inv; }

/* одиночный результат — оставить / продать */
function showSingleResult(item,r){
  const rar=R_BY_ID[item.r], units=toUnits(item.p,item.k);
  const feats=(item.features||[]).slice(0,4).map(f=>esc(f.t)).join(" · ");
  const emoji=R_IDX(item.r)>=7?"🌌":R_IDX(item.r)>=5?"👑":R_IDX(item.r)>=3?"💎":"📱";
  openSheet(`<div id="resWrap" class="r${R_IDX(item.r)}">
    <div class="resGlow">${emoji}</div>
    <div class="resRar" style="background:${rar.c}">${esc(item.rarity_name||rar.n)}</div>
    <div class="resNum">${FLAG(item.k)} ${esc(item.num)}</div>
    <div class="resFeat">${esc(item.op_short||opOf(item.k,item.op)[0])}${feats?" · "+feats:""}</div>
    <div class="resPrice">${nativePrice(item.p,item.k)}</div>
    <div class="resCur">номинальная цена · ${esc((C_BY_ID[item.k]||CTRY[0]).name)} · удача ×${r.luck_mult||1}</div>
    <div class="resActs">
      <button class="btn ghost" id="rSell">Продать +${nativePrice(item.p,item.k)}</button>
      <button class="btn good" id="rKeep">Оставить</button>
    </div></div>`);
  if(R_IDX(item.r)>=2){ confetti(); beep(880,.2,"triangle",.08); }
  else beep(520,.12);
  $("rKeep").onclick=async()=>{
    await api("/api/decide","POST",{action:"keep"}); closeSheet(); toast("📱 Номер в коллекции"); renderHeader();
  };
  $("rSell").onclick=async()=>{
    const rr=await api("/api/decide","POST",{action:"sell"});
    closeSheet(); toast(`💰 +${nativePrice(item.p,item.k)}`); S.player.balance=rr.balance; renderHeader(); beep(700,.12);
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
      <div class="mp">${nativePrice(i.p,i.k)}</div>
      ${i.sold?'<div class="sold">✅ продан</div>':'<div class="sold" style="color:var(--mut)">📱 в коллекции</div>'}</div>`;
  }).join("");
  openSheet(`<div class="sheetTitle">🎉 Результат ×${items.length}</div>
    <div class="msum">
      <div><div class="v">${kept}</div><div class="l">в коллекции</div></div>
      <div><div class="v">${sold}</div><div class="l">продано</div></div>
      <div><div class="v">${nativePrice(best.p,best.k)}</div><div class="l">лучший</div></div>
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

/* --- Обмен валют --- */
RENDER.currencies=async(body)=>{
  $("panelTitle").textContent="💱 Обмен валют"; $("panelSearch").classList.add("hide");
  body.innerHTML='<div class="empty"><div class="ic">⏳</div>Загружаем актуальные курсы…</div>';
  let data;
  try{ data=await api("/api/currencies","GET",{}); }
  catch(e){
    body.innerHTML=`<div class="empty"><div class="ic">📡</div>Не удалось получить текущие курсы валют.<br><span class="hint">Проверь подключение и попробуй ещё раз.</span><br><button class="btn sm" id="fxRetry">Повторить</button></div>`;
    const retry=$("fxRetry"); if(retry) retry.onclick=()=>RENDER.currencies(body);
    return;
  }
  S.player.wallets=data.wallets||S.player.wallets||{};
  if(data.balance!=null) S.player.balance=data.balance;
  renderHeader();
  const list=data.currencies||[];
  const opts=list.map(c=>`<option value="${esc(c.code)}">${esc(c.name)} · ${esc(c.code)}</option>`).join("");
  const update=()=>{
    const from=$("fxFrom").value, to=$("fxTo").value;
    const amount=Number($("fxAmount").value)||0;
    const rate=(data.rates[to]||0)/(data.rates[from]||1);
    const output=amount*rate;
    $("fxQuote").textContent=amount>0?`${amount.toLocaleString("ru-RU",{maximumFractionDigits:2})} ${from} ≈ ${output.toLocaleString("ru-RU",{maximumFractionDigits:2})} ${to}`:"Введите сумму для расчёта";
    $("fxRate").textContent=`1 ${from} = ${rate.toLocaleString("ru-RU",{maximumFractionDigits:6})} ${to}`;
    $("fxSubmit").disabled=!(amount>0&&from!==to);
  };
  body.innerHTML=`<div class="fx-intro">Курсы валют обновляются автоматически. Обмен без комиссии.</div>
    <div class="fx-wallets">${list.map(c=>`<div class="fx-wallet"><span class="fx-symbol">${esc(c.symbol)}</span><span class="fx-name">${esc(c.name)}<small>${esc(c.code)}</small></span><b>${Number(data.wallets[c.code]||0).toLocaleString("ru-RU",{minimumFractionDigits:2,maximumFractionDigits:2})}</b></div>`).join("")}</div>
    <div class="fx-card"><div class="shead">Конвертация</div>
      <div class="fx-pair"><label>От<select id="fxFrom">${opts}</select></label><button type="button" id="fxSwap" class="fx-swap" aria-label="Поменять валюты">⇄</button><label>В<select id="fxTo">${opts}</select></label></div>
      <label class="fx-amount-label">Сумма<input id="fxAmount" type="number" min="0.01" step="0.01" value="1000" inputmode="decimal"></label>
      <div id="fxQuote" class="fx-quote"></div><div id="fxRate" class="fx-rate"></div>
      <button class="btn good" id="fxSubmit" style="width:100%;padding:14px;margin-top:14px">Обменять</button>
    </div>
    <div class="fx-foot">Источник: ${esc(data.source)} · данные на ${new Date(data.updated_at*1000).toLocaleString("ru-RU")}${data.stale?" · сохранённый курс":""}</div>`;
  $("fxFrom").value="RUB"; $("fxTo").value="USD";
  $("fxAmount").oninput=update; $("fxFrom").onchange=update; $("fxTo").onchange=update;
  $("fxSwap").onclick=()=>{ const a=$("fxFrom").value; $("fxFrom").value=$("fxTo").value; $("fxTo").value=a; update(); };
  $("fxSubmit").onclick=async()=>{
    const button=$("fxSubmit"); button.disabled=true; button.textContent="Обмениваем…";
    try{
      const r=await api("/api/currencies/convert","POST",{from:$("fxFrom").value,to:$("fxTo").value,amount:Number($("fxAmount").value)});
      S.player.balance=r.balance; renderHeader(); toast(`Обменено: ${r.debited} ${r.from} → ${r.credited} ${r.to}`); beep(700,.12);
      RENDER.currencies(body);
    }catch(e){
      toast(e.data&&e.data.error==="no_funds"?"Недостаточно средств в выбранной валюте":"Обмен не выполнен: "+(e.data&&e.data.error||e.message));
      button.disabled=false; button.textContent="Обменять";
    }
  };
  update();
};

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
  try{ const item=S.inv.find(n=>n.id===id); const r=await api("/api/sell","POST",{number_id:id});
    S.player.balance=r.balance; S.inv=await refreshInv(); renderHeader();
    const received=r.earned_native!=null?r.earned_native:(item?item.p:r.earned);
    toast(`💰 +${nativePrice(received,item?item.k:"RU")}`); beep(700,.1);
    RENDER.inv($("panelBody"),$("panelSearch"));
  }catch(e){ toast("Ошибка продажи"); }
}
async function massSell(mode){
  const label=mode==="common"?"обычные и необычные":mode==="worst"?"кроме 5 лучших":"все номера";
  if(!await askConfirm(`Продать ${label}? Это действие нельзя отменить.`, "Массовая продажа")) return;
  try{ const r=await api("/api/mass_sell","POST",{mode});
    S.player.balance=r.balance; S.inv=await refreshInv(); renderHeader();
    toast(`💰 Продано ${r.count} · +${dotSep(r.earned_rub??(r.earned*100))}₽ экв.`); confetti();
    RENDER.inv($("panelBody"),$("panelSearch"));
  }catch(e){ toast(e.data&&e.data.error==="nothing"?"Нечего продавать":"Ошибка"); }
}

/* --- Апгрейд (раздел из меню) --- */
RENDER.upg=(body,search)=>{
  $("panelTitle").textContent="⬆️ Апгрейд";
  const draw=(q)=>{
    const list=S.inv.filter(n=>n.r!=="sec"&&(!q||n.num.includes(q)));
    if(!list.length){ body.innerHTML=emptyBlock("⬆️","Нет номеров для улучшения.<br>Секретные уже максимум!"); return; }
    body.innerHTML=`<div class="field"><div class="hint">Выбери номер, задай процентную ставку и крути колесо. Попал в зелёный сектор — номер улучшается, в красный — сгорает!</div></div>
      <div class="ngrid">${list.map(n=>numCard(n,
        `<button class="btn sm" data-up="${n.id}" style="flex:1">⬆️ Улучшить</button>`
      )).join("")}</div>`;
    body.querySelectorAll("[data-up]").forEach(b=>b.onclick=()=>openUpgrade(b.dataset.up));
  };
  search.oninput=e=>draw(e.target.value.toLowerCase().trim());
  search.classList.remove("hide");
  draw("");
};

/* --- Колесо апгрейда --- */
function openUpgrade(id){
  const n=S.inv.find(x=>x.id===id); if(!n) return;
  if(n.r==="sec"){ toast("🌌 Секретный номер — максимум"); return; }
  const rar=R_BY_ID[n.r];
  const wmin=(S.limits&&S.limits.wheel_min)||5;
  const wmax=(S.limits&&S.limits.wheel_max)||90;
  const stepsFor=ch=>{
    const bands=(S.limits&&S.limits.wheel_steps)||[
      {max_chance:10,steps:4},{max_chance:25,steps:3},
      {max_chance:45,steps:2},{max_chance:90,steps:1}
    ];
    return (bands.find(b=>ch<=b.max_chance)||bands[bands.length-1]).steps;
  };
  let chance=45, spinning=false;

  const presets=[10,25,45,70,90].filter(x=>x>=wmin&&x<=wmax);
  openSheet(`<div class="sheetTitle">🎡 Колесо апгрейда</div>
    <div class="ncard r${R_IDX(n.r)} upNumCard">
      <div class="top"><span class="flag">${FLAG(n.k)}</span><span class="rchip" style="background:${rar.c}">${esc(rar.n)}</span></div>
      <div class="num">${esc(n.num)}</div>
      <div class="price">${priceOf(n.p,n.k)}</div>
    </div>

    <div class="upWheelWrap">
      <div class="upWheel" id="upWheel"></div>
      <div class="upRim"></div>
      <div class="upArrow"></div>
      <div class="upHub">⬆</div>
      <div class="upSector win" id="upWinLbl"></div>
      <div class="upSector lose" id="upLoseLbl"></div>
    </div>

    <div class="upStatRow">
      <div><span>Сектор победы</span><b id="upChanceVal">45%</b></div>
      <div><span>При успехе</span><b id="upStepsVal">+2 кат.</b></div>
      <div><span>Стоимость</span><b>Бесплатно</b></div>
    </div>

    <div class="field"><label>Процентная ставка: <span id="upSliderVal">45%</span></label>
      <input type="range" id="upChance" min="${wmin}" max="${wmax}" step="5" value="45">
      <div class="seg upPresets" id="upPresets">${presets.map(v=>`<button data-v="${v}" class="${v===45?'on':''}">${v}%</button>`).join("")}</div>
      <div class="hint">Зелёный сектор = улучшение. Красный = номер сгорает.<br>
        Меньше сектор → больше категорий при успехе, но выше риск.</div>
    </div>
    <button class="btn" id="upGo" style="width:100%;padding:15px;font-size:15px">🎡 КРУТИТЬ КОЛЕСО</button>`);

  const wheel=$("upWheel"), winLbl=$("upWinLbl"), loseLbl=$("upLoseLbl");
  const place=(el,deg)=>{
    const a=(deg-90)*Math.PI/180, r=74;
    el.style.left=`calc(50% + ${Math.cos(a)*r}px)`;
    el.style.top=`calc(50% + ${Math.sin(a)*r}px)`;
  };
  function draw(ch){
    chance=ch;
    const edge=ch/100*360;
    wheel.style.background=`conic-gradient(from 0deg,
      rgba(74,222,128,.92) 0deg ${edge}deg,
      rgba(179,36,58,.92) ${edge}deg 360deg)`;
    winLbl.textContent=`${ch}%`; loseLbl.textContent=`${100-ch}%`;
    place(winLbl,edge/2); place(loseLbl,(edge+360)/2);
    $("upChanceVal").textContent=`${ch}%`;
    $("upStepsVal").textContent=`+${stepsFor(ch)} кат.`;
    $("upSliderVal").textContent=`${ch}%`;
    $("upChance").value=ch;
    $("upPresets").querySelectorAll("button").forEach(b=>b.classList.toggle("on",+b.dataset.v===ch));
    $("upGo").disabled=spinning;
    $("upGo").textContent="🎡 КРУТИТЬ КОЛЕСО";
  }
  draw(45);
  $("upChance").oninput=e=>draw(+e.target.value);
  $("upPresets").querySelectorAll("button").forEach(b=>b.onclick=()=>{ draw(+b.dataset.v); beep(600,.06); });

  $("upGo").onclick=async()=>{
    if(spinning) return;
    spinning=true;
    const btn=$("upGo"); btn.disabled=true; btn.textContent="🎡 Крутим…";
    $("upChance").disabled=true;
    let r;
    try{
      r=await api("/api/upgrade","POST",{number_id:id,chance});
    }catch(e){
      spinning=false; $("upChance").disabled=false; draw(chance);
      if(e.data&&e.data.error==="maintenance") toast("🔧 Идёт техперерыв");
      else toast("Ошибка: "+(e.data&&e.data.error||e.message));
      return;
    }

    // Сервер уже решил исход и прислал угол. Крутим ровно до него.
    const landing=r.wheel.landing;
    const rotation=360*5+((360-landing)%360);
    wheel.style.transition="transform 3.4s cubic-bezier(.13,.72,.16,1)";
    beep(520,.1,"triangle",.05);
    const ticker=setInterval(()=>beep(240+Math.random()*160,.03,"square",.022),110);
    requestAnimationFrame(()=>{ wheel.style.transform=`rotate(${rotation}deg)`; });
    setTimeout(()=>{ clearInterval(ticker); showWheelResult(r,n); },3500);
  };
}

async function showWheelResult(r,n){
  try{ S.inv=await refreshInv(); }catch(e){}
  S.player.balance=r.balance; S.player.donate_balance=r.donate_balance;
  renderHeader();
  if(r.win){
    confetti(); beep(880,.25,"triangle",.09); buzz([30,40,30,40,60]);
    const nr=R_BY_ID[r.item.r];
    openSheet(`<div class="sheetTitle">🎉 Сектор победы!</div>
      <div class="upResultWin">
        <div class="ncard r${R_IDX(r.item.r)}">
          <div class="top"><span class="flag">${FLAG(r.item.k)}</span><span class="rchip" style="background:${nr.c}">${esc(nr.n)}</span></div>
          <div class="num">${esc(r.item.num)}</div>
          <div class="price">${priceOf(r.item.p,r.item.k)}</div>
        </div>
      </div>
      <div class="upResultNote">+${r.steps} категории · шанс был ${r.chance}% · бесплатно</div>
      <button class="btn good" id="upOk" style="width:100%;margin-top:14px;padding:14px">Отлично!</button>`);
  }else{
    beep(180,.35,"sawtooth",.07); buzz([90,60,90]);
    openSheet(`<div class="sheetTitle">💔 Сектор сгорания</div>
      <div class="upResultLose"><div class="ic">🔥</div>
        <div class="big">Номер сгорел</div>
        <div class="sub">${esc(n.num)} потерян безвозвратно</div>
      </div>
      <div class="upResultNote">Выпало на красный сектор · шанс был ${r.chance}%</div>
      <button class="btn ghost" id="upOk" style="width:100%;padding:14px">Жаль…</button>`);
  }
  $("upOk").onclick=()=>{
    closeSheet();
    if($("panel").classList.contains("show")){
      const title=$("panelTitle").textContent;
      if(title.indexOf("Коллекция")>=0) RENDER.inv($("panelBody"),$("panelSearch"));
      else if(title.indexOf("Апгрейд")>=0) RENDER.upg($("panelBody"),$("panelSearch"));
    }
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
              <div class="lprice">${nativePrice(l.price,it.k)}</div>
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
  const def=Math.round(Number(n.p)*100)/100;   // валюта НОМЕРА (так цену принимает API)
  const cur=(C_BY_ID[n.k]||CTRY[0]).cur;
  openSheet(`<div class="sheetTitle">${kind==="swap"?"🔄 Обмен":"💰 Цена"}</div>
    <div class="ncard r${R_IDX(n.r)}" style="margin-bottom:12px">
      <div class="top"><span class="flag">${FLAG(n.k)}</span><span class="rchip" style="background:${R_BY_ID[n.r].c}">${esc(R_BY_ID[n.r].n)}</span></div>
      <div class="num">${esc(n.num)}</div>
      <div class="price">${nativePrice(def,n.k)}</div></div>
    <div class="field"><label>Цена (${cur} — валюта номера)</label>
      <input id="priceIn" type="number" min="1" value="${def}">
      <div class="hint">Цена указана в валюте страны номера. Выручка зачислится в баланс по курсу · комиссия рынка 5%.</div></div>
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
    const paidCountry=CTRY.find(c=>c.code===r.currency);
    toast(`✅ Куплено за ${(Number(r.paid_native)||0).toLocaleString("ru-RU",{maximumFractionDigits:2})}${paidCountry?paidCountry.cur:r.currency||""}`); confetti(); beep(760,.15);
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
      <div class="vl">${nativePrice(x.item.p,x.item.k)}</div></div>`).join("");
  body.innerHTML=`<div class="shead">💰 Богатейшие игроки</div><div class="podium">${moneyRows||'<div class="empty">Пусто</div>'}</div>
    <div class="shead">💎 Лучшие номера</div><div class="podium">${nums||'<div class="empty">Пусто</div>'}</div>`;
};

/* --- Задания --- */
function taskResetText(seconds){
  const sec=Math.max(0,Number(seconds)||0);
  const days=Math.floor(sec/86400), hours=Math.floor((sec%86400)/3600), mins=Math.floor((sec%3600)/60);
  if(days) return `${days} д ${hours} ч`;
  if(hours) return `${hours} ч ${mins} мин`;
  return `${Math.max(1,mins)} мин`;
}
RENDER.tasks=async(body)=>{
  $("panelTitle").textContent="🎯 Задания"; $("panelSearch").classList.add("hide");
  body.innerHTML='<div class="empty"><div class="ic">⏳</div>Загрузка…</div>';
  let d; try{ d=await api("/api/tasks","GET",{}); }catch(e){ body.innerHTML='<div class="empty">Ошибка</div>'; return; }
  const tasks=(d.tasks||[]).map(t=>{
    const done=t.v>=t.n;
    const pct=Math.round((t.v/t.n)*100);
    return `<div class="row"><span class="ic">${done?"✅":"🎯"}</span>
      <span class="mid"><span class="t">${esc(t.t)}</span>
        <span class="s">${t.v}/${t.n} · +${money(t.u)} · ${esc(t.cadence_label||"")}</span>
        <span class="s taskReset">Обновится через ${taskResetText(t.resets_in)}</span>
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
      <div><div class="v">${walletMoney(S.country)}</div><div class="l">баланс · ${esc(C_BY_ID[S.country].name)}</div></div>
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
    <div class="tog" id="calmTog"><div><div class="tl">Спокойный режим</div><div class="ts">Убрать анимацию фона и свечение</div></div>
      <div class="switch ${localStorage.getItem("simroulette_calm")==="1"?"on":""}"><i></i></div></div>
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
  const calm=$("calmTog");
  if(calm) calm.onclick=()=>{
    const sw=calm.querySelector(".switch");
    const on=!sw.classList.contains("on");
    sw.classList.toggle("on",on);
    document.body.classList.toggle("calm",on);
    try{ localStorage.setItem("simroulette_calm",on?"1":"0"); }catch(e){}
  };
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
    <div class="row" style="${mt.started?'border-color:rgba(255,95,109,.5)':''}">
      <span class="ic">${mt.started?"🔴":mt.active?"🟡":"🟢"}</span>
      <span class="mid"><span class="t">${mt.started?"Игра закрыта для игроков":mt.active?"Перерыв запланирован":"Перерыв не активен"}</span>
        <span class="s">${mt.active?esc(mt.message||""):"Игроки играют в обычном режиме"}${
          mt.active&&mt.seconds_left>0?" · старт через "+fmtTime(mt.seconds_left):
          mt.started&&mt.ends_in>0?" · откроется через "+fmtTime(mt.ends_in):""}</span></span>
      ${mt.active?'<span class="end"><button class="btn sm good" id="mtStopQuick">Открыть</button></span>':""}
    </div>
    ${mt.active?`<div class="pbar" style="margin-bottom:12px"><i style="width:${mt.started?(mt.progress||8):Math.max(4,100-(mt.seconds_left||0)/36)}%"></i></div>`:""}
    <div class="field"><label>Сообщение игрокам</label><input id="mtMsg" value="${esc(mt.message||"Плановые технические работы")}"></div>
    <div class="field"><label>Закрыть через (минуты, 0 = сразу)</label><input id="mtMin" type="number" min="0" value="5"></div>
    <div class="field"><label>…или к времени МСК (ЧЧ:ММ)</label><input id="mtAt" placeholder="например 23:30"></div>
    <div class="field"><label>Автооткрытие через (минуты, 0 = вручную)</label><input id="mtDur" type="number" min="0" value="0"></div>
    <div class="field"><label>…или открыть в ЧЧ:ММ МСК</label><input id="mtOpen" placeholder="например 04:00"></div>
    <div style="display:flex;gap:8px;margin-bottom:8px">
      <button class="btn bad" id="mtStart" style="flex:1;padding:13px">Закрыть игру</button>
      <button class="btn good" id="mtStop" style="flex:1;padding:13px">Открыть игру</button>
    </div>
    <div class="field"><div class="hint">🛠 Ты и другие админы продолжите играть во время перерыва в общей базе — для обычных игроков игра будет закрыта.</div></div>
    ${(stats.maintenance_log||[]).length?`<div class="shead">Журнал перерывов</div>${stats.maintenance_log.slice(0,5).map(l=>
      `<div class="row"><span class="ic">${l.action==="start"?"🔧":l.action==="auto_end"?"⏰":"🟢"}</span>
        <span class="mid"><span class="t">${l.action==="start"?"Старт":l.action==="auto_end"?"Автооткрытие":"Открыто"}</span>
        <span class="s">${esc(l.message||"")} · ${new Date(l.ts).toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})}</span></span></div>`).join("")}`:""}

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

    <div class="shead">💸 Выдать баланс игроку</div>
    <div class="field"><label>Telegram username игрока</label>
      <input id="grantUser" type="text" autocomplete="off" placeholder="@username" maxlength="33"></div>
    <div class="grantRow">
      <div class="field"><label>Страна / валюта</label>
        <select id="grantCountry">${CTRY.map(c=>`<option value="${c.k}" ${c.k===S.country?'selected':''}>${c.flag} ${c.name} · ${c.cur}</option>`).join("")}</select>
      </div>
      <div class="field"><label id="grantAmountLabel">Сумма (${(C_BY_ID[S.country]||CTRY[0]).cur})</label>
        <input id="grantAmount" type="number" min="0.01" step="0.01" placeholder="1000"></div>
    </div>
    <div class="hint grantHint">Сумма конвертируется во внутренний баланс по курсу выбранной страны.</div>
    <button class="btn good" id="grantGo" style="width:100%;padding:13px;margin:8px 0 12px">💸 Выдать деньги</button>
    ${(stats.admin_grants||[]).length?`<div class="shead">Последние выдачи</div><div class="grantLog">${stats.admin_grants.slice(0,8).map(g=>
      `<div class="row"><span class="ic">💸</span><span class="mid"><span class="t">@${esc(g.username||g.to_id)}</span>
      <span class="s">${new Date(g.created_at).toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})}</span></span>
      <span class="end grantValue">+${nativePrice(g.amount_native,g.currency)}</span></div>`).join("")}</div>`:""}

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
  const stopMaint=async()=>{
    try{ const r=await api("/api/admin/maintenance","POST",{action:"stop"});
      toast("🟢 Игра открыта"); S.maintenance=r; renderAdminMaintBar(); RENDER.admin(body);
    }catch(e){ toast("Ошибка: "+(e.data&&e.data.error||e.message)); }
  };
  $("mtStart").onclick=async()=>{
    const msg=$("mtMsg").value.trim()||"Плановые технические работы";
    const min=+$("mtMin").value||0;
    const at=$("mtAt").value.trim();
    const dur=+$("mtDur").value||0;
    const openAt=$("mtOpen").value.trim();
    const body2={action:"start",message:msg};
    if(at){ body2.at=at; }
    else{ body2.delay_sec=min*60; }
    if(openAt){ body2.open_at=openAt; }
    else if(dur>0){ body2.duration_min=dur; }
    try{ const r=await api("/api/admin/maintenance","POST",body2);
      toast("🔧 Техперерыв запущен"); S.maintenance=r; renderAdminMaintBar(); RENDER.admin(body);
    }catch(e){ toast("Ошибка: "+(e.data&&e.data.error||e.message)); }
  };
  $("mtStop").onclick=stopMaint;
  if($("mtStopQuick")) $("mtStopQuick").onclick=stopMaint;

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

  // --- выдача денег по Telegram username ---
  $("grantCountry").onchange=()=>{
    const c=C_BY_ID[$("grantCountry").value]||CTRY[0];
    $("grantAmountLabel").textContent=`Сумма (${c.cur})`;
  };
  $("grantGo").onclick=async()=>{
    const username=$("grantUser").value.trim().replace(/^@/,"");
    const amount=+$("grantAmount").value;
    const country=$("grantCountry").value;
    if(!username) return toast("Введите Telegram username");
    if(!(amount>0)) return toast("Введите сумму больше нуля");

    if(!await askConfirm(`Зачислить ${nativePrice(amount,country)} игроку @${username}?`,"Подтвердите выдачу")) return;
    const btn=$("grantGo"); btn.disabled=true; btn.textContent="⏳ Зачисляем…";
    try{
      const r=await api("/api/admin/grant","POST",{username,amount,country});
      toast(`✅ @${r.username}: +${nativePrice(r.amount,r.country)}`);
      beep(760,.16,"triangle",.08); buzz([30,25,45]);
      $("grantUser").value=""; $("grantAmount").value="";
      RENDER.admin(body);
    }catch(e){
      const code=e.data&&e.data.error;
      toast(code==="player_not_found"?"Игрок не найден — он должен хотя бы раз открыть игру":
        code==="bad_username"?"Проверьте Telegram username":"Ошибка: "+(code||e.message));
    }finally{ btn.disabled=false; btn.textContent="💸 Выдать деньги"; }
  };

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

/* ---------------------------------------------------------- ТЕХПЕРЕРЫВ */
let newsShown=false, wasBlocked=false, lastMaintId=null, forcePoll=false;
const maintAlerts=new Set();
function mtBeep(freq,dur,type,vol,pattern){
  if(S.player&&S.player.sound_on) beep(freq,dur,type,vol);
  if(S.player&&S.player.vibration) buzz(pattern);
}
const fmtTime=sec=>{
  sec=Math.max(0,Math.floor(sec||0));
  const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),x=sec%60;
  return (h>0?String(h).padStart(2,"0")+":":"")+String(m).padStart(2,"0")+":"+String(x).padStart(2,"0");
};

function setupMaintenance(){
  pollMaintenance();
  setInterval(pollMaintenance,3000);
  $("newsClose").onclick=()=>{ $("newsPop").classList.remove("show"); newsShown=true; };
  $("maintRefresh").onclick=async()=>{ toast("🔄 Проверяем…"); await pollMaintenance(); };
  $("adminMaintBar").onclick=(e)=>{ if(e.target.id==="ambStop") return; openPanel("admin"); };
  $("ambStop").onclick=async(e)=>{
    e.stopPropagation();
    try{
      const r=await api("/api/admin/maintenance","POST",{action:"stop"});
      S.maintenance=r; toast("🟢 Игра открыта"); renderAdminMaintBar(); await pollMaintenance();
    }catch(err){ toast("Ошибка: "+(err.data&&err.data.error||err.message)); }
  };
}

async function pollMaintenance(){
  let m;
  try{ m=await api("/api/maintenance","GET",{}); }catch(e){ return; }
  if(m.id!==lastMaintId){ newsShown=false; lastMaintId=m.id; maintAlerts.clear(); }
  S.maintenance=m;
  renderAdminMaintBar();

  // Сервер решает, закрыта ли игра именно для этого пользователя.
  if(m.blocked){
    if(!wasBlocked) mtBeep(240,.4,"sawtooth",.08,[120,70,120]);
    showMaintenanceBlock();
    wasBlocked=true;
    $("newsPop").classList.remove("show");
    $("mtimer").classList.add("hide");
    return;
  }
  hideMaintenanceBlock();
  if(wasBlocked){
    wasBlocked=false;
    toast("🎉 Игра снова открыта!");
    mtBeep(780,.18,"triangle",.08,[40,30,40,30,60]);
    confetti();
  }

  // Предупреждение игрокам (админу достаточно баннера).
  if(m.active && m.seconds_left>0 && !newsShown && !m.is_admin){
    $("newsMsg").textContent=(m.message||"Скоро начнутся технические работы.")+
      "\nИграть можно до начала перерыва.";
    $("newsCd").textContent=fmtTime(m.seconds_left);
    $("newsPop").classList.add("show");
    mtBeep(520,.16,"triangle",.07,[60,40,60]);
    newsShown=true;   // не показываем повторно до следующего перерыва
  }
  updateMtTimer();
  renderMaintBlock();
}

function showMaintenanceBlock(){
  ["menu","sheet","panel","newsPop","confirmPop","onboard"].forEach(id=>$(id).classList.remove("show"));
  $("app").style.display="none";
  $("maintBlock").classList.remove("hide");
  renderMaintBlock();
}
function hideMaintenanceBlock(){
  $("maintBlock").classList.add("hide");
  $("app").style.display="";
}

function renderMaintBlock(){
  const m=S.maintenance||{};
  $("maintMsg").textContent=m.message||"Мы обновляем игру. Скоро всё заработает!";
  $("maintLink").href=m.channel_url||"#";
  const t=$("maintTimer"),lbl=$("maintTimerLbl"),pw=$("maintProgressWrap"),
        pb=$("maintProgress"),pl=$("maintProgressLbl");
  if(m.ends_in>0){
    t.textContent=fmtTime(m.ends_in);
    t.classList.toggle("soon",m.ends_in<60);
    lbl.textContent="До окончания работ";
    pw.style.display="";
    pb.style.width=(m.progress||0)+"%";
    pl.textContent=`Готово ≈ ${m.progress||0}% · игра откроется автоматически`;
  }else{
    t.textContent="⏳";
    t.classList.remove("soon");
    lbl.textContent="Ориентировочное время";
    pw.style.display="none";
    pl.textContent="Работы идут — следи за новостями в канале";
  }
  const steps=$("maintSteps").children;
  const p=m.ends_in>0?(m.progress||0):35;
  for(let i=0;i<steps.length;i++){
    steps[i].className="maintStep"+(p>=(i+1)*33?" done":p>=i*33?" now":"");
  }
}

function renderAdminMaintBar(){
  const bar=$("adminMaintBar"),m=S.maintenance||{};
  if(!S.is_admin || !(m.active||m.started)){ bar.classList.add("hide"); return; }
  bar.classList.remove("hide");
  if(m.started){
    $("ambTitle").textContent="🛠 Режим администратора";
    $("ambSub").textContent="Игроки видят заглушку"+(m.ends_in>0?` · откроется через ${fmtTime(m.ends_in)}`:"");
  }else{
    $("ambTitle").textContent="⏳ Перерыв запланирован";
    $("ambSub").textContent=`Игроки увидят заглушку через ${fmtTime(m.seconds_left)} · ${esc(m.message||"")}`;
  }
  $("ambStop").classList.toggle("hide",!m.active);
}

function updateMtTimer(){
  const m=S.maintenance,bar=$("mtimer");
  if(m&&m.active&&!m.started&&m.seconds_left>0&&!m.blocked&&!m.is_admin){
    bar.classList.remove("hide");
    $("mtimerTxt").textContent=`⚠️ Техперерыв через ${fmtTime(m.seconds_left)}`;
    if($("newsPop").classList.contains("show")) $("newsCd").textContent=fmtTime(m.seconds_left);
  }else bar.classList.add("hide");
}

// локальный отсчёт между запросами к серверу
setInterval(()=>{
  const m=S.maintenance;
  if(!m||!m.active) return;
  let changed=false;
  if(!m.started&&m.seconds_left>0){ m.seconds_left--; changed=true; }
  if(m.started&&m.ends_in>0){ m.ends_in--; changed=true; }
  if(!changed) return;
  updateMtTimer(); renderAdminMaintBar();
  if(m.blocked) renderMaintBlock();
  if(!m.blocked && !m.started){
    [60,30,10].forEach(th=>{
      const key=`${m.id}:${th}`;
      if(m.seconds_left<=th && m.seconds_left>0 && !maintAlerts.has(key)){
        maintAlerts.add(key);
        mtBeep(th===10?760:600,th===10?.22:.14,"triangle",.07,
               th===10?[50,40,50,40,80]:[60,40,60]);
        toast(`⚠️ Техперерыв через ${fmtTime(m.seconds_left)}`);
      }
    });
  }
  const deadline=(!m.started&&m.seconds_left<=0)||(m.started&&m.ends_in<=0);
  if(deadline&&!forcePoll){
    forcePoll=true;
    pollMaintenance().finally(()=>{ forcePoll=false; });
  }
},1000);

/* ---------------------------------------------------------- старт */
window.addEventListener("load",boot);
if(TG){ try{ TG.onEvent&&TG.onEvent("viewportChanged",()=>{}); }catch(e){} }
