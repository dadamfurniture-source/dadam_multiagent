/**
 * Dadam SaaS Frontend - Project creation, SSE streaming, result display
 */

const API_BASE = '/api/v1';

// Auth token (set after login)
let authToken = localStorage.getItem('dadam_token') || '';

function setToken(token) {
  authToken = token;
  localStorage.setItem('dadam_token', token);
}

function apiHeaders() {
  return {
    'Authorization': `Bearer ${authToken}`,
    'Content-Type': 'application/json',
  };
}

// ============================================================
// Category & Style Data
// ============================================================

const CATEGORIES = {
  sink: { label: '싱크대', emoji: '🚰' },
  island: { label: '아일랜드', emoji: '🏝️' },
  closet: { label: '붙박이장', emoji: '👔' },
  fridge_cabinet: { label: '냉장고장', emoji: '🧊' },
  shoe_cabinet: { label: '신발장', emoji: '👟' },
  vanity: { label: '화장대', emoji: '💄' },
  storage: { label: '수납장', emoji: '📦' },
  utility_closet: { label: '창고장', emoji: '🧹' },
};

const STYLES = ['modern', 'nordic', 'classic', 'natural', 'industrial', 'luxury'];
const STYLE_LABELS = {
  modern: '모던', nordic: '북유럽', classic: '클래식',
  natural: '내추럴', industrial: '인더스트리얼', luxury: '럭셔리',
};

// ============================================================
// Page: New Project
// ============================================================

function initNewProjectPage() {
  const form = document.getElementById('project-form');
  if (!form) return;

  let selectedCategory = null;
  let selectedStyle = null;
  let selectedFile = null;

  // Category grid
  const categoryGrid = document.getElementById('category-grid');
  Object.entries(CATEGORIES).forEach(([key, { label, emoji }]) => {
    const div = document.createElement('div');
    div.className = 'category-item';
    div.innerHTML = `<span class="category-emoji">${emoji}</span>${label}`;
    div.onclick = () => {
      document.querySelectorAll('.category-item').forEach(el => el.classList.remove('selected'));
      div.classList.add('selected');
      selectedCategory = key;
    };
    categoryGrid.appendChild(div);
  });

  // Style grid
  const styleGrid = document.getElementById('style-grid');
  STYLES.forEach(style => {
    const div = document.createElement('div');
    div.className = 'style-item';
    div.textContent = STYLE_LABELS[style] || style;
    div.onclick = () => {
      document.querySelectorAll('.style-item').forEach(el => el.classList.remove('selected'));
      div.classList.add('selected');
      selectedStyle = style;
    };
    styleGrid.appendChild(div);
  });

  // Upload zone
  const uploadZone = document.getElementById('upload-zone');
  const fileInput = document.getElementById('file-input');
  const previewImg = document.getElementById('upload-preview');

  uploadZone.onclick = () => fileInput.click();
  uploadZone.ondragover = (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); };
  uploadZone.ondragleave = () => uploadZone.classList.remove('dragover');
  uploadZone.ondrop = (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    handleFile(e.dataTransfer.files[0]);
  };
  fileInput.onchange = (e) => handleFile(e.target.files[0]);

  function handleFile(file) {
    if (!file || !file.type.startsWith('image/')) return;
    selectedFile = file;
    const reader = new FileReader();
    reader.onload = (e) => {
      previewImg.src = e.target.result;
      previewImg.style.display = 'block';
      uploadZone.querySelector('.upload-text').style.display = 'none';
      uploadZone.querySelector('.upload-icon').style.display = 'none';
    };
    reader.readAsDataURL(file);
  }

  // Submit
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (!selectedFile) return alert('사진을 업로드해주세요.');
    if (!selectedCategory) return alert('가구 카테고리를 선택해주세요.');

    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = '프로젝트 생성 중...';

    try {
      const formData = new FormData();
      formData.append('image', selectedFile);
      formData.append('category', selectedCategory);
      if (selectedStyle) formData.append('style', selectedStyle);

      const budgetInput = document.getElementById('budget');
      if (budgetInput && budgetInput.value) formData.append('budget', budgetInput.value);

      const notesInput = document.getElementById('notes');
      if (notesInput && notesInput.value) formData.append('notes', notesInput.value);

      const resp = await fetch(`${API_BASE}/projects`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${authToken}` },
        body: formData,
      });

      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || data.message || 'Failed');

      const projectId = data.data.project_id;

      // Start AI pipeline
      await fetch(`${API_BASE}/projects/${projectId}/run`, {
        method: 'POST',
        headers: apiHeaders(),
      });

      // Navigate to result page with SSE
      window.location.href = `/project.html?id=${projectId}`;

    } catch (err) {
      alert(`오류: ${err.message}`);
      submitBtn.disabled = false;
      submitBtn.textContent = '시뮬레이션 시작';
    }
  };
}

// ============================================================
// Page: Project Result (SSE streaming)
// ============================================================

const PIPELINE_STAGES = ['space_analysis', 'design', 'image_gen', 'quote'];
const STAGE_LABELS = {
  space_analysis: '공간 분석',
  design: '배치 설계',
  image_gen: '이미지 생성',
  quote: '견적 산출',
};

function initProjectPage() {
  const urlParams = new URLSearchParams(window.location.search);
  const projectId = urlParams.get('id');
  if (!projectId) return;

  // Render pipeline steps
  const stepsEl = document.getElementById('pipeline-steps');
  if (stepsEl) {
    PIPELINE_STAGES.forEach(stage => {
      const div = document.createElement('div');
      div.className = 'pipeline-step';
      div.id = `step-${stage}`;
      div.textContent = STAGE_LABELS[stage];
      stepsEl.appendChild(div);
    });
  }

  // Start SSE connection
  const eventSource = new EventSource(`${API_BASE}/projects/${projectId}/stream`);
  let currentStageIdx = 0;

  eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);

    switch (data.type) {
      case 'status':
        updateStatus(data.stage);
        if (data.stage === 'completed') {
          eventSource.close();
          loadProjectResult(projectId);
        }
        break;

      case 'progress':
        appendLog(data.content);
        // Auto-advance pipeline steps based on content keywords
        if (data.content.includes('space') || data.content.includes('analyz')) {
          setStageActive('space_analysis');
        } else if (data.content.includes('layout') || data.content.includes('design')) {
          setStageDone('space_analysis');
          setStageActive('design');
        } else if (data.content.includes('image') || data.content.includes('generat')) {
          setStageDone('design');
          setStageActive('image_gen');
        } else if (data.content.includes('quote') || data.content.includes('price')) {
          setStageDone('image_gen');
          setStageActive('quote');
        }
        break;

      case 'result':
        setStageDone('quote');
        eventSource.close();
        displayResult(data.data);
        break;

      case 'error':
        eventSource.close();
        showError(data.error);
        break;
    }
  };

  eventSource.onerror = () => {
    eventSource.close();
    // Check if project already completed
    loadProjectResult(projectId);
  };
}

function setStageActive(stage) {
  const el = document.getElementById(`step-${stage}`);
  if (el) el.className = 'pipeline-step active';
}

function setStageDone(stage) {
  const el = document.getElementById(`step-${stage}`);
  if (el) el.className = 'pipeline-step done';
}

function updateStatus(stage) {
  const statusEl = document.getElementById('project-status');
  if (statusEl) statusEl.textContent = stage;
}

function appendLog(content) {
  const logEl = document.getElementById('progress-log');
  if (!logEl) return;
  const line = document.createElement('div');
  line.textContent = content;
  line.style.fontSize = '12px';
  line.style.color = '#64748b';
  line.style.padding = '2px 0';
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function showError(message) {
  const el = document.getElementById('error-message');
  if (el) {
    el.textContent = message;
    el.style.display = 'block';
  }
}

async function loadProjectResult(projectId) {
  try {
    const resp = await fetch(`${API_BASE}/projects/${projectId}`, { headers: apiHeaders() });
    const data = await resp.json();
    if (data.data) displayFullResult(data.data);
  } catch (err) {
    console.error('Failed to load result:', err);
  }
}

function displayResult(result) {
  // Display simulation images
  const imagesEl = document.getElementById('result-images');
  if (!imagesEl || !result) return;

  if (result.images) {
    imagesEl.innerHTML = '';
    result.images.forEach(img => {
      const div = document.createElement('div');
      div.className = 'result-image';
      div.innerHTML = `
        <img src="${img.url || `data:image/png;base64,${img.image_base64}`}" alt="${img.stage || img.type}">
        <div class="label">${img.stage || img.type}</div>
      `;
      imagesEl.appendChild(div);
    });
  }

  // Display quote
  if (result.quote) displayQuote(result.quote);
}

function displayFullResult(data) {
  const imagesEl = document.getElementById('result-images');
  if (imagesEl && data.images) {
    imagesEl.innerHTML = '';
    data.images.forEach(img => {
      const div = document.createElement('div');
      div.className = 'result-image';
      div.innerHTML = `
        <img src="${img.image_url}" alt="${img.type}">
        <div class="label">${img.type}</div>
      `;
      imagesEl.appendChild(div);
    });
  }

  if (data.quote) displayQuote(data.quote);
  if (data.layout) displayLayout(data.layout);

  // Mark all stages done
  PIPELINE_STAGES.forEach(s => setStageDone(s));
}

function displayQuote(quote) {
  const quoteEl = document.getElementById('quote-section');
  if (!quoteEl) return;
  quoteEl.style.display = 'block';

  const items = quote.items_json || quote.items || [];
  const total = quote.total_price || quote.total || 0;

  let html = '<table class="quote-table"><thead><tr><th>항목</th><th>수량</th><th>단가</th><th>금액</th></tr></thead><tbody>';
  items.forEach(item => {
    html += `<tr>
      <td>${item.name || item.module}</td>
      <td>${item.quantity || 1}</td>
      <td>${(item.unit_price || item.base_price || 0).toLocaleString()}원</td>
      <td>${(item.total || 0).toLocaleString()}원</td>
    </tr>`;
  });
  html += `<tr class="total-row"><td colspan="3">합계</td><td>${total.toLocaleString()}원</td></tr>`;
  html += '</tbody></table>';

  quoteEl.innerHTML = `<h3 style="margin-bottom:16px">견적서</h3>${html}`;
}

function displayLayout(layout) {
  const layoutEl = document.getElementById('layout-section');
  if (!layoutEl || !layout.layout_json) return;
  layoutEl.style.display = 'block';

  const data = typeof layout.layout_json === 'string' ? JSON.parse(layout.layout_json) : layout.layout_json;
  const modules = data.modules || [];

  let html = '<h3 style="margin-bottom:16px">배치 계획</h3><div style="display:flex;gap:4px;align-items:flex-end;padding:20px;background:#f8fafc;border-radius:8px">';
  modules.forEach(m => {
    const widthPx = Math.max(40, m.width_mm / 10);
    const heightPx = m.type === 'upper_cabinet' ? 60 : 80;
    const color = m.features?.includes('sink_bowl') ? '#60a5fa' :
                  m.features?.includes('gas_range') ? '#f97316' : '#94a3b8';
    html += `<div style="width:${widthPx}px;height:${heightPx}px;background:${color};border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:10px;color:white" title="${m.type} ${m.width_mm}mm">${m.width_mm}</div>`;
  });
  html += '</div>';
  layoutEl.innerHTML = html;
}

// ============================================================
// Page: Project List
// ============================================================

async function initProjectListPage() {
  const listEl = document.getElementById('project-list');
  if (!listEl) return;

  try {
    const resp = await fetch(`${API_BASE}/projects`, { headers: apiHeaders() });
    const data = await resp.json();

    if (!data.data?.items?.length) {
      listEl.innerHTML = '<p style="text-align:center;color:var(--text-secondary);padding:48px">아직 프로젝트가 없습니다.</p>';
      return;
    }

    listEl.innerHTML = data.data.items.map(p => `
      <a href="/project.html?id=${p.id}" class="card" style="display:block;text-decoration:none;color:inherit;margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div>
            <strong>${p.name || CATEGORIES[p.category]?.label || p.category}</strong>
            <div style="font-size:12px;color:var(--text-secondary);margin-top:4px">
              ${new Date(p.created_at).toLocaleDateString('ko-KR')}
              ${p.style ? ` · ${STYLE_LABELS[p.style] || p.style}` : ''}
            </div>
          </div>
          <span class="badge badge-${p.status}">${p.status}</span>
        </div>
      </a>
    `).join('');

  } catch (err) {
    listEl.innerHTML = `<p style="color:var(--error)">로딩 실패: ${err.message}</p>`;
  }
}

// ============================================================
// Init
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
  initNewProjectPage();
  initProjectPage();
  initProjectListPage();
});
