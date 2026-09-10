document.addEventListener('DOMContentLoaded', () => {
  bindNavigation();
  bindPageActions();
  initializeSchedulePage();
  initializeMailingPage();
  initializeMailboxPage();
  initializeUploadPage();
});

const scheduleRanges = [];
const scheduleDays = [
  { key: 'sun', label: 'א' },
  { key: 'mon', label: 'ב' },
  { key: 'tue', label: 'ג' },
  { key: 'wed', label: 'ד' },
  { key: 'thu', label: 'ה' },
  { key: 'fri', label: 'ו' },
  { key: 'sat', label: 'ש' }
];

function bindNavigation() {
  document.querySelectorAll('[data-target]').forEach((button) => {
    button.addEventListener('click', () => {
      window.location.href = button.dataset.target;
    });
  });
}

function bindPageActions() {
  document.addEventListener('click', (event) => {
    const actionButton = event.target.closest('[data-action]');
    if (!actionButton) return;

    const action = actionButton.dataset.action;
    if (action === 'add-range') addOrUpdateRange();
    if (action === 'cancel-edit') resetRangeEditor();
    if (action === 'edit-range') editRange(Number(actionButton.dataset.index));
    if (action === 'delete-range') deleteRange(Number(actionButton.dataset.index));
    if (action === 'save-schedule') saveSchedule();
    if (action === 'upload-file') handleUpload();
    if (action === 'add-email') addEmailRow();
    if (action === 'delete-email') deleteEmailRow(actionButton);
    if (action === 'save-mailing-list') saveMailingList();
  });

}

async function initializeSchedulePage() {
  if (!document.getElementById('rangeList')) return;

  try {
    const ranges = await apiRequest('/api/v1/schedule');
    scheduleRanges.splice(0, scheduleRanges.length, ...normalizeScheduleRanges(ranges));
    renderScheduleRanges();
  } catch {
    renderScheduleRanges();
    setStatus(document.getElementById('pageStatus'), 'לא ניתן לטעון את טווחי התאריכים מהשרת.', true);
  }
}

function initializeUploadPage() {
  const input = document.getElementById('excelUpload');
  if (!input) return;

  input.addEventListener('change', () => {
    const selectedFile = document.getElementById('selectedFile');
    const status = document.getElementById('pageStatus');

    if (!input.files.length) {
      selectedFile.textContent = 'קובץ עבור: לא נבחר קובץ';
      setStatus(status, '');
      return;
    }

    const file = input.files[0];
    if (!isExcelFile(file)) {
      input.value = '';
      selectedFile.textContent = 'קובץ עבור: לא נבחר קובץ';
      setStatus(status, 'סוג הקובץ אינו נתמך. יש לבחור קובץ Excel בפורמט xlsx או xls.', true);
      return;
    }

    selectedFile.textContent = `קובץ עבור: ${file.name}`;
    setStatus(status, '');
  });
}

async function initializeMailingPage() {
  if (!document.getElementById('emailList')) return;

  try {
    const emails = await apiRequest('/api/v1/mailing_list');
    renderMailingList(Array.isArray(emails) ? emails : []);
  } catch {
    renderMailingEmptyState();
    setStatus(document.getElementById('pageStatus'), 'לא ניתן לטעון את רשימת התפוצה מהשרת.', true);
  }
}

async function initializeMailboxPage() {
  const emailField = document.getElementById('senderEmail');
  if (!emailField) return;

  const status = document.getElementById('pageStatus');
  try {
    const notifier = await apiRequest('/api/v1/notifier');
    emailField.textContent = notifier?.email || '—';
    if (notifier?.configured) {
      setStatus(status, 'תיבת הדואר מוגדרת.');
    } else {
      setStatus(status, 'תיבת הדואר לא מוגדרת. יש למלא את ערכי Google בקובץ .env, להריץ bot/gmail_auth.py ולהפעיל את השרת מחדש.', true);
    }
  } catch {
    setStatus(status, 'לא ניתן לטעון את הגדרות תיבת הדואר מהשרת.', true);
  }
}

async function apiRequest(path, options = {}) {
  const response = await fetch("http://127.0.0.1:5000"+path, options);
  const text = await response.text();
  let data = text;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }

  if (!response.ok) {
    throw new Error(data?.detail || data?.message || data?.error || text || 'הבקשה נכשלה.');
  }

  return data;
}

function setStatus(element, message, isError = false) {
  if (!element) return;
  element.textContent = message;
  element.style.color = isError ? '#c62828' : '#2e7d32';
}

function addOrUpdateRange() {
  const status = document.getElementById('pageStatus');
  const startDate = document.getElementById('startDate')?.value;
  const endDate = document.getElementById('endDate')?.value;
  const editingIndex = document.getElementById('editingRangeIndex')?.value;
  const days = getRangeDayConfig();

  if (!startDate || !endDate) {
    setStatus(status, 'יש לבחור תאריך התחלה ותאריך סיום.', true);
    return;
  }

  if (new Date(startDate) > new Date(endDate)) {
    setStatus(status, 'תאריך ההתחלה חייב להיות לפני תאריך הסיום.', true);
    return;
  }

  if (!Object.values(days).some((day) => day.enabled)) {
    setStatus(status, 'יש לבחור לפחות יום אחד עבור הטווח.', true);
    return;
  }

  const range = { from: startDate, to: endDate, days };

  if (editingIndex !== '') {
    scheduleRanges[Number(editingIndex)] = range;
    setStatus(status, 'הטווח עודכן.');
  } else {
    scheduleRanges.push(range);
    setStatus(status, 'הטווח נוסף.');
  }

  resetRangeEditor();
  renderScheduleRanges();
}

async function saveSchedule() {
  const status = document.getElementById('pageStatus');

  if (!scheduleRanges.length) {
    setStatus(status, 'יש להוסיף לפחות טווח תאריכים אחד לפני שמירה.', true);
    return;
  }

  try {
    await apiRequest('/api/v1/schedule', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(scheduleRanges)
    });
    setStatus(status, 'הסוכן נשמר בהצלחה.');
  } catch (error) {
    setStatus(status, error.message || 'לא ניתן לשמור את הגדרות הסוכן.', true);
  }
}

function getRangeDayConfig() {
  return scheduleDays.reduce((days, day) => {
    days[day.key] = {
      enabled: document.querySelector(`[data-day-enabled="${day.key}"]`)?.checked || false,
      time: document.querySelector(`[data-day-time="${day.key}"]`)?.value || '08:00'
    };
    return days;
  }, {});
}

function normalizeScheduleRanges(ranges) {
  if (!Array.isArray(ranges)) return [];

  return ranges
    .filter((range) => range && typeof range === 'object')
    .map((range) => ({
      from: range.from || '',
      to: range.to || '',
      days: normalizeScheduleDays(range)
    }))
    .filter((range) => range.from && range.to);
}

function normalizeScheduleDays(range) {
  return scheduleDays.reduce((days, day) => {
    const dayConfig = range.days?.[day.key];
    if (dayConfig && typeof dayConfig === 'object') {
      days[day.key] = {
        enabled: Boolean(dayConfig.enabled),
        time: dayConfig.time || '08:00'
      };
      return days;
    }

    days[day.key] = {
      enabled: Boolean(range[day.key]),
      time: range.time || '08:00'
    };
    return days;
  }, {});
}

function editRange(index) {
  const range = scheduleRanges[index];
  if (!range) return;

  document.getElementById('editingRangeIndex').value = String(index);
  document.getElementById('startDate').value = range.from;
  document.getElementById('endDate').value = range.to;

  scheduleDays.forEach((day) => {
    const checkbox = document.querySelector(`[data-day-enabled="${day.key}"]`);
    const time = document.querySelector(`[data-day-time="${day.key}"]`);
    checkbox.checked = Boolean(range.days[day.key]?.enabled);
    time.value = range.days[day.key]?.time || '08:00';
  });

  document.querySelector('[data-action="add-range"]').textContent = 'עדכן טווח';
  setStatus(document.getElementById('pageStatus'), '');
}

function deleteRange(index) {
  scheduleRanges.splice(index, 1);
  resetRangeEditor();
  renderScheduleRanges();
  setStatus(document.getElementById('pageStatus'), 'הטווח נמחק.');
}

function resetRangeEditor() {
  const editingInput = document.getElementById('editingRangeIndex');
  if (!editingInput) return;

  editingInput.value = '';
  document.getElementById('startDate').value = '';
  document.getElementById('endDate').value = '';
  scheduleDays.forEach((day) => {
    document.querySelector(`[data-day-enabled="${day.key}"]`).checked = false;
    document.querySelector(`[data-day-time="${day.key}"]`).value = '08:00';
  });
  document.querySelector('[data-action="add-range"]').textContent = 'הוסף טווח';
}

function renderScheduleRanges() {
  const list = document.getElementById('rangeList');
  if (!list) return;

  if (!scheduleRanges.length) {
    list.innerHTML = '<div class="empty-ranges">לא נוספו טווחי תאריכים</div>';
    return;
  }

  list.innerHTML = scheduleRanges.map((range, index) => `
    <div class="range-card">
      <div class="range-card-header">
        <strong>${escapeHtml(range.from)} - ${escapeHtml(range.to)}</strong>
        <div class="range-card-actions">
          <button type="button" data-action="edit-range" data-index="${index}" aria-label="ערוך טווח"><i class="fa-solid fa-pen"></i></button>
          <button type="button" data-action="delete-range" data-index="${index}" aria-label="מחק טווח"><i class="fa-solid fa-trash"></i></button>
        </div>
      </div>
      <div class="range-card-days">
        ${formatRangeDays(range)}
      </div>
    </div>
  `).join('');
}

function formatRangeDays(range) {
  return scheduleDays
    .filter((day) => range.days[day.key]?.enabled)
    .map((day) => `<span>${day.label} ${escapeHtml(range.days[day.key].time)}</span>`)
    .join('');
}

function isExcelFile(file) {
  const extension = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
  return ['.xlsx', '.xls'].includes(extension);
}

async function handleUpload() {
  const input = document.getElementById('excelUpload');
  const status = document.getElementById('pageStatus');

  if (!input.files.length) {
    setStatus(status, 'יש לבחור קובץ לפני ההעלאה.', true);
    return;
  }

  const file = input.files[0];
  if (!isExcelFile(file)) {
    setStatus(status, 'סוג הקובץ אינו נתמך. יש לבחור קובץ Excel בפורמט xlsx או xls.', true);
    return;
  }

  const formData = new FormData();
  formData.append('file', file);

  try {
    await apiRequest('/api/v1/master', {
      method: 'POST',
      body: formData
    });
    setStatus(status, 'הקובץ הועלה בהצלחה.');
  } catch (error) {
    setStatus(status, error.message || 'לא ניתן להעלות את הקובץ לשרת.', true);
  }
}

function addEmailRow() {
  const emailInput = document.getElementById('newEmail');
  const email = emailInput.value.trim();
  const status = document.getElementById('pageStatus');

  if (!emailInput.checkValidity() || !email) {
    setStatus(status, 'יש להזין כתובת אימייל תקינה.', true);
    return;
  }

  document.querySelector('#emailList .empty-mailing')?.remove();
  document.getElementById('emailList').appendChild(createEmailRow(email));
  emailInput.value = '';
  setStatus(status, '');
}

function deleteEmailRow(button) {
  button.closest('.email-row')?.remove();
  renderMailingEmptyState();
}

function renderMailingEmptyState() {
  const emailList = document.getElementById('emailList');
  if (!emailList || emailList.querySelector('.email-row')) return;

  emailList.innerHTML = '<div class="empty-mailing">לא נוספו כתובות אימייל</div>';
}

function renderMailingList(emails) {
  const emailList = document.getElementById('emailList');
  if (!emailList) return;

  emailList.innerHTML = '';
  emails
    .map((email) => String(email).trim())
    .filter(Boolean)
    .forEach((email) => {
      emailList.appendChild(createEmailRow(email));
    });

  renderMailingEmptyState();
}

function createEmailRow(email) {
  const row = document.createElement('div');
  row.className = 'email-row';
  row.innerHTML = `
    <input type="email" value="${escapeAttribute(email)}">
    <button class="delete-box" type="button" data-action="delete-email" aria-label="מחק כתובת"><i class="fa-solid fa-trash"></i></button>
  `;
  return row;
}

async function saveMailingList() {
  const status = document.getElementById('pageStatus');
  const emails = Array.from(document.querySelectorAll('#emailList input'))
    .map((input) => input.value.trim())
    .filter(Boolean);

  if (!emails.length) {
    setStatus(status, 'יש להוסיף לפחות כתובת אימייל אחת לפני שמירה.', true);
    return;
  }

  try {
    await apiRequest('/api/v1/mailing_list', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(emails)
    });
    setStatus(status, 'רשימת התפוצה נשמרה.');
  } catch (error) {
    setStatus(status, error.message || 'לא ניתן לשמור את רשימת התפוצה.', true);
  }
}

function escapeAttribute(value) {
  return value.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

function escapeHtml(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}
