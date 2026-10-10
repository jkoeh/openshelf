'use strict';
const $ = id => document.getElementById(id);
const fragment = location.hash.slice(1);
if (fragment) { sessionStorage.setItem('monitor-session', fragment); history.replaceState(null, '', '/'); }
const token = sessionStorage.getItem('monitor-session') || '';
let snapshot, busy = false, closed = false, refreshing = false;
async function api(path, data) {
  const response = await fetch(path, {method: data ? 'POST' : 'GET', headers: {
    'X-Monitor-Token': token, ...(data ? {'Content-Type': 'application/json'} : {})
  }, ...(data ? {body: JSON.stringify(data)} : {}), cache: 'no-store', credentials: 'omit'});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Could not reach the monitor.');
  return result;
}
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function stage(job) {
  if (job.state === 'queued') return [0, 'Waiting in the wings', 'Queued · ready when the consumer is free'];
  if (job.state === 'completed') return [100, 'Ready for the bookshelf', 'Audio published'];
  if (['claimed', 'download', 'parse', 'direction', 'preparing'].includes(job.stage)) return [25, 'Ruffling the pages', 'Preparing the book for narration'];
  return [50, 'Hooting the story to life', 'Creating, aligning, and publishing audio'];
}
function button(text, fn, style = 'secondary') { const node = element('button', text, style); node.disabled = busy; node.addEventListener('click', fn); return node; }
function confirm(title, copy, action) {
  $('confirm-title').textContent = title; $('confirm-copy').textContent = copy;
  $('confirm-yes').textContent = action; $('confirm').returnValue = 'cancel'; $('confirm').showModal();
  return new Promise(resolve => $('confirm').addEventListener('close', () => resolve($('confirm').returnValue === 'ok'), {once:true}));
}
async function mutate(path, data, message) {
  if (busy) return;
  busy = true; if (snapshot) render(); $('notice').textContent = ''; $('error').hidden = true;
  try { await api(path, data); $('notice').textContent = message; }
  catch (error) { $('error').textContent = error.message; $('error').hidden = false; }
  finally { busy = false; if (snapshot) render(); await refresh(false); }
}
async function jobAction(job, action) {
  if (action === 'cancel' && !await confirm('Cancel this audiobook?', `${job.title} will stop at the next consumer heartbeat, usually within 30 seconds. Its daily start is not refunded.`, 'Cancel audiobook')) return;
  if (action === 'retry' && !await confirm('Give this book another go?', `${job.title} will return to the queue. Daily and queue limits still apply.`, 'Retry audiobook')) return;
  await mutate('/api/job', {id:job.id, action}, action === 'cancel' ? 'Cancellation requested. The consumer will stop at its next heartbeat.' : action === 'retry' ? 'Book returned to the queue.' : 'Queue priority updated.');
}
function actions(job) {
  const node = element('div', undefined, 'book-actions');
  if (job.state === 'queued') node.append(button(job.priority ? '↓ Normal priority' : '↑ Move up next', () => jobAction(job, job.priority ? 'normal' : 'high')));
  if (['queued','running'].includes(job.state)) node.append(button('Cancel', () => jobAction(job, 'cancel'), 'secondary cancel'));
  if (['failed','canceled'].includes(job.state)) node.append(button('Retry', () => jobAction(job, 'retry')));
  return node;
}
function render() {
  const {active, recent, consumer} = snapshot;
  $('queued').textContent = active.filter(j => j.state === 'queued').length;
  $('working').textContent = active.filter(j => j.status === 'Working').length;
  $('stuck').textContent = active.filter(j => j.status.startsWith('Stuck')).length;
  $('active-count').textContent = active.length;
  $('consumer-state').textContent = consumer.running ? '● Running' : '○ Offline';
  $('consumer-detail').textContent = consumer.running ? `Process ${consumer.pid} · on this PC` : 'Start to pick up queued books';
  $('consumer-action').textContent = consumer.running ? 'Stop while idle' : 'Start consumer';
  $('consumer-action').disabled = busy || (consumer.running && active.some(j => j.state === 'running'));
  $('consumer-action').title = $('consumer-action').disabled && !busy ? 'Wait for the running book to finish, or cancel it first.' : '';
  $('active').replaceChildren();
  for (const job of active) {
    const working = job.status === 'Working'; const card = element('article', undefined, `book ${working ? 'working' : ''}`);
    const top = element('div', undefined, 'book-top'); const badge = element('span', job.status.startsWith('Stuck') ? 'LEASE EXPIRED' : working ? 'IN PRODUCTION' : job.priority ? 'UP NEXT · HIGH PRIORITY' : 'QUEUED', `badge ${job.status.startsWith('Stuck') ? 'stuck' : ''}`);
    const owl = element('div', '🦉', 'owl'); owl.setAttribute('aria-hidden','true'); top.append(badge, owl);
    const [percent, label, note] = stage(job); const progress = element('div', undefined, 'progress'); progress.append(element('span', undefined, `p${percent}`)); progress.setAttribute('role','progressbar'); progress.setAttribute('aria-valuenow',percent); progress.setAttribute('aria-valuemin','0'); progress.setAttribute('aria-valuemax','100'); progress.setAttribute('aria-label','Audio creation milestone');
    const metadata = element('div', undefined, 'book-meta'); metadata.append(element('span',job.mode === 'standard' ? 'Kokoro · Heart' : 'Chatterbox · Expressive'), element('span',`Attempt ${job.attempts}`), element('span', working ? `Heartbeat ${job.heartbeat_age} ago` : `Added ${job.age} ago`));
    card.append(top, element('h3',job.title), element('p',job.author,'author'), element('div', label, 'stage'), progress, element('div',job.status.startsWith('Stuck') ? 'Lease expired. The consumer can reclaim this job.' : `${note} · ${percent}% milestone`, 'stage-note'), metadata, actions(job));
    $('active').append(card);
  }
  if (!active.length) $('active').append(element('div','All quiet on the workbench. Read a new book to add its audiobook to the queue.','empty'));
  $('recent').replaceChildren();
  for (const job of recent) {
    const row = element('div', undefined, 'history-row'); const title = element('div', undefined, 'history-title'); title.append(element('strong',job.title),element('small',`${job.author} · ${job.mode === 'standard' ? 'Kokoro' : 'Expressive'}${job.error_code ? ' · '+job.error_code : ''}`));
    row.append(element('div',undefined,'book-icon'),title,element('span',job.state.toUpperCase(),`badge ${job.state}`),element('time',`${job.heartbeat_age} ago`),actions(job)); $('recent').append(row);
  }
  if (!recent.length) $('recent').append(element('div','Your finished books will appear here.','empty'));
  $('log').textContent = snapshot.log;
  $('refresh').disabled = busy || refreshing;
}
async function refresh(clearError = true) {
  if (refreshing || closed || busy) return;
  refreshing = true; $('refresh').disabled = true;
  try { snapshot = await api('/api/status'); render(); $('updated').textContent = `Updated ${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}`; if (clearError) $('error').hidden = true; }
  catch(error) { $('error').textContent = error.message; $('error').hidden = false; $('updated').textContent = 'Connection needs attention'; }
  finally { refreshing = false; $('refresh').disabled = busy; }
}
$('refresh').addEventListener('click', () => refresh());
$('consumer-action').addEventListener('click', async () => {
  const running = snapshot?.consumer.running;
  if (running && !await confirm('Pause the workshop?', 'The idle consumer will stop picking up books. You can start it again here whenever you’re ready.', 'Stop consumer')) return;
  await mutate(`/api/consumer/${running ? 'stop' : 'start'}`, {}, running ? 'Consumer stopped.' : 'Consumer started. It will pick up the next queued book.');
});
$('close').addEventListener('click', async () => {
  if (!await confirm('Close this monitor?', 'The local dashboard server will shut down. Your audiobook consumer will keep running.', 'Close monitor')) return;
  try { await api('/api/close', {}); closed = true; $('notice').textContent = 'Monitor closed. Your consumer keeps running. You can close this tab.'; document.querySelectorAll('button').forEach(b => b.disabled = true); }
  catch(error) { $('error').textContent = error.message; $('error').hidden = false; }
});
refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
