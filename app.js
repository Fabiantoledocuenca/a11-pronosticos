const $ = (id) => document.getElementById(id);
const lima = 'America/Lima';
const media = matchMedia('(max-width: 850px)');
const refreshMs = 60_000;
const formatTime = new Intl.DateTimeFormat('es-PE', {timeZone:lima, hour:'2-digit', minute:'2-digit', hour12:false});
const formatDate = new Intl.DateTimeFormat('es-PE', {timeZone:lima, day:'numeric', month:'short', year:'numeric'});
const dateParts = new Intl.DateTimeFormat('en-CA', {timeZone:lima, year:'numeric', month:'2-digit', day:'2-digit'});
const formatPct = new Intl.NumberFormat('es-PE', {style:'percent', maximumFractionDigits:2});
let feed = null;
let selected = null;
let failure = false;
let busy = false;
let signature = '';
let detailOpeningButton = null;

function dayAt(instant) {
  const parts = Object.fromEntries(dateParts.formatToParts(instant).map(({type,value})=>[type,value]));
  return [parts.year,parts.month,parts.day].join('-');
}
const showDate = (iso) => formatDate.format(new Date(iso + 'T12:00:00Z'));
const dateOffset = (instant, date) => (Date.parse(date) - Date.parse(dayAt(instant))) / 86_400_000;
const validText = (text) => typeof text === 'string' && text.trim().length > 0 && text.length < 200;
const probability = (value) => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
const isoDate = (value) => typeof value === 'string' && /^\d{4}-\d\d-\d\d$/.test(value) &&
  Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value;
const validTime = (value) => typeof value === 'string' &&
  /T\d\d:\d\d.*(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const normalize = (s) => s.toLocaleLowerCase('es').normalize('NFD').replace(/\p{Diacritic}/gu, '').trim();
function node(tag, className, label) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (label !== undefined) element.textContent = label;
  return element;
}
function validate(payload) {
  if (payload?.version !== 1 || payload.source !== 'A12 Champion' ||
      !isoDate(payload.date) || !validTime(payload.generatedAt) ||
      ![0,1].includes(dateOffset(new Date(payload.generatedAt),payload.date)) ||
      Date.parse(payload.generatedAt) > Date.now() + 300_000 ||
      !Array.isArray(payload.forecasts) || payload.forecasts.length > 500) {
    throw new Error('Edición incompleta');
  }
  const valid = new Set();
  for (const row of payload.forecasts) {
    if (!row || ![row.id,row.home,row.away,row.league,row.scenario].every(validText) ||
        !probability(row.probability) || row.probability === 0 ||
        !((row.lower === null && row.upper === null) ||
          (probability(row.lower) && probability(row.upper) &&
            row.lower <= row.probability && row.upper >= row.probability)) ||
        (row.kickoff !== null && (!validTime(row.kickoff) ||
          dayAt(new Date(row.kickoff)) !== payload.date ||
          Date.parse(row.kickoff) <= Date.parse(payload.generatedAt))) || valid.has(row.id)) {
      throw new Error('Pronóstico fuera del contrato');
    }
    valid.add(row.id);
  }
  return payload;
}
const upcoming = (row, now) => row.kickoff !== null && Date.parse(row.kickoff) - 300_000 > now;
const order = (a,b) => ((a.kickoff ? Date.parse(a.kickoff) : Infinity) - (b.kickoff ? Date.parse(b.kickoff) : Infinity)) ||
  a.league.localeCompare(b.league, 'es') || a.home.localeCompare(b.home, 'es');

function updateEdition() {
  const daysAhead = feed ? dateOffset(new Date(),feed.date) : -1;
  const live = feed && !failure && [0,1].includes(daysAhead) && feed.forecasts.some(row=>upcoming(row, Date.now()));
  $('signal').classList.toggle('current', Boolean(live));
  $('edition-label').textContent = feed ? live ? daysAhead === 0 ? 'Edición de hoy' : 'Próxima jornada' : 'Última edición' : failure ? 'Sin conexión al feed' : 'Cargando edición';
  const message = !feed ? failure ? 'No se pudo cargar la edición del motor. Vuelve a intentarlo.' : 'Consultando la última edición del motor…'
    : failure ? 'No se pudo actualizar. Se muestra la última edición consultada.'
    : feed.forecasts.length === 0 ? 'La edición no contiene pronósticos disponibles.'
    : live ? 'Pronósticos calculados antes del inicio. Puedes filtrar los partidos por iniciar.'
    : 'Se muestra la última edición publicada. No hay partidos por iniciar con horario confirmado en esta edición.';
  if ($('feed-status').textContent !== message) $('feed-status').textContent = message;
  if (!feed) return;
  $('stats-date').textContent = showDate(feed.date);
  $('stats-count').textContent = feed.forecasts.length;
  $('stats-leagues').textContent = new Set(feed.forecasts.map(p=>p.league)).size;
  const generated = new Date(feed.generatedAt);
  $('stats-time').textContent = (dayAt(generated) === feed.date ? '' :
    new Intl.DateTimeFormat('es-PE',{timeZone:lima,day:'numeric',month:'short'}).format(generated) + ' · ') +
    formatTime.format(generated);
  $('filters').hidden = false;
  $('results').hidden = false;
}
function timing(row) {
  if (!row.kickoff) return 'Horario no confirmado';
  return (upcoming(row, Date.now()) ? 'Por iniciar · ' : 'Hora programada · ') + formatTime.format(new Date(row.kickoff));
}
function detail(target, row) {
  target.replaceChildren();
  if (!row) return;
  target.append(node('p','kicker',row.league), node('h3','',row.home + ' vs ' + row.away),
    node('p','date-line',showDate(feed.date) + ' · ' + timing(row)), node('p','scenario-label',row.scenario),
    node('p','big',formatPct.format(row.probability)));
  const generated = new Date(feed.generatedAt);
  const source = 'A12 Champion · Calculado el ' + showDate(dayAt(generated)) + ' a las ' + formatTime.format(generated) + ' (Lima).';
  if (row.lower === null) {
    target.append(node('p','rangemeta','Intervalo no disponible'), node('p','detail-foot',source));
    return;
  }
  const range = node('div', 'range');
  range.setAttribute('aria-hidden','true');
  const interval = node('span');
  interval.style.left = (row.lower * 100) + '%';
  interval.style.width = ((row.upper - row.lower) * 100) + '%';
  const point = node('i');
  point.style.left = (row.probability * 100) + '%';
  range.append(interval,point);
  const endpoints = node('div','rangemeta');
  endpoints.append(node('span', '', formatPct.format(row.lower)), node('span','', formatPct.format(row.upper)));
  const lower = formatPct.format(row.lower);
  const upper = formatPct.format(row.upper);
  target.append(node('p','rangemeta','Intervalo de incertidumbre del modelo'), range, endpoints,
    node('p','detail-foot',source + ' La amplitud del rango (' + lower + ' a ' + upper +
      ') indica cuán incierta es la estimación.'));
}
function options() {
  const prior = $('league').value;
  const values = [...new Set(feed.forecasts.map(p=>p.league))].sort((a,b)=>a.localeCompare(b,'es'));
  $('league').replaceChildren(new Option('Todas las competiciones',''), ...values.map(x=>new Option(x,x)));
  $('league').value = values.includes(prior) ? prior : '';
}
function renderMatches(force = false) {
  if (!feed) return;
  const now = Date.now();
  const q = normalize($('query').value);
  const league = $('league').value;
  const scope = $('scope').value;
  const rows = feed.forecasts.filter(row=> (!league || league === row.league) &&
    (!q || normalize(row.home + ' ' + row.away + ' ' + row.league).includes(q)) &&
    (scope !== 'upcoming' || upcoming(row, now))).sort(order);
  const nextSignature = JSON.stringify([feed.generatedAt,rows.map(row=>[row,upcoming(row,now)]),scope]);
  if (!force && signature === nextSignature) return;
  signature = nextSignature;
  const focusId = document.activeElement?.dataset.matchId;
  if (!rows.some(row=>row.id === selected)) selected = rows[0]?.id ?? null;
  $('result-count').textContent = rows.length === 1 ? '1 partido' : rows.length + ' partidos';
  $('empty').hidden = rows.length !== 0;
  const entries = rows.map(row=>{
    const entry = node('li');
    const button = node('button','match');
    button.type = 'button';
    button.dataset.matchId = row.id;
    if (!media.matches) button.setAttribute('aria-pressed',String(row.id === selected));
    else button.setAttribute('aria-haspopup','dialog');
    const main = node('span','match-main');
    main.append(node('small','',row.league + ' · ' + timing(row)), node('strong','',row.home + ' vs ' + row.away),
      node('span','scenario',row.scenario));
    const score = node('span','match-num');
    score.append(node('b','',formatPct.format(row.probability)),node('small','','Probabilidad'));
    button.append(main,score);
    button.addEventListener('click', ()=>{
      selected = row.id;
      if (media.matches) {
        detailOpeningButton = button;
        detail($('mobile-detail'),row);
        $('dialog').showModal();
      } else {
        for (const b of $('matches').querySelectorAll('button')) b.setAttribute('aria-pressed',String(b === button));
        detail($('desktop-detail'),row);
      }
    });
    entry.append(button);
    return entry;
  });
  $('matches').replaceChildren(...entries);
  if (focusId) ($('matches').querySelectorAll('button').length ?
    [...$('matches').querySelectorAll('button')].find(b=>b.dataset.matchId === focusId) ?? $('query') : $('query')).focus();
  detail($('desktop-detail'),rows.find(row=>row.id === selected));
}
async function refresh() {
  if (busy) return;
  busy = true;
  $('reload').setAttribute('aria-busy','true');
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(),12_000);
  try {
    const url = new URL('./feed.json', location.href);
    url.searchParams.set('t', String(Date.now()));
    const response = await fetch(url, {cache:'no-store', signal:controller.signal});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const incoming = validate(await response.json());
    if (feed && Date.parse(incoming.generatedAt) < Date.parse(feed.generatedAt)) throw new Error('Edición anterior');
    if (!feed || feed.generatedAt !== incoming.generatedAt) {
      feed = incoming;
      options();
      if ($('dialog').open) {
        const current = feed.forecasts.find(row=>row.id === selected);
        if (current) detail($('mobile-detail'),current);
        else $('dialog').close();
      }
    }
    failure = false;
  } catch {
    failure = true;
  } finally {
    clearTimeout(timer);
    busy = false;
    $('reload').removeAttribute('aria-busy');
    updateEdition();
    renderMatches();
  }
}
function theme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  $('theme-label').textContent = dark ? 'Modo claro' : 'Modo oscuro';
  $('theme').setAttribute('aria-pressed',String(dark));
  $('theme').firstElementChild.textContent = dark ? '☼' : '☾';
}
let savedTheme;
try {savedTheme = localStorage.getItem('sentinel-theme')} catch {}
theme(savedTheme ? savedTheme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches);
$('theme').addEventListener('click',()=>{
  const next = document.documentElement.dataset.theme !== 'dark';
  theme(next);
  try {localStorage.setItem('sentinel-theme',next ? 'dark' : 'light')} catch {}
});
$('reload').addEventListener('click',refresh);
$('query').addEventListener('input',()=>renderMatches(true));
$('league').addEventListener('change',()=>renderMatches(true));
$('scope').addEventListener('change',()=>renderMatches(true));
$('dialog').addEventListener('close',()=> {
  if (detailOpeningButton?.isConnected) detailOpeningButton.focus();
});
media.addEventListener('change',()=>{
  if ($('dialog').open && !media.matches) $('dialog').close();
  renderMatches(true);
});
setInterval(()=>{
  if (!document.hidden) refresh();
},refreshMs);
document.addEventListener('visibilitychange',()=>{if (!document.hidden) refresh()});
refresh();
