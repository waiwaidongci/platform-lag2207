import {request, saveFile} from '../api/http.js';
import {session} from './session.js';

const call = (path, body) => request(path, {token:session.token, body});
export const workspace = {
  spec: null,
  datasets: [],
  runs: [],
  async init() { this.spec = await request('/spec'); },
  async refresh() { [this.datasets, this.runs] = await Promise.all([call('/datasets'), call('/runs')]); },
  getDataset: (id, offset = 0) => call(`/datasets/${id}?offset=${offset}`),
  getRun: id => call(`/runs/${id}`),
  upload: data => call('/datasets', data),
  start: data => call('/runs', data),
  review: (id, data) => call(`/runs/${id}/review`, data),
  audit: () => call('/audit'),
  schedules: () => call('/schedules'),
  deliveries: () => call('/deliveries'),
  schedule: data => call('/schedules', data),
  disable: id => call(`/schedules/${id}/disable`, {}),
  archives: () => call('/archives'),
  archiveDataset: id => call(`/datasets/${id}/archive`, {}),
  restoreArchive: id => call(`/archives/${id}/restore`, {}),
  retryArchive: id => call(`/archives/${id}/retry`, {}),
  abandonArchive: id => call(`/archives/${id}/abandon`, {}),
  archivePreview: (id, seconds) => call(`/archives/${id}/preview?sample_seconds=${seconds}`),
  download: (path, filename) => saveFile(path, session.token, filename),
};
