// ── VOICE ENGINE ──────────────────────────────────────────────────────────────
const VoiceEngine = {
  synth: window.speechSynthesis,
  enabled: false,
  language: 'en',

  init(voiceEnabled, lang) {
    this.enabled = voiceEnabled;
    this.language = lang;
  },

  speak(text, priority = false) {
    if (!this.enabled || !this.synth) return;
    if (priority) this.synth.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = this.language === 'ta' ? 'ta-IN' : 'en-IN';
    utter.rate = 0.85;
    utter.pitch = 1.0;
    utter.volume = 1.0;
    this.synth.speak(utter);
  },

  stop() { if (this.synth) this.synth.cancel(); }
};

// ── CUSTOM TIME REMINDER ENGINE ───────────────────────────────────────────────
const ReminderEngine = {
  medicines: [],
  voiceEnabled: false,
  checkInterval: null,
  shownToday: new Set(),   // "medId_HH:MM" keys already reminded today

  init(medicines, voiceEnabled) {
    this.medicines = medicines;
    this.voiceEnabled = voiceEnabled;
    // Load today's shown reminders from sessionStorage
    const saved = sessionStorage.getItem('shownReminders_' + new Date().toDateString());
    if (saved) this.shownToday = new Set(JSON.parse(saved));
    this.checkNow();
    this.checkInterval = setInterval(() => this.checkNow(), 30000); // check every 30s
  },

  checkNow() {
    const now = new Date();
    const currentHHMM = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');

    this.medicines.forEach(med => {
      if (!med.times || med.times.length === 0) return;
      if (med.today_taken) return;

      med.times.forEach(t => {
        const key = `${med.id}_${t}`;
        // Trigger if within 1 minute window of the scheduled time
        if (this.isTimeMatch(currentHHMM, t) && !this.shownToday.has(key)) {
          this.shownToday.add(key);
          this.saveShown();
          ReminderPopup.show(med, t);
        }
      });
    });
  },

  isTimeMatch(current, scheduled) {
    // Match if current time == scheduled OR 1 minute before
    const [ch, cm] = current.split(':').map(Number);
    const [sh, sm] = scheduled.split(':').map(Number);
    const curMins = ch * 60 + cm;
    const schMins = sh * 60 + sm;
    return curMins === schMins || curMins === schMins - 1;
  },

  saveShown() {
    sessionStorage.setItem(
      'shownReminders_' + new Date().toDateString(),
      JSON.stringify([...this.shownToday])
    );
  }
};

// ── REMINDER POPUP ────────────────────────────────────────────────────────────
const ReminderPopup = {
  currentMed: null,
  currentTime: null,
  missedTimer: null,
  missedCount: 0,

  show(med, scheduledTime) {
    this.currentMed = med;
    this.currentTime = scheduledTime;
    this.missedCount = 0;
    this.clearTimer();

    const overlay = document.getElementById('reminderOverlay');
    if (!overlay) return;

    // Format time for display
    const [h, m] = scheduledTime.split(':').map(Number);
    const ampm = h < 12 ? 'AM' : 'PM';
    const hr = h % 12 === 0 ? 12 : h % 12;
    const timeDisplay = `${hr}:${m.toString().padStart(2,'0')} ${ampm}`;

    document.getElementById('reminderMedName').textContent = med.name;
    document.getElementById('reminderDosage').textContent = med.dosage || '';
    document.getElementById('reminderTime').textContent = `⏰ Scheduled at ${timeDisplay}`;
    document.getElementById('reminderFood').textContent =
      med.food_condition !== 'anytime' ? `🍽️ Take ${med.food_condition} food` : '';

    overlay.classList.remove('hidden');

    if (VoiceEngine.enabled) {
      VoiceEngine.speak(
        `Reminder! It is ${timeDisplay}. Time to take your medicine ${med.name}. ${
          med.food_condition !== 'anytime' ? 'Take ' + med.food_condition + ' food.' : ''
        }`, true
      );
    }

    // Re-remind after 2 minutes if no response
    this.missedTimer = setTimeout(() => this.onNoResponse(), 120000);
  },

  hide() {
    const overlay = document.getElementById('reminderOverlay');
    if (overlay) overlay.classList.add('hidden');
    this.clearTimer();
  },

  clearTimer() {
    if (this.missedTimer) { clearTimeout(this.missedTimer); this.missedTimer = null; }
  },

  onNoResponse() {
    this.missedCount++;
    if (this.missedCount < 3) {
      // Reminder 2 and 3 go to patient only
      if (VoiceEngine.enabled)
        VoiceEngine.speak(`Reminder ${this.missedCount + 1} of 3. Please take your medicine ${this.currentMed.name} now!`, true);
      showNotification(`⚠️ Reminder ${this.missedCount + 1}/3: Time to take ${this.currentMed.name}!`, 'warning');
      this.missedTimer = setTimeout(() => this.onNoResponse(), 120000);
    } else {
      // Only after 3rd reminder fails → alert caretaker
      this.hide();
      this.alertCaretaker(this.currentMed);
    }
  },

  alertCaretaker(med) {
    fetch('/api/missed-medicine', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ medicine_id: med.id })
    }).then(r => r.json()).then(data => {
      if (data.success) {
        showNotification(`⚠️ Alert sent to caretaker ${data.caretaker_name} — ${med.name} was missed!`, 'warning');
        if (VoiceEngine.enabled)
          VoiceEngine.speak(`Alert sent to your caretaker ${data.caretaker_name}. You missed ${med.name}.`, true);
      }
    });
  }
};

// ── TAKE MEDICINE ─────────────────────────────────────────────────────────────
function takeMedicine(medId) {
  fetch('/api/take-medicine', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ medicine_id: medId })
  }).then(r => r.json()).then(data => {
    if (data.success) {
      ReminderPopup.hide();
      const btn = document.querySelector(`[data-med-id="${medId}"] .take-btn`);
      if (btn) { btn.textContent = '✅ Taken'; btn.disabled = true; }
      const rem = document.querySelector(`[data-med-id="${medId}"] .tablets-remaining`);
      if (rem) rem.textContent = data.tablets_remaining;

      if (data.low_stock_msg) {
        showNotification(data.low_stock_msg, 'warning');
        if (VoiceEngine.enabled) VoiceEngine.speak(data.low_stock_msg, true);
      } else {
        showNotification('✅ Medicine marked as taken!', 'success');
        if (VoiceEngine.enabled) VoiceEngine.speak('Great! Medicine taken. Stay healthy!', true);
      }
    } else {
      showNotification(data.message || 'Could not update.', 'error');
    }
  });
}

// ── EMERGENCY ─────────────────────────────────────────────────────────────────
function triggerEmergency() {
  if (!confirm('Send emergency alert to your caretaker?')) return;
  fetch('/api/emergency', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({})
  }).then(r => r.json()).then(data => {
    if (data.success) {
      showNotification(`🚨 Emergency alert sent to ${data.caretaker_name} (${data.caretaker_mobile})!`, 'error');
      if (VoiceEngine.enabled)
        VoiceEngine.speak(`Emergency alert sent to your caretaker ${data.caretaker_name}. Help is on the way.`, true);
    }
  });
}

// ── NOTIFICATIONS ─────────────────────────────────────────────────────────────
function showNotification(msg, type = 'info') {
  let container = document.getElementById('notifContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'notifContainer';
    container.style.cssText = 'position:fixed;top:80px;right:20px;z-index:9998;display:flex;flex-direction:column;gap:10px;max-width:360px;';
    document.body.appendChild(container);
  }
  const icons = { success:'✅', error:'🚨', warning:'⚠️', info:'ℹ️' };
  const colors = { success:'#276749', error:'#c53030', warning:'#7b341e', info:'#2b6cb0' };
  const bgs = { success:'#f0fff4', error:'#fff5f5', warning:'#fffbeb', info:'#ebf8ff' };

  const notif = document.createElement('div');
  notif.style.cssText = `background:${bgs[type]};border-left:4px solid ${colors[type]};padding:14px 16px;border-radius:10px;display:flex;align-items:flex-start;gap:10px;box-shadow:0 4px 20px rgba(0,0,0,0.15);animation:slideIn 0.3s ease;font-family:'Nunito',sans-serif;font-size:0.9rem;font-weight:600;color:${colors[type]};`;
  notif.innerHTML = `<span style="font-size:1.2rem">${icons[type]}</span><span style="flex:1">${msg}</span><button onclick="this.parentElement.remove()" style="background:none;border:none;cursor:pointer;font-size:1rem;opacity:0.6;padding:0 0 0 8px;">×</button>`;
  container.appendChild(notif);
  setTimeout(() => { if (notif.parentElement) notif.remove(); }, 7000);
}

// ── EXERCISES ─────────────────────────────────────────────────────────────────
const EXERCISE_MAP = {
  'Diabetes': ['🚶 30-min brisk walk after breakfast','🧘 10 min light yoga','🚴 Gentle cycling 20 min'],
  'Hypertension (BP)': ['🌬️ Deep breathing 5 min','🧘 Meditation 10 min','🚶 Slow walk in fresh air'],
  'Heart Disease': ['🚶 Gentle 20-min walk','🧘 Relaxation breathing','💪 Light stretching'],
  'Thyroid': ['🏊 Swimming or water aerobics','🚶 Daily 30-min walk','🧘 Yoga'],
  'Arthritis': ['🤸 Gentle joint movements','🏊 Water exercises','🧘 Chair yoga'],
  'Asthma': ['🌬️ Pursed lip breathing','🧘 Diaphragmatic breathing','🚶 Short walks']
};

function showExercises(diseases) {
  const container = document.getElementById('exerciseList');
  if (!container) return;
  let html = '';
  if (diseases) {
    diseases.split(',').forEach(d => {
      d = d.trim();
      if (EXERCISE_MAP[d]) {
        html += `<div class="exercise-card mb-16">
          <div style="font-weight:800;color:var(--accent);margin-bottom:10px">🏃 For ${d}:</div>`;
        EXERCISE_MAP[d].forEach(ex => {
          html += `<div style="padding:8px 0;border-bottom:1px solid var(--border);font-size:0.9rem">${ex}</div>`;
        });
        html += '</div>';
      }
    });
  }
  if (html) container.innerHTML = html;
  else container.innerHTML = '<div class="exercise-card"><div style="color:var(--text-muted)">No specific exercises. General tip: Walk 30 minutes daily! 🚶</div></div>';
}

// ── AGE HINT ──────────────────────────────────────────────────────────────────
function onAgeChange(val) {
  const hint = document.getElementById('ageHint');
  if (!hint) return;
  if (parseInt(val) > 45) {
    hint.innerHTML = '🔊 <strong>Voice assistance will be enabled</strong> for this user';
    hint.style.color = 'var(--primary)';
  } else {
    hint.innerHTML = '📱 Standard text interface';
    hint.style.color = 'var(--text-muted)';
  }
}

// ── UPLOAD ZONE ───────────────────────────────────────────────────────────────
function initUploadZone() {
  const zone = document.getElementById('uploadZone');
  const input = document.getElementById('prescriptionFile');
  if (!zone || !input) return;
  zone.addEventListener('click', () => input.click());
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => {
    e.preventDefault(); zone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) {
      input.files = e.dataTransfer.files;
      showFileName(e.dataTransfer.files[0].name);
    }
  });
  input.addEventListener('change', () => {
    if (input.files[0]) showFileName(input.files[0].name);
  });
}

function showFileName(name) {
  const label = document.getElementById('uploadFileName');
  if (label) { label.textContent = `📄 ${name}`; label.style.display = 'block'; }
  document.getElementById('uploadBtn').style.display = 'block';
}

// ── INIT ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initUploadZone();
  setTimeout(() => {
    document.querySelectorAll('.flash-msg').forEach(el => el.remove());
  }, 4000);
  const ageField = document.getElementById('ageField');
  if (ageField) {
    ageField.addEventListener('input', () => onAgeChange(ageField.value));
    onAgeChange(ageField.value);
  }
  const style = document.createElement('style');
  style.textContent = `@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}`;
  document.head.appendChild(style);
});
