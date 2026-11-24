import {heading, panel} from '../components/common/heading.js';
import {entries, records} from '../components/common/records.js';
import {archiveLabels} from '../constants/workflow.js';
import {topic} from '../constants/project.js';
import {date, esc} from '../utils/dom.js';
import {go} from '../router/hash.js';

const abadge = state=>`<span class="status" data-state="${esc(state)}">${esc(archiveLabels[state]||state)}</span>`;

function archivePanel(selected, job, archived, operator) {
  const progress = job ? `已核对 ${job.verified_count}/${job.segment_count} 段` : '尚未发起迁存';
  const segments = job?.segments?.length ? records(['段','水位范围','状态','校验码'], job.segments.map(row=>
    [`#${row.segment_index}`, `${row.first_seq} – ${row.last_seq}`, abadge(row.state), `<code>${esc(row.digest.slice(0,12))}…</code>`])) : '';
  const action = operator ? (archived ? `<button id="rerun" data-id="${selected.id}">重跑预检</button>`
    : `<button id="archive-start" data-id="${selected.id}">${job ? '继续迁存' : '分段迁存'}</button>`) : '';
  return panel('分段迁存', `<p>${abadge(archived ? 'archived' : job?.status || 'original')} ${esc(progress)}</p>
    ${job?.error ? `<p class="error">${esc(job.error)}</p>` : ''}
    ${archived ? '<p class="note">原件已清理，读取来自迁存段；重跑预检的证据校验码与迁前一致。</p>' : ''}
    ${segments}${action ? `<div class="actions">${action}</div>` : ''}`);
}

export function casesPage(state, identity) {
  const {spec, session, cases, runs, archives} = state;
  const selected = cases.find(row=>row.id===identity) || cases[0];
  const operator = session.role==='operator';
  const jobs = new Map((archives || []).map(row=>[row.case_id, row]));
  const rows = cases.map(row=>{
    const job = jobs.get(row.id);
    const store = row.archive_state==='archived' ? 'archived' : job?.status || 'original';
    return [`<a href="#cases/${row.id}" ${row.id===selected?.id?'aria-current="true"':''}>${esc(row.title)}</a>`, abadge(store), esc(row.author), esc(date(row.created_at))];
  });
  const job = selected ? jobs.get(selected.id) : null;
  const archived = selected?.archive_state==='archived';
  const instruction = selected ? `<h2>${esc(selected.title)}</h2><p>${esc(spec.guidance)}</p>` : '';
  const input = selected && selected.payload ? Object.entries(selected.payload).map(([key, value])=>{
    const label = spec.field_labels[key] || key;
    const rows = Array.isArray(value) && value.every(x=>x && typeof x === 'object' && !Array.isArray(x));
    return panel(label, rows ? entries(value.slice(0,10), spec.field_labels) + (value.length>10?`<p class="note">显示前 10 条，共 ${value.length} 条。</p>`:'') : `<pre>${esc(value)}</pre>`);
  }).join('') : '';
  return `${heading(topic.modules[0], spec.summary, operator?'<a class="button" href="#editor">建立档案</a>':'')}
    <div class="spread"><section aria-label="档案目录">${records([spec.entity_labels[0], '迁存', '经办', '建立时间'], rows, '尚未建立档案。经办员可以从演示资料建立第一份。')}</section>
    <aside class="brief">${instruction || `<h2>${esc(spec.customer)}</h2>`}${selected?`<p class="note">该档案记录 ${runs.filter(row=>row.case_id===selected.id).length} 次执行。</p>`:''}${selected&&operator?`<a href="#editor/${selected.id}">复制为新档案</a>`:''}</aside></div>
    ${selected ? archivePanel(selected, job, archived, operator) : ''}
    ${input ? `<div class="inspection">${input}</div>`:''}`;
}

export function bindCases(api, refresh, fail) {
  document.getElementById('archive-start')?.addEventListener('click', async event=>{
    try {
      await api(`/cases/${event.target.dataset.id}/archive`, {method:'POST'});
      await refresh();
    } catch (error) { await refresh(); fail(error); }
  });
  document.getElementById('rerun')?.addEventListener('click', async event=>{
    try {
      const run = await api(`/cases/${event.target.dataset.id}/runs`, {method:'POST'});
      go(`runs/${run.id}`);
    } catch (error) { fail(error); }
  });
}
