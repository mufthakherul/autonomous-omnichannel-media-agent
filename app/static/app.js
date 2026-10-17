let currentCommentFilter = 'all';
let currentVideoFilter = 'all';
let currentStudioFilter = 'all';
let currentLogStageFilter = 'all';

let allVideosCache = [];
let allStudioCache = [];
let allSourcesCache = [];
let allLogsCache = [];
let activeModalVideoId = null;

function showToast(msg) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => el.classList.add('hidden'), 4500);
}

function switchTab(tabId) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const targetPanel = document.getElementById(tabId);
  if (targetPanel) targetPanel.classList.add('active');
  const targetBtn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
  if (targetBtn) targetBtn.classList.add('active');
}

function togglePasswordField(inputId, btnEl) {
  const inp = document.getElementById(inputId);
  if (!inp) return;
  if (inp.type === 'password') {
    inp.type = 'text';
    btnEl.textContent = '🙈';
  } else {
    inp.type = 'password';
    btnEl.textContent = '👁️';
  }
}

async function copyToClipboard(text, label = 'Text') {
  try {
    await navigator.clipboard.writeText(text || '');
    showToast(`📋 Copied ${label} to clipboard!`);
  } catch (_) {
    showToast(`Could not copy ${label}`);
  }
}

async function apiCall(path, options = {}) {
  const resp = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!resp.ok) {
    let errText = resp.statusText;
    try {
      const errData = await resp.json();
      errText = errData.detail || JSON.stringify(errData);
    } catch (_) {}
    throw new Error(errText);
  }
  return resp.json();
}

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// --- Dashboard Overview & Telemetry ---
async function loadDashboardOverview() {
  const data = await apiCall('/api/dashboard');
  const c = data.counts || {};
  const d = data.dedup_stats || {};

  // Top KPI Cards
  setText('metricTotalVideos', c.total_videos || 0);
  setText('metricReadyAsIs', `${c.ready_as_is || 0} / ${c.remediated || 0}`);
  setText('metricStudioCount', `${c.ai_videos_generated || 0} / ${c.ai_images_generated || 0}`);
  setText('metricPublished', c.published || 0);
  setText('metricDedupShield', `${d.duplicates_blocked_count || 0} Blocked`);
  setText('metricDedupSub', `${d.unique_source_hashes || 0} Hashes • ${d.unique_news_topics || 0} Topics`);
  setText('metricComments', `${c.api_keys_healthy || 0} Keys • ${(c.comments_needing_reply || 0) + (c.comments_replied || 0)} Replies`);

  // Sidebar Live Badges
  setText('navCountVideos', c.total_videos || 0);
  setText('navCountStudio', c.studio_creations_total || 0);
  setText('navCountKeys', `${c.api_keys_healthy || 0}/${c.api_keys_total || 0}`);
  setText('navCountSources', c.sources_total || 0);
  setText('navCountComments', c.comments_needing_reply || 0);
  setText('navCountDedup', d.duplicates_blocked_count || 0);

  // 5-Stage Pipeline Flow dynamic subtexts
  setText('flowStage1Text', `${c.sources_total || 0} active sources • ${c.total_videos || 0} total videos collected.`);
  setText('flowStage2Text', `${c.ready_as_is || 0} Ready As-Is • ${c.needs_remediation || 0} flagged for remix.`);
  setText('flowStage3Text', `${c.remediated || 0} videos transformed with 1.03x tempo, EQ & metadata strip.`);
  setText('flowStage4Text', `${c.ai_videos_generated || 0} AI Videos • ${c.ai_images_generated || 0} AI News Images created.`);
  setText('flowStage5Text', `${c.published || 0} omnichannel posts • ${c.comments_replied || 0} comments replied.`);

  // Dedup Tab Counters
  setText('dedupTotalFingerprints', d.total_fingerprints || 0);
  setText('dedupSourceHashes', d.unique_source_hashes || 0);
  setText('dedupNewsTopics', d.unique_news_topics || 0);
  setText('dedupPlatformLocks', d.platform_publish_locks || 0);
  setText('dedupBlockedCount', d.duplicates_blocked_count || 0);

  // Header Badges
  const dedupBadge = document.getElementById('dedupBadge');
  if (dedupBadge) {
    dedupBadge.textContent = `🛡️ ${d.duplicates_blocked_count || 0} Duplicates Blocked (${d.total_fingerprints || 0} Locks)`;
  }

  const keyPoolBadge = document.getElementById('keyPoolBadge');
  if (keyPoolBadge) {
    const hCount = c.api_keys_healthy || 0;
    const tCount = c.api_keys_total || 0;
    keyPoolBadge.textContent = `🔑 ${hCount}/${tCount} Keys (${data.agent.max_parallel_workers || 4} Workers)`;
    keyPoolBadge.className = hCount > 0 ? 'badge badge-green' : 'badge badge-blue';
  }

  const modeBadge = document.getElementById('modeBadge');
  if (modeBadge) {
    if (data.agent.simulation_mode) {
      modeBadge.textContent = '🧪 Simulation Mode';
      modeBadge.className = 'badge badge-sim';
    } else {
      modeBadge.textContent = '🔴 Live API Connected';
      modeBadge.className = 'badge badge-green';
    }
  }

  const loopBtn = document.getElementById('toggleLoopBtn');
  if (loopBtn) {
    if (data.agent.auto_loop_enabled) {
      loopBtn.textContent = '🟢 Auto-Loop: Active';
      loopBtn.className = 'btn btn-success';
    } else {
      loopBtn.textContent = '⏸ Auto-Loop: Paused';
      loopBtn.className = 'btn btn-secondary';
    }
  }

  // Sidebar footer status
  const sidebarState = document.getElementById('sidebarAgentState');
  if (sidebarState) {
    if (data.agent.is_running_cycle) {
      sidebarState.textContent = '⚡ RUNNING CYCLE';
      sidebarState.className = 'badge badge-amber';
    } else {
      sidebarState.textContent = '● READY';
      sidebarState.className = 'badge badge-green';
    }
  }
  setText(
    'sidebarLastCycleText',
    `Cycles completed: ${data.agent.cycle_count || 0}${data.agent.last_cycle_at ? ` • Last: ${data.agent.last_cycle_at.slice(11, 19)}` : ''}`
  );

  renderApiKeysPool(data.api_keys || []);
  renderParallelTasks(data.parallel_tasks || []);

  allLogsCache = data.logs || [];
  renderFilteredLogs();
  renderOverviewLogs(allLogsCache.slice(0, 12));
}

function renderOverviewLogs(logs) {
  const container = document.getElementById('overviewLogsList');
  if (!container) return;
  if (!logs.length) {
    container.innerHTML = '<p class="muted">No activity logs yet.</p>';
    return;
  }
  container.innerHTML = logs.map(l => `
    <div class="log-row ${l.level}">
      <div>
        <span class="badge badge-blue" style="margin-right:0.35rem; font-size:0.65rem;">${escapeHtml(l.stage)}</span>
        ${escapeHtml(l.message)}
      </div>
      <span class="muted" style="font-size:0.72rem; white-space:nowrap;">${escapeHtml((l.created_at || '').slice(11, 19))}</span>
    </div>
  `).join('');
}

function setLogStageFilter(stage, btnEl) {
  currentLogStageFilter = stage;
  btnEl.parentElement.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
  btnEl.classList.add('active');
  renderFilteredLogs();
}

function renderFilteredLogs() {
  const container = document.getElementById('activityLogsList');
  if (!container) return;
  const q = (document.getElementById('logSearchInput')?.value || '').toLowerCase().trim();

  const filtered = allLogsCache.filter(l => {
    if (currentLogStageFilter !== 'all') {
      if (currentLogStageFilter === 'DEDUP' && !['DEDUP', 'DEDUP_GUARD'].includes(l.stage)) return false;
      if (currentLogStageFilter !== 'DEDUP' && l.stage !== currentLogStageFilter) return false;
    }
    if (q) {
      const hay = `${l.stage} ${l.message} ${l.level}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  if (!filtered.length) {
    container.innerHTML = '<p class="muted" style="padding:1rem;">No matching activity logs found.</p>';
    return;
  }

  container.innerHTML = filtered.map(l => `
    <div class="log-row ${l.level}">
      <div>
        <span class="badge ${l.stage.includes('DEDUP') ? 'badge-cyan' : 'badge-blue'}" style="margin-right:0.45rem; font-size:0.68rem;">${escapeHtml(l.stage)}</span>
        <span>${escapeHtml(l.message)}</span>
      </div>
      <span class="muted" style="font-size:0.73rem; white-space:nowrap;">${escapeHtml((l.created_at || '').slice(11, 19))}</span>
    </div>
  `).join('');
}

// --- Multi-Account API Key Pool & Parallel Workers ---
function renderApiKeysPool(keys) {
  const container = document.getElementById('apiKeysList');
  if (!container) return;
  if (!keys.length) {
    container.innerHTML = `
      <div class="source-item">
        <span class="muted">No API keys in the pool yet. Paste your and your family's Google Pro Gemini API keys on the left to enable multi-account load balancing & automatic 429 rate-limit failover!</span>
      </div>
    `;
    return;
  }

  container.innerHTML = keys.map(k => {
    let statusBadge = `<span class="badge badge-green">● HEALTHY</span>`;
    if (!k.active || k.status === 'paused') {
      statusBadge = `<span class="badge badge-sim">⏸ PAUSED</span>`;
    } else if (k.status === 'cooldown') {
      statusBadge = `<span class="badge badge-amber">⏳ COOLDOWN (${k.cooldown_remaining_sec}s)</span>`;
    } else if (k.status === 'invalid') {
      statusBadge = `<span class="badge badge-red">⛔ INVALID KEY</span>`;
    }

    const total = k.total_calls || 0;
    const succ = k.success_calls || 0;
    const ratePct = total > 0 ? Math.round((succ / total) * 100) : 100;

    return `
      <div class="source-item">
        <div style="flex:1;">
          <div style="display:flex; align-items:center; gap:0.45rem; flex-wrap:wrap;">
            <strong>${escapeHtml(k.label)}</strong>
            <code style="color:#38bdf8; font-size:0.78rem;">${escapeHtml(k.masked_key)}</code>
            <span class="badge badge-blue">${escapeHtml((k.provider || 'gemini').toUpperCase())}</span>
            ${statusBadge}
          </div>
          <div class="muted" style="font-size:0.76rem; margin-top:0.3rem;">
            Calls: <strong>${total}</strong> (Success: ${succ} • ${ratePct}%) • 429 Auto-Failovers: <strong>${k.rate_limit_hits || 0}</strong>
            ${k.last_used_at ? ` • Last Used: ${escapeHtml(k.last_used_at.slice(11, 19))}` : ''}
          </div>
          ${k.last_error ? `<div style="color:#fbbf24; font-size:0.73rem; margin-top:0.2rem;">Note: ${escapeHtml(k.last_error.slice(0, 110))}</div>` : ''}
        </div>
        <div style="display:flex; gap:0.4rem;">
          <button class="btn btn-secondary btn-sm" onclick="toggleApiKey(${k.id})">${k.active ? '⏸ Pause' : '▶ Enable'}</button>
          <button class="btn btn-danger-ghost btn-sm" onclick="deleteApiKey(${k.id})">✕</button>
        </div>
      </div>
    `;
  }).join('');
}

function renderParallelTasks(tasks) {
  const html = !tasks.length
    ? `<p class="muted" style="padding:0.5rem 0;">No parallel tasks recorded yet. Click "⚡ Run Parallel Agent Cycle" to watch concurrent workers execute in real time!</p>`
    : tasks.slice(0, 15).map(t => {
        const badge = t.status === 'completed'
          ? `<span class="badge badge-green">✓ ${t.duration_ms}ms</span>`
          : t.status === 'running'
            ? `<span class="badge badge-blue">⚙️ RUNNING</span>`
            : `<span class="badge badge-red">✕ FAILED</span>`;

        return `
          <div class="source-item">
            <div>
              <span class="badge badge-blue" style="margin-right:0.3rem;">Worker #${t.worker_slot}</span>
              <span class="badge badge-sim" style="margin-right:0.3rem;">${escapeHtml(t.task_type)}</span>
              <strong>${escapeHtml(t.title)}</strong>
              <div class="muted" style="font-size:0.75rem; margin-top:0.2rem;">
                ${escapeHtml(t.result_summary || '')}
                ${t.api_key_label ? ` • Key: ${escapeHtml(t.api_key_label)}` : ''}
              </div>
            </div>
            <div>${badge}</div>
          </div>
        `;
      }).join('');

  const mainContainer = document.getElementById('parallelTasksList');
  if (mainContainer) mainContainer.innerHTML = html;

  const overviewContainer = document.getElementById('overviewParallelList');
  if (overviewContainer) overviewContainer.innerHTML = html;
}

async function submitBulkApiKeys(e) {
  e.preventDefault();
  const raw_keys = document.getElementById('bulkApiKeysInput').value.trim();
  const provider = document.getElementById('keyProviderSelect').value;
  if (!raw_keys) return;
  showToast('Adding API keys to Smart Rotation Pool...');
  const res = await apiCall('/api/keys/bulk', {
    method: 'POST',
    body: JSON.stringify({ raw_keys, provider }),
  });
  document.getElementById('bulkApiKeysInput').value = '';
  await refreshAll();
  showToast(`Added ${res.added} API key(s) to the pool (${res.skipped} duplicate/skipped)!`);
}

async function toggleApiKey(id) {
  await apiCall(`/api/keys/${id}/toggle`, { method: 'POST' });
  await loadDashboardOverview();
}

async function deleteApiKey(id) {
  await apiCall(`/api/keys/${id}`, { method: 'DELETE' });
  await loadDashboardOverview();
}

async function resetAllKeyCooldowns() {
  await apiCall('/api/keys/reset-cooldowns', { method: 'POST' });
  await loadDashboardOverview();
  showToast('All API key cooldowns reset to Healthy!');
}

// --- AI News, Video & Image Studio ---
function fillStudioPreset(topic, niche) {
  const tEl = document.getElementById('studioTopic');
  const nEl = document.getElementById('studioNiche');
  if (tEl) tEl.value = topic;
  if (nEl) nEl.value = niche;
  showToast('✨ Loaded preset topic into Studio form!');
}

function setStudioFilter(filterType, btnEl) {
  currentStudioFilter = filterType;
  btnEl.parentElement.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
  btnEl.classList.add('active');
  renderFilteredStudioCreations();
}

async function loadStudioCreations() {
  const data = await apiCall('/api/studio/creations');
  allStudioCache = data.creations || [];
  renderFilteredStudioCreations();
}

function renderFilteredStudioCreations() {
  const container = document.getElementById('studioCreationsList');
  if (!container) return;
  const q = (document.getElementById('studioSearchInput')?.value || '').toLowerCase().trim();

  const filtered = allStudioCache.filter(item => {
    if (currentStudioFilter !== 'all' && item.creation_type !== currentStudioFilter) return false;
    if (q) {
      const hay = `${item.headline} ${item.niche} ${item.summary} ${item.caption}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  if (!filtered.length) {
    container.innerHTML = `
      <div class="panel-card" style="text-align:center; padding: 2.2rem;">
        <h4>No matching AI Studio creations or trends</h4>
        <p class="muted" style="margin:0.5rem 0 1rem;">Click "🔍 Scan Live Breaking News & Viral Trends" or enter any topic on the left to generate original AI Videos and AI Image Posts!</p>
        <button class="btn btn-primary" onclick="discoverTrendingTopicsNow()">🔍 Discover Live Breaking News Now</button>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered.map(item => {
    const plats = item.platforms_published || {};
    const platBadges = Object.keys(plats).map(p => {
      const info = plats[p] || {};
      return `<span class="badge badge-green" style="margin-right:0.3rem;">✓ ${escapeHtml(p.toUpperCase())} (${escapeHtml(info.external_id || info.status || 'ok')})</span>`;
    }).join('');

    if (item.creation_type === 'news_trend') {
      return `
        <div class="comment-card" style="border-left: 3px solid var(--accent);">
          <div class="video-header">
            <div>
              <span class="badge badge-cyan">🔥 LIVE BREAKING TREND • ${escapeHtml(item.niche)}</span>
              <h4 style="margin-top:0.35rem; font-size:0.98rem;">${escapeHtml(item.headline)}</h4>
            </div>
          </div>
          <p class="muted" style="font-size:0.83rem; margin:0.35rem 0;">${escapeHtml(item.summary)}</p>
          <div style="display:flex; gap:0.45rem; flex-wrap:wrap; margin-top:0.65rem;">
            <button class="btn btn-primary btn-sm" onclick="generateFromTrend(${JSON.stringify(item.headline).replace(/"/g, '&quot;')}, ${JSON.stringify(item.niche).replace(/"/g, '&quot;')}, 'both')">
              ⚡ Generate Both Video + Image (Parallel)
            </button>
            <button class="btn btn-secondary btn-sm" onclick="generateFromTrend(${JSON.stringify(item.headline).replace(/"/g, '&quot;')}, ${JSON.stringify(item.niche).replace(/"/g, '&quot;')}, 'ai_video')">
              🎬 Generate AI Video Short
            </button>
            <button class="btn btn-secondary btn-sm" onclick="generateFromTrend(${JSON.stringify(item.headline).replace(/"/g, '&quot;')}, ${JSON.stringify(item.niche).replace(/"/g, '&quot;')}, 'ai_image_post')">
              🖼️ Generate AI Image Post
            </button>
          </div>
        </div>
      `;
    }

    const isVideo = item.creation_type === 'ai_video';
    const typeBadge = isVideo
      ? `<span class="badge badge-green">🎬 AI GENERATED VIDEO ${item.linked_video_id ? `(Pipeline #${item.linked_video_id})` : ''}</span>`
      : `<span class="badge badge-blue">🖼️ AI GENERATED IMAGE POST</span>`;

    const mediaPreview = isVideo
      ? `<video src="${escapeHtml(item.media_url)}" controls style="width:100%; max-height:220px; border-radius:8px; background:#000;"></video>`
      : `<img src="${escapeHtml(item.media_url)}" alt="AI Generated Post" style="width:100%; max-height:220px; object-fit:cover; border-radius:8px;" />`;

    const tagsLine = (item.hashtags || []).map(t => `#${t.replace(/^#/, '')}`).join(' ');

    return `
      <div class="video-card">
        <div class="video-card-grid">
          <div class="thumb-box">
            ${mediaPreview}
            <div class="thumb-meta">
              <div><strong>Niche:</strong> ${escapeHtml(item.niche)}</div>
              <div><strong>Created:</strong> ${escapeHtml(item.created_at?.slice(11, 19) || '')}</div>
            </div>
          </div>
          <div>
            <div class="video-header">
              <div>
                ${typeBadge}
                <h3 style="margin-top:0.35rem;">${escapeHtml(item.headline)}</h3>
                ${platBadges ? `<div style="margin-top:0.35rem;">${platBadges}</div>` : ''}
              </div>
            </div>
            <p style="font-size:0.84rem; margin:0.4rem 0; white-space:pre-line; color:var(--text-secondary);">${escapeHtml(item.caption)}</p>
            ${isVideo && item.voiceover_script ? `
              <div class="muted" style="font-size:0.77rem; background:var(--bg-elevated); padding:0.55rem 0.7rem; border-radius:6px; border:1px solid var(--border); margin-bottom:0.45rem;">
                <strong>🎙️ AI Voiceover Script:</strong> "${escapeHtml(item.voiceover_script)}"
              </div>
            ` : ''}
            <div style="color:var(--accent); font-size:0.77rem; margin-bottom:0.65rem;">${escapeHtml(tagsLine)}</div>
            <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
              <button class="btn btn-primary btn-sm" onclick="publishStudioCreation(${item.id})">
                🌐 Publish Omnichannel (YT • FB • IG • TikTok)
              </button>
              <button class="btn btn-secondary btn-sm" onclick="copyToClipboard(${JSON.stringify(`${item.caption}\n\n${tagsLine}`).replace(/"/g, '&quot;')}, 'Caption & Hashtags')">
                📋 Copy Caption & Tags
              </button>
            </div>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function discoverTrendingTopicsNow() {
  const niche = document.getElementById('trendQueryInput')?.value || 'Technology & AI, World News, Viral Events';
  showToast(`🔍 Scanning Google News RSS & Gemini Search Grounding for '${niche}'...`);
  const res = await apiCall('/api/studio/discover-trends', {
    method: 'POST',
    body: JSON.stringify({ niche, max_items: 4 }),
  });
  await refreshAll();
  showToast(`Discovered ${res.trends.length} fresh unseen trending stories!`);
}

async function submitStudioGeneration(e) {
  e.preventDefault();
  const btn = document.getElementById('studioGenBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Researching & Generating in Parallel...';

  const payload = {
    topic: document.getElementById('studioTopic').value.trim(),
    niche: document.getElementById('studioNiche').value.trim() || 'Trending News',
    creation_type: document.getElementById('studioType').value,
    aspect_ratio: document.getElementById('studioAspectRatio').value,
    use_search_grounding: true,
    auto_publish: document.getElementById('studioAutoPublish').value === 'true',
    platforms: ['youtube', 'facebook', 'instagram', 'tiktok'],
  };

  showToast(`Generating ${payload.creation_type === 'both' ? 'AI Video + AI Image Post in Parallel' : payload.creation_type}...`);
  try {
    const res = await apiCall('/api/studio/generate', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    document.getElementById('studioTopic').value = '';
    await refreshAll();
    const skippedAny = (res.created || []).some(c => c.dedup_skipped);
    if (skippedAny) {
      showToast('🛡️ Zero-Duplicate Guard: Topic was already covered — returned existing creation!');
    } else {
      showToast(`✨ Generated ${res.created.length} new AI Studio creation(s)!`);
    }
  } catch (err) {
    showToast(`Studio error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = '✨ Research & Generate AI Media Now';
  }
}

async function generateFromTrend(headline, niche, creationType) {
  showToast(`Generating ${creationType === 'both' ? 'AI Video Short + AI Image Post in Parallel' : creationType} for "${headline.slice(0, 45)}..."`);
  await apiCall('/api/studio/generate', {
    method: 'POST',
    body: JSON.stringify({
      topic: headline,
      niche: niche || 'Trending News',
      creation_type: creationType,
      aspect_ratio: creationType === 'ai_image_post' ? '1:1' : '9:16',
      use_search_grounding: true,
      auto_publish: false,
      platforms: ['youtube', 'facebook', 'instagram', 'tiktok'],
    }),
  });
  await refreshAll();
  showToast('✨ AI Studio generation completed!');
}

async function publishStudioCreation(creationId) {
  showToast(`Publishing Studio Creation #${creationId} across YouTube, Facebook, Instagram & TikTok...`);
  await apiCall(`/api/studio/creations/${creationId}/publish`, {
    method: 'POST',
    body: JSON.stringify({ platforms: ['youtube', 'facebook', 'instagram', 'tiktok'], schedule: false }),
  });
  await refreshAll();
  showToast(`🌐 Published Studio Creation #${creationId} across all platforms!`);
}

// --- Built-In Video Player Modal ---
function openVideoPreviewModal(videoId) {
  const v = allVideosCache.find(item => item.id === videoId);
  if (!v) return;
  activeModalVideoId = videoId;
  const modal = document.getElementById('videoPreviewModal');
  const player = document.getElementById('modalVideoPlayer');
  setText('modalVideoTitle', `#${v.id} — ${v.seo_title || v.original_filename}`);
  setText('modalVideoBadge', v.remediated_path ? '🛠️ FFmpeg Copyright-Safe Remixed MP4' : '🎬 Original Source MP4');
  setText('modalVideoSpecs', `${v.width}x${v.height} • ${v.duration_sec}s • Readiness ${v.readiness_score}/100`);

  player.src = `/api/videos/${videoId}/stream?variant=best&t=${Date.now()}`;
  modal.classList.remove('hidden');
  player.play().catch(() => {});
}

function switchModalVideoVariant(variant) {
  if (!activeModalVideoId) return;
  const player = document.getElementById('modalVideoPlayer');
  player.src = `/api/videos/${activeModalVideoId}/stream?variant=${variant}&t=${Date.now()}`;
  player.play().catch(() => {});
  showToast(variant === 'original' ? '📂 Playing original untouched source file' : '✨ Playing active / FFmpeg-remixed version');
}

function closeVideoPreviewModal() {
  const modal = document.getElementById('videoPreviewModal');
  const player = document.getElementById('modalVideoPlayer');
  if (player) {
    player.pause();
    player.removeAttribute('src');
  }
  if (modal) modal.classList.add('hidden');
  activeModalVideoId = null;
}

function closeVideoModalOnBackdrop(e) {
  if (e.target && e.target.id === 'videoPreviewModal') {
    closeVideoPreviewModal();
  }
}

// --- Video Pipeline & Remix Queue ---
function renderStatusBadge(v) {
  if (v.status === 'published') return `<span class="badge badge-green">🎉 PUBLISHED (${escapeHtml(v.youtube_video_id || 'LIVE')})</span>`;
  if (v.status === 'scheduled') return `<span class="badge badge-blue">📅 SCHEDULED (${escapeHtml(v.scheduled_publish_at?.slice(0, 16))})</span>`;
  if (v.status === 'precheck_pending') return `<span class="badge badge-blue">🔍 UNLISTED PRE-CHECK (${escapeHtml(v.youtube_video_id)})</span>`;
  if (v.status === 'copyright_flagged') return `<span class="badge badge-red">⛔ COPYRIGHT / POLICY FLAGGED</span>`;
  if (v.status === 'remediated') return `<span class="badge badge-cyan">🛠️ FFMPEG REMIXED (100% READY)</span>`;
  if (v.can_upload_as_is) return `<span class="badge badge-green">✅ READY AS-IS</span>`;
  if (v.status === 'needs_remediation') return `<span class="badge badge-amber">⚠️ NEEDS FFMPEG REMIX</span>`;
  return `<span class="badge badge-sim">${escapeHtml((v.status || 'discovered').toUpperCase())}</span>`;
}

function setVideoStatusFilter(status, btnEl) {
  currentVideoFilter = status;
  btnEl.parentElement.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
  btnEl.classList.add('active');
  renderFilteredVideos();
}

async function loadVideos() {
  const data = await apiCall('/api/videos');
  allVideosCache = data.videos || [];
  renderFilteredVideos();
}

function renderFilteredVideos() {
  const container = document.getElementById('videosList');
  if (!container) return;
  const q = (document.getElementById('videoSearchInput')?.value || '').toLowerCase().trim();

  const filtered = allVideosCache.filter(v => {
    if (currentVideoFilter === 'ready_as_is' && !v.can_upload_as_is) return false;
    if (currentVideoFilter === 'remediated' && v.status !== 'remediated') return false;
    if (currentVideoFilter === 'needs_remediation' && v.status !== 'needs_remediation') return false;
    if (currentVideoFilter === 'published' && !['published', 'scheduled'].includes(v.status)) return false;

    if (q) {
      const hay = `${v.seo_title || ''} ${v.original_filename || ''} ${(v.seo_tags || []).join(' ')} ${v.source_type || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  if (!filtered.length) {
    container.innerHTML = `
      <div class="panel-card" style="text-align:center; padding: 2.5rem;">
        <h3>No matching videos in this view</h3>
        <p class="muted" style="margin: 0.5rem 0 1.2rem;">Try clearing your search filter, click "+ Test 1080p Video" above, or sync your Mother Folders.</p>
        <button class="btn btn-primary" onclick="generateSampleVideo('clean_landscape')">+ Generate Sample 1080p Video Now</button>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered.map(v => {
    const thumbUrl = v.thumbnail_path ? `/thumbnails/thumb_video_${v.id}.jpg?t=${Date.now()}` : '';
    const issuesList = (v.issues_detected || []).length
      ? `<div style="margin-top:0.35rem; color:#fbbf24; font-size:0.79rem;"><strong>⚠️ Detected Issues:</strong> ${escapeHtml(v.issues_detected.join(' • '))}</div>`
      : `<div style="margin-top:0.35rem; color:#34d399; font-size:0.79rem;">✓ Zero visual watermarks • Clean audio (${escapeHtml(v.audio_risk_level || 'low')} risk) • Policy safe</div>`;

    const appliedFixes = (v.remediation_applied || []).length
      ? `<div style="margin-top:0.25rem; color:var(--accent); font-size:0.76rem;"><strong>🛠️ Active Transforms:</strong> ${escapeHtml(v.remediation_applied.join(', '))}</div>`
      : '';

    const plats = v.platforms_published || {};
    const platBadges = Object.keys(plats).map(p => {
      const info = plats[p] || {};
      return `<span class="badge badge-blue" style="margin-right:0.3rem;">✓ ${escapeHtml(p.toUpperCase())} (${escapeHtml(info.external_id || info.status)})</span>`;
    }).join('');

    const score = Math.max(0, Math.min(100, Number(v.readiness_score || 0)));

    return `
      <div class="video-card">
        <div class="video-card-grid">
          <div class="thumb-box">
            <div class="thumb-preview-wrapper" onclick="openVideoPreviewModal(${v.id})" title="Click to preview video">
              ${thumbUrl
                ? `<img src="${thumbUrl}" alt="Thumbnail" onerror="this.style.display='none'" />`
                : `<div style="height:142px;display:flex;align-items:center;justify-content:center;color:#64748b;font-size:0.8rem;">Click to Preview Video</div>`
              }
              <div class="play-overlay">
                <div class="play-circle">▶</div>
              </div>
            </div>
            <div class="thumb-meta">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span><strong>Score:</strong> ${score}/100</span>
                <span class="badge badge-blue" style="font-size:0.65rem; padding:0.1rem 0.45rem;">${v.is_short ? '📱 9:16 SHORT' : '🖥️ 16:9 HD'}</span>
              </div>
              <div class="score-bar-track"><div class="score-bar-fill" style="width:${score}%;"></div></div>
              <div style="margin-top:0.2rem;"><strong>Specs:</strong> ${v.width}x${v.height} • ${v.duration_sec}s • ${escapeHtml((v.video_codec || 'h264').toUpperCase())}</div>
              <div><strong>Source:</strong> ${escapeHtml((v.source_type || 'local').toUpperCase())}</div>
              <button class="btn btn-secondary btn-sm full-width" style="margin-top:0.25rem;" onclick="openVideoPreviewModal(${v.id})">
                ▶ Watch / Preview Video
              </button>
            </div>
          </div>

          <div>
            <div class="video-header">
              <div style="flex:1;">
                <div style="display:flex; align-items:center; gap:0.45rem; flex-wrap:wrap; margin-bottom:0.25rem;">
                  <span class="badge badge-blue">#${v.id}</span>
                  ${renderStatusBadge(v)}
                </div>
                <h3>${escapeHtml(v.seo_title || v.original_filename)}</h3>
                <div class="muted" style="font-size:0.75rem;">Original File: <code>${escapeHtml(v.original_filename)}</code></div>
                ${issuesList}
                ${appliedFixes}
                ${platBadges ? `<div style="margin-top:0.45rem;">${platBadges}</div>` : ''}
              </div>
            </div>

            <!-- Collapsible SEO Metadata Drawer -->
            <details class="seo-drawer">
              <summary>
                <span>✏️ Edit AI-Generated SEO Title, Description, Playlist & Tags</span>
                <span class="muted" style="font-size:0.72rem;">Click to Expand ▾</span>
              </summary>
              <div class="seo-fields">
                <div>
                  <label>AI-Generated SEO Title / Hook</label>
                  <input type="text" id="title_${v.id}" value="${escapeHtml(v.seo_title)}" />
                </div>
                <div>
                  <label>Suggested Playlist & YouTube Category ID</label>
                  <div style="display:flex; gap:0.5rem;">
                    <input type="text" id="playlist_${v.id}" value="${escapeHtml(v.playlist_name)}" placeholder="Playlist Name" />
                    <input type="text" id="cat_${v.id}" value="${escapeHtml(v.category_id)}" style="max-width:75px;" title="YouTube Category ID" />
                  </div>
                </div>
                <div class="full-span">
                  <label>AI-Generated SEO Description / Caption (Chapters & Hashtags)</label>
                  <textarea id="desc_${v.id}" rows="2">${escapeHtml(v.seo_description)}</textarea>
                </div>
                <div class="full-span">
                  <label>SEO Search Tags (comma-separated)</label>
                  <input type="text" id="tags_${v.id}" value="${escapeHtml((v.seo_tags || []).join(', '))}" />
                </div>
                <div class="full-span" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
                  <div style="display:flex; gap:0.4rem;">
                    <button type="button" class="btn btn-primary btn-sm" onclick="saveVideoMeta(${v.id})">💾 Save Metadata</button>
                    <button type="button" class="btn btn-secondary btn-sm" onclick="copyToClipboard(document.getElementById('title_${v.id}').value, 'SEO Title')">📋 Copy Title</button>
                    <button type="button" class="btn btn-secondary btn-sm" onclick="copyToClipboard(document.getElementById('tags_${v.id}').value, 'SEO Tags')">📋 Copy Tags</button>
                  </div>
                  <button type="button" class="btn btn-danger-ghost btn-sm" onclick="deleteVideoItem(${v.id})">🗑️ Remove from Queue</button>
                </div>
              </div>
            </details>

            <div class="card-actions">
              <div style="display:flex; gap:0.45rem; flex-wrap:wrap;">
                <button class="btn btn-secondary btn-sm" onclick="reinspectVideo(${v.id})">🔍 Re-Audit (Gemini)</button>
                <button class="btn btn-amber btn-sm" onclick="remediateVideo(${v.id})">🛠️ Copyright-Safe Remix (FFmpeg)</button>
              </div>
              <div style="display:flex; gap:0.45rem; flex-wrap:wrap;">
                <button class="btn btn-secondary btn-sm" onclick="preuploadVideo(${v.id})">🔒 YT Pre-Check</button>
                <button class="btn btn-secondary btn-sm" onclick="publishVideo(${v.id}, false)">▶ Publish YT</button>
                <button class="btn btn-primary btn-sm" onclick="publishOmnichannel(${v.id})">🌐 Publish All (YT • FB • IG • TikTok)</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function deleteVideoItem(id) {
  await apiCall(`/api/videos/${id}`, { method: 'DELETE' });
  await refreshAll();
  showToast(`Removed Video #${id} from queue (SHA-256 retained in Dedup Guard).`);
}

async function generateSampleVideo(preset) {
  showToast('🎬 Generating test video and running Gemini 3.8 Flash inspection...');
  await apiCall(`/api/videos/generate-sample?preset=${encodeURIComponent(preset)}`, { method: 'POST' });
  await refreshAll();
  showToast('✨ Test video inspected and added to pipeline!');
}

async function uploadLocalVideoFile(event) {
  const file = event.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  showToast(`📂 Uploading & inspecting '${file.name}'...`);
  try {
    await apiCall('/api/videos/upload-local', { method: 'POST', body: formData });
    await refreshAll();
    showToast(`✨ '${file.name}' inspected and added!`);
  } catch (err) {
    showToast(`Upload notice: ${err.message}`);
  }
  event.target.value = '';
}

async function saveVideoMeta(id) {
  const seo_title = document.getElementById(`title_${id}`).value;
  const seo_description = document.getElementById(`desc_${id}`).value;
  const playlist_name = document.getElementById(`playlist_${id}`).value;
  const category_id = document.getElementById(`cat_${id}`).value;
  const seo_tags = document.getElementById(`tags_${id}`).value.split(',').map(t => t.trim()).filter(Boolean);

  await apiCall(`/api/videos/${id}`, {
    method: 'PUT',
    body: JSON.stringify({ seo_title, seo_description, playlist_name, category_id, seo_tags }),
  });
  showToast(`💾 Saved SEO metadata for Video #${id}`);
  await refreshAll();
}

async function reinspectVideo(id) {
  showToast(`🔍 Running Technical + Gemini 3.8 Flash inspection on Video #${id}...`);
  await apiCall(`/api/videos/${id}/inspect`, { method: 'POST' });
  await refreshAll();
  showToast(`✅ Inspection completed for Video #${id}`);
}

async function remediateVideo(id) {
  showToast(`🛠️ Applying FFmpeg Copyright-Safe Remix on Video #${id}...`);
  await apiCall(`/api/videos/${id}/remediate`, { method: 'POST' });
  await refreshAll();
  showToast(`✨ FFmpeg Copyright-Safe Remix complete for Video #${id}!`);
}

async function preuploadVideo(id) {
  showToast(`🔒 Uploading Video #${id} as Private/Unlisted for YouTube Content ID check...`);
  await apiCall(`/api/videos/${id}/preupload`, { method: 'POST' });
  await refreshAll();
  showToast(`✅ Pre-upload complete for Video #${id}!`);
}

async function publishVideo(id, schedule) {
  showToast(schedule ? `Verifying Content ID & scheduling Video #${id}...` : `Verifying Content ID & publishing Video #${id}...`);
  await apiCall(`/api/videos/${id}/publish?schedule=${schedule}`, { method: 'POST' });
  await refreshAll();
  showToast(schedule ? `📅 Video #${id} scheduled & added to playlist!` : `🎉 Video #${id} published to YouTube!`);
}

async function publishOmnichannel(id) {
  showToast(`🌐 Publishing Video #${id} across YouTube, Facebook Page, Instagram Reels & TikTok...`);
  await apiCall(`/api/videos/${id}/publish-omnichannel`, {
    method: 'POST',
    body: JSON.stringify({ platforms: ['youtube', 'facebook', 'instagram', 'tiktok'], schedule: false }),
  });
  await refreshAll();
  showToast(`🎉 Video #${id} published across YouTube, Facebook, Instagram & TikTok!`);
}

// --- Mother Folders & Video Sources ---
async function submitBulkSources() {
  const raw_links = document.getElementById('bulkLinksInput').value.trim();
  if (!raw_links) {
    showToast('Please paste one or more folder/video URLs first.');
    return;
  }
  showToast('📡 Inspecting pasted Mother Folders, resolving titles & registering sources...');
  const res = await apiCall('/api/sources/bulk', {
    method: 'POST',
    body: JSON.stringify({ raw_links, max_items_per_sync: 4 }),
  });
  document.getElementById('bulkLinksInput').value = '';
  await refreshAll();
  showToast(`✅ Added ${res.added_sources.length} new source(s) from ${res.total_links_found} link(s)!`);
}

function updateSourcePlaceholder() {
  const type = document.getElementById('srcType').value;
  const input = document.getElementById('srcValue');
  const label = document.getElementById('srcValueLabel');
  const placeholders = {
    gdrive: ['Google Drive Mother Folder URL or ID', 'https://drive.google.com/drive/folders/1abc...'],
    local: ['Local Folder Path', 'e:\\Documents\\agent\\data\\incoming'],
    s3: ['S3 Bucket & Prefix', 'my-copyright-free-videos/nature-4k/'],
    pexels: ['Pexels Search Query', 'drone mountain landscape 4k'],
    pixabay: ['Pixabay Search Query', 'ocean waves sunset'],
    telegram: ['Telegram Channel Username or Chat ID', '@copyright_free_clips'],
    rss: ['RSS / Atom Media Feed URL', 'https://example.com/videos/feed.xml'],
    url: ['Direct MP4 Video URL', 'https://example.com/sample.mp4'],
  };
  if (placeholders[type]) {
    label.textContent = placeholders[type][0];
    input.placeholder = placeholders[type][1];
  }
}

async function loadSources() {
  const data = await apiCall('/api/sources');
  allSourcesCache = data.sources || [];
  renderFilteredSources();
}

function renderFilteredSources() {
  const container = document.getElementById('sourcesList');
  if (!container) return;
  const q = (document.getElementById('sourceSearchInput')?.value || '').toLowerCase().trim();

  const filtered = allSourcesCache.filter(s => {
    if (!q) return true;
    return `${s.name} ${s.source_type} ${s.config_value}`.toLowerCase().includes(q);
  });

  if (!filtered.length) {
    container.innerHTML = '<p class="muted">No matching sources found.</p>';
    return;
  }

  container.innerHTML = filtered.map(s => `
    <div class="source-item">
      <div style="flex:1; min-width:0;">
        <div style="display:flex; align-items:center; gap:0.45rem; flex-wrap:wrap;">
          <strong>${escapeHtml(s.name)}</strong>
          <span class="badge badge-blue">${escapeHtml(s.source_type.toUpperCase())}</span>
          <span class="badge badge-green">Collected: ${s.total_collected || 0}</span>
        </div>
        <div class="muted" style="font-size:0.76rem; margin-top:0.25rem; word-break:break-all;">
          Target: <code>${escapeHtml(s.config_value)}</code>
          ${s.last_synced_at ? ` • Last synced: ${escapeHtml(s.last_synced_at.slice(11, 19))}` : ''}
        </div>
      </div>
      <div style="display:flex; gap:0.4rem;">
        <button class="btn btn-secondary btn-sm" onclick="syncSource(${s.id})">🔄 Sync</button>
        <button class="btn btn-danger-ghost btn-sm" onclick="deleteSource(${s.id})">✕</button>
      </div>
    </div>
  `).join('');
}

async function createNewSource(e) {
  e.preventDefault();
  const payload = {
    name: document.getElementById('srcName').value,
    source_type: document.getElementById('srcType').value,
    config_value: document.getElementById('srcValue').value,
    max_items_per_sync: parseInt(document.getElementById('srcMaxItems').value || '5', 10),
    active: true,
  };
  await apiCall('/api/sources', { method: 'POST', body: JSON.stringify(payload) });
  document.getElementById('addSourceForm').reset();
  await refreshAll();
  showToast('➕ Added new video source!');
}

async function syncSource(id) {
  showToast('🔄 Syncing video source (with Pre-Download Dedup Guard)...');
  const res = await apiCall(`/api/sources/${id}/sync`, { method: 'POST' });
  await refreshAll();
  showToast(`✅ Collected ${res.collected_count} new unique video(s)!`);
}

async function syncAllSources() {
  showToast('🔄 Syncing all active Mother Folders & sources...');
  const res = await apiCall('/api/sources/sync-all', { method: 'POST' });
  await refreshAll();
  showToast(`✅ Synced all sources: ${res.collected_count} new unique video(s) collected!`);
}

async function deleteSource(id) {
  await apiCall(`/api/sources/${id}`, { method: 'DELETE' });
  await refreshAll();
}

async function uploadPdfBundle(event) {
  const file = event.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  showToast(`📄 Extracting video source links from '${file.name}'...`);
  try {
    const res = await apiCall('/api/sources/import-pdf', { method: 'POST', body: formData });
    await refreshAll();
    showToast(`✅ Imported ${res.added_sources.length} new source(s) from '${file.name}'!`);
  } catch (err) {
    showToast(`PDF Import Error: ${err.message}`);
  }
  event.target.value = '';
}

// --- Smart Comment Responder ---
function filterComments(filterType, btnEl) {
  currentCommentFilter = filterType;
  btnEl.parentElement.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
  btnEl.classList.add('active');
  loadComments();
}

async function loadComments() {
  const query = currentCommentFilter === 'all' ? '' : `?filter_type=${currentCommentFilter}`;
  const data = await apiCall(`/api/comments${query}`);
  const container = document.getElementById('commentsList');
  if (!container) return;

  if (!data.comments.length) {
    container.innerHTML = `
      <div class="panel-card" style="text-align:center; padding: 2rem;">
        <p class="muted">No comments in this view yet. Click "🧪 Load 5 Sample Viewer Comments" to see how the AI filters noise and replies only to comments that need a response.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = data.comments.map(c => {
    const needsReplyBadge = c.needs_reply
      ? `<span class="badge badge-green">✅ NEEDS REPLY (${escapeHtml(c.category.toUpperCase())} • P${c.priority})</span>`
      : `<span class="badge badge-amber">🚫 FILTERED OUT (${escapeHtml(c.category.toUpperCase())})</span>`;

    const statusBadge = c.status === 'replied'
      ? `<span class="badge badge-blue">✓ REPLIED ON YOUTUBE</span>`
      : '';

    return `
      <div class="comment-card">
        <div class="video-header">
          <div>
            <strong>${escapeHtml(c.author_name)}</strong>
            <span class="muted">on <em>${escapeHtml(c.video_title)}</em></span>
          </div>
          <div style="display:flex; gap:0.4rem; flex-wrap:wrap;">
            ${needsReplyBadge}
            ${statusBadge}
          </div>
        </div>
        <p style="font-size:0.94rem; margin-bottom:0.5rem;">"${escapeHtml(c.comment_text)}"</p>
        <div class="muted" style="font-size:0.79rem; margin-bottom:0.65rem;">
          <strong>🤖 AI Triage Rationale:</strong> ${escapeHtml(c.ai_reason)}
        </div>
        ${c.needs_reply ? `
          <div style="background:var(--bg-elevated); padding:0.8rem; border-radius:8px; border:1px solid var(--border);">
            <label>AI Drafted Creator Reply</label>
            <textarea id="reply_text_${c.id}" rows="2">${escapeHtml(c.draft_reply)}</textarea>
            <div style="display:flex; gap:0.5rem; margin-top:0.55rem;">
              ${c.status !== 'replied' ? `
                <button class="btn btn-primary btn-sm" onclick="sendCommentReply(${c.id})">💬 Approve & Post Reply</button>
                <button class="btn btn-secondary btn-sm" onclick="ignoreComment(${c.id})">Skip / Ignore</button>
              ` : `<span class="muted" style="font-size:0.76rem;">✓ Replied at ${escapeHtml(c.replied_at?.slice(0, 19))} (ID: ${escapeHtml(c.reply_comment_id)})</span>`}
            </div>
          </div>
        ` : ''}
      </div>
    `;
  }).join('');
}

async function seedSampleComments() {
  showToast('🧪 Seeding 5 realistic viewer comments and running Gemini comment triage...');
  await apiCall('/api/comments/seed-samples', { method: 'POST' });
  await refreshAll();
  showToast('✅ Evaluated 5 comments: Questions & praise queued; spam, troll & emoji filtered out!');
}

async function submitCustomTestComment(e) {
  e.preventDefault();
  const author_name = document.getElementById('testCommentAuthor').value;
  const comment_text = document.getElementById('testCommentText').value;
  showToast('🤖 Evaluating viewer comment with Gemini 3.8 Flash...');
  await apiCall('/api/comments/custom-test', {
    method: 'POST',
    body: JSON.stringify({ author_name, comment_text }),
  });
  document.getElementById('testCommentText').value = '';
  await refreshAll();
  showToast('✅ Comment evaluated!');
}

async function syncAndTriageComments() {
  showToast('🔄 Polling channel comments & running AI triage...');
  await apiCall('/api/comments/sync-and-triage', { method: 'POST' });
  await refreshAll();
  showToast('✅ Comments synced and triaged!');
}

async function sendCommentReply(id) {
  const reply_text = document.getElementById(`reply_text_${id}`).value;
  await apiCall(`/api/comments/${id}/reply`, {
    method: 'POST',
    body: JSON.stringify({ reply_text }),
  });
  await refreshAll();
  showToast('💬 Reply posted to YouTube comment!');
}

async function ignoreComment(id) {
  await apiCall(`/api/comments/${id}/ignore`, { method: 'POST' });
  await refreshAll();
}

// --- Settings & Agent Controls ---
async function loadSettings() {
  const data = await apiCall('/api/settings');
  const s = data.settings || {};
  document.getElementById('setGeminiKey').value = s.gemini_api_key || '';
  document.getElementById('setMaxWorkers').value = s.max_parallel_workers || 4;
  document.getElementById('setGeminiModel').value = s.gemini_model || 'gemini-3.8-flash';
  document.getElementById('setGeminiImageModel').value = s.gemini_image_model || 'gemini-nano-banana-2.1';
  document.getElementById('setGeminiVideoModel').value = s.gemini_video_model || 'gemini-omni-1.1-flash';
  document.getElementById('setGeminiTtsModel').value = s.gemini_tts_model || 'gemini-3.8-flash-tts';
  document.getElementById('setAutoTrending').value = String(Boolean(s.auto_generate_trending_posts ?? true));
  document.getElementById('setTrendingNiches').value = s.trending_niches || 'Technology & AI, World News, Science & Space, Viral Pop Culture';
  document.getElementById('setSimulationMode').value = String(Boolean(s.youtube_simulation_mode));
  document.getElementById('setPrecheckPrivacy').value = s.precheck_privacy_status || 'private';
  document.getElementById('setAutoRemediate').value = String(Boolean(s.auto_remediate_failed_videos));
  document.getElementById('setAutoReply').value = String(Boolean(s.auto_reply_comments));
  document.getElementById('setDailyLimit').value = s.daily_upload_limit || 4;
  document.getElementById('setSpacingHours').value = s.upload_spacing_hours || 4;
  document.getElementById('setPexelsKey').value = s.pexels_api_key || '';
  document.getElementById('setPixabayKey').value = s.pixabay_api_key || '';
  document.getElementById('setTelegramToken').value = s.telegram_bot_token || '';
  document.getElementById('setLoopInterval').value = s.agent_loop_interval_seconds || 300;
  document.getElementById('setAlwaysTransform').value = String(Boolean(s.always_apply_copyright_safe_edit ?? true));
  document.getElementById('setFbPageId').value = s.facebook_page_id || '';
  document.getElementById('setIgAccountId').value = s.instagram_account_id || '';
  document.getElementById('setFbAccessToken').value = s.facebook_access_token || '';
  document.getElementById('setTiktokToken').value = s.tiktok_access_token || '';
  document.getElementById('setWebhookUrl').value = s.omnichannel_webhook_url || '';
  document.getElementById('setChannelPersona').value = s.channel_persona || '';
}

async function saveSettings(e) {
  e.preventDefault();
  const payload = {
    gemini_api_key: document.getElementById('setGeminiKey').value.trim(),
    max_parallel_workers: parseInt(document.getElementById('setMaxWorkers').value || '4', 10),
    gemini_model: document.getElementById('setGeminiModel').value.trim() || 'gemini-3.8-flash',
    gemini_image_model: document.getElementById('setGeminiImageModel').value.trim() || 'gemini-nano-banana-2.1',
    gemini_video_model: document.getElementById('setGeminiVideoModel').value.trim() || 'gemini-omni-1.1-flash',
    gemini_tts_model: document.getElementById('setGeminiTtsModel').value.trim() || 'gemini-3.8-flash-tts',
    auto_generate_trending_posts: document.getElementById('setAutoTrending').value === 'true',
    trending_niches: document.getElementById('setTrendingNiches').value.trim(),
    youtube_simulation_mode: document.getElementById('setSimulationMode').value === 'true',
    precheck_privacy_status: document.getElementById('setPrecheckPrivacy').value,
    auto_remediate_failed_videos: document.getElementById('setAutoRemediate').value === 'true',
    auto_reply_comments: document.getElementById('setAutoReply').value === 'true',
    daily_upload_limit: parseInt(document.getElementById('setDailyLimit').value || '4', 10),
    upload_spacing_hours: parseInt(document.getElementById('setSpacingHours').value || '4', 10),
    pexels_api_key: document.getElementById('setPexelsKey').value.trim(),
    pixabay_api_key: document.getElementById('setPixabayKey').value.trim(),
    telegram_bot_token: document.getElementById('setTelegramToken').value.trim(),
    agent_loop_interval_seconds: parseInt(document.getElementById('setLoopInterval').value || '300', 10),
    always_apply_copyright_safe_edit: document.getElementById('setAlwaysTransform').value === 'true',
    facebook_page_id: document.getElementById('setFbPageId').value.trim(),
    instagram_account_id: document.getElementById('setIgAccountId').value.trim(),
    facebook_access_token: document.getElementById('setFbAccessToken').value.trim(),
    tiktok_access_token: document.getElementById('setTiktokToken').value.trim(),
    omnichannel_webhook_url: document.getElementById('setWebhookUrl').value.trim(),
    channel_persona: document.getElementById('setChannelPersona').value.trim(),
  };
  await apiCall('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
  await refreshAll();
  showToast('💾 Agent configuration saved!');
}

async function authorizeYouTubeOAuth() {
  try {
    showToast('🔑 Launching YouTube OAuth 2.0 browser flow...');
    await apiCall('/api/youtube/authorize', { method: 'POST' });
    await refreshAll();
    showToast('✅ YouTube OAuth 2.0 connected!');
  } catch (err) {
    showToast(`OAuth Notice: ${err.message}`);
  }
}

async function runSingleCycle() {
  const btn = document.getElementById('runCycleBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Running Parallel Cycle...';
  showToast('⚡ Executing Parallel Autonomous Cycle (Sources + Audits + Remixes + News Studio + Publishing)...');
  try {
    await apiCall('/api/agent/run-cycle', { method: 'POST' });
    await refreshAll();
    showToast('✅ Parallel Autonomous Cycle completed!');
  } catch (err) {
    showToast(`Cycle error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = '⚡ Run Parallel Agent Cycle';
  }
}

async function toggleAutonomousLoop() {
  await apiCall('/api/agent/toggle-loop', { method: 'POST' });
  await refreshAll();
}

async function refreshAll() {
  await Promise.all([
    loadDashboardOverview(),
    loadVideos(),
    loadStudioCreations(),
    loadSources(),
    loadComments(),
  ]);
}

window.addEventListener('DOMContentLoaded', async () => {
  await refreshAll();
  await loadSettings();
  setInterval(loadDashboardOverview, 10000);
});
