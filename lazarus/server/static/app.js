// Lazarus Mobile Web Client
(function() {
  'use strict';

  // --- State ---
  const state = {
    currentQuery: 'tag:inbox',
    threads: [],
    currentThread: null,
    offset: 0,
    limit: 50,
    allTags: [],
    accounts: [],
    activeModalThreadId: null,
    activeModalTags: new Set(),
    composeAttachments: [],
    autocompleteTimer: null,
    undoTimer: null,
    undoAction: null,
  };

  // --- DOM Elements ---
  const el = {
    searchInput: document.getElementById('search-input'),
    searchClear: document.getElementById('search-clear'),
    chips: document.querySelectorAll('.chip'),
    threadList: document.getElementById('thread-list'),
    viewList: document.getElementById('view-list'),
    viewDetail: document.getElementById('view-detail'),
    btnLoadMore: document.getElementById('btn-load-more'),
    loadMoreWrap: document.getElementById('load-more-wrap'),
    // Drawer
    btnMenu: document.getElementById('btn-menu'),
    btnCloseDrawer: document.getElementById('btn-close-drawer'),
    drawer: document.getElementById('drawer'),
    drawerBackdrop: document.getElementById('drawer-backdrop'),
    drawerTagsList: document.getElementById('drawer-tags-list'),
    drawerAccountLabel: document.getElementById('drawer-account-label'),
    // Detail
    btnDetailBack: document.getElementById('btn-detail-back'),
    detailSubject: document.getElementById('detail-subject'),
    detailTagsRow: document.getElementById('detail-tags-row'),
    detailMessagesList: document.getElementById('detail-messages-list'),
    btnDetailArchive: document.getElementById('btn-detail-archive'),
    btnDetailTrash: document.getElementById('btn-detail-trash'),
    btnDetailTag: document.getElementById('btn-detail-tag'),
    btnDetailStar: document.getElementById('btn-detail-star'),
    btnDetailReply: document.getElementById('btn-detail-reply'),
    btnDetailReplyAll: document.getElementById('btn-detail-reply-all'),
    // Compose
    btnComposeTop: document.getElementById('btn-compose-top'),
    composeSheet: document.getElementById('compose-sheet'),
    composeBackdrop: document.getElementById('compose-backdrop'),
    btnComposeClose: document.getElementById('btn-compose-close'),
    btnComposeSend: document.getElementById('btn-compose-send'),
    composeSheetTitle: document.getElementById('compose-sheet-title'),
    composeAccountRow: document.getElementById('compose-account-row'),
    composeAccountSelect: document.getElementById('compose-account-select'),
    composeTo: document.getElementById('compose-to'),
    btnToggleCc: document.getElementById('btn-toggle-cc'),
    composeCcRow: document.getElementById('compose-cc-row'),
    composeBccRow: document.getElementById('compose-bcc-row'),
    composeCc: document.getElementById('compose-cc'),
    composeBcc: document.getElementById('compose-bcc'),
    composeSubject: document.getElementById('compose-subject'),
    composeBody: document.getElementById('compose-body'),
    composeFileInput: document.getElementById('compose-file-input'),
    composeAttachmentsList: document.getElementById('compose-attachments-list'),
    toAutocompleteDropdown: document.getElementById('to-autocomplete-dropdown'),
    composeInReplyTo: document.getElementById('compose-in-reply-to'),
    composeReferences: document.getElementById('compose-references'),
    // Tag Modal
    tagModal: document.getElementById('tag-modal'),
    tagModalBackdrop: document.getElementById('tag-modal-backdrop'),
    btnTagModalDone: document.getElementById('btn-tag-modal-done'),
    tagNewInput: document.getElementById('tag-new-input'),
    btnAddCustomTag: document.getElementById('btn-add-custom-tag'),
    tagModalList: document.getElementById('tag-modal-list'),
    // Toast
    toast: document.getElementById('toast'),
    toastMessage: document.getElementById('toast-message'),
    toastUndo: document.getElementById('toast-undo'),
  };

  // --- API Client ---
  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Accept': 'application/json',
        ...(options.headers || {})
      }
    });
    if (!res.ok) {
      let errText = res.statusText;
      try {
        const errJson = await res.json();
        if (errJson.error) errText = errJson.error;
      } catch (_) {}
      throw new Error(errText);
    }
    return res.json();
  }

  // --- Initialisation ---
  async function init() {
    setupEventListeners();
    await loadAccounts();
    await loadTags();
    runSearch(state.currentQuery, false);
  }

  // --- Event Listeners ---
  function setupEventListeners() {
    // Search input
    el.searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        el.searchInput.blur();
        const q = el.searchInput.value.trim() || 'tag:inbox';
        runSearch(q);
      }
    });

    el.searchInput.addEventListener('input', () => {
      if (el.searchInput.value) {
        el.searchClear.classList.remove('hidden');
      } else {
        el.searchClear.classList.add('hidden');
      }
    });

    el.searchClear.addEventListener('click', () => {
      el.searchInput.value = '';
      el.searchClear.classList.add('hidden');
      runSearch('tag:inbox');
    });

    // Filter Chips
    el.chips.forEach(chip => {
      chip.addEventListener('click', () => {
        el.chips.forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        const q = chip.getAttribute('data-query');
        el.searchInput.value = q === 'tag:inbox' ? '' : q;
        runSearch(q);
      });
    });

    // Drawer Toggle
    el.btnMenu.addEventListener('click', openDrawer);
    el.btnCloseDrawer.addEventListener('click', closeDrawer);
    el.drawerBackdrop.addEventListener('click', closeDrawer);

    // Drawer Nav items
    document.querySelectorAll('.drawer-item').forEach(item => {
      item.addEventListener('click', () => {
        const q = item.getAttribute('data-query');
        closeDrawer();
        updateActiveChip(q);
        el.searchInput.value = q === 'tag:inbox' ? '' : q;
        runSearch(q);
      });
    });

    // Load more
    el.btnLoadMore.addEventListener('click', () => {
      state.offset += state.limit;
      runSearch(state.currentQuery, true);
    });

    // Detail Back
    el.btnDetailBack.addEventListener('click', showListView);

    // Detail Actions
    el.btnDetailArchive.addEventListener('click', () => {
      if (!state.currentThread) return;
      archiveThread(state.currentThread.thread_id, state.currentThread.subject);
      showListView();
    });

    el.btnDetailTrash.addEventListener('click', () => {
      if (!state.currentThread) return;
      trashThread(state.currentThread.thread_id, state.currentThread.subject);
      showListView();
    });

    el.btnDetailTag.addEventListener('click', () => {
      if (!state.currentThread) return;
      openTagModal(state.currentThread.thread_id, state.currentThread.tags);
    });

    el.btnDetailStar.addEventListener('click', () => {
      if (!state.currentThread) return;
      const isStarred = state.currentThread.tags.includes('flagged');
      toggleStar(state.currentThread.thread_id, !isStarred);
    });

    el.btnDetailReply.addEventListener('click', () => {
      if (!state.currentThread) return;
      const msgs = state.currentThread.messages;
      const target = msgs[msgs.length - 1];
      if (target) openComposeReply(target.id, false);
    });

    el.btnDetailReplyAll.addEventListener('click', () => {
      if (!state.currentThread) return;
      const msgs = state.currentThread.messages;
      const target = msgs[msgs.length - 1];
      if (target) openComposeReply(target.id, true);
    });

    // Compose
    el.btnComposeTop.addEventListener('click', () => openComposeNew());
    el.btnComposeClose.addEventListener('click', closeComposeSheet);
    el.composeBackdrop.addEventListener('click', closeComposeSheet);
    el.btnToggleCc.addEventListener('click', () => {
      el.composeCcRow.classList.toggle('hidden');
      el.composeBccRow.classList.toggle('hidden');
    });

    el.btnComposeSend.addEventListener('click', handleSend);

    // Autocomplete on To input
    el.composeTo.addEventListener('input', (e) => {
      clearTimeout(state.autocompleteTimer);
      const val = e.target.value.trim();
      if (val.length < 2) {
        el.toAutocompleteDropdown.classList.add('hidden');
        return;
      }
      state.autocompleteTimer = setTimeout(async () => {
        try {
          const matches = await api(`/api/contacts?q=${encodeURIComponent(val)}`);
          renderAutocomplete(matches);
        } catch (_) {}
      }, 150);
    });

    document.addEventListener('click', (e) => {
      if (!el.toAutocompleteDropdown.contains(e.target) && e.target !== el.composeTo) {
        el.toAutocompleteDropdown.classList.add('hidden');
      }
    });

    // Attachments Upload
    el.composeFileInput.addEventListener('change', (e) => {
      const files = Array.from(e.target.files || []);
      state.composeAttachments.push(...files);
      renderComposeAttachments();
      el.composeFileInput.value = '';
    });

    // Tag Modal
    el.btnTagModalDone.addEventListener('click', closeTagModal);
    el.tagModalBackdrop.addEventListener('click', closeTagModal);
    el.btnAddCustomTag.addEventListener('click', addCustomTag);
    el.tagNewInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        addCustomTag();
      }
    });

    // Toast Undo
    el.toastUndo.addEventListener('click', handleUndo);
  }

  // --- Search and Threads ---
  async function runSearch(query, append = false) {
    state.currentQuery = query;
    if (!append) {
      state.offset = 0;
      el.threadList.innerHTML = `
        <div class="loading-spinner-wrap">
          <div class="spinner"></div>
          <span>Loading threads…</span>
        </div>`;
    }

    try {
      const threads = await api(`/api/search?q=${encodeURIComponent(query)}&limit=${state.limit}&offset=${state.offset}`);
      if (!append) {
        state.threads = threads;
      } else {
        state.threads.push(...threads);
      }
      renderThreadList(state.threads);

      if (threads.length >= state.limit) {
        el.loadMoreWrap.classList.remove('hidden');
      } else {
        el.loadMoreWrap.classList.add('hidden');
      }
    } catch (err) {
      el.threadList.innerHTML = `<div class="empty-hint">Error: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderThreadList(threads) {
    if (!threads || threads.length === 0) {
      el.threadList.innerHTML = `<div class="empty-hint">No messages match "${escapeHtml(state.currentQuery)}"</div>`;
      return;
    }

    el.threadList.innerHTML = '';
    threads.forEach(t => {
      const card = document.createElement('div');
      const isUnread = (t.tags || []).includes('unread');
      const isStarred = (t.tags || []).includes('flagged');
      card.className = `thread-card ${isUnread ? 'unread' : ''}`;
      card.setAttribute('data-thread-id', t.thread);

      const tagsHtml = (t.tags || []).map(tag => {
        let tagClass = 'tag-badge';
        if (tag === 'unread') tagClass += ' tag-unread';
        if (tag === 'flagged') tagClass += ' tag-flagged';
        if (tag === 'trash') tagClass += ' tag-trash';
        return `<span class="${tagClass}">${escapeHtml(tag)}</span>`;
      }).join('');

      card.innerHTML = `
        <div class="thread-card-top">
          <span class="thread-authors">${escapeHtml(t.authors || '(no author)')}</span>
          <span class="thread-date">${escapeHtml(t.date_relative || '')}</span>
        </div>
        <div class="thread-subject">${escapeHtml(t.subject || '(no subject)')}</div>
        <div class="thread-card-bottom">
          <div class="thread-tags">${tagsHtml}</div>
          <div class="thread-quick-actions">
            <button class="quick-action-btn action-archive" title="Archive" aria-label="Archive">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="21 8 21 21 3 21 3 8"></polyline><rect x="1" y="3" width="22" height="5"></rect><line x1="10" y1="12" x2="14" y2="12"></line></svg>
            </button>
            <button class="quick-action-btn action-trash" title="Trash" aria-label="Trash">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
            </button>
            <button class="quick-action-btn action-tag" title="Tags" aria-label="Tags">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"></path><line x1="7" y1="7" x2="7.01" y2="7"></line></svg>
            </button>
            <button class="quick-action-btn action-star" title="Star" aria-label="Star">
              <svg class="star-icon ${isStarred ? 'starred' : ''}" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
            </button>
          </div>
        </div>
      `;

      // Click on card body opens detail view
      card.addEventListener('click', (e) => {
        if (e.target.closest('.thread-quick-actions')) return;
        openThread(t.thread);
      });

      // Quick actions
      card.querySelector('.action-archive').addEventListener('click', (e) => {
        e.stopPropagation();
        archiveThread(t.thread, t.subject);
      });

      card.querySelector('.action-trash').addEventListener('click', (e) => {
        e.stopPropagation();
        trashThread(t.thread, t.subject);
      });

      card.querySelector('.action-tag').addEventListener('click', (e) => {
        e.stopPropagation();
        openTagModal(t.thread, t.tags || []);
      });

      card.querySelector('.action-star').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleStar(t.thread, !isStarred);
      });

      el.threadList.appendChild(card);
    });
  }

  // --- Thread Detail View ---
  async function openThread(threadId) {
    showDetailView();
    el.detailSubject.textContent = 'Loading…';
    el.detailTagsRow.innerHTML = '';
    el.detailMessagesList.innerHTML = `
      <div class="loading-spinner-wrap">
        <div class="spinner"></div>
        <span>Loading messages…</span>
      </div>`;

    try {
      const data = await api(`/api/threads/${encodeURIComponent(threadId)}`);
      state.currentThread = data;
      renderThreadDetail(data);

      // Auto-mark unread tag removed locally if present
      if (data.tags.includes('unread')) {
        api('/api/tag', {
          method: 'POST',
          body: JSON.stringify({ ids: [`thread:${threadId}`], add: [], remove: ['unread'] })
        }).catch(() => {});
      }
    } catch (err) {
      el.detailMessagesList.innerHTML = `<div class="empty-hint">Error: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderThreadDetail(data) {
    el.detailSubject.textContent = data.subject || '(no subject)';

    // Render tags
    el.detailTagsRow.innerHTML = '';
    (data.tags || []).forEach(tag => {
      const pill = document.createElement('span');
      pill.className = 'tag-badge';
      pill.textContent = tag;
      el.detailTagsRow.appendChild(pill);
    });
    const addBtn = document.createElement('button');
    addBtn.className = 'add-tag-pill';
    addBtn.textContent = '+ Tag';
    addBtn.addEventListener('click', () => openTagModal(data.thread_id, data.tags));
    el.detailTagsRow.appendChild(addBtn);

    // Star icon state
    const isStarred = (data.tags || []).includes('flagged');
    const starSvg = el.btnDetailStar.querySelector('.star-icon');
    if (isStarred) {
      starSvg.classList.add('starred');
    } else {
      starSvg.classList.remove('starred');
    }

    // Render messages
    el.detailMessagesList.innerHTML = '';
    const msgs = data.messages || [];
    msgs.forEach((m, idx) => {
      const isLast = idx === msgs.length - 1;
      const isUnread = (m.tags || []).includes('unread');
      const card = document.createElement('article');
      card.className = `message-card ${!isLast && !isUnread ? 'collapsed' : ''}`;

      const nameOrAddr = m.from.split('<')[0].trim() || m.from;
      const initials = (nameOrAddr[0] || '?').toUpperCase();

      let bodyContentHtml = '';
      if (m.body_html) {
        // Embed inside responsive sandboxed iframe
        const frameId = `frame-${m.id.replace(/[^a-zA-Z0-9]/g, '_')}`;
        bodyContentHtml = `<iframe id="${frameId}" class="message-html-frame" sandbox="allow-same-origin" srcdoc="${escapeAttr(m.body_html)}"></iframe>`;
      } else {
        bodyContentHtml = `<pre class="message-plain-body">${linkify(escapeHtml(m.body_text || ''))}</pre>`;
      }

      let attachmentsHtml = '';
      if (m.attachments && m.attachments.length > 0) {
        attachmentsHtml = `
          <div class="attachments-box">
            <div class="attachments-title">${m.attachments.length} Attachment(s)</div>
            <div class="attachments-grid">
              ${m.attachments.map(att => `
                <a href="/api/messages/${encodeURIComponent(m.id)}/parts/${att.part_id}" download="${escapeAttr(att.filename)}" class="attachment-item">
                  <span class="attachment-name">${escapeHtml(att.filename)}</span>
                  <span class="attachment-size">${formatBytes(att.size)}</span>
                </a>
              `).join('')}
            </div>
          </div>
        `;
      }

      card.innerHTML = `
        <div class="message-header">
          <div class="message-avatar">${initials}</div>
          <div class="message-header-info">
            <div class="message-from-line">
              <span class="message-from">${escapeHtml(m.from)}</span>
              <span class="message-date">${escapeHtml(m.date_relative || m.date)}</span>
            </div>
            <div class="message-recipients">to ${escapeHtml(m.to || 'me')}</div>
          </div>
        </div>
        <div class="message-body-wrap">
          ${bodyContentHtml}
          ${attachmentsHtml}
        </div>
      `;

      // Header click toggles collapse
      card.querySelector('.message-header').addEventListener('click', () => {
        card.classList.toggle('collapsed');
        resizeIframes();
      });

      el.detailMessagesList.appendChild(card);
    });

    // Hook auto-height on all iframes
    setTimeout(resizeIframes, 50);
  }

  function resizeIframes() {
    document.querySelectorAll('.message-html-frame').forEach(frame => {
      try {
        if (frame.contentWindow && frame.contentWindow.document.body) {
          const h = frame.contentWindow.document.body.scrollHeight;
          if (h > 0) frame.style.height = `${h + 20}px`;
        }
      } catch (_) {}
    });
  }

  // --- Triage Actions ---
  async function archiveThread(threadId, subject) {
    const card = document.querySelector(`[data-thread-id="${threadId}"]`);
    if (card) {
      card.style.opacity = '0';
      card.style.transform = 'translateX(100px)';
      setTimeout(() => card.remove(), 200);
    }
    try {
      await api(`/api/threads/${encodeURIComponent(threadId)}/archive`, { method: 'POST' });
      showToast('Archived', {
        type: 'archive',
        threadId: threadId,
        subject: subject,
      });
    } catch (err) {
      showToast(`Archive failed: ${err.message}`);
    }
  }

  async function trashThread(threadId, subject) {
    const card = document.querySelector(`[data-thread-id="${threadId}"]`);
    if (card) {
      card.style.opacity = '0';
      card.style.transform = 'translateX(-100px)';
      setTimeout(() => card.remove(), 200);
    }
    try {
      await api(`/api/threads/${encodeURIComponent(threadId)}/trash`, { method: 'POST' });
      showToast('Moved to Trash', {
        type: 'trash',
        threadId: threadId,
        subject: subject,
      });
    } catch (err) {
      showToast(`Trash failed: ${err.message}`);
    }
  }

  async function toggleStar(threadId, flag) {
    try {
      await api(`/api/threads/${encodeURIComponent(threadId)}/star`, {
        method: 'POST',
        body: JSON.stringify({ flag })
      });
      // Update local state
      if (state.currentThread && state.currentThread.thread_id === threadId) {
        if (flag) {
          if (!state.currentThread.tags.includes('flagged')) state.currentThread.tags.push('flagged');
        } else {
          state.currentThread.tags = state.currentThread.tags.filter(t => t !== 'flagged');
        }
        renderThreadDetail(state.currentThread);
      }
      const card = document.querySelector(`[data-thread-id="${threadId}"]`);
      if (card) {
        const starSvg = card.querySelector('.star-icon');
        if (flag) starSvg.classList.add('starred');
        else starSvg.classList.remove('starred');
      }
    } catch (err) {
      showToast(`Flag update failed: ${err.message}`);
    }
  }

  async function handleUndo() {
    if (!state.undoAction) return;
    const { type, threadId } = state.undoAction;
    hideToast();
    try {
      if (type === 'archive') {
        await api(`/api/threads/${encodeURIComponent(threadId)}/unarchive`, { method: 'POST' });
      } else if (type === 'trash') {
        await api(`/api/threads/${encodeURIComponent(threadId)}/untrash`, { method: 'POST' });
      }
      runSearch(state.currentQuery);
    } catch (err) {
      showToast(`Undo failed: ${err.message}`);
    }
  }

  // --- Tag Management Modal ---
  function openTagModal(threadId, currentTags) {
    state.activeModalThreadId = threadId;
    state.activeModalTags = new Set(currentTags || []);
    el.tagNewInput.value = '';
    renderTagModalList();
    el.tagModalBackdrop.classList.remove('hidden');
    el.tagModal.classList.remove('hidden');
    setTimeout(() => el.tagModal.classList.add('open'), 10);
  }

  function renderTagModalList() {
    el.tagModalList.innerHTML = '';
    const allSet = new Set(state.allTags.map(t => t.name));
    state.activeModalTags.forEach(t => allSet.add(t));
    const sorted = Array.from(allSet).sort();

    sorted.forEach(tagName => {
      const isSelected = state.activeModalTags.has(tagName);
      const row = document.createElement('div');
      row.className = `tag-select-row ${isSelected ? 'selected' : ''}`;
      row.innerHTML = `
        <span>${escapeHtml(tagName)}</span>
        <span class="tag-check-mark">${isSelected ? '✓' : ''}</span>
      `;
      row.addEventListener('click', () => {
        if (state.activeModalTags.has(tagName)) {
          state.activeModalTags.delete(tagName);
        } else {
          state.activeModalTags.add(tagName);
        }
        renderTagModalList();
      });
      el.tagModalList.appendChild(row);
    });
  }

  function addCustomTag() {
    const val = el.tagNewInput.value.trim().toLowerCase();
    if (!val) return;
    state.activeModalTags.add(val);
    el.tagNewInput.value = '';
    renderTagModalList();
  }

  async function closeTagModal() {
    el.tagModal.classList.remove('open');
    setTimeout(() => {
      el.tagModal.classList.add('hidden');
      el.tagModalBackdrop.classList.add('hidden');
    }, 250);

    if (!state.activeModalThreadId) return;

    // Apply tag diff
    const threadId = state.activeModalThreadId;
    let oldTags = [];
    if (state.currentThread && state.currentThread.thread_id === threadId) {
      oldTags = state.currentThread.tags || [];
    } else {
      const card = state.threads.find(t => t.thread === threadId);
      if (card) oldTags = card.tags || [];
    }

    const newTags = Array.from(state.activeModalTags);
    const toAdd = newTags.filter(t => !oldTags.includes(t));
    const toRemove = oldTags.filter(t => !newTags.includes(t));

    if (toAdd.length === 0 && toRemove.length === 0) return;

    try {
      await api('/api/tag', {
        method: 'POST',
        body: JSON.stringify({
          ids: [`thread:${threadId}`],
          add: toAdd,
          remove: toRemove,
        })
      });

      if (state.currentThread && state.currentThread.thread_id === threadId) {
        state.currentThread.tags = newTags;
        renderThreadDetail(state.currentThread);
      }
      runSearch(state.currentQuery);
      loadTags();
    } catch (err) {
      showToast(`Tag update failed: ${err.message}`);
    }
  }

  // --- Compose and Replies ---
  function openComposeNew() {
    resetComposeForm();
    el.composeSheetTitle.textContent = 'New message';
    el.composeBackdrop.classList.remove('hidden');
    el.composeSheet.classList.remove('hidden');
    setTimeout(() => el.composeSheet.classList.add('open'), 10);
  }

  async function openComposeReply(msgId, toAll) {
    resetComposeForm();
    el.composeSheetTitle.textContent = toAll ? 'Reply All' : 'Reply';
    el.composeBackdrop.classList.remove('hidden');
    el.composeSheet.classList.remove('hidden');
    setTimeout(() => el.composeSheet.classList.add('open'), 10);

    try {
      const seed = await api(`/api/messages/${encodeURIComponent(msgId)}/reply-seed?to_all=${toAll}`);
      el.composeTo.value = seed.to || '';
      el.composeCc.value = seed.cc || '';
      if (seed.cc) el.composeCcRow.classList.remove('hidden');
      el.composeSubject.value = seed.subject || '';
      el.composeBody.value = seed.body || '';
      el.composeInReplyTo.value = seed.in_reply_to || '';
      el.composeReferences.value = seed.references || '';
      el.composeBody.focus();
      el.composeBody.setSelectionRange(0, 0);
    } catch (err) {
      showToast(`Could not load reply template: ${err.message}`);
    }
  }

  function resetComposeForm() {
    el.composeTo.value = '';
    el.composeCc.value = '';
    el.composeBcc.value = '';
    el.composeCcRow.classList.add('hidden');
    el.composeBccRow.classList.add('hidden');
    el.composeSubject.value = '';
    el.composeBody.value = '';
    el.composeInReplyTo.value = '';
    el.composeReferences.value = '';
    state.composeAttachments = [];
    renderComposeAttachments();
  }

  function closeComposeSheet() {
    el.composeSheet.classList.remove('open');
    setTimeout(() => {
      el.composeSheet.classList.add('hidden');
      el.composeBackdrop.classList.add('hidden');
    }, 250);
  }

  function renderComposeAttachments() {
    el.composeAttachmentsList.innerHTML = '';
    state.composeAttachments.forEach((file, idx) => {
      const chip = document.createElement('div');
      chip.className = 'compose-att-chip';
      chip.innerHTML = `
        <span>${escapeHtml(file.name)} (${formatBytes(file.size)})</span>
        <span class="att-remove-btn" data-idx="${idx}">&times;</span>
      `;
      chip.querySelector('.att-remove-btn').addEventListener('click', () => {
        state.composeAttachments.splice(idx, 1);
        renderComposeAttachments();
      });
      el.composeAttachmentsList.appendChild(chip);
    });
  }

  function renderAutocomplete(contacts) {
    if (!contacts || contacts.length === 0) {
      el.toAutocompleteDropdown.classList.add('hidden');
      return;
    }
    el.toAutocompleteDropdown.innerHTML = '';
    contacts.forEach(c => {
      const item = document.createElement('div');
      item.className = 'autocomplete-item';
      item.textContent = c.display || c.address;
      item.addEventListener('click', () => {
        el.composeTo.value = c.display || c.address;
        el.toAutocompleteDropdown.classList.add('hidden');
      });
      el.toAutocompleteDropdown.appendChild(item);
    });
    el.toAutocompleteDropdown.classList.remove('hidden');
  }

  async function handleSend() {
    const to = el.composeTo.value.trim();
    if (!to) {
      alert('Please specify at least one recipient.');
      return;
    }

    const sendBtnText = el.btnComposeSend.querySelector('.send-text');
    const sendSpinner = el.btnComposeSend.querySelector('.send-spinner');
    sendBtnText.classList.add('hidden');
    sendSpinner.classList.remove('hidden');
    el.btnComposeSend.disabled = true;

    try {
      const formData = new FormData();
      formData.append('account', el.composeAccountSelect.value || '');
      formData.append('to', to);
      formData.append('cc', el.composeCc.value.trim());
      formData.append('bcc', el.composeBcc.value.trim());
      formData.append('subject', el.composeSubject.value.trim());
      formData.append('body_text', el.composeBody.value);
      formData.append('in_reply_to', el.composeInReplyTo.value);
      formData.append('references', el.composeReferences.value);

      state.composeAttachments.forEach(f => {
        formData.append('attachment', f, f.name);
      });

      await api('/api/send', {
        method: 'POST',
        body: formData,
      });

      closeComposeSheet();
      showToast('Message sent');
      runSearch(state.currentQuery);
    } catch (err) {
      alert(`Send failed: ${err.message}`);
    } finally {
      sendBtnText.classList.remove('hidden');
      sendSpinner.classList.add('hidden');
      el.btnComposeSend.disabled = false;
    }
  }

  // --- Drawer & Tags ---
  function openDrawer() {
    el.drawerBackdrop.classList.remove('hidden');
    el.drawer.classList.add('open');
  }

  function closeDrawer() {
    el.drawer.classList.remove('open');
    setTimeout(() => el.drawerBackdrop.classList.add('hidden'), 220);
  }

  async function loadAccounts() {
    try {
      const data = await api('/api/accounts');
      state.accounts = data.accounts || [];
      el.composeAccountSelect.innerHTML = '';
      state.accounts.forEach(acct => {
        const opt = document.createElement('option');
        opt.value = acct;
        opt.textContent = acct;
        el.composeAccountSelect.appendChild(opt);
      });
      if (state.accounts.length > 1) {
        el.composeAccountRow.classList.remove('hidden');
      }
      if (state.accounts.length > 0) {
        el.drawerAccountLabel.textContent = state.accounts[0];
      }
    } catch (_) {}
  }

  async function loadTags() {
    try {
      const tags = await api('/api/tags');
      state.allTags = tags || [];
      renderDrawerTags(state.allTags);
    } catch (_) {}
  }

  function renderDrawerTags(tags) {
    el.drawerTagsList.innerHTML = '';
    tags.forEach(t => {
      const row = document.createElement('button');
      row.className = 'drawer-tag-item';
      row.innerHTML = `
        <span>${escapeHtml(t.name)}</span>
        <span class="drawer-tag-count">${t.count}</span>
      `;
      row.addEventListener('click', () => {
        closeDrawer();
        const q = `tag:${t.name}`;
        updateActiveChip(q);
        el.searchInput.value = q;
        runSearch(q);
      });
      el.drawerTagsList.appendChild(row);
    });
  }

  function updateActiveChip(query) {
    el.chips.forEach(c => {
      if (c.getAttribute('data-query') === query) {
        c.classList.add('active');
      } else {
        c.classList.remove('active');
      }
    });
  }

  // --- Views Navigation ---
  function showListView() {
    el.viewDetail.classList.remove('active-view');
    el.viewDetail.classList.add('hidden-view');
    el.viewList.classList.remove('hidden-view');
    el.viewList.classList.add('active-view');
    state.currentThread = null;
  }

  function showDetailView() {
    el.viewList.classList.remove('active-view');
    el.viewList.classList.add('hidden-view');
    el.viewDetail.classList.remove('hidden-view');
    el.viewDetail.classList.add('active-view');
  }

  // --- Toast ---
  function showToast(msg, undoAction = null) {
    clearTimeout(state.undoTimer);
    el.toastMessage.textContent = msg;
    state.undoAction = undoAction;
    if (undoAction) {
      el.toastUndo.classList.remove('hidden');
    } else {
      el.toastUndo.classList.add('hidden');
    }
    el.toast.classList.remove('hidden');
    state.undoTimer = setTimeout(hideToast, 5000);
  }

  function hideToast() {
    el.toast.classList.add('hidden');
    state.undoAction = null;
  }

  // --- Utilities ---
  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function escapeAttr(str) {
    return escapeHtml(str);
  }

  function linkify(str) {
    const urlPattern = /(https?:\/\/[^\s<]+[^<.,:;"')\]\s])/g;
    return str.replace(urlPattern, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
  }

  function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  // Start
  document.addEventListener('DOMContentLoaded', init);
})();
