// AI Clipper — dashboard JavaScript
// Handles: health check, job submission, job list polling, clip approval, archive view

const STATUS_EMOJI = {
  pending: '⏳', downloading: '⬇️', transcribing: '📝',
  analyzing: '🤖', clipping: '✂️', done: '✅', failed: '❌'
};

let currentView = (typeof INITIAL_VIEW !== 'undefined' ? INITIAL_VIEW : 'active');

// ─── Health indicator ─────────────────────────────────���──────────────────────
async function checkHealth() {
  const dot = document.getElementById('health-dot');
  if (!dot) return;
  try {
    const r = await fetch('/health');
    dot.className = 'status-dot ' + (r.ok ? 'ok' : 'err');
  } catch {
    dot.className = 'status-dot err';
  }
}

// ─── Archive helpers ─────────────────────────────────────────────────────────
function isArchived(job) {
  if (job.status === 'failed') return true;
  if (job.status !== 'done') return false;
  if (job.clips.length === 0) return true;
  return job.clips.every(c => c.approval !== 'pending');
}

function setView(view) {
  currentView = view;
  document.getElementById('btn-active')?.classList.toggle('active', view === 'active');
  document.getElementById('btn-archive')?.classList.toggle('active', view === 'archive');
  loadJobs();
}

// ─── Index page: job list ────────────────────────────────────────────────────
async function loadJobs() {
  const list = document.getElementById('jobs-list');
  const badge = document.getElementById('job-count');
  if (!list) return;

  try {
    const r = await fetch('/jobs?limit=200');
    const allJobs = await r.json();

    const jobs = allJobs.filter(j => currentView === 'archive' ? isArchived(j) : !isArchived(j));
    badge.textContent = jobs.length;

    if (jobs.length === 0) {
      const msg = currentView === 'archive'
        ? 'No archived jobs yet.'
        : 'No active jobs — submit a URL above.';
      list.innerHTML = `<div class="loading">${msg}</div>`;
      return;
    }

    list.innerHTML = jobs.map(job => {
      const emoji = STATUS_EMOJI[job.status] || '❓';
      const approved = job.clips.filter(c => c.approval === 'approved').length;
      const rejected = job.clips.filter(c => c.approval === 'rejected').length;
      const clipInfo = job.clips.length
        ? `${job.clips.length} clip${job.clips.length !== 1 ? 's' : ''}${approved ? ` · ${approved} ✅` : ''}${rejected ? ` · ${rejected} ❌` : ''}`
        : '';
      const time = job.created_at.slice(0, 16).replace('T', ' ');
      const finishedTime = (job.status === 'done' || job.status === 'failed')
        ? job.updated_at.slice(0, 16).replace('T', ' ') : null;
      const shortUrl = job.url.replace(/^https?:\/\/(www\.)?/, '').slice(0, 60);
      const nameHtml = job.title
        ? `<span class="job-row-title">${job.title}</span>${job.uploader ? `<span class="job-row-uploader">${job.uploader}</span>` : ''}`
        : `<span class="job-row-url" title="${job.url}">${shortUrl}</span>`;

      let archiveInfo = '';
      if (currentView === 'archive') {
        if (job.clips_deleted) {
          archiveInfo = `<span class="job-row-deleted">Files deleted</span>`;
        } else {
          const deleteDate = new Date(new Date(job.updated_at).getTime() + 7 * 24 * 60 * 60 * 1000);
          const deleteDateStr = deleteDate.toISOString().slice(0, 10);
          archiveInfo = `<span class="job-row-delete-date">Delete after ${deleteDateStr}</span>`;
        }
      }

      return `<a class="job-row" href="/jobs/${job.job_id}/view">
        <span class="job-row-main">${nameHtml}</span>
        <span class="job-row-status status-${job.status}">${emoji} ${job.status}</span>
        ${clipInfo ? `<span class="job-row-clips">${clipInfo}</span>` : ''}
        ${archiveInfo}
        <span class="job-row-time">${finishedTime ? `✅ ${finishedTime}` : time}</span>
      </a>`;
    }).join('');
  } catch (e) {
    list.innerHTML = '<div class="loading" style="color:#ef4444">Failed to load jobs.</div>';
  }
}

// ─── Index page: URL submission ──────────────────────────────────────────────
function setupSubmitForm() {
  const form = document.getElementById('submit-form');
  const msg  = document.getElementById('submit-msg');
  const btn  = document.getElementById('submit-btn');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const url = document.getElementById('url-input').value.trim();
    btn.disabled = true;
    msg.textContent = 'Submitting…';
    msg.className = 'submit-msg';

    try {
      const r = await fetch('/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      });
      const data = await r.json();

      if (r.ok) {
        msg.textContent = `✅ Job submitted — ID: ${data.job_id.slice(0, 8)}…`;
        msg.className = 'submit-msg ok';
        document.getElementById('url-input').value = '';
        setView('active');
      } else {
        msg.textContent = `❌ ${data.detail || 'Submission failed'}`;
        msg.className = 'submit-msg err';
      }
    } catch {
      msg.textContent = '❌ Network error — is the API running?';
      msg.className = 'submit-msg err';
    }
    btn.disabled = false;
  });
}

// ─── Job detail page: status polling ─────────────────────────────────────────
async function pollJobStatus() {
  if (typeof JOB_ID === 'undefined') return;
  if (JOB_STATUS === 'done' || JOB_STATUS === 'failed') return;

  const r = await fetch(`/jobs/${JOB_ID}`);
  const job = await r.json();

  const statusEl = document.getElementById('job-status');
  if (statusEl) {
    statusEl.textContent = `${STATUS_EMOJI[job.status] || ''} ${job.status}`;
    statusEl.className = `job-status status-${job.status}`;
  }

  if (job.status === 'done' || job.status === 'failed') {
    document.getElementById('progress-banner')?.remove();
    window.location.reload(); // reload to show clips
  }
}

// ─── Clip approval ──────────────────────────────────────────��─────────────────
async function approveClip(jobId, index) {
  await updateClipApproval(jobId, index, 'approve');
}
async function rejectClip(jobId, index) {
  await updateClipApproval(jobId, index, 'reject');
}

async function updateClipApproval(jobId, index, action) {
  const card  = document.getElementById(`clip-${index}`);
  const badge = document.getElementById(`badge-${index}`);

  try {
    const r = await fetch(`/jobs/${jobId}/clips/${index}/${action}`, { method: 'POST' });
    if (!r.ok) throw new Error('Failed');
    const data = await r.json();
    const approval = data.approval;

    card.className = `clip-card approval-${approval}`;
    const emojis = { approved: '✅', rejected: '❌', pending: '⏳' };
    badge.textContent = `${emojis[approval] || ''} ${approval}`;

    if (action === 'reject' && data.file_deleted) {
      // Remove video player and action buttons, show deleted message
      const video = card.querySelector('video');
      if (video) video.remove();
      const actions = card.querySelector('.clip-actions');
      if (actions) actions.innerHTML = '<span class="clip-deleted">File deleted</span>';
    }
  } catch {
    alert('Failed to update clip status.');
  }
}

// ─── Boot ─────────────────────────────────────────────────────────────────────
checkHealth();
setupSubmitForm();

if (document.getElementById('jobs-list')) {
  setView(currentView);
  setInterval(loadJobs, 5000); // refresh job list every 5s
}

if (typeof JOB_ID !== 'undefined' && JOB_STATUS !== 'done' && JOB_STATUS !== 'failed') {
  setInterval(pollJobStatus, 3000); // poll job status every 3s while running
}
