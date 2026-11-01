// Base API endpoint resolver (supports subdirectories like /telegrambot/ and custom backends)
function getApiEndpoint(url) {
  if (typeof url !== 'string') return url;
  if (url.includes('/telegrambot/')) return url;
  if (!url.startsWith('/api')) return url;
  
  // If window.API_BASE_URL is explicitly configured, use it directly
  if (window.API_BASE_URL) {
    const base = window.API_BASE_URL.replace(/\/+$/, '');
    return url.replace(/^\/api/, base);
  }

  // Detect subdirectory hosting (e.g. /telegrambot/)
  const pathname = window.location.pathname;
  const match = pathname.match(/^(\/[^\/]+)\/?/);
  if (match && match[1] && !match[1].includes('.') && match[1] !== '/static') {
    return match[1] + url;
  }

  // Domain-specific safeguard for omeglevideochat.online
  if (window.location.hostname.includes('omeglevideochat.online')) {
    return '/telegrambot' + url;
  }

  return url;
}

// Global fetch interceptor to attach Bearer token, route API URLs, and handle 401
const _originalFetch = window.fetch;
window.fetch = async function(url, options = {}) {
  url = getApiEndpoint(url);
  options.headers = options.headers || {};
  const token = localStorage.getItem('auth_token');
  if (token) {
    if (options.headers instanceof Headers) {
      if (!options.headers.has('Authorization')) options.headers.set('Authorization', `Bearer ${token}`);
    } else if (Array.isArray(options.headers)) {
      options.headers.push(['Authorization', `Bearer ${token}`]);
    } else {
      if (!options.headers['Authorization']) options.headers['Authorization'] = `Bearer ${token}`;
    }
  }
  const res = await _originalFetch(url, options);
  if (res.status === 401 && typeof url === 'string' && !url.includes('/api/auth/login') && !url.includes('/api/auth/register')) {
    stopStatusPolling();
    localStorage.removeItem('auth_token');
    const wasAuthed = !!currentUser;
    currentUser = null;
    updateUserUI();
    const modal = document.getElementById('modal-auth');
    const isModalOpen = modal && !modal.classList.contains('hidden');
    // Only open auth modal if not already open (prevents resetting the registration form)
    if (!isModalOpen) {
      openAuthModal('login');
    }
  }
  return res;
};

// State variables
let currentUser = null;
let currentStatus = null;
let activeTab = 'schedule';
let logEventSource = null;
let allPosts = [];
let currentPost = null;
let scannedGroups = [];
let allAccounts = [];
let selectedAccountId = 'all';
let selectedPostIds = 'all';
let adminTargetUserId = null;
let statusPollingInterval = null;

function startStatusPolling() {
  if (statusPollingInterval) return;
  loadStatus();
  statusPollingInterval = setInterval(loadStatus, 2500);
}

function stopStatusPolling() {
  if (statusPollingInterval) {
    clearInterval(statusPollingInterval);
    statusPollingInterval = null;
  }
}

// Initialization
document.addEventListener('DOMContentLoaded', async () => {
  const authed = await checkCurrentUser();
  if (authed) {
    if (!currentUser || currentUser.role !== 'admin') {
      switchTab('schedule');
    }
    loadAllData();
    startStatusPolling();
  }
});

function loadAllData() {
  loadConfig();
  loadStatus();
  loadPosts();
  loadAccounts();
  loadGroups();
  loadLogs();
  initLogStream();
  if (currentUser && currentUser.role === 'admin' && activeTab === 'admin') {
    loadAdminData();
  }
}

// Toast notification helper
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  const colors = {
    success: 'bg-emerald-600 text-white border-emerald-500',
    error: 'bg-rose-600 text-white border-rose-500',
    warning: 'bg-amber-600 text-white border-amber-500',
    info: 'bg-blue-600 text-white border-blue-500'
  };
  const icons = {
    success: 'fa-check-circle',
    error: 'fa-circle-xmark',
    warning: 'fa-triangle-exclamation',
    info: 'fa-circle-info'
  };
  toast.className = `flex items-center gap-2.5 px-4 py-2.5 rounded-xl shadow-xl text-xs font-semibold border transition-all duration-300 transform translate-y-2 pointer-events-auto ${colors[type] || colors.info}`;
  toast.innerHTML = `<i class="fa-solid ${icons[type] || icons.info}"></i> <span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('opacity-0', '-translate-y-2');
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Tab switcher
function switchTab(tabId) {
  // Security guard: Only Super Admin can view or switch to the Admin Portal tab
  if (tabId === 'admin' && (!currentUser || currentUser.role !== 'admin')) {
    tabId = 'schedule';
  }

  activeTab = tabId;
  document.querySelectorAll('.tab-pane').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.remove('border-blue-500', 'text-blue-400', 'border-amber-500', 'text-amber-400');
    btn.classList.add('border-transparent', 'text-slate-400');
  });

  const targetContent = document.getElementById(`tab-content-${tabId}`);
  const targetBtn = document.getElementById(`tab-btn-${tabId}`);
  if (targetContent) targetContent.classList.remove('hidden');
  if (targetBtn) {
    targetBtn.classList.remove('border-transparent', 'text-slate-400');
    if (tabId === 'admin') {
      targetBtn.classList.add('border-amber-500', 'text-amber-400');
    } else {
      targetBtn.classList.add('border-blue-500', 'text-blue-400');
    }
  }

  // Ensure admin content is strictly hidden if not admin
  if (!currentUser || currentUser.role !== 'admin') {
    const adminContent = document.getElementById('tab-content-admin');
    if (adminContent) adminContent.classList.add('hidden');
  }

  if (tabId === 'admin' && currentUser && currentUser.role === 'admin') {
    loadAdminData();
  }
  if (tabId === 'logs') {
    loadLogs();
  }
}

// ----------------- STATUS & SCHEDULER -----------------

async function loadStatus() {
  if (!currentUser) return;
  try {
    const res = await fetch('/api/status');
    if (!res.ok) return;
    const data = await res.json();
    currentStatus = data;

    if (data.user) {
      currentUser = data.user;
      updateUserUI();
    }

    // Update top header status
    const statusDot = document.getElementById('status-dot');
    const statusText = document.getElementById('status-text');
    const btnStart = document.getElementById('btn-start');
    const btnStop = document.getElementById('btn-stop');

    if (data.stats && data.stats.is_broadcasting) {
      statusDot.className = 'w-2.5 h-2.5 rounded-full bg-amber-400 animate-ping';
      statusText.textContent = 'BROADCASTING NOW...';
      statusText.className = 'font-bold text-amber-400';
    } else if (data.is_running) {
      statusDot.className = 'w-2.5 h-2.5 rounded-full bg-emerald-400 pulse-dot';
      const rem = data.remaining_seconds;
      const hours = Math.floor(rem / 3600);
      const mins = Math.floor((rem % 3600) / 60);
      const secs = rem % 60;
      const timeStr = `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
      statusText.textContent = `RUNNING (Next: ${timeStr})`;
      statusText.className = 'font-semibold text-emerald-400';
      btnStart.classList.add('hidden');
      btnStop.classList.remove('hidden');
    } else {
      statusDot.className = 'w-2.5 h-2.5 rounded-full bg-slate-500';
      statusText.textContent = 'STOPPED / IDLE';
      statusText.className = 'font-semibold text-slate-400';
      btnStart.classList.remove('hidden');
      btnStop.classList.add('hidden');
    }

    // Update Stat Cards
    const val = data.interval_value;
    const unit = data.interval_unit;
    document.getElementById('stat-interval').textContent = `Every ${val} ${unit}`;
    document.getElementById('active-interval-badge').textContent = `${val} ${unit}`;

    if (data.is_running) {
      const rem = data.remaining_seconds;
      const hours = Math.floor(rem / 3600);
      const mins = Math.floor((rem % 3600) / 60);
      const secs = rem % 60;
      document.getElementById('stat-countdown').textContent = `Next in: ${hours}h ${mins}m ${secs}s`;
    } else {
      document.getElementById('stat-countdown').textContent = 'Idle (Not started)';
    }

    if (selectedAccountId && selectedAccountId !== 'all' && allAccounts && allAccounts.length > 0) {
      const match = allAccounts.find(a => (a.id === selectedAccountId || String(a.user_id) === String(selectedAccountId) || a.phone === selectedAccountId));
      if (match) {
        document.getElementById('stat-accounts').textContent = `1 Active (${match.first_name || 'User'})`;
      } else {
        document.getElementById('stat-accounts').textContent = `${data.active_accounts} / ${data.total_accounts} Active`;
      }
    } else {
      document.getElementById('stat-accounts').textContent = `${data.active_accounts} / ${data.total_accounts} Active`;
    }
    document.getElementById('stat-groups').textContent = `${data.total_groups} Groups`;
    document.getElementById('tab-acc-count').textContent = data.total_accounts;
    document.getElementById('tab-grp-count').textContent = data.total_groups;

    if (data.stats) {
      document.getElementById('stat-stats').textContent = `${data.stats.total_sent} / ${data.stats.total_failed}`;
      document.getElementById('stat-lastrun').textContent = data.stats.last_run_time ? `Last: ${data.stats.last_run_time}` : 'Never run';
    }
  } catch (e) {
    console.error('Status fetch error:', e);
  }
}

async function startAutomation() {
  try {
    const res = await fetch('/api/scheduler/start', { method: 'POST' });
    const data = await res.json();
    showToast('Automation scheduler started!', 'success');
    loadStatus();
  } catch (e) {
    showToast('Failed to start scheduler', 'error');
  }
}

async function stopAutomation() {
  try {
    const res = await fetch('/api/scheduler/stop', { method: 'POST' });
    const data = await res.json();
    showToast('Automation stopped', 'warning');
    loadStatus();
  } catch (e) {
    showToast('Failed to stop scheduler', 'error');
  }
}

async function triggerPostNow() {
  try {
    const res = await fetch('/api/scheduler/post_now', { method: 'POST' });
    const data = await res.json();
    showToast('Manual broadcast triggered! Check live logs.', 'info');
    loadStatus();
  } catch (e) {
    showToast('Failed to trigger broadcast', 'error');
  }
}

// ----------------- CONFIG & TIMING -----------------

async function loadConfig() {
  try {
    const res = await fetch('/api/config');
    const cfg = await res.json();
    document.getElementById('cfg-interval-val').value = cfg.interval_value || 2;
    document.getElementById('cfg-interval-unit').value = cfg.interval_unit || 'hours';
    document.getElementById('cfg-delay-min').value = cfg.random_delay_min || 10;
    document.getElementById('cfg-delay-max').value = cfg.random_delay_max || 25;
    document.getElementById('cfg-rotation').value = cfg.account_rotation || 'round_robin';
    if (document.getElementById('cfg-post-rotation')) {
      document.getElementById('cfg-post-rotation').value = cfg.post_rotation_mode || 'random';
    }
    if (document.getElementById('cfg-post-scope')) {
      document.getElementById('cfg-post-scope').value = cfg.post_selection_scope || 'per_group';
    }
    if (cfg.selected_account) {
      selectedAccountId = cfg.selected_account;
    }
    if (cfg.selected_posts) {
      selectedPostIds = cfg.selected_posts;
    }
    document.getElementById('cfg-autojoin').checked = cfg.auto_join_groups !== false;
    document.getElementById('delay-display').textContent = `${cfg.random_delay_min}s - ${cfg.random_delay_max}s`;
    highlightPresetButton(cfg.interval_value);

    // If accounts already loaded, update selector selection
    if (allAccounts && allAccounts.length > 0) {
      populateAccountSelector(allAccounts);
    }
    renderSchedulePostsChecklist();
  } catch (e) {
    console.error('Config load error:', e);
  }
}

function populateAccountSelector(accounts) {
  const sel = document.getElementById('cfg-selected-account');
  const chipsContainer = document.getElementById('account-selection-chips');
  if (!sel) return;

  const currentVal = selectedAccountId || 'all';
  sel.innerHTML = '<option value="all">🔄 All Connected Accounts (Automatic Rotation / Fair Round-Robin)</option>';

  if (chipsContainer) {
    chipsContainer.innerHTML = '';
    // All Accounts Chip
    const allChip = document.createElement('button');
    allChip.type = 'button';
    allChip.className = `account-chip px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition ${currentVal === 'all' ? 'bg-blue-600/20 border-blue-500 text-blue-300 shadow-sm' : 'bg-slate-900 border-slate-800 text-slate-400 hover:text-white'}`;
    allChip.innerHTML = '<i class="fa-solid fa-arrows-rotate text-[10px]"></i> All Accounts (Rotate)';
    allChip.onclick = () => selectPostingAccount('all');
    chipsContainer.appendChild(allChip);
  }

  if (accounts && accounts.length > 0) {
    accounts.forEach(acc => {
      const opt = document.createElement('option');
      const accId = acc.id || acc.user_id;
      opt.value = accId;
      const name = `${acc.first_name || ''} ${acc.last_name || ''}`.trim() || 'Telegram User';
      const u = acc.username ? `@${acc.username}` : (acc.phone || '');
      const prem = acc.is_premium ? ' ⭐' : '';
      opt.textContent = `👤 ${name} (${u})${prem}`;
      sel.appendChild(opt);

      if (chipsContainer) {
        const chip = document.createElement('button');
        chip.type = 'button';
        const isSelected = String(currentVal) === String(accId);
        chip.className = `account-chip px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition ${isSelected ? 'bg-emerald-600/20 border-emerald-500 text-emerald-300 shadow-sm' : 'bg-slate-900 border-slate-800 text-slate-400 hover:text-white'}`;
        chip.innerHTML = `<i class="fa-solid fa-user text-[10px]"></i> ${name} <span class="text-[10px] text-slate-500 font-mono">(${u})</span>`;
        chip.onclick = () => selectPostingAccount(accId);
        chipsContainer.appendChild(chip);
      }
    });
  }

  // Restore selection
  if (currentVal && Array.from(sel.options).some(o => o.value === String(currentVal))) {
    sel.value = currentVal;
  } else {
    sel.value = 'all';
  }

  updateSelectedAccountUI();
}

function selectPostingAccount(accId) {
  const sel = document.getElementById('cfg-selected-account');
  if (sel) {
    sel.value = accId;
    selectedAccountId = accId;
    updateSelectedAccountUI();
    saveScheduleConfig();
  }
}

function onAccountSelectChange() {
  const sel = document.getElementById('cfg-selected-account');
  if (sel) {
    selectedAccountId = sel.value;
  }
  updateSelectedAccountUI();
  saveScheduleConfig();
}

function updateSelectedAccountUI() {
  const sel = document.getElementById('cfg-selected-account');
  if (!sel) return;
  const val = sel.value;
  selectedAccountId = val;

  const badgeText = document.getElementById('selected-account-badge-text');
  const badge = document.getElementById('selected-account-badge');
  const desc = document.getElementById('selected-account-desc');

  if (val === 'all') {
    if (badgeText) badgeText.textContent = 'All Accounts (Auto-Rotate)';
    if (badge) badge.className = 'px-3.5 py-1.5 rounded-full bg-blue-500/10 text-blue-400 text-xs font-semibold border border-blue-500/20 w-fit flex items-center gap-1.5';
    if (desc) desc.textContent = 'Posting will cycle automatically between all active connected Telegram accounts.';
  } else {
    const acc = allAccounts.find(a => (a.id === val || String(a.user_id) === String(val) || a.phone === val));
    const name = acc ? `${acc.first_name || ''} ${acc.last_name || ''}`.trim() : 'Selected Account';
    const u = acc && acc.username ? `@${acc.username}` : (acc ? acc.phone : '');
    
    if (badgeText) badgeText.textContent = `Locked: ${name}`;
    if (badge) badge.className = 'px-3.5 py-1.5 rounded-full bg-emerald-500/10 text-emerald-300 text-xs font-semibold border border-emerald-500/20 w-fit flex items-center gap-1.5';
    if (desc) desc.innerHTML = `Strictly posting via <strong class="text-white">${name}</strong> (${u}). All broadcasts will originate from this account.`;
  }

  // Highlight active chip
  const chipsContainer = document.getElementById('account-selection-chips');
  if (chipsContainer && allAccounts) {
    chipsContainer.childNodes.forEach((btn, idx) => {
      if (idx === 0) {
        if (val === 'all') {
          btn.className = 'account-chip px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition bg-blue-600/20 border-blue-500 text-blue-300 shadow-sm';
        } else {
          btn.className = 'account-chip px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition bg-slate-900 border-slate-800 text-slate-400 hover:text-white';
        }
      } else {
        const acc = allAccounts[idx - 1];
        if (acc && String(acc.id || acc.user_id) === String(val)) {
          btn.className = 'account-chip px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition bg-emerald-600/20 border-emerald-500 text-emerald-300 shadow-sm';
        } else {
          btn.className = 'account-chip px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition bg-slate-900 border-slate-800 text-slate-400 hover:text-white';
        }
      }
    });
  }
  renderSchedulePostsChecklist();
}

function setPresetInterval(value, unit, label) {
  document.getElementById('cfg-interval-val').value = value;
  document.getElementById('cfg-interval-unit').value = unit;
  highlightPresetButton(value);
  saveScheduleConfig();
}

function highlightPresetButton(val) {
  document.querySelectorAll('.preset-btn').forEach(b => {
    if (parseFloat(b.dataset.val) === parseFloat(val)) {
      b.className = 'preset-btn px-3 py-2.5 rounded-lg border border-blue-500 bg-blue-600/20 text-blue-300 text-xs font-semibold transition text-center';
    } else {
      b.className = 'preset-btn px-3 py-2.5 rounded-lg border border-slate-800 bg-slate-800/50 hover:bg-slate-800 text-xs font-semibold transition text-center text-slate-300';
    }
  });
}

async function saveScheduleConfig() {
  const selectedAcc = document.getElementById('cfg-selected-account') ? document.getElementById('cfg-selected-account').value : (selectedAccountId || 'all');
  const payload = {
    interval_value: parseFloat(document.getElementById('cfg-interval-val').value) || 2,
    interval_unit: document.getElementById('cfg-interval-unit').value,
    random_delay_min: parseInt(document.getElementById('cfg-delay-min').value) || 10,
    random_delay_max: parseInt(document.getElementById('cfg-delay-max').value) || 25,
    auto_join_groups: document.getElementById('cfg-autojoin').checked,
    account_rotation: document.getElementById('cfg-rotation').value,
    selected_account: selectedAcc,
    selected_posts: selectedPostIds,
    post_rotation_mode: document.getElementById('cfg-post-rotation') ? document.getElementById('cfg-post-rotation').value : 'random',
    post_selection_scope: document.getElementById('cfg-post-scope') ? document.getElementById('cfg-post-scope').value : 'per_group'
  };

  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    let accDesc = 'All Accounts (Auto-Rotate)';
    if (selectedAcc !== 'all') {
      const match = allAccounts.find(a => (a.id === selectedAcc || String(a.user_id) === String(selectedAcc) || a.phone === selectedAcc));
      if (match) accDesc = `${match.first_name || ''} (@${match.username || match.phone})`;
    }
    showToast(`Saved! Account: ${accDesc} | Interval: ${payload.interval_value} ${payload.interval_unit}`, 'success');
    document.getElementById('delay-display').textContent = `${payload.random_delay_min}s - ${payload.random_delay_max}s`;
    loadStatus();
  } catch (e) {
    showToast('Failed to save config', 'error');
  }
}

function renderSchedulePostsChecklist() {
  const container = document.getElementById('schedule-posts-checklist');
  if (!container) return;

  if (!allPosts || !allPosts.length) {
    container.innerHTML = '<p class="text-xs text-slate-500 col-span-3">No posts configured yet in Post Studio.</p>';
    return;
  }

  container.innerHTML = allPosts.map(p => {
    const isChecked = selectedPostIds === 'all' || (Array.isArray(selectedPostIds) && selectedPostIds.includes(p.id));

    // Account tag
    let accountBadge = '<span class="text-[10px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700">🌐 All Accounts</span>';
    if (p.assigned_account && p.assigned_account !== 'all') {
      const match = allAccounts.find(a => (a.id === p.assigned_account || String(a.user_id) === String(p.assigned_account) || a.phone === p.assigned_account));
      const accName = match ? (match.first_name ? match.first_name.split(' ')[0] : 'Account') : 'Account';
      accountBadge = `<span class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">👤 ${accName}</span>`;
    }

    const emojiCount = p.custom_emojis_count ? `<span class="text-[9px] px-1 rounded bg-cyan-500/20 text-cyan-300 font-mono">${p.custom_emojis_count} 3D</span>` : '';

    return `
      <label class="schedule-post-item flex items-start gap-3 p-3 rounded-lg border transition cursor-pointer ${isChecked ? 'bg-slate-900 border-blue-500/50 shadow-sm' : 'bg-slate-950/60 border-slate-800/80 text-slate-500 opacity-60'}">
        <input type="checkbox" value="${p.id}" ${isChecked ? 'checked' : ''} onchange="onSchedulePostCheckboxChange()" class="schedule-post-chk w-4 h-4 rounded text-blue-600 bg-slate-800 border-slate-700 mt-0.5">
        <div class="flex-1 min-w-0">
          <div class="flex items-center justify-between gap-1 mb-1">
            <p class="text-xs font-bold text-white truncate">${p.title || p.id}</p>
            ${emojiCount}
          </div>
          <div class="flex items-center justify-between gap-2 mt-1">
            ${accountBadge}
            <span class="text-[10px] text-slate-500">Times: ${p.times_posted || 0}</span>
          </div>
        </div>
      </label>
    `;
  }).join('');

  updateSchedulePostsBadge();
}

function onSchedulePostCheckboxChange() {
  const chks = document.querySelectorAll('.schedule-post-chk');
  const checked = Array.from(chks).filter(c => c.checked).map(c => c.value);
  if (checked.length === chks.length) {
    selectedPostIds = 'all';
  } else {
    selectedPostIds = checked;
  }
  updateSchedulePostsBadge();
  saveScheduleConfig();
}

function selectAllSchedulePosts(select) {
  const chks = document.querySelectorAll('.schedule-post-chk');
  chks.forEach(c => c.checked = select);
  if (select) {
    selectedPostIds = 'all';
  } else {
    selectedPostIds = [];
  }
  updateSchedulePostsBadge();
  saveScheduleConfig();
}

function updateSchedulePostsBadge() {
  const badgeText = document.getElementById('selected-posts-count-text');
  const badge = document.getElementById('selected-posts-badge');
  const chks = document.querySelectorAll('.schedule-post-chk');
  const checkedCount = Array.from(chks).filter(c => c.checked).length;
  const total = allPosts ? allPosts.length : 0;

  if (!badgeText || !badge) return;

  if (checkedCount === total || selectedPostIds === 'all') {
    badgeText.textContent = `All ${total} Posts Active`;
    badge.className = 'px-3.5 py-1.5 rounded-full bg-blue-500/10 text-blue-400 text-xs font-semibold border border-blue-500/20 w-fit flex items-center gap-1.5';
  } else if (checkedCount === 0) {
    badgeText.textContent = '0 Posts Selected (Warning)';
    badge.className = 'px-3.5 py-1.5 rounded-full bg-rose-500/10 text-rose-400 text-xs font-semibold border border-rose-500/20 w-fit flex items-center gap-1.5';
  } else {
    badgeText.textContent = `${checkedCount} of ${total} Posts Selected`;
    badge.className = 'px-3.5 py-1.5 rounded-full bg-emerald-500/10 text-emerald-300 text-xs font-semibold border border-emerald-500/20 w-fit flex items-center gap-1.5';
  }
}

// ----------------- MULTI-POST CAMPAIGNS & 3D EMOJIS -----------------

async function loadPosts() {
  try {
    const res = await fetch('/api/posts');
    const posts = await res.json();
    allPosts = posts;

    const container = document.getElementById('post-pills-container');
    if (!container) return;

    // Update posts count badge
    const badgeEl = document.getElementById('posts-count-badge');
    if (badgeEl) {
      const activeCount = posts.filter(x => x.is_active !== false).length;
      badgeEl.textContent = `${posts.length} Posts (${activeCount} Active)`;
    }

    if (!posts.length) {
      container.innerHTML = '<span class="text-xs text-slate-500 py-2">No posts. Click "+ Add New Post" above to create one.</span>';
      return;
    }

    container.innerHTML = posts.map(p => {
      const isSel = currentPost && currentPost.id === p.id;
      const activeDot = p.is_active !== false 
        ? '<span class="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0" title="Active in rotation"></span>'
        : '<span class="w-2 h-2 rounded-full bg-slate-500 flex-shrink-0" title="Paused"></span>';
      
      const badge = p.times_posted ? `<span class="text-[10px] text-slate-400 font-mono flex-shrink-0">(${p.times_posted})</span>` : '';
      const customEmojiTag = p.custom_emojis_count ? `<span class="text-[9px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 font-mono flex-shrink-0">${p.custom_emojis_count} 3D</span>` : '';

      let accTag = '';
      if (p.assigned_account && p.assigned_account !== 'all') {
        const m = allAccounts.find(a => (a.id === p.assigned_account || String(a.user_id) === String(p.assigned_account) || a.phone === p.assigned_account));
        const shortName = m ? (m.first_name ? m.first_name.split(' ')[0] : 'Account') : 'Account';
        accTag = `<span class="text-[9px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300 font-medium flex-shrink-0" title="Assigned exclusively to ${m ? m.first_name : ''}">👤 ${shortName}</span>`;
      }

      const mediaIndicator = p.has_media ? '<span class="text-amber-400 text-[10px] flex-shrink-0" title="Has attached media"><i class="fa-regular fa-image"></i></span>' : '';

      const btnClasses = isSel 
        ? 'px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition cursor-pointer max-w-full border-blue-500 bg-blue-600/20 text-blue-300 shadow-sm shadow-blue-500/20 ring-1 ring-blue-500/40' 
        : 'px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition cursor-pointer max-w-full border-slate-800 bg-slate-800/60 text-slate-400 hover:text-white hover:border-slate-700 hover:bg-slate-800';

      return `
        <button type="button" onclick="selectPost('${p.id}')" class="${btnClasses}" title="${p.title || p.id}">
          ${activeDot}
          <span class="truncate max-w-[130px] sm:max-w-[200px] text-left">${p.title || p.id}</span>
          ${mediaIndicator}
          ${accTag}
          ${customEmojiTag}
          ${badge}
        </button>
      `;
    }).join('');

    populatePostAccountAssignOptions();
    renderSchedulePostsChecklist();

    if (!currentPost || !posts.some(p => p.id === currentPost.id)) {
      selectPost(posts[0].id);
    } else {
      selectPost(currentPost.id);
    }
  } catch (e) {
    console.error('Posts load error:', e);
  }
}

function populatePostAccountAssignOptions() {
  const sel = document.getElementById('post-assigned-account');
  if (!sel) return;
  const cur = currentPost && currentPost.assigned_account ? currentPost.assigned_account : 'all';
  sel.innerHTML = '<option value="all">🌐 All Accounts (Shared)</option>';
  if (allAccounts && allAccounts.length > 0) {
    allAccounts.forEach(acc => {
      const opt = document.createElement('option');
      const accId = acc.id || acc.user_id;
      opt.value = accId;
      const name = `${acc.first_name || ''} ${acc.last_name || ''}`.trim() || 'Telegram User';
      const u = acc.username ? `@${acc.username}` : (acc.phone || '');
      opt.textContent = `👤 ${name} (${u})`;
      sel.appendChild(opt);
    });
  }
  sel.value = cur;
}

function onPostAccountAssignChange() {
  if (currentPost) {
    const sel = document.getElementById('post-assigned-account');
    if (sel) {
      currentPost.assigned_account = sel.value;
      renderSchedulePostsChecklist();
      loadPostsPillStyles();
    }
  }
}

function selectPost(postId) {
  const p = allPosts.find(x => x.id === postId);
  if (!p) return;
  currentPost = p;

  // Re-render pills to reflect active outline
  loadPostsPillStyles();

  // Populate editor fields
  document.getElementById('post-title-input').value = p.title || p.id;
  document.getElementById('post-active-toggle').checked = p.is_active !== false;
  populatePostAccountAssignOptions();
  if (document.getElementById('post-assigned-account')) {
    document.getElementById('post-assigned-account').value = p.assigned_account || 'all';
  }
  document.getElementById('post-html').value = p.html_content || '';
  
  // Update Delete button state
  const btnDelete = document.getElementById('btn-delete-post');
  if (btnDelete) {
    btnDelete.style.display = allPosts.length > 1 ? 'block' : 'none';
  }

  updatePreview();

  const container = document.getElementById('post-pills-container');
  if (container) {
    const activeBtn = container.querySelector(`button[onclick*="${postId}"]`);
    if (activeBtn && activeBtn.scrollIntoView) {
      activeBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    }
  }
}

function loadPostsPillStyles() {
  const container = document.getElementById('post-pills-container');
  if (!container) return;
  const buttons = container.querySelectorAll('button');
  allPosts.forEach((p, idx) => {
    if (buttons[idx]) {
      const isSel = currentPost && currentPost.id === p.id;
      if (isSel) {
        buttons[idx].className = 'group px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition cursor-pointer max-w-full border-blue-500 bg-blue-600/20 text-blue-300 shadow-sm shadow-blue-500/20 ring-1 ring-blue-500/40';
      } else {
        buttons[idx].className = 'group px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition cursor-pointer max-w-full border-slate-800 bg-slate-800/60 text-slate-400 hover:text-white hover:border-slate-700 hover:bg-slate-800';
      }
    }
  });
}

async function createNewPost() {
  const newNum = allPosts.length + 1;
  const newPostData = {
    id: `post_${Date.now()}`,
    title: `Post #${newNum} (Campaign)`,
    html_content: `🔥 <b>Announcement #${newNum}!</b>\nCheck out our latest update 🚀\n<i>Powered by Telegram AutoPost</i>`,
    has_media: false,
    is_active: true,
    assigned_account: selectedAccountId !== 'all' ? selectedAccountId : 'all'
  };

  try {
    const res = await fetch('/api/posts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newPostData)
    });
    const data = await res.json();
    await loadPosts();
    selectPost(data.post.id);
    showToast(`Created ${newPostData.title}!`, 'success');
  } catch (e) {
    showToast('Failed to create new post', 'error');
  }
}

async function saveCurrentPost() {
  if (!currentPost) return;
  const title = document.getElementById('post-title-input').value.trim() || currentPost.id;
  const html = document.getElementById('post-html').value;
  const isActive = document.getElementById('post-active-toggle').checked;
  const assignedAccount = document.getElementById('post-assigned-account') ? document.getElementById('post-assigned-account').value : (currentPost.assigned_account || 'all');

  const payload = {
    ...currentPost,
    title: title,
    html_content: html,
    is_active: isActive,
    assigned_account: assignedAccount
  };

  try {
    const res = await fetch('/api/posts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    currentPost = data.post;
    await loadPosts();
    showToast(`Saved '${title}' successfully!`, 'success');
  } catch (e) {
    showToast('Failed to save post', 'error');
  }
}

async function deleteCurrentPost() {
  if (!currentPost) return;
  if (allPosts.length <= 1) {
    showToast('You must have at least one campaign post in the pool', 'warning');
    return;
  }

  if (!confirm(`Are you sure you want to delete '${currentPost.title || currentPost.id}'?`)) return;

  try {
    const res = await fetch(`/api/posts/${currentPost.id}`, { method: 'DELETE' });
    const data = await res.json();
    showToast('Post removed from campaign', 'info');
    currentPost = null;
    await loadPosts();
  } catch (e) {
    showToast('Failed to delete post', 'error');
  }
}

async function toggleCurrentPostActive() {
  if (!currentPost) return;
  try {
    const res = await fetch(`/api/posts/${currentPost.id}/toggle`, { method: 'POST' });
    const data = await res.json();
    currentPost.is_active = data.is_active;
    await loadPosts();
    showToast(`Post is now ${data.is_active ? 'ACTIVE' : 'PAUSED'} in rotation`, 'info');
  } catch (e) {
    showToast('Failed to toggle active state', 'error');
  }
}

function updatePreview() {
  const html = document.getElementById('post-html').value;
  const previewText = document.getElementById('preview-text');

  // Convert <tg-emoji emoji-id="123">🔥</tg-emoji> to highlighted animated emoji pill in preview
  let formatted = html.replace(/<tg-emoji emoji-id="([^"]+)">([^<]+)<\/tg-emoji>/g, (match, id, char) => {
    return `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/40 text-xs font-mono" title="3D Animated Custom Emoji (ID: ${id})">${char} <span class="text-[9px] opacity-75 font-sans">3D</span></span>`;
  });

  previewText.innerHTML = formatted || '<span class="text-slate-500 italic">No content</span>';

  // Count 3D emojis
  const matches = html.match(/<tg-emoji/g);
  const count = matches ? matches.length : 0;
  document.getElementById('badge-emoji-count').textContent = `${count} 3D Emojis Detected`;

  // Media preview
  const mediaContainer = document.getElementById('preview-media-container');
  const mediaImg = document.getElementById('preview-media-img');
  const mediaStatus = document.getElementById('media-status-text');
  const btnRemove = document.getElementById('btn-remove-media');

  if (currentPost && currentPost.has_media && (currentPost.media_path || currentPost.media_filename || currentPost.media_url)) {
    mediaContainer.classList.remove('hidden');

    // Extract disk filename (prioritizing media_filename, then base of media_path)
    let diskFile = '';
    if (currentPost.media_filename) {
      diskFile = currentPost.media_filename;
    } else if (currentPost.media_path) {
      diskFile = currentPost.media_path.split('\\').pop().split('/').pop();
    }

    const mediaPrefix = window.API_BASE_URL 
      ? window.API_BASE_URL.replace(/\/api\/?$/, '') 
      : (getApiEndpoint('/api').replace(/\/api\/?$/, ''));

    let imgSrc = '';
    if (currentPost.media_url) {
      imgSrc = currentPost.media_url.startsWith('http')
        ? currentPost.media_url
        : `${mediaPrefix}${currentPost.media_url.startsWith('/') ? '' : '/'}${currentPost.media_url}`;
    } else if (diskFile) {
      imgSrc = `${mediaPrefix}/media/${diskFile}`;
    }

    mediaImg.src = imgSrc;
    mediaImg.onerror = function() {
      // Fallback: try proxying via /api/media/ if /media/ is not directly reachable
      if (diskFile && !this.src.includes('/api/media/')) {
        this.src = `${mediaPrefix}/api/media/${diskFile}`;
      }
    };

    const displayName = currentPost.media_name || diskFile || 'Media attachment';
    mediaStatus.textContent = `Attached: ${displayName}`;
    btnRemove.classList.remove('hidden');
  } else {
    mediaContainer.classList.add('hidden');
    mediaImg.src = '';
    mediaStatus.textContent = 'No media attached';
    btnRemove.classList.add('hidden');
  }

  // Preview Time
  const now = new Date();
  document.getElementById('preview-time').textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function insertTag(tag) {
  const textarea = document.getElementById('post-html');
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = textarea.value.substring(start, end) || 'text';
  const replacement = `<${tag}>${selected}</${tag}>`;
  textarea.value = textarea.value.substring(0, start) + replacement + textarea.value.substring(end);
  textarea.focus();
  textarea.setSelectionRange(start + tag.length + 2, start + tag.length + 2 + selected.length);
  updatePreview();
}

function insertLink() {
  const url = prompt('Enter URL (e.g. https://example.com):', 'https://');
  if (!url) return;
  const textarea = document.getElementById('post-html');
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = textarea.value.substring(start, end) || 'Link Text';
  const replacement = `<a href="${url}">${selected}</a>`;
  textarea.value = textarea.value.substring(0, start) + replacement + textarea.value.substring(end);
  textarea.focus();
  updatePreview();
}

function insertCustomEmojiTag() {
  const docId = prompt('Enter Telegram Custom 3D Emoji Document ID (64-bit integer, e.g. 5368324170671202286):', '5368324170671202286');
  if (!docId) return;
  const char = prompt('Enter fallback character or emoji (e.g. 🔥 or 🚀):', '🔥') || '🔥';
  const tag = `<tg-emoji emoji-id="${docId}">${char}</tg-emoji>`;
  insertChar(tag);
}

function insertChar(char) {
  const textarea = document.getElementById('post-html');
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  textarea.value = textarea.value.substring(0, start) + char + textarea.value.substring(end);
  textarea.focus();
  textarea.setSelectionRange(start + char.length, start + char.length);
  updatePreview();
}

async function fetchPostFromTelegram() {
  const sourceChat = document.getElementById('fetch-chat').value.trim();
  const msgIdVal = document.getElementById('fetch-msgid').value.trim();
  if (!sourceChat) {
    showToast('Please specify a source chat (e.g. "me" or @channel)', 'warning');
    return;
  }

  const btn = document.getElementById('btn-fetch');
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Fetching...';

  try {
    const res = await fetch('/api/posts/fetch_telegram', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_chat: sourceChat,
        message_id: msgIdVal ? parseInt(msgIdVal) : null,
        target_post_id: currentPost ? currentPost.id : null
      })
    });
    const data = await res.json();
    if (data.status === 'success') {
      currentPost = data.post;
      await loadPosts();
      selectPost(data.post.id);
      showToast(`Post fetched! ${data.post.custom_emojis_count} 3D emojis preserved!`, 'success');
    } else {
      showToast(data.message || 'Failed to fetch post', 'error');
    }
  } catch (e) {
    showToast('Network error fetching post', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-cloud-arrow-down"></i> Fetch Post & 3D Emojis';
  }
}

async function uploadMediaFile(input) {
  if (!input.files || !input.files[0] || !currentPost) return;
  const file = input.files[0];
  const formData = new FormData();
  formData.append('file', file);

  const statusText = document.getElementById('media-status-text');
  if (statusText) {
    statusText.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-blue-400"></i> Uploading media...';
  }

  try {
    const uploadUrl = getApiEndpoint(`/api/posts/${currentPost.id}/upload_media`);
    const token = localStorage.getItem('auth_token');
    const headers = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const res = await window.fetch(uploadUrl, {
      method: 'POST',
      headers: headers,
      body: formData
    });

    const data = await res.json();
    if (res.ok && data.status === 'success') {
      currentPost = data.post;
      const idx = allPosts.findIndex(p => p.id === currentPost.id);
      if (idx !== -1) allPosts[idx] = currentPost;
      await loadPosts();
      updatePreview();
      showToast('Media uploaded and attached to this post!', 'success');
    } else {
      showToast(data.detail || data.message || 'Failed to upload media', 'error');
      updatePreview();
    }
  } catch (e) {
    console.error('Upload media error:', e);
    showToast('Failed to upload media. Please check server connection.', 'error');
    updatePreview();
  } finally {
    input.value = '';
  }
}

async function removeMedia() {
  if (!currentPost) return;
  try {
    const removeUrl = getApiEndpoint(`/api/posts/${currentPost.id}/media`);
    const token = localStorage.getItem('auth_token');
    const headers = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const res = await window.fetch(removeUrl, {
      method: 'DELETE',
      headers: headers
    });

    const data = await res.json();
    if (res.ok && data.status === 'success') {
      currentPost = data.post;
      const idx = allPosts.findIndex(p => p.id === currentPost.id);
      if (idx !== -1) allPosts[idx] = currentPost;
      await loadPosts();
      updatePreview();
      showToast('Media attachment removed', 'info');
    } else {
      showToast(data.detail || data.message || 'Failed to remove media', 'error');
    }
  } catch (e) {
    console.error('Remove media error:', e);
    showToast('Failed to remove media', 'error');
  }
}

// ----------------- AUTO-FETCH GROUPS FROM TELEGRAM ACCOUNT -----------------

async function openFetchGroupsModal() {
  const select = document.getElementById('fetch-group-account-select');
  select.innerHTML = '<option value="">Loading accounts...</option>';

  try {
    const res = await fetch('/api/accounts');
    const accounts = await res.json();
    if (!accounts.length) {
      select.innerHTML = '<option value="">No accounts connected. Add an account first.</option>';
    } else {
      select.innerHTML = accounts.map(a => {
        return `<option value="${a.id}">${a.first_name || 'Account'} (${a.phone})</option>`;
      }).join('');
    }
  } catch (e) {
    select.innerHTML = '<option value="">Failed to load accounts</option>';
  }

  document.getElementById('scanned-groups-container').classList.add('hidden');
  document.getElementById('modal-fetch-groups').classList.remove('hidden');
  document.getElementById('modal-fetch-groups').classList.add('flex');
}

function closeFetchGroupsModal() {
  document.getElementById('modal-fetch-groups').classList.add('hidden');
  document.getElementById('modal-fetch-groups').classList.remove('flex');
}

async function startGroupScan() {
  const accountId = document.getElementById('fetch-group-account-select').value;
  if (!accountId) {
    showToast('Please select a Telegram account to scan', 'warning');
    return;
  }

  const btn = document.getElementById('btn-scan-groups');
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Scanning dialogs...';

  try {
    const res = await fetch(`/api/accounts/${accountId}/fetch_groups`);
    const data = await res.json();
    if (data.status === 'success') {
      scannedGroups = data.groups || [];
      document.getElementById('scanned-count').textContent = scannedGroups.length;
      
      const listEl = document.getElementById('scanned-groups-list');
      if (!scannedGroups.length) {
        listEl.innerHTML = '<p class="text-slate-500 text-center py-4">No groups found on this account.</p>';
      } else {
        listEl.innerHTML = scannedGroups.map((g, idx) => {
          const members = g.participants_count ? `<span class="text-slate-500 text-[10px] font-mono">(${g.participants_count} members)</span>` : '';
          return `
            <label class="flex items-center justify-between p-2 rounded bg-slate-900 border border-slate-800 hover:border-slate-700 cursor-pointer">
              <div class="flex items-center gap-2.5 overflow-hidden">
                <input type="checkbox" class="scanned-group-chk w-3.5 h-3.5 rounded text-blue-600 bg-slate-800 border-slate-700" value="${g.target}" checked>
                <div class="truncate">
                  <p class="font-semibold text-white text-xs truncate">${g.title}</p>
                  <p class="text-[10px] text-slate-400 font-mono">${g.target}</p>
                </div>
              </div>
              <div class="flex items-center gap-2">
                ${members}
                <span class="text-[9px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-300 border border-blue-500/20">${g.is_megagroup ? 'Supergroup' : 'Group'}</span>
              </div>
            </label>
          `;
        }).join('');
      }

      document.getElementById('scanned-groups-container').classList.remove('hidden');
      showToast(`Scan complete: Found ${scannedGroups.length} groups!`, 'success');
    } else {
      showToast(data.message || 'Scan failed', 'error');
    }
  } catch (e) {
    showToast('Network error scanning groups', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> Scan Groups';
  }
}

function toggleSelectAllScanned(selectAll) {
  document.querySelectorAll('.scanned-group-chk').forEach(chk => {
    chk.checked = selectAll;
  });
}

async function importSelectedScannedGroups() {
  const accountId = document.getElementById('fetch-group-account-select').value;
  const checkboxes = document.querySelectorAll('.scanned-group-chk:checked');
  const selectedTargets = Array.from(checkboxes).map(c => c.value);

  if (!selectedTargets.length) {
    showToast('Please select at least one group to import', 'warning');
    return;
  }

  const btn = document.getElementById('btn-import-scanned');
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Importing...';

  try {
    const res = await fetch(`/api/accounts/${accountId}/import_groups`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ targets: selectedTargets })
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast(`Imported ${data.added} new groups! Total targets: ${data.total}`, 'success');
      closeFetchGroupsModal();
      loadGroups();
      loadStatus();
    } else {
      showToast(data.message || 'Import failed', 'error');
    }
  } catch (e) {
    showToast('Network error importing groups', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-file-import"></i> Add Selected Groups to Target List';
  }
}

// ----------------- ACCOUNTS & AUTH -----------------

async function loadAccounts() {
  try {
    const res = await fetch('/api/accounts');
    const accounts = await res.json();
    allAccounts = accounts;
    populateAccountSelector(accounts);
    populatePostAccountAssignOptions();
    renderSchedulePostsChecklist();
    const tbody = document.getElementById('accounts-table-body');

    if (!accounts.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-slate-500">No accounts connected yet. Click "Connect Telegram Account" to add one.</td></tr>`;
      return;
    }

    tbody.innerHTML = accounts.map(a => {
      const isPrem = a.is_premium;
      const isActive = a.is_active;
      return `
        <tr class="hover:bg-slate-900/60 transition">
          <td class="py-3 px-4">
            <div class="flex items-center gap-2.5">
              <div class="w-8 h-8 rounded-full bg-blue-600/20 border border-blue-500/30 flex items-center justify-center text-blue-400 font-bold text-xs">
                ${(a.first_name || 'U')[0]}
              </div>
              <div>
                <p class="font-semibold text-white text-xs">${a.first_name || 'Telegram User'} ${a.last_name || ''}</p>
                <p class="text-[11px] text-slate-400">${a.phone} ${a.username ? '(@' + a.username + ')' : ''}</p>
              </div>
            </div>
          </td>
          <td class="py-3 px-4 font-mono text-[11px] text-slate-400">
            ${a.api_id} / ${a.api_hash_masked || '******'}
          </td>
          <td class="py-3 px-4">
            ${isPrem 
              ? '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 text-[10px] font-bold border border-amber-500/30"><i class="fa-solid fa-star"></i> Premium (3D Emojis)</span>'
              : '<span class="text-slate-500 text-[11px]">Standard</span>'}
          </td>
          <td class="py-3 px-4">
            <span class="px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 text-[10px] font-semibold border border-emerald-500/30">
              Ready
            </span>
          </td>
          <td class="py-3 px-4 text-center">
            <button onclick="toggleAccount('${a.id}')" class="px-2.5 py-1 rounded text-[11px] font-semibold transition ${isActive ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-800 text-slate-400 border border-slate-700'}">
              ${isActive ? 'Active' : 'Paused'}
            </button>
          </td>
          <td class="py-3 px-4 text-right">
            <button onclick="deleteAccount('${a.id}')" class="text-slate-400 hover:text-rose-400 p-1.5 transition" title="Delete account">
              <i class="fa-solid fa-trash-can"></i>
            </button>
          </td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    console.error('Accounts load error:', e);
  }
}

function switchLoginMethod(method) {
  const panelOtp = document.getElementById('panel-method-otp');
  const panelStr = document.getElementById('panel-method-string');
  const btnOtp = document.getElementById('btn-method-otp');
  const btnStr = document.getElementById('btn-method-string');

  if (method === 'string') {
    panelOtp.classList.add('hidden');
    panelStr.classList.remove('hidden');
    btnOtp.className = 'py-1.5 rounded-md text-slate-400 hover:text-white transition text-center';
    btnStr.className = 'py-1.5 rounded-md bg-blue-600 text-white transition text-center';
  } else {
    panelStr.classList.add('hidden');
    panelOtp.classList.remove('hidden');
    btnStr.className = 'py-1.5 rounded-md text-slate-400 hover:text-white transition text-center';
    btnOtp.className = 'py-1.5 rounded-md bg-blue-600 text-white transition text-center';
  }
}

function openAddAccountModal() {
  document.getElementById('acc-step-1').classList.remove('hidden');
  document.getElementById('acc-step-2').classList.add('hidden');
  switchLoginMethod('otp');
  document.getElementById('modal-account').classList.remove('hidden');
  document.getElementById('modal-account').classList.add('flex');
}

function closeAddAccountModal() {
  document.getElementById('modal-account').classList.add('hidden');
  document.getElementById('modal-account').classList.remove('flex');
}

function backToStep1() {
  document.getElementById('acc-step-1').classList.remove('hidden');
  document.getElementById('acc-step-2').classList.add('hidden');
}

async function sendAccountCode() {
  const phone = document.getElementById('acc-phone').value.trim();
  const apiId = document.getElementById('acc-api-id').value.trim();
  const apiHash = document.getElementById('acc-api-hash').value.trim();

  if (!phone || !apiId || !apiHash) {
    showToast('Please fill all fields (Phone, API ID, API Hash)', 'warning');
    return;
  }

  const btn = document.getElementById('btn-send-code');
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Requesting code...';

  try {
    const res = await fetch('/api/accounts/request_code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_id: parseInt(apiId),
        api_hash: apiHash,
        phone: phone
      })
    });
    const data = await res.json();
    if (data.status === 'code_sent') {
      showToast(`Verification code sent via ${data.delivery_channel || 'Telegram'}!`, 'success');
      document.getElementById('display-verify-phone').textContent = phone;
      document.getElementById('display-delivery-channel').textContent = data.delivery_channel || 'Telegram App';
      document.getElementById('display-delivery-instructions').textContent = data.delivery_details || 'Check your official Telegram service notifications chat.';
      document.getElementById('acc-step-1').classList.add('hidden');
      document.getElementById('acc-step-2').classList.remove('hidden');
    } else {
      showToast(data.message || 'Failed to request code', 'error');
    }
  } catch (e) {
    showToast('Network error requesting code', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span>Request Verification Code</span> <i class="fa-solid fa-arrow-right"></i>';
  }
}

async function resendCodeViaSms() {
  const phone = document.getElementById('acc-phone').value.trim();
  const btn = document.getElementById('btn-resend-sms');
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Requesting SMS...';

  try {
    const res = await fetch('/api/accounts/resend_code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone })
    });
    const data = await res.json();
    if (data.status === 'code_sent') {
      showToast('Code resent via SMS text message! Check your phone inbox.', 'success');
      document.getElementById('display-delivery-channel').textContent = 'SMS Text Message';
      document.getElementById('display-delivery-instructions').textContent = 'Telegram sent the code via SMS text message to your phone. Check your phone\'s SMS text messages.';
    } else {
      showToast(data.message || 'Failed to resend via SMS', 'error');
    }
  } catch (e) {
    showToast('Error requesting SMS resend', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-comment-sms"></i> Not in app? Resend as SMS';
  }
}

async function submitAccountCode() {
  const phone = document.getElementById('acc-phone').value.trim();
  let code = document.getElementById('acc-otp').value.trim();
  const errBox = document.getElementById('step2-error-alert');
  const errText = document.getElementById('step2-error-text');

  if (errBox) errBox.classList.add('hidden');

  // Sanitize code to digits
  code = code.replace(/\D/g, '');

  if (!code) {
    const msg = 'Please enter the verification code numbers';
    if (errBox && errText) {
      errText.textContent = msg;
      errBox.classList.remove('hidden');
    }
    showToast(msg, 'warning');
    return;
  }

  const btn = document.getElementById('btn-submit-code');
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Verifying...';

  try {
    const res = await fetch('/api/accounts/submit_code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, code, password: null })
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('Telegram account connected successfully!', 'success');
      closeAddAccountModal();
      loadAccounts();
      loadStatus();
    } else if (data.status === '2fa_required') {
      // 2FA password requested only if account specifically has it enabled
      const pwd = prompt(data.message || 'Two-Step Verification (2FA) is enabled on this Telegram account. Please enter your 2FA password:');
      if (pwd) {
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Submitting 2FA...';
        const res2 = await fetch('/api/accounts/submit_code', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ phone, code, password: pwd.trim() })
        });
        const data2 = await res2.json();
        if (data2.status === 'success') {
          showToast('Telegram account connected successfully with 2FA!', 'success');
          closeAddAccountModal();
          loadAccounts();
          loadStatus();
          return;
        } else {
          const msg2 = data2.message || '2FA verification failed';
          if (errBox && errText) {
            errText.textContent = msg2;
            errBox.classList.remove('hidden');
          }
          showToast(msg2, 'error');
          return;
        }
      } else {
        const msg = '2-Step Verification password is required for this account.';
        if (errBox && errText) {
          errText.textContent = msg;
          errBox.classList.remove('hidden');
        }
        showToast(msg, 'warning');
      }
    } else {
      const msg = data.message || 'Login failed';
      if (errBox && errText) {
        errText.textContent = msg;
        errBox.classList.remove('hidden');
      }
      showToast(msg, 'error');
    }
  } catch (e) {
    const msg = 'Error verifying account: ' + e.message;
    if (errBox && errText) {
      errText.textContent = msg;
      errBox.classList.remove('hidden');
    }
    showToast(msg, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span>Verify & Connect</span> <i class="fa-solid fa-check"></i>';
  }
}

async function submitStringSession() {
  const apiId = document.getElementById('str-api-id').value.trim();
  const apiHash = document.getElementById('str-api-hash').value.trim();
  const sessionStr = document.getElementById('str-session-key').value.trim();

  if (!apiId || !apiHash || !sessionStr) {
    showToast('Please enter API ID, API Hash, and Session String', 'warning');
    return;
  }

  const btn = document.getElementById('btn-submit-string');
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Connecting...';

  try {
    const res = await fetch('/api/accounts/login_string', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_id: parseInt(apiId),
        api_hash: apiHash,
        session_string: sessionStr
      })
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('Account connected instantly via String Session!', 'success');
      closeAddAccountModal();
      loadAccounts();
      loadStatus();
    } else {
      showToast(data.message || 'String session login failed', 'error');
    }
  } catch (e) {
    showToast('Network error connecting session string', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-plug"></i> Connect String Session';
  }
}

async function toggleAccount(accId) {
  try {
    const res = await fetch(`/api/accounts/${accId}/toggle`, { method: 'POST' });
    const data = await res.json();
    loadAccounts();
    loadStatus();
  } catch (e) {
    showToast('Failed to toggle account', 'error');
  }
}

async function deleteAccount(accId) {
  if (!confirm('Are you sure you want to disconnect and remove this Telegram account?')) return;
  try {
    const res = await fetch(`/api/accounts/${accId}`, { method: 'DELETE' });
    const data = await res.json();
    showToast('Account removed', 'info');
    loadAccounts();
    loadStatus();
  } catch (e) {
    showToast('Failed to delete account', 'error');
  }
}

// ----------------- GROUPS -----------------

async function loadGroups() {
  try {
    const res = await fetch('/api/groups');
    const groups = await res.json();
    const box = document.getElementById('groups-list-box');
    document.getElementById('groups-list-count').textContent = groups.length;

    if (!groups.length) {
      box.innerHTML = '<p class="text-slate-500 text-center py-6">No groups added yet.</p>';
      return;
    }

    box.innerHTML = groups.map(g => {
      const statusColor = g.status === 'Success' ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                        : g.status === 'Ready' ? 'text-blue-400 bg-blue-500/10 border-blue-500/20'
                        : 'text-amber-400 bg-amber-500/10 border-amber-500/20';
      return `
        <div class="flex items-center justify-between p-2 rounded bg-slate-900 border border-slate-800">
          <div class="flex items-center gap-2">
            <i class="fa-brands fa-telegram text-blue-400"></i>
            <div>
              <p class="font-mono text-white text-xs">${g.target}</p>
              ${g.title && g.title !== g.target ? `<p class="text-[10px] text-slate-400 truncate max-w-[200px]">${g.title}</p>` : ''}
            </div>
          </div>
          <div class="flex items-center gap-2">
            <span class="px-2 py-0.5 rounded text-[10px] font-semibold border ${statusColor}">${g.status}</span>
            ${g.last_posted ? `<span class="text-[10px] text-slate-500">${g.last_posted.split(' ')[1]}</span>` : ''}
          </div>
        </div>
      `;
    }).join('');
  } catch (e) {
    console.error('Groups load error:', e);
  }
}

async function importGroups() {
  const text = document.getElementById('groups-bulk-input').value.trim();
  if (!text) {
    showToast('Please paste one or more group links or usernames', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw_text: text })
    });
    const data = await res.json();
    showToast(`Added ${data.added} groups! Total: ${data.total}`, 'success');
    document.getElementById('groups-bulk-input').value = '';
    loadGroups();
    loadStatus();
  } catch (e) {
    showToast('Failed to import groups', 'error');
  }
}

async function clearGroups() {
  if (!confirm('Are you sure you want to remove all target groups?')) return;
  try {
    const res = await fetch('/api/groups', { method: 'DELETE' });
    showToast('Target groups cleared', 'info');
    loadGroups();
    loadStatus();
  } catch (e) {
    showToast('Failed to clear groups', 'error');
  }
}

// ----------------- LIVE ACTIVITY LOGS STREAM & POLLING -----------------

let logPollingTimer = null;
let lastLogTimestamp = '';

async function loadLogs() {
  if (!currentUser) return;
  try {
    const { ok, data } = await safeApiCall('/api/logs?limit=100');
    if (!ok || !Array.isArray(data)) return;
    const box = document.getElementById('console-stream');
    if (!box) return;

    if (box.children.length === 0 || box.querySelector('.text-slate-500') || box.querySelector('.text-slate-600')) {
      box.innerHTML = '';
      if (data.length === 0) {
        box.innerHTML = '<div class="text-slate-500 italic p-4 text-center">No activity logs recorded yet. Automation events will appear here.</div>';
        return;
      }
      data.forEach(entry => appendLogEntry(entry));
      if (data.length > 0) {
        lastLogTimestamp = data[data.length - 1].timestamp;
      }
    } else {
      // Append only newer logs
      data.forEach(entry => {
        if (!lastLogTimestamp || entry.timestamp > lastLogTimestamp) {
          appendLogEntry(entry);
          lastLogTimestamp = entry.timestamp;
        }
      });
    }
  } catch (e) {
    console.warn('Error fetching logs:', e);
  }
}

function initLogStream() {
  // 1. Immediately fetch and display current logs
  loadLogs();

  // 2. Start continuous polling (every 4 seconds) to guarantee logs work even on PHP proxy where SSE is buffered
  if (logPollingTimer) clearInterval(logPollingTimer);
  logPollingTimer = setInterval(loadLogs, 4000);

  // 3. Also try EventSource if supported
  if (logEventSource) {
    try { logEventSource.close(); } catch (e) {}
  }
  const token = localStorage.getItem('auth_token') || '';
  try {
    const streamUrl = getApiEndpoint('/api/logs/stream') + '?token=' + encodeURIComponent(token);
    logEventSource = new EventSource(streamUrl);
    logEventSource.onmessage = (event) => {
      if (!event.data || event.data.startsWith(':')) return;
      try {
        const entry = JSON.parse(event.data);
        appendLogEntry(entry);
        lastLogTimestamp = entry.timestamp;
      } catch (e) {
        console.error('Log parse error', e);
      }
    };
    logEventSource.onerror = () => {
      // On shared hosting proxies SSE may disconnect, graceful fallback continues via logPollingTimer
    };
  } catch (e) {
    console.warn('EventSource not supported or blocked:', e);
  }
}

function appendLogEntry(entry) {
  const box = document.getElementById('console-stream');
  const autoscroll = document.getElementById('autoscroll-toggle').checked;

  const colorMap = {
    INFO: 'text-slate-300',
    SUCCESS: 'text-emerald-400 font-semibold',
    WARNING: 'text-amber-400 font-semibold',
    ERROR: 'text-rose-400 font-bold'
  };

  const line = document.createElement('div');
  line.className = 'flex items-start gap-2 leading-tight';
  line.innerHTML = `
    <span class="text-slate-600 select-none">[${entry.timestamp.split(' ')[1]}]</span>
    <span class="uppercase text-[10px] px-1 rounded bg-slate-900 border border-slate-800 ${colorMap[entry.level] || 'text-slate-400'}">${entry.level}</span>
    <span class="${colorMap[entry.level] || 'text-slate-300'} break-all">${entry.message}</span>
  `;
  box.appendChild(line);

  // Keep last 300 logs in DOM
  if (box.children.length > 300) {
    box.removeChild(box.firstChild);
  }

  if (autoscroll) {
    box.scrollTop = box.scrollHeight;
  }
}

function clearConsoleView() {
  document.getElementById('console-stream').innerHTML = '<p class="text-slate-600">// Live stream cleared by user...</p>';
}

// ========================================================
// ----------------- AUTH & USER PORTAL -------------------
// ========================================================

async function checkCurrentUser() {
  const token = localStorage.getItem('auth_token');
  if (!token) {
    currentUser = null;
    updateUserUI();
    openAuthModal('login');
    return false;
  }
  try {
    const res = await window.fetch('/api/auth/me', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (res.ok) {
      currentUser = await res.json();
      updateUserUI();
      closeAuthModal();
      return true;
    } else {
      localStorage.removeItem('auth_token');
      currentUser = null;
      updateUserUI();
      openAuthModal('login');
      return false;
    }
  } catch (e) {
    currentUser = null;
    updateUserUI();
    openAuthModal('login');
    return false;
  }
}

function updateUserUI() {
  const userBar = document.getElementById('user-header-bar');
  const guestBar = document.getElementById('guest-header-bar');
  const subBanner = document.getElementById('subscription-alert-banner');
  const adminNavBtn = document.getElementById('btn-admin-nav');
  const adminTabBtn = document.getElementById('tab-btn-admin');
  const btnStart = document.getElementById('btn-start');
  const btnPostNow = document.getElementById('btn-post-now');
  const openAddAccBtn = document.getElementById('btn-open-add-acc');

  if (!currentUser) {
    if (userBar) userBar.classList.add('hidden');
    if (guestBar) guestBar.classList.remove('hidden');
    if (subBanner) subBanner.classList.add('hidden');
    if (adminNavBtn) adminNavBtn.classList.add('hidden');
    if (adminTabBtn) adminTabBtn.classList.add('hidden');
    return;
  }

  if (userBar) userBar.classList.remove('hidden');
  if (guestBar) guestBar.classList.add('hidden');

  // Username
  const nameEl = document.getElementById('user-display-name');
  if (nameEl) nameEl.textContent = currentUser.username;

  // Plan badge
  const planEl = document.getElementById('user-plan-badge');
  if (planEl) {
    const pName = currentUser.plan_info ? currentUser.plan_info.name : 'Starter Plan';
    const pPrice = currentUser.subscription_price !== undefined ? currentUser.subscription_price : 10;
    planEl.textContent = `${pName} ($${pPrice}/mo)`;
  }

  // Subscription countdown badge
  const subEl = document.getElementById('user-sub-badge');
  if (subEl) {
    if (currentUser.role === 'admin') {
      subEl.textContent = '👑 Unlimited Admin';
      subEl.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20';
    } else if (currentUser.status === 'active') {
      subEl.textContent = `🟢 ${currentUser.days_remaining} Days Left`;
      subEl.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20';
    } else if (currentUser.status === 'expired') {
      subEl.textContent = '🔴 Expired (Locked)';
      subEl.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/30';
    } else if (currentUser.status === 'pending') {
      subEl.textContent = '⏳ Pending Admin Approval';
      subEl.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30';
    } else {
      subEl.textContent = `⚠️ ${currentUser.status.toUpperCase()}`;
      subEl.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-800 text-slate-300 border border-slate-700';
    }
  }

  // Admin buttons & tab access
  if (currentUser.role === 'admin') {
    if (adminNavBtn) adminNavBtn.classList.remove('hidden');
    if (adminTabBtn) adminTabBtn.classList.remove('hidden');
  } else {
    if (adminNavBtn) adminNavBtn.classList.add('hidden');
    if (adminTabBtn) adminTabBtn.classList.add('hidden');
    
    // Explicitly hide admin portal tab content from regular users
    const adminContent = document.getElementById('tab-content-admin');
    if (adminContent) adminContent.classList.add('hidden');

    // Wipe admin table so non-admin users NEVER see other tenants' data
    const adminTbody = document.getElementById('admin-users-table-body');
    if (adminTbody) adminTbody.innerHTML = '';
    const statTotal = document.getElementById('admin-stat-total-users');
    if (statTotal) statTotal.textContent = '0';
    const statActive = document.getElementById('admin-stat-active-users');
    if (statActive) statActive.textContent = '0';
    const statPending = document.getElementById('admin-stat-pending-users');
    if (statPending) statPending.textContent = '0';
    const statMrr = document.getElementById('admin-stat-mrr');
    if (statMrr) statMrr.textContent = '$0 / mo';

    // If activeTab is currently admin, immediately switch back to schedule tab
    if (activeTab === 'admin') {
      switchTab('schedule');
    }
  }

  // Locked Banner check
  const isLocked = currentUser.role !== 'admin' && (currentUser.is_locked || currentUser.status !== 'active');
  if (isLocked) {
    if (subBanner) subBanner.classList.remove('hidden');
    const alertText = document.getElementById('subscription-alert-text');
    if (alertText) {
      if (currentUser.status === 'pending') {
        alertText.innerHTML = '<b>Awaiting Admin Approval:</b> Your portal is registered. Super Admin must approve your account with your 1-month subscription before posting automations run.';
      } else {
        alertText.innerHTML = '<b>Subscription Expired:</b> Your 1-Month subscription has ended. Posting automation and "Post Now" are automatically locked. Please contact Admin to renew ($10/mo 1 Acc, $20/mo 2 Acc, $30/mo 3 Acc).';
      }
    }
    if (btnStart) {
      btnStart.disabled = true;
      btnStart.classList.add('opacity-50', 'cursor-not-allowed');
    }
    if (btnPostNow) {
      btnPostNow.disabled = true;
      btnPostNow.classList.add('opacity-50', 'cursor-not-allowed');
    }
  } else {
    if (subBanner) subBanner.classList.add('hidden');
    if (btnStart) {
      btnStart.disabled = false;
      btnStart.classList.remove('opacity-50', 'cursor-not-allowed');
    }
    if (btnPostNow) {
      btnPostNow.disabled = false;
      btnPostNow.classList.remove('opacity-50', 'cursor-not-allowed');
    }
  }

  // Tab 3 Account Limit Tracker
  const planNameLabel = document.getElementById('plan-name-label');
  const planAccLimitLabel = document.getElementById('plan-acc-limit-label');
  const planAccConnLabel = document.getElementById('plan-acc-connected-label');
  const accLimitNote = document.getElementById('account-limit-action-note');

  const maxAcc = currentUser.max_accounts || (currentUser.role === 'admin' ? 999 : 1);
  const connAcc = (allAccounts && Array.isArray(allAccounts)) ? allAccounts.length : 0;

  if (planNameLabel) planNameLabel.textContent = (currentUser.plan_info ? currentUser.plan_info.name : 'Starter Plan') + ` ($${currentUser.subscription_price || 10}/mo)`;
  if (planAccLimitLabel) planAccLimitLabel.textContent = maxAcc;
  if (planAccConnLabel) planAccConnLabel.textContent = connAcc;

  if (accLimitNote) {
    if (currentUser.role !== 'admin' && connAcc >= maxAcc) {
      accLimitNote.innerHTML = `<span class="text-amber-400 font-bold">⚠️ Plan Limit Reached (${connAcc}/${maxAcc} Accounts).</span> Contact Admin to upgrade tier ($10=1, $20=2, $30=3 accounts).`;
    } else {
      accLimitNote.innerHTML = `Tier Limits: $10 = 1 Account | $20 = 2 Accounts | $30 = 3 Accounts`;
    }
  }
}

function openAuthModal(tab = 'login') {
  const modal = document.getElementById('modal-auth');
  if (modal) {
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    switchAuthTab(tab);
  }
}

function closeAuthModal() {
  if (!currentUser) return; // Prevent closing if not logged in
  const modal = document.getElementById('modal-auth');
  if (modal) {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    backToLoginStep1();
    backToRegisterStep1();
  }
}

// Pending Auth & OTP Verification State
let pendingLoginUserId = null;
let pendingLoginUsername = null;
let pendingRegEmail = null;

// Safe API fetcher with subfolder support and robust non-JSON response handling
async function safeApiCall(endpoint, options = {}) {
  const finalUrl = getApiEndpoint(endpoint);
  options.headers = options.headers || {};
  if (!options.headers['Content-Type'] && options.body && typeof options.body === 'string') {
    options.headers['Content-Type'] = 'application/json';
  }

  const res = await window.fetch(finalUrl, options);
  const contentType = res.headers.get('content-type') || '';
  let data = null;

  if (contentType.includes('application/json')) {
    try {
      data = await res.json();
      if (data && typeof data === 'object') {
        if (!data.detail && (data.message || data.error)) {
          data.detail = data.message || data.error;
        }
      }
    } catch (e) {
      data = { detail: 'Invalid JSON response from server' };
    }
  } else {
    const rawText = await res.text();
    let cleanMessage = rawText;
    if (rawText.includes('<title>')) {
      const titleMatch = rawText.match(/<title>([^<]+)<\/title>/i);
      cleanMessage = titleMatch ? titleMatch[1].trim() : `Server error (${res.status})`;
    } else if (rawText.length > 120) {
      cleanMessage = `Server returned status ${res.status}`;
    }
    data = { detail: cleanMessage || `Server error (${res.status})` };
  }

  return { ok: res.ok, status: res.status, data };
}

function switchAuthTab(tab) {
  const loginForm = document.getElementById('auth-login-form');
  const regForm = document.getElementById('auth-register-form');
  const tabLogin = document.getElementById('btn-auth-tab-login');
  const tabReg = document.getElementById('btn-auth-tab-register');

  backToLoginStep1();
  backToRegisterStep1();

  if (tab === 'login') {
    if (loginForm) loginForm.classList.remove('hidden');
    if (regForm) regForm.classList.add('hidden');
    if (tabLogin) tabLogin.className = 'py-2 rounded-lg bg-blue-600 text-white transition text-center font-semibold';
    if (tabReg) tabReg.className = 'py-2 rounded-lg text-slate-400 hover:text-white transition text-center';
  } else {
    if (loginForm) loginForm.classList.add('hidden');
    if (regForm) regForm.classList.remove('hidden');
    if (tabLogin) tabLogin.className = 'py-2 rounded-lg text-slate-400 hover:text-white transition text-center';
    if (tabReg) tabReg.className = 'py-2 rounded-lg bg-emerald-600 text-white transition text-center font-semibold';
  }
}

// ----------------- LOGIN STEP 1: CREDENTIALS -----------------
async function submitLogin(e) {
  e.preventDefault();
  const username = document.getElementById('login-username').value.trim();
  const password = document.getElementById('login-password').value;
  const btn = document.getElementById('btn-login-submit');
  const errBox = document.getElementById('login-error-alert');
  const errText = document.getElementById('login-error-text');

  if (errBox) errBox.classList.add('hidden');

  if (!username || !password) {
    const msg = 'Please enter both username and password';
    if (errBox && errText) {
      errText.textContent = msg;
      errBox.classList.remove('hidden');
    }
    showToast(msg, 'error');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Checking credentials...';

  try {
    const { ok, data } = await safeApiCall('/api/auth/login/request_otp', {
      method: 'POST',
      body: JSON.stringify({ username, password })
    });

    if (!ok) {
      const msg = data?.detail || 'Invalid username or password';
      if (errBox && errText) {
        errText.textContent = msg;
        errBox.classList.remove('hidden');
      }
      showToast(msg, 'error');
      return;
    }

    // If super admin direct login or account without email
    if (data.status === 'direct_login' && data.token) {
      localStorage.setItem('auth_token', data.token);
      currentUser = data.user;
      switchTab('schedule');
      updateUserUI();
      closeAuthModal();
      showToast(`Welcome back, @${data.user.username}!`, 'success');
      loadAllData();
      startStatusPolling();
      return;
    }

    // OTP has been dispatched to user's registered email
    if (data.status === 'otp_sent') {
      pendingLoginUserId = data.user_id;
      pendingLoginUsername = username;

      document.getElementById('login-step-credentials').classList.add('hidden');
      document.getElementById('login-step-otp').classList.remove('hidden');
      const maskEl = document.getElementById('login-otp-masked-email');
      if (maskEl) maskEl.textContent = data.masked_email || 'your registered email';

      const infoBox = document.getElementById('login-info-alert');
      const infoText = document.getElementById('login-info-text');
      if (infoBox && infoText) {
        infoText.textContent = `Security code sent to ${data.masked_email}. Please check your Inbox and Spam folder.`;
        infoBox.classList.remove('hidden');
      }

      showToast(`Security code sent to ${data.masked_email}!`, 'info');
      const otpInput = document.getElementById('login-otp-code');
      if (otpInput) {
        otpInput.value = '';
        setTimeout(() => otpInput.focus(), 100);
      }
    }
  } catch (err) {
    const msg = 'Login request failed: ' + err.message;
    if (errBox && errText) {
      errText.textContent = msg;
      errBox.classList.remove('hidden');
    }
    showToast(msg, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span>Sign In to Portal</span> <i class="fa-solid fa-arrow-right"></i>';
  }
}

// ----------------- LOGIN STEP 2: VERIFY OTP -----------------
async function verifyLoginOtp() {
  const otpInput = document.getElementById('login-otp-code');
  const otp = otpInput ? otpInput.value.trim() : '';
  const btn = document.getElementById('btn-login-verify');
  const errBox = document.getElementById('login-error-alert');
  const errText = document.getElementById('login-error-text');

  if (errBox) errBox.classList.add('hidden');

  if (!otp || otp.length < 4) {
    const msg = 'Please enter the 6-digit code received on your email';
    if (errBox && errText) {
      errText.textContent = msg;
      errBox.classList.remove('hidden');
    }
    showToast(msg, 'error');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Verifying security code...';

  try {
    const { ok, data } = await safeApiCall('/api/auth/login/verify_otp', {
      method: 'POST',
      body: JSON.stringify({ user_id: pendingLoginUserId, otp })
    });

    if (!ok) {
      const msg = data?.detail || 'Invalid or expired security code.';
      if (errBox && errText) {
        errText.textContent = msg;
        errBox.classList.remove('hidden');
      }
      showToast(msg, 'error');
      return;
    }

    localStorage.setItem('auth_token', data.token);
    currentUser = data.user;
    backToLoginStep1();
    switchTab('schedule');
    updateUserUI();
    closeAuthModal();
    showToast(`Welcome back, @${data.user.username}!`, 'success');
    loadAllData();
    startStatusPolling();
  } catch (err) {
    const msg = 'Verification failed: ' + err.message;
    if (errBox && errText) {
      errText.textContent = msg;
      errBox.classList.remove('hidden');
    }
    showToast(msg, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-lock-open"></i> <span>Verify & Enter Portal</span>';
  }
}

async function resendLoginOtp() {
  if (!pendingLoginUserId) return;
  const resendBtn = document.getElementById('btn-login-resend');
  if (resendBtn) {
    resendBtn.disabled = true;
    resendBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Resending...';
  }

  try {
    const { ok, data } = await safeApiCall('/api/auth/resend_otp', {
      method: 'POST',
      body: JSON.stringify({ target: pendingLoginUserId, purpose: 'login' })
    });
    if (ok) {
      showToast('A new security code has been sent to your email!', 'success');
    } else {
      showToast(data?.detail || 'Could not resend code. Please try again.', 'error');
    }
  } catch (e) {
    showToast('Failed to resend code: ' + e.message, 'error');
  } finally {
    if (resendBtn) {
      resendBtn.disabled = false;
      resendBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i> Resend Code';
    }
  }
}

function backToLoginStep1() {
  const creds = document.getElementById('login-step-credentials');
  const otpStep = document.getElementById('login-step-otp');
  const errBox = document.getElementById('login-error-alert');
  const infoBox = document.getElementById('login-info-alert');
  if (creds) creds.classList.remove('hidden');
  if (otpStep) otpStep.classList.add('hidden');
  if (errBox) errBox.classList.add('hidden');
  if (infoBox) infoBox.classList.add('hidden');
  pendingLoginUserId = null;
  pendingLoginUsername = null;
}

// ----------------- REGISTER STEP 1: DETAILS & SEND OTP -----------------
async function submitRegister(e) {
  e.preventDefault();
  const username = document.getElementById('reg-username').value.trim();
  const email = document.getElementById('reg-email').value.trim();
  const password = document.getElementById('reg-password').value;
  const planEl = document.querySelector('input[name="plan_tier"]:checked');
  const plan_tier = planEl ? planEl.value : 'tier_1';
  const btn = document.getElementById('btn-reg-submit');
  const errBox = document.getElementById('reg-error-alert');
  const errText = document.getElementById('reg-error-text');

  if (errBox) errBox.classList.add('hidden');

  if (!username || !email || !password) {
    const msg = 'Please fill all required fields';
    if (errBox && errText) {
      errText.textContent = msg;
      errBox.classList.remove('hidden');
    }
    showToast(msg, 'error');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Sending verification code to email...';

  try {
    const { ok, data } = await safeApiCall('/api/auth/register/request_otp', {
      method: 'POST',
      body: JSON.stringify({ username, email, password, plan_tier })
    });

    if (!ok) {
      const msg = data?.detail || 'Registration request failed';
      if (errBox && errText) {
        errText.textContent = msg;
        errBox.classList.remove('hidden');
      }
      showToast(msg, 'error');
      return;
    }

    // OTP dispatched to email
    pendingRegEmail = email;
    document.getElementById('reg-step-details').classList.add('hidden');
    document.getElementById('reg-step-otp').classList.remove('hidden');
    const maskEl = document.getElementById('reg-otp-masked-email');
    if (maskEl) maskEl.textContent = data.masked_email || email;

    const infoBox = document.getElementById('reg-info-alert');
    const infoText = document.getElementById('reg-info-text');
    if (infoBox && infoText) {
      infoText.textContent = `Verification code sent to ${data.masked_email || email}! Please check your Inbox & Spam folder.`;
      infoBox.classList.remove('hidden');
    }

    showToast(`Verification code sent to ${data.masked_email || email}!`, 'success');
    const otpInput = document.getElementById('reg-otp-code');
    if (otpInput) {
      otpInput.value = '';
      setTimeout(() => otpInput.focus(), 100);
    }
  } catch (err) {
    const msg = 'Registration error: ' + err.message;
    if (errBox && errText) {
      errText.textContent = msg;
      errBox.classList.remove('hidden');
    }
    showToast(msg, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span>Continue & Send Email Verification Code</span> <i class="fa-solid fa-arrow-right"></i>';
  }
}

// ----------------- REGISTER STEP 2: VERIFY OTP -----------------
async function verifyRegisterOtp() {
  const otpInput = document.getElementById('reg-otp-code');
  const otp = otpInput ? otpInput.value.trim() : '';
  const btn = document.getElementById('btn-reg-verify');
  const errBox = document.getElementById('reg-error-alert');
  const errText = document.getElementById('reg-error-text');

  if (errBox) errBox.classList.add('hidden');

  if (!otp || otp.length < 4) {
    const msg = 'Please enter the 6-digit verification code from your email';
    if (errBox && errText) {
      errText.textContent = msg;
      errBox.classList.remove('hidden');
    }
    showToast(msg, 'error');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Verifying code & activating account...';

  try {
    const { ok, data } = await safeApiCall('/api/auth/register/verify_otp', {
      method: 'POST',
      body: JSON.stringify({ email: pendingRegEmail, otp })
    });

    if (!ok) {
      const msg = data?.detail || 'Invalid or expired verification code.';
      if (errBox && errText) {
        errText.textContent = msg;
        errBox.classList.remove('hidden');
      }
      showToast(msg, 'error');
      return;
    }

    localStorage.setItem('auth_token', data.token);
    currentUser = data.user;
    backToRegisterStep1();
    switchTab('schedule');
    updateUserUI();
    closeAuthModal();
    showToast('Email verified! Account registered & portal activated (Pending Admin approval for 1-month subscription).', 'success');
    loadAllData();
    startStatusPolling();
  } catch (err) {
    const msg = 'Verification failed: ' + err.message;
    if (errBox && errText) {
      errText.textContent = msg;
      errBox.classList.remove('hidden');
    }
    showToast(msg, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-check-circle"></i> <span>Verify Code & Complete Registration</span>';
  }
}

async function resendRegisterOtp() {
  if (!pendingRegEmail) return;
  const resendBtn = document.getElementById('btn-reg-resend');
  if (resendBtn) {
    resendBtn.disabled = true;
    resendBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Resending...';
  }

  try {
    const { ok, data } = await safeApiCall('/api/auth/resend_otp', {
      method: 'POST',
      body: JSON.stringify({ target: pendingRegEmail, purpose: 'registration' })
    });
    if (ok) {
      showToast('A new verification code has been sent to your email!', 'success');
    } else {
      showToast(data?.detail || 'Could not resend code. Please try again.', 'error');
    }
  } catch (e) {
    showToast('Failed to resend code: ' + e.message, 'error');
  } finally {
    if (resendBtn) {
      resendBtn.disabled = false;
      resendBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i> Resend Code';
    }
  }
}

function backToRegisterStep1() {
  const details = document.getElementById('reg-step-details');
  const otpStep = document.getElementById('reg-step-otp');
  const errBox = document.getElementById('reg-error-alert');
  const infoBox = document.getElementById('reg-info-alert');
  if (details) details.classList.remove('hidden');
  if (otpStep) otpStep.classList.add('hidden');
  if (errBox) errBox.classList.add('hidden');
  if (infoBox) infoBox.classList.add('hidden');
  pendingRegEmail = null;
}

async function handleLogout() {
  if (!confirm('Are you sure you want to log out?')) return;
  stopStatusPolling();
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {}
  localStorage.removeItem('auth_token');
  currentUser = null;
  
  // Wipe all session state & admin DOM tables
  const adminTbody = document.getElementById('admin-users-table-body');
  if (adminTbody) adminTbody.innerHTML = '';
  const adminContent = document.getElementById('tab-content-admin');
  if (adminContent) adminContent.classList.add('hidden');
  
  switchTab('schedule');
  updateUserUI();
  openAuthModal('login');
}

// ========================================================
// ----------------- ADMIN DASHBOARD ----------------------
// ========================================================

async function loadAdminData() {
  if (!currentUser || currentUser.role !== 'admin') {
    const adminContent = document.getElementById('tab-content-admin');
    if (adminContent) adminContent.classList.add('hidden');
    const adminTbody = document.getElementById('admin-users-table-body');
    if (adminTbody) adminTbody.innerHTML = '';
    return;
  }
  try {
    loadEmailConfig();
    const [statsRes, usersRes] = await Promise.all([
      safeApiCall('/api/admin/stats'),
      safeApiCall('/api/admin/users')
    ]);

    if (!statsRes.ok || !usersRes.ok) {
      showToast('Admin access denied or unauthorized', 'error');
      return;
    }

    const stats = statsRes.data;
    const users = usersRes.data;

    // Render Metrics
    document.getElementById('admin-stat-total-users').textContent = stats.total_users;
    document.getElementById('admin-stat-active-users').textContent = stats.active_users;
    document.getElementById('admin-stat-pending-users').textContent = stats.pending_users;
    document.getElementById('admin-stat-mrr').textContent = `$${stats.monthly_revenue} / mo`;

    // Render Table
    const tbody = document.getElementById('admin-users-table-body');
    if (!users || users.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-center py-6 text-slate-500">No registered users found.</td></tr>';
      return;
    }

    tbody.innerHTML = users.map(u => {
      const isSuperAdmin = (u.role === 'admin');
      
      // Status Badge
      let statusBadge = '';
      if (isSuperAdmin) {
        statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">Super Admin</span>';
      } else if (u.status === 'active') {
        statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">Active</span>';
      } else if (u.status === 'pending') {
        statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30 animate-pulse">Pending Approval</span>';
      } else if (u.status === 'expired') {
        statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30">Expired (Locked)</span>';
      } else {
        statusBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700">${u.status}</span>`;
      }

      // Tier label
      let tierHtml = '';
      if (isSuperAdmin) {
        tierHtml = '<span class="font-bold text-amber-400">Unlimited Plan</span>';
      } else {
        const pName = u.plan_info ? u.plan_info.name : 'Starter Plan';
        const pPrice = u.subscription_price !== undefined ? u.subscription_price : 10;
        tierHtml = `<span class="font-bold text-white">${pName}</span> <span class="text-emerald-400 font-semibold">($${pPrice}/mo)</span>`;
      }

      // Days Left
      const daysText = isSuperAdmin ? '∞ Forever' : (u.days_remaining > 0 ? `${u.days_remaining} Days` : '0 Days (Expired)');
      const daysClass = isSuperAdmin ? 'text-amber-400' : (u.days_remaining > 5 ? 'text-emerald-400 font-bold' : (u.days_remaining > 0 ? 'text-amber-400 font-bold' : 'text-rose-400 font-bold'));

      // Action buttons
      let actionsHtml = '';
      if (!isSuperAdmin) {
        const approveBtn = (u.status === 'pending' || u.status === 'expired')
          ? `<button onclick="openAdminApproveModal('${u.id}', '${u.username}', '${u.plan_tier}')" class="px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-[10px] transition shadow-sm">
               <i class="fa-solid fa-check"></i> Approve 1 Mo
             </button>`
          : '';

        const extendBtn = `<button onclick="adminExtendUser('${u.id}', 30)" class="px-2 py-1 rounded bg-blue-600/20 hover:bg-blue-600/30 text-blue-400 border border-blue-500/30 font-semibold text-[10px] transition" title="Extend 30 Days">
          +30 Days
        </button>`;

        const planSelect = `<select onchange="adminChangePlan('${u.id}', this.value)" class="bg-slate-950 border border-slate-700 text-slate-200 text-[10px] rounded px-1.5 py-1 focus:outline-none">
          <option value="tier_1" ${u.plan_tier === 'tier_1' ? 'selected' : ''}>$10 (1 Acc)</option>
          <option value="tier_2" ${u.plan_tier === 'tier_2' ? 'selected' : ''}>$20 (2 Acc)</option>
          <option value="tier_3" ${u.plan_tier === 'tier_3' ? 'selected' : ''}>$30 (3 Acc)</option>
        </select>`;

        const toggleBtn = `<button onclick="adminToggleUserStatus('${u.id}', '${u.status}')" class="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[10px]" title="Toggle Active / Suspended">
          ${u.status === 'active' ? '<i class="fa-solid fa-pause text-amber-400"></i>' : '<i class="fa-solid fa-play text-emerald-400"></i>'}
        </button>`;

        const deleteBtn = `<button onclick="adminDeleteUser('${u.id}', '${u.username}')" class="px-2 py-1 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 text-[10px]" title="Delete Tenant">
          <i class="fa-solid fa-trash"></i>
        </button>`;

        actionsHtml = `
          <div class="flex items-center justify-end gap-1.5 flex-wrap">
            ${approveBtn}
            ${extendBtn}
            ${planSelect}
            ${toggleBtn}
            ${deleteBtn}
          </div>
        `;
      } else {
        actionsHtml = '<span class="text-slate-500 italic text-[11px]">Primary Super Admin</span>';
      }

      return `
        <tr class="hover:bg-slate-800/30 transition">
          <td class="py-3 px-3">
            <div class="font-bold text-white flex items-center gap-1.5">
              <i class="fa-solid fa-circle-user text-slate-400"></i>
              ${u.username}
            </div>
            <div class="text-[10px] text-slate-500">${u.email || 'N/A'}</div>
          </td>
          <td class="py-3 px-3">${tierHtml}</td>
          <td class="py-3 px-3">
            <span class="font-bold ${u.accounts_count >= u.max_accounts ? 'text-amber-400' : 'text-white'}">${u.accounts_count}</span>
            <span class="text-slate-500">/ ${u.max_accounts}</span>
          </td>
          <td class="py-3 px-3 text-slate-300">
            ${u.groups_count || 0} groups &bull; ${u.posts_count || 0} posts
          </td>
          <td class="py-3 px-3 text-[11px] text-slate-400">
            <div>${u.subscription_start ? u.subscription_start.split(' ')[0] : 'Not started'}</div>
            <div class="text-slate-500 text-[10px]">until ${u.subscription_end ? u.subscription_end.split(' ')[0] : 'N/A'}</div>
          </td>
          <td class="py-3 px-3 ${daysClass}">${daysText}</td>
          <td class="py-3 px-3">${statusBadge}</td>
          <td class="py-3 px-3 text-right">${actionsHtml}</td>
        </tr>
      `;
    }).join('');

  } catch (err) {
    showToast('Failed to load admin management data: ' + err.message, 'error');
  }
}

function openAdminApproveModal(userId, username, currentTier) {
  adminTargetUserId = userId;
  document.getElementById('approve-modal-username').textContent = '@' + username;
  document.getElementById('approve-plan-tier').value = currentTier || 'tier_1';
  document.getElementById('approve-duration').value = '30';
  const modal = document.getElementById('modal-admin-approve');
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

function closeAdminApproveModal() {
  adminTargetUserId = null;
  const modal = document.getElementById('modal-admin-approve');
  modal.classList.add('hidden');
  modal.classList.remove('flex');
}

async function confirmAdminApprove() {
  if (!adminTargetUserId) return;
  const plan_tier = document.getElementById('approve-plan-tier').value;
  const duration_days = parseInt(document.getElementById('approve-duration').value) || 30;
  const btn = document.getElementById('btn-confirm-approve');

  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Activating...';

  try {
    const res = await fetch(`/api/admin/users/${adminTargetUserId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_tier, duration_days })
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || 'Failed to approve user', 'error');
      return;
    }

    showToast(`User approved! Subscription active for ${duration_days} days.`, 'success');
    closeAdminApproveModal();
    loadAdminData();
    checkCurrentUser();
  } catch (e) {
    showToast('Approve error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-check"></i> <span>Activate 1 Month</span>';
  }
}

async function adminExtendUser(userId, extra_days = 30) {
  try {
    const res = await fetch(`/api/admin/users/${userId}/extend`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ extra_days })
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || 'Failed to extend subscription', 'error');
      return;
    }
    showToast(`Extended subscription by +${extra_days} days!`, 'success');
    loadAdminData();
  } catch (e) {
    showToast('Extend error: ' + e.message, 'error');
  }
}

async function adminChangePlan(userId, plan_tier) {
  try {
    const res = await fetch(`/api/admin/users/${userId}/plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_tier })
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || 'Failed to update plan', 'error');
      return;
    }
    showToast(`User plan updated to ${data.user.plan_info?.name}!`, 'success');
    loadAdminData();
  } catch (e) {
    showToast('Plan update error: ' + e.message, 'error');
  }
}

async function adminToggleUserStatus(userId, currentStatus) {
  const newStatus = (currentStatus === 'active') ? 'suspended' : 'active';
  try {
    const res = await fetch(`/api/admin/users/${userId}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus })
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || 'Failed to change status', 'error');
      return;
    }
    showToast(`User status set to ${newStatus}.`, 'info');
    loadAdminData();
  } catch (e) {
    showToast('Status error: ' + e.message, 'error');
  }
}

async function adminDeleteUser(userId, username) {
  if (!confirm(`Are you sure you want to permanently delete user @${username}? All their connected accounts, groups, and posts will be removed.`)) return;
  try {
    const res = await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || 'Failed to delete user', 'error');
      return;
    }
    showToast(`User @${username} deleted.`, 'info');
    loadAdminData();
  } catch (e) {
    showToast('Delete error: ' + e.message, 'error');
  }
}

// ----------------- ADMIN PASSWORD CHANGE WITH EMAIL OTP -----------------
let adminPwdOtpRequested = false;

async function handleAdminPasswordChange() {
  const curPass = document.getElementById('admin-cur-password').value;
  const newPass = document.getElementById('admin-new-password').value;
  const otpCode = document.getElementById('admin-pwd-otp-code').value.trim();
  const btn = document.getElementById('btn-admin-pwd-action');
  const errBox = document.getElementById('admin-pwd-error-alert');
  const errText = document.getElementById('admin-pwd-error-text');
  const successBox = document.getElementById('admin-pwd-success-alert');
  const successText = document.getElementById('admin-pwd-success-text');

  if (errBox) errBox.classList.add('hidden');
  if (successBox) successBox.classList.add('hidden');

  if (!curPass) {
    showToast('Please enter your current password.', 'error');
    return;
  }
  if (!newPass || newPass.length < 6) {
    showToast('New password must be at least 6 characters.', 'error');
    return;
  }

  // Step 1: Request OTP to admin email
  if (!adminPwdOtpRequested) {
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Sending code to simardeep647.ss@gmail.com...';
    try {
      const { ok, data } = await safeApiCall('/api/admin/password/request_otp', { method: 'POST' });
      if (!ok) {
        const msg = data?.detail || 'Failed to send verification code.';
        if (errBox && errText) {
          errText.textContent = msg;
          errBox.classList.remove('hidden');
        }
        showToast(msg, 'error');
        return;
      }

      adminPwdOtpRequested = true;
      document.getElementById('admin-pwd-otp-section').classList.remove('hidden');
      const maskEl = document.getElementById('admin-pwd-masked-email');
      if (maskEl) maskEl.textContent = data.masked_email || 'simardeep647.ss@gmail.com';
      btn.innerHTML = '<i class="fa-solid fa-shield-check"></i> <span>Verify OTP & Update Password</span>';
      btn.classList.remove('bg-blue-600', 'hover:bg-blue-500');
      btn.classList.add('bg-emerald-600', 'hover:bg-emerald-500');
      showToast(`Security verification code sent to ${data.masked_email || 'simardeep647.ss@gmail.com'}!`, 'info');
      document.getElementById('admin-pwd-otp-code').focus();
    } catch (e) {
      showToast('Error requesting OTP: ' + e.message, 'error');
    } finally {
      btn.disabled = false;
    }
    return;
  }

  // Step 2: Verify OTP & Change Password
  if (!otpCode || otpCode.length < 4) {
    showToast('Please enter the 6-digit code received on your email.', 'error');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Verifying code & updating password...';

  try {
    const { ok, data } = await safeApiCall('/api/admin/password/verify_and_update', {
      method: 'POST',
      body: JSON.stringify({
        current_password: curPass,
        new_password: newPass,
        otp: otpCode
      })
    });

    if (!ok) {
      const msg = data?.detail || 'Password update failed.';
      if (errBox && errText) {
        errText.textContent = msg;
        errBox.classList.remove('hidden');
      }
      showToast(msg, 'error');
      return;
    }

    if (successBox && successText) {
      successText.textContent = 'Admin password changed successfully! Please use your new password on next login.';
      successBox.classList.remove('hidden');
    }
    showToast('Admin password changed successfully!', 'success');

    // Reset inputs
    document.getElementById('admin-cur-password').value = '';
    document.getElementById('admin-new-password').value = '';
    document.getElementById('admin-pwd-otp-code').value = '';
    document.getElementById('admin-pwd-otp-section').classList.add('hidden');
    adminPwdOtpRequested = false;
    btn.innerHTML = '<i class="fa-solid fa-paper-plane"></i> <span>Send Security Code to simardeep647.ss@gmail.com</span>';
    btn.classList.remove('bg-emerald-600', 'hover:bg-emerald-500');
    btn.classList.add('bg-blue-600', 'hover:bg-blue-500');
  } catch (e) {
    showToast('Update error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

// ========================================================
// EMAIL & SMTP SETTINGS LOGIC
// ========================================================
function applySmtpPreset(preset) {
  const hostInput = document.getElementById('cfg-smtp-host');
  const portSelect = document.getElementById('cfg-smtp-port');
  const senderEmail = document.getElementById('cfg-sender-email');

  if (preset === 'gmail') {
    if (hostInput) hostInput.value = 'smtp.gmail.com';
    if (portSelect) portSelect.value = '465_ssl';
    if (senderEmail && (!senderEmail.value || senderEmail.value.includes('omeglevideochat.online'))) {
      senderEmail.value = 'simardeep647.ss@gmail.com';
    }
  } else if (preset === 'hostinger') {
    if (hostInput) hostInput.value = 'smtp.hostinger.com';
    if (portSelect) portSelect.value = '465_ssl';
    if (senderEmail && (!senderEmail.value || senderEmail.value.includes('gmail.com'))) {
      senderEmail.value = 'noreply@omeglevideochat.online';
    }
  }
}

async function loadEmailConfig() {
  try {
    const { ok, data } = await safeApiCall('/api/admin/email_config');
    if (!ok || !data) return;

    const hostInput = document.getElementById('cfg-smtp-host');
    const portSelect = document.getElementById('cfg-smtp-port');
    const senderEmail = document.getElementById('cfg-sender-email');
    const senderName = document.getElementById('cfg-sender-name');
    const passInput = document.getElementById('cfg-smtp-password');
    const badge = document.getElementById('smtp-status-badge');
    const presetSelect = document.getElementById('cfg-smtp-preset');

    if (hostInput) hostInput.value = data.smtp_host || 'smtp.gmail.com';
    if (senderEmail) senderEmail.value = data.sender_email || 'simardeep647.ss@gmail.com';
    if (senderName) senderName.value = data.sender_name || 'Telegram AutoPost Pro';
    
    if (portSelect) {
      if (data.smtp_port === 587 || data.smtp_use_tls) {
        portSelect.value = '587_tls';
      } else {
        portSelect.value = '465_ssl';
      }
    }

    if (presetSelect) {
      if ((data.smtp_host || '').includes('gmail')) {
        presetSelect.value = 'gmail';
      } else if ((data.smtp_host || '').includes('hostinger')) {
        presetSelect.value = 'hostinger';
      } else {
        presetSelect.value = 'custom';
      }
    }

    if (badge) {
      if (data.smtp_password_set) {
        badge.className = 'px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30';
        badge.innerHTML = '<i class="fa-solid fa-circle-check mr-1"></i> SMTP Active & Ready';
        if (passInput) passInput.placeholder = '•••••••• (Leave blank to keep existing password)';
      } else {
        badge.className = 'px-2.5 py-1 rounded-full text-[11px] font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30';
        badge.innerHTML = '<i class="fa-solid fa-circle-exclamation mr-1"></i> SMTP Password Not Set';
      }
    }
  } catch (e) {
    console.warn('Could not load email config:', e);
  }
}

async function saveEmailConfig() {
  const hostInput = document.getElementById('cfg-smtp-host').value.trim();
  const portVal = document.getElementById('cfg-smtp-port').value;
  const senderEmail = document.getElementById('cfg-sender-email').value.trim();
  const senderName = document.getElementById('cfg-sender-name').value.trim();
  const passInput = document.getElementById('cfg-smtp-password').value.trim();
  const btn = document.getElementById('btn-save-email');
  const errBox = document.getElementById('email-cfg-error-alert');
  const errText = document.getElementById('email-cfg-error-text');
  const successBox = document.getElementById('email-cfg-success-alert');
  const successText = document.getElementById('email-cfg-success-text');

  if (errBox) errBox.classList.add('hidden');
  if (successBox) successBox.classList.add('hidden');

  if (!hostInput) {
    showToast('Please enter SMTP Host', 'error');
    return;
  }
  if (!senderEmail) {
    showToast('Please enter Sender Email', 'error');
    return;
  }

  const isSsl = portVal === '465_ssl';
  const port = isSsl ? 465 : 587;

  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving...';

  try {
    const payload = {
      enabled: true,
      smtp_host: hostInput,
      smtp_port: port,
      smtp_user: senderEmail,
      smtp_use_ssl: isSsl,
      smtp_use_tls: !isSsl,
      sender_email: senderEmail,
      sender_name: senderName || 'Telegram AutoPost Pro'
    };
    if (passInput) {
      payload.smtp_password = passInput;
    }

    const { ok, data } = await safeApiCall('/api/admin/email_config', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    if (!ok) {
      const msg = data?.detail || 'Failed to save email settings.';
      if (errBox && errText) {
        errText.textContent = msg;
        errBox.classList.remove('hidden');
      }
      showToast(msg, 'error');
      return;
    }

    if (successBox && successText) {
      successText.textContent = 'Email & SMTP settings saved successfully!';
      successBox.classList.remove('hidden');
    }
    showToast('Email settings saved successfully!', 'success');
    loadEmailConfig();
  } catch (e) {
    showToast('Error saving email settings: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> <span>Save Email Settings</span>';
  }
}

async function testEmailConfig() {
  const btn = document.getElementById('btn-test-email');
  const errBox = document.getElementById('email-cfg-error-alert');
  const errText = document.getElementById('email-cfg-error-text');
  const successBox = document.getElementById('email-cfg-success-alert');
  const successText = document.getElementById('email-cfg-success-text');

  if (errBox) errBox.classList.add('hidden');
  if (successBox) successBox.classList.add('hidden');

  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Sending test verification email...';

  try {
    const { ok, data } = await safeApiCall('/api/admin/test_email', {
      method: 'POST'
    });

    if (!ok) {
      const msg = data?.detail || 'Failed to dispatch test email.';
      if (errBox && errText) {
        errText.textContent = msg;
        errBox.classList.remove('hidden');
      }
      showToast(msg, 'error');
      return;
    }

    const msg = data?.message || 'Test email dispatched successfully! Please check your inbox and spam folder.';
    if (successBox && successText) {
      successText.textContent = msg;
      successBox.classList.remove('hidden');
    }
    showToast('Test email sent! Check your inbox & spam folder.', 'success');
  } catch (e) {
    showToast('Test error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-paper-plane text-cyan-400"></i> <span>Send Test Email to simardeep647.ss@gmail.com</span>';
  }
}

// Enter-key listeners for OTP inputs
document.addEventListener('DOMContentLoaded', () => {
  const loginOtp = document.getElementById('login-otp-code');
  if (loginOtp) {
    loginOtp.addEventListener('keyup', (e) => {
      if (e.key === 'Enter' || loginOtp.value.trim().length === 6) {
        if (e.key === 'Enter') verifyLoginOtp();
      }
    });
  }
  const regOtp = document.getElementById('reg-otp-code');
  if (regOtp) {
    regOtp.addEventListener('keyup', (e) => {
      if (e.key === 'Enter' || regOtp.value.trim().length === 6) {
        if (e.key === 'Enter') verifyRegisterOtp();
      }
    });
  }
});
