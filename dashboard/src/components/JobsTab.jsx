import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Loader2, Activity, ChevronDown, ChevronRight, FolderOpen, Sparkles, Film } from 'lucide-react';
import { apiJson } from '../lib/api';

// Every job the server is holding, not just the one this browser started.
//
// The rest of the dashboard follows a single job id kept in React state, so a
// job submitted through the API, the MCP server or an agent was invisible here:
// the UI kept showing the last job it happened to know about — including its
// stale error — while a different job ran to completion on the server. This tab
// reads /api/jobs, which merges the clip pipeline and the AI Shorts pipeline.

// Live jobs move; finished ones don't. One interval either way keeps the code
// simple, and 5s is below the pipeline's own log cadence so nothing looks stuck.
const POLL_MS = 5000;

const ACTIVE = ['queued', 'processing'];

const STATUS_STYLE = {
  queued: 'text-muted border-rule',
  processing: 'text-brass border-brass',
  completed: 'text-ink border-rule',
  complete: 'text-ink border-rule',
  failed: 'text-danger border-danger',
  error: 'text-danger border-danger',
};

// "3 min" / "2 h 14" — an age, not a clock time. What matters when reading the
// list is how long a job has been going, and a wall-clock start is one more
// thing to convert in your head.
function age(startedAt) {
  if (!startedAt) return '';
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min`;
  const hrs = Math.floor(mins / 60);
  return `${hrs} h ${String(mins % 60).padStart(2, '0')}`;
}

function JobRow({ job, onOpen }) {
  const [open, setOpen] = useState(false);
  const [logs, setLogs] = useState(null);
  const isActive = ACTIVE.includes(job.status);
  const isDone = job.status === 'completed' || job.status === 'complete';

  // Full logs are only fetched for the row you expand: /api/status returns the
  // whole log array per job, and doing that for every row on every poll would
  // grow with the job list for something nobody is reading.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const path = job.kind === 'shorts'
      ? `/api/saasshorts/status/${job.job_id}`
      : `/api/status/${job.job_id}`;
    const load = () => apiJson(path)
      .then((d) => { if (!cancelled) setLogs(d.logs || []); })
      .catch(() => { if (!cancelled) setLogs(['Could not load this job’s logs.']); });
    load();
    if (!isActive) return () => { cancelled = true; };
    const t = setInterval(load, POLL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [open, job.job_id, job.kind, isActive]);

  const Icon = job.kind === 'shorts' ? Sparkles : Film;

  return (
    <div className="card">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left p-4 flex items-start gap-3"
      >
        {open ? <ChevronDown size={16} className="mt-1 shrink-0 text-muted" />
              : <ChevronRight size={16} className="mt-1 shrink-0 text-muted" />}

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className={`text-micro font-mono uppercase px-2 py-0.5 border rounded ${STATUS_STYLE[job.status] || 'text-muted border-rule'}`}>
              {isActive && <Loader2 size={10} className="inline animate-spin mr-1" />}
              {job.status}
            </span>
            <Icon size={13} className="text-muted shrink-0" />
            <span className="readout">{job.kind === 'shorts' ? 'ai shorts' : 'clips'}</span>
            {job.clips > 0 && (
              <span className="readout">· {job.clips} clip{job.clips === 1 ? '' : 's'}</span>
            )}
            {job.started_at > 0 && <span className="readout">· {age(job.started_at)}</span>}
          </div>

          <p className="text-sm text-ink font-medium truncate" title={job.label || job.job_id}>
            {job.label || job.job_id}
          </p>
          {job.stage && (
            <p className="text-xs text-muted truncate mt-0.5" title={job.stage}>{job.stage}</p>
          )}
        </div>

        {isDone && job.kind === 'clip' && job.clips > 0 && onOpen && (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => { e.stopPropagation(); onOpen(job.job_id); }}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); onOpen(job.job_id); } }}
            className="btn-ghost px-3 py-2 text-xs shrink-0 flex items-center gap-1"
            title="Load this job's clips in the Clip Generator"
          >
            <FolderOpen size={14} /> open
          </span>
        )}
      </button>

      {open && (
        <div className="px-4 pb-4">
          <p className="readout mb-2">{job.job_id}</p>
          <pre className="text-xs font-mono text-muted bg-black/30 border border-rule rounded p-3 max-h-64 overflow-auto whitespace-pre-wrap">
            {logs === null ? 'loading…' : (logs.length ? logs.join('\n') : 'no logs')}
          </pre>
        </div>
      )}
    </div>
  );
}

export default function JobsTab({ onOpenJob }) {
  const [jobs, setJobs] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    apiJson('/api/jobs')
      .then((d) => { setJobs(d.jobs || []); setError(''); })
      // Keep whatever is on screen: a single failed poll during a redeploy
      // shouldn't blank a list the user is reading.
      .catch(() => setError('Could not reach the server.'));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const [active, finished] = useMemo(() => {
    const rows = jobs || [];
    return [
      rows.filter((j) => ACTIVE.includes(j.status)),
      rows.filter((j) => !ACTIVE.includes(j.status)),
    ];
  }, [jobs]);

  if (jobs === null && !error) {
    return <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>;
  }

  return (
    <div className="h-full overflow-y-auto p-8 max-w-4xl mx-auto animate-fade">
      <p className="eyebrow mb-1.5">06 · JOBS</p>
      <h1 className="font-display lowercase text-2xl text-ink mb-2">Running jobs</h1>
      <p className="text-muted text-sm mb-8 lowercase">
        Everything the server is working on, whoever started it — this dashboard, the API, an agent or the MCP server. Click a job to read its logs.
      </p>

      {error && <p className="text-danger text-sm mb-4">{error}</p>}

      {jobs && jobs.length === 0 && (
        <div className="text-center py-20 text-muted">
          <Activity size={40} className="mx-auto mb-4 text-muted" />
          <p className="lowercase">No jobs yet. Start one from the Clip Generator or AI Shorts.</p>
        </div>
      )}

      {active.length > 0 && (
        <section className="mb-10">
          <p className="eyebrow mb-3">in progress · {active.length}</p>
          <div className="space-y-3">
            {active.map((j) => <JobRow key={j.job_id} job={j} onOpen={onOpenJob} />)}
          </div>
        </section>
      )}

      {finished.length > 0 && (
        <section>
          <p className="eyebrow mb-3">finished · {finished.length}</p>
          <div className="space-y-3">
            {finished.map((j) => <JobRow key={j.job_id} job={j} onOpen={onOpenJob} />)}
          </div>
        </section>
      )}
    </div>
  );
}
