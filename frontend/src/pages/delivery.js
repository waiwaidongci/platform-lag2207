import {feedback, field} from '../components/common/fields.js';
import {metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {ARCHIVE, REVIEW} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {bytes, date} from '../utils/format.js';
import {runPage} from './run.js';

const ACTIVE = ['building','verifying','releasing','restoring','reclaiming'];
const settled = data => !data.items.some(item => ACTIVE.includes(item.state));

function previewSection(item, spec, message) {
  const box = el('div');
  const render = data => {
    const rows = table(spec.fields.map(entry=>({key:entry.key,label:entry.label})), data.rows,
      {caption:'复原的原批读数', empty:'归档内没有读数。'});
    const gaps = table([
      {key:'channel',label:'采集通道'}, {key:'start',label:'首个缺测时刻'},
      {key:'end',label:'末个缺测时刻'}, {key:'missing_slots',label:'缺测格数'},
    ], data.missing, {caption:'缺测位置', empty:'观测窗口内两路数据完整。'});
    const again = button('重新核对', {type:'submit', class:'quiet'});
    const form = el('form', {class:'fields', onSubmit:handler(again, message, async event => {
      const seconds = Number(new FormData(event.target).get('sample_seconds'));
      render(await workspace.archivePreview(item.archive.id, seconds));
    })}, field({key:'sample_seconds', label:'采样间隔（秒）', type:'int', min:1, max:3600}, data.sample_seconds), again);
    box.replaceChildren(panel(`复原核对 · ${data.name}`,
      el('div', {class:'actions'},
        pill(data.consistent ? '复原与原批指纹一致' : '复原与原批指纹不一致', data.consistent ? 'complete' : 'failed'),
        p(`共 ${data.total} 行，显示前 ${data.rows.length} 行`, 'subtle')),
      rows, el('div', {}, h(3, '缺测位置'), gaps), form));
  };
  return {box, load: seconds => workspace.archivePreview(item.archive.id, seconds).then(render)};
}

function archiveActions(item, message, refresh, previewBox) {
  const wrap = el('div', {class:'actions'});
  const state = item.state;
  const watch = async task => {
    await task();
    await poll(()=>workspace.archives(), settled, ()=>true);
    await refresh();
  };
  if (item.archive?.has_mapping) {
    const show = button('复原核对', {class:'quiet'});
    show.addEventListener('click', handler(show, message, async () => {
      const view = previewSection(item, workspace.spec, message);
      previewBox.replaceChildren(view.box);
      await view.load(10);
    }));
    wrap.append(show);
  }
  if (!session.canWrite) return wrap;
  if (!state || state === 'retired') {
    const archive = button('归档', {class:'quiet'});
    archive.addEventListener('click', handler(archive, message, () => watch(()=>workspace.archiveDataset(item.dataset_id))));
    wrap.append(archive);
  } else if (state === 'archived') {
    const restore = button('解档', {class:'quiet'});
    restore.addEventListener('click', handler(restore, message, () => watch(()=>workspace.restoreArchive(item.archive.id))));
    wrap.append(restore);
  } else if (state === 'failed') {
    const retry = button('重试', {class:'quiet'});
    retry.addEventListener('click', handler(retry, message, () => watch(()=>workspace.retryArchive(item.archive.id))));
    wrap.append(retry);
    if (item.archive.resume_state !== 'restoring') {
      const abandon = button('放弃', {class:'quiet'});
      abandon.addEventListener('click', handler(abandon, message, () => watch(()=>workspace.abandonArchive(item.archive.id))));
      wrap.append(abandon);
    }
  } else {
    wrap.append(p('后台处理中…', 'subtle'));
  }
  return wrap;
}

async function archivePanel(refresh) {
  const info = await workspace.archives();
  const message = feedback();
  const previewBox = el('div');
  const summary = metrics([
    {label:'净省空间', value:bytes(info.summary.net_saved), detail:'已释放原批减去归档库占用'},
    {label:'已释放原批读数', value:bytes(info.summary.released_bytes), detail:`${info.summary.archived_count} 批已归档`},
    {label:'归档库占用', value:bytes(info.summary.store_bytes),
      detail:`规范读数 ${bytes(info.summary.readings_bytes)} · 行序映射 ${bytes(info.summary.map_bytes)}`},
    {label:'处理中', value:info.summary.active_count, detail:`${info.summary.retired_count} 批已复原`},
  ]);
  const items = table([
    {label:'批次', render:item=>link(item.name, `#inputs/${item.dataset_id}`)},
    {label:'状态', render:item=>{
      if (!item.state) return '未归档';
      const state = pill(ARCHIVE[item.state], item.state);
      return item.state === 'failed' && item.archive?.error
        ? el('div', {}, state, p(item.archive.error, 'subtle')) : state;
    }},
    {key:'row_count', label:'行数'},
    {label:'原始占用', render:item=>item.archive ? bytes(item.archive.original_bytes) : '—'},
    {label:'去重读数', render:item=>item.archive?.unique_count ? `${item.archive.unique_count}（新增 ${item.archive.created_count}）` : '—'},
    {label:'已回收', render:item=>item.archive?.reclaimed_bytes ? bytes(item.archive.reclaimed_bytes) : '—'},
    {label:'更新', render:item=>date(item.archive?.updated_at)},
    {label:'操作', render:item=>archiveActions(item, message, refresh, previewBox)},
  ], info.items, {caption:'批次归档', empty:'还没有数据批次。'});
  return panel('批次归档与空间',
    p('同站台同风机同一时刻的相同读数只存一份，冲突值与原行序照留；每批复原核对一致后才释放原读数。', 'subtle'),
    summary, items, message, previewBox);
}

export async function deliveryPage(route, refresh) {
  const {spec, runs} = workspace;
  const message = feedback();
  const artifacts = table([
    {label:'报告', render:row=>link(row.name,`#results/${row.id}`)},
    {label:'复核', render:row=>pill(REVIEW[row.review],row.review)},
    {label:'计算时间', render:row=>date(row.finished_at)},
    {label:'下载', render:row=>{
      const download = button('下载 CSV',{class:'quiet'});
      download.addEventListener('click', handler(download,message,() =>
        workspace.download(`/runs/${row.id}/export`,`${spec.short_name}-${row.id}.csv`)));
      return download;
    }},
  ], runs.filter(row=>row.status==='complete'), {caption:'可导出的结果', empty:'完成计算后会生成对应的报告。'});
  const delivery = spec.scheduled ? panel('站内分发回执', table([
    {key:'recipient',label:'收件团队'},
    {label:'回执',render:row=>pill(row.state==='delivered'?'已投递':'已拦截',row.state)},
    {key:'detail',label:'说明'}, {label:'时间',render:row=>date(row.created_at)},
    {label:'查看',render:row=>link(`运行 ${row.run_id}`,`#results/${row.run_id}`)},
  ],await workspace.deliveries(),{caption:'站内收件箱',empty:'计划到时执行，回执将在此更新。'})) : null;
  return el('div',{}, panel(spec.pages[5], p('按运行批次下载 CSV，复核状态随报告保留。','subtle'), artifacts, message),
    await archivePanel(refresh), delivery, spec.scheduled && await runPage({...route,scheduling:true},refresh));
}
