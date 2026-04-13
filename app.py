from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
import os, json, re, threading, time as time_module
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

try:
    import pytesseract
    from PIL import Image
    import pdf2image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

import os
base_dir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__, template_folder=os.path.join(base_dir, 'templates'), static_folder=os.path.join(base_dir, 'static'))
app.secret_key = 'smart_health_secret_key_2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///smart_health.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# ─── TWILIO SMS CONFIG ────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN  = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_FROM_NUMBER = os.environ.get('TWILIO_FROM_NUMBER', '')

# ─── VERIFIED NUMBERS (Twilio Trial) ─────────────────────────────────────────
VERIFIED_NUMBERS = [
    '+916384767475',
    '+919677316373',
    '+919043217859',
    '+916382640523',
    '+918248293715',
    '+917339000779',
    '+919843186904',
]

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
db = SQLAlchemy(app)

# ─── MODELS ───────────────────────────────────────────────────────────────────

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    dob = db.Column(db.String(20))
    phone = db.Column(db.String(15), nullable=False)
    alt_phone = db.Column(db.String(15))
    email = db.Column(db.String(100))
    language = db.Column(db.String(10), default='en')
    voice_enabled = db.Column(db.Boolean, default=False)
    diseases = db.Column(db.Text)
    physical_conditions = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    medicines = db.relationship('Medicine', backref='user', lazy=True, cascade='all, delete-orphan')
    caretaker = db.relationship('Caretaker', backref='user', lazy=True, uselist=False, cascade='all, delete-orphan')
    alerts = db.relationship('Alert', backref='user', lazy=True, cascade='all, delete-orphan')

class Caretaker(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    mobile = db.Column(db.String(15), nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Medicine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    dosage = db.Column(db.String(50))
    custom_times = db.Column(db.Text, default='[]')
    food_condition = db.Column(db.String(20), default='anytime')
    total_tablets = db.Column(db.Integer, default=30)
    tablets_remaining = db.Column(db.Integer, default=30)
    start_date = db.Column(db.Date, default=date.today)
    active = db.Column(db.Boolean, default=True)
    tablet_grid = db.relationship('TabletGrid', backref='medicine', lazy=True, cascade='all, delete-orphan')

    def get_times(self):
        try:
            return json.loads(self.custom_times) if self.custom_times else []
        except:
            return []

    def set_times(self, times_list):
        self.custom_times = json.dumps(sorted(times_list))

class TabletGrid(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    medicine_id = db.Column(db.Integer, db.ForeignKey('medicine.id'), nullable=False)
    day_number = db.Column(db.Integer, nullable=False)
    taken = db.Column(db.Boolean, default=False)
    taken_at = db.Column(db.DateTime)
    scheduled_date = db.Column(db.Date)
    missed = db.Column(db.Boolean, default=False)       # Red — missed (past date, not taken)
    taken_on_time = db.Column(db.Boolean, default=False)  # Blue — taken exactly at scheduled time

class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    medicine_name = db.Column(db.String(100))
    alert_type = db.Column(db.String(50))
    message = db.Column(db.Text)
    caretaker_notified = db.Column(db.Boolean, default=False)
    sms_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved = db.Column(db.Boolean, default=False)

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(15), unique=True, nullable=False)
    specialist = db.Column(db.String(100), default='General Physician')
    qualification = db.Column(db.String(200), default='MBBS')
    assigned_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DoctorReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False, default=5)
    comment = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ─── SMS SERVICE ───────────────────────────────────────────────────────────────

def send_sms(phone_number, message):
    return send_sms_twilio(phone_number, message)

def send_sms_twilio(phone_number, message):
    try:
        import urllib.request, urllib.parse, base64
        time_module.sleep(2)

        clean = re.sub(r'[^0-9]', '', str(phone_number))
        if clean.startswith('0'):
            clean = clean[1:]
        if len(clean) == 10:
            clean = '+91' + clean
        elif len(clean) == 12 and clean.startswith('91'):
            clean = '+' + clean
        elif len(clean) == 13 and clean.startswith('091'):
            clean = '+' + clean[1:]
        elif not clean.startswith('+'):
            clean = '+91' + clean[-10:]

        print(f"[SMS] Sending to: {clean}")

        if clean not in VERIFIED_NUMBERS:
            print(f"[SMS] {clean} not in verified list — skipping (Twilio trial)")
            return False

        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
        data = urllib.parse.urlencode({
            'To':   clean,
            'From': TWILIO_FROM_NUMBER,
            'Body': message,
        }).encode('utf-8')

        credentials = base64.b64encode(
            f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()
        ).decode()

        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Authorization', f'Basic {credentials}')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')

        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            print(f"[SMS] Twilio sent to {clean}")
            return True

    except Exception as e:
        print(f"[SMS] Twilio failed: {e}")
        return False

# ─── BACKGROUND SCHEDULER — 3 SMS then caretaker alert ───────────────────────

def check_medicine_reminders():
    while True:
        try:
            with app.app_context():
                now = datetime.now()
                hhmm = now.strftime('%H:%M')
                today = date.today()
                print(f"[Scheduler] Checking at {hhmm} on {today}")

                medicines = Medicine.query.filter_by(active=True).all()
                for med in medicines:
                    times = med.get_times()
                    if hhmm not in times:
                        continue

                    print(f"[Scheduler] Time matched: {med.name} at {hhmm}")

                    # DB-level dedup — check if alert already fired for this med+time+day
                    sms_key = f"sched_{med.id}_{hhmm}_{today}"
                    already_fired = Alert.query.filter_by(
                        user_id=med.user_id,
                        medicine_name=med.name,
                        alert_type='sched_fired',
                        message=sms_key
                    ).first()
                    if already_fired:
                        continue
                    # Mark as fired in DB immediately (prevents double-fire across processes)
                    fire_marker = Alert(
                        user_id=med.user_id,
                        medicine_name=med.name,
                        alert_type='sched_fired',
                        message=sms_key,
                        sms_sent=False
                    )
                    db.session.add(fire_marker)
                    db.session.commit()

                    grid = TabletGrid.query.filter_by(
                        medicine_id=med.id, scheduled_date=today
                    ).first()
                    if grid and grid.taken:
                        print(f"[Scheduler] Already taken: {med.name}")
                        continue

                    user = db.session.get(User, med.user_id)
                    caretaker = Caretaker.query.filter_by(user_id=med.user_id).first()

                    h, m = map(int, hhmm.split(':'))
                    ap = 'AM' if h < 12 else 'PM'
                    hr = 12 if h % 12 == 0 else h % 12
                    time_display = f"{hr}:{m:02d} {ap}"

                    food_note = f"Take {med.food_condition} food. " if med.food_condition != 'anytime' else ""
                    dosage_note = f" ({med.dosage})" if med.dosage else ""

                    def reminder_flow(med_id, user_id, med_name, dosage_n, food_n,
                                      ct_mobile, ct_name, usr_name, usr_phone, t_disp):
                        """
                        T+0min  -> SMS #1 to patient
                        T+2min  -> SMS #2 to patient  (if not taken)
                        T+4min  -> SMS #3 to patient  (if not taken)
                        T+6min  -> SMS to CARETAKER   (if still not taken) + Alert saved
                        """

                        def is_taken():
                            with app.app_context():
                                g = TabletGrid.query.filter_by(
                                    medicine_id=med_id,
                                    scheduled_date=date.today()
                                ).first()
                                return g and g.taken

                        # SMS #1 — at medicine time
                        if not is_taken():
                            msg1 = (f"MEDICINE REMINDER! Dear {usr_name}, "
                                    f"time to take {med_name}{dosage_n} at {t_disp}. "
                                    f"{food_n}Please take it now! (1/3)")
                            send_sms(usr_phone, msg1)
                            print(f"[SMS] Reminder 1 -> {usr_name} for {med_name}")

                        # Wait 2 min -> SMS #2
                        time_module.sleep(120)
                        if not is_taken():
                            msg2 = (f"2nd REMINDER! {usr_name}, "
                                    f"you have NOT taken {med_name} yet! "
                                    f"Please take it NOW. {food_n}(2/3)")
                            send_sms(usr_phone, msg2)
                            print(f"[SMS] Reminder 2 -> {usr_name} for {med_name}")

                        # Wait 2 min -> SMS #3
                        time_module.sleep(120)
                        if not is_taken():
                            msg3 = (f"FINAL REMINDER! {usr_name}, "
                                    f"URGENT! {med_name} still not taken! "
                                    f"Caretaker will be alerted if no response! (3/3)")
                            send_sms(usr_phone, msg3)
                            print(f"[SMS] Reminder 3 -> {usr_name} for {med_name}")

                        # Wait 2 min -> Alert caretaker
                        time_module.sleep(120)
                        if not is_taken():
                            print(f"[ALERT] {usr_name} missed {med_name} — alerting caretaker!")
                            with app.app_context():
                                alert = Alert(
                                    user_id=user_id,
                                    medicine_name=med_name,
                                    alert_type='missed',
                                    message=f'{med_name} not taken at {t_disp}. Caretaker notified.',
                                    caretaker_notified=True,
                                    sms_sent=True
                                )
                                db.session.add(alert)
                                # Mark today's grid as missed (RED) immediately
                                today_d = date.today()
                                missed_grid = TabletGrid.query.filter_by(
                                    medicine_id=med_id, scheduled_date=today_d
                                ).first()
                                if missed_grid and not missed_grid.taken:
                                    missed_grid.missed = True
                                db.session.commit()
                            if ct_mobile:
                                ct_msg = (f"SmartHealth ALERT! Your patient {usr_name} "
                                          f"has NOT taken {med_name} at {t_disp}. "
                                          f"3 reminders sent — no response! "
                                          f"Please contact immediately: {usr_phone}")
                                send_sms(ct_mobile, ct_msg)
                                print(f"[ALERT] Caretaker {ct_name} alerted for {med_name}")
                        else:
                            print(f"[OK] {usr_name} took {med_name} — Done!")

                    ct_mobile = caretaker.mobile if caretaker else None
                    ct_name   = caretaker.name   if caretaker else None
                    usr_name  = user.name  if user else 'Patient'
                    usr_phone = user.phone if user else ''

                    t = threading.Thread(
                        target=reminder_flow,
                        args=(med.id, med.user_id, med.name, dosage_note,
                              food_note, ct_mobile, ct_name, usr_name,
                              usr_phone, time_display),
                        daemon=True
                    )
                    t.start()

                # Clean up old sched_fired markers from previous days
                old_markers = Alert.query.filter(
                    Alert.alert_type == 'sched_fired',
                    Alert.message.notlike(f'%{today}%')
                ).all()
                for m in old_markers:
                    db.session.delete(m)
                if old_markers:
                    db.session.commit()

        except Exception as e:
            print(f"[Scheduler Error] {e}")

        time_module.sleep(60)

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('user_login'))
        return f(*args, **kwargs)
    return decorated

def caretaker_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'caretaker_id' not in session:
            return redirect(url_for('caretaker_login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

def doctor_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'doctor_id' not in session:
            return redirect(url_for('doctor_login'))
        return f(*args, **kwargs)
    return decorated

# ─── MEDICINE DB + OCR ────────────────────────────────────────────────────────

MEDICINE_DB = [
    "Paracetamol","Dolo","Dolo 650","Crocin","Calpol","Ibuprofen","Combiflam","Diclofenac",
    "Nimesulide","Aceclofenac","Tramadol","Aspirin","Disprin",
    "Antibiotic","Amoxicillin","Azithromycin","Ciprofloxacin","Doxycycline","Cefixime",
    "Cefpodoxime","Ceftriaxone","Metronidazole","Clindamycin","Erythromycin",
    "Levofloxacin","Ofloxacin","Norfloxacin","Augmentin","Ampicillin","Tetracycline",
    "Clarithromycin","Cephalexin","Cefadroxil",
    "Metformin","Glipizide","Gliclazide","Glibenclamide","Glimepiride","Insulin","Glimisave",
    "Januvia","Jardiance","Vildagliptin","Dapagliflozin","Sitagliptin",
    "Amlodipine","Atenolol","Metoprolol","Losartan","Telmisartan","Ramipril","Enalapril",
    "Nifedipine","Lisinopril","Valsartan","Hydrochlorothiazide","Bisoprolol","Carvedilol",
    "Clopidogrel","Atorvastatin","Rosuvastatin","Warfarin","Digoxin","Furosemide",
    "Omeprazole","Pantoprazole","Ranitidine","Esomeprazole","Rabeprazole","Domperidone",
    "Ondansetron","Metoclopramide","Sucralfate","Gelusil","Digene","Pantosec",
    "Cetirizine","Loratadine","Fexofenadine","Chlorpheniramine","Montelukast","Montair",
    "Levocetrizine","Allegra","Zyrtec","Benadryl",
    "Vitamin D","Vitamin C","Vitamin B12","Calcium","Iron","Folic Acid","Zinc",
    "Multivitamin","Becosules","Neurobion","Shelcal","Calcirol","Evion",
    "Levothyroxine","Thyroxine","Eltroxin","Thyronorm","Methimazole",
    "Prednisolone","Dexamethasone","Hydrocortisone","Methylprednisolone","Wysolone",
    "Salbutamol","Theophylline","Fluticasone",
    "Cetzine","Pan D","Nexpro","Razo","Gudcef","Taxim","Zifi","Mox","Cifran",
    "Zenflox","Taxim O","Ceftas","Mahacef","Omez","Veloz","Macpod","Doxinate",
    "Aciloc","Rantac","Cyclopam","Drotin","Buscopan","Meftal","Zerodol","Hifenac",
    "Brufen","Voveran","Telma","Tazloc","Cardace","Olsar","Olmezest",
    "Deplatt","Ecosprin","Clopivas","Rosuvas","Storvas","Lipitor",
    "Glycomet","Glyciphage","Galvus","Janumet","Glucophage",
    "Zyloric","Febuxostat","Colchicine","Allopurinol",
]

SKIP_WORDS = {
    'medicine','moring','morning','afternoon','evening','night','tablet','capsule',
    'dose','dosage','mg','ml','mcg','iu','times','daily','days','food','before','after',
    'take','with','the','and','for','per','day','week','month','tab','cap','syp','inj',
    'rx','patient','prescription','doctor','hospital','clinic','date','name','age',
    'bd','od','tds','qid','sos','stat','prn','hs','ac','pc','bid','tid',
}

COMMON_ENGLISH = {
    'This','That','Take','With','After','Before','Food','Water','Once','Twice',
    'Daily','Week','Month','Days','Each','Every','When','Then','Also','Note',
    'Keep','Away','From','Children','Morning','Night','Evening','Afternoon',
    'Medicine','Tablet','Capsule','Syrup','Patient','Doctor','Hospital',
}

def _clean_med_name(name):
    name = re.sub(r'\s*\d+\.?\d*\s*(mg|ml|mcg|g|iu)\b.*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+\b(BD|OD|TDS|QID|SOS|STAT|PRN|HS|AC|PC|BID|TID)\b.*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[\s\-\d]+$', '', name)
    return name.strip()

def extract_medicines_from_text(text):
    found = []
    def add(name):
        name = _clean_med_name(name)
        if not name or len(name) < 3 or '\n' in name or len(name.split()) > 4:
            return
        if all(w.lower() in SKIP_WORDS for w in name.split()):
            return
        if not any(name.upper() == f.upper() for f in found):
            found.append(name)

    text_upper = text.upper()
    for med in MEDICINE_DB:
        if med.upper() in text_upper:
            add(med)

    for m in re.finditer(
        r'(?:Tab\.?|Cap\.?|Syp\.?|Inj\.?|Tablet|Capsule|Syrup|Drop)\s+([A-Za-z][A-Za-z0-9\-\/ ]{2,28})',
        text, re.IGNORECASE
    ):
        raw = m.group(1)
        name = re.split(r'\s+\d', raw)[0].strip()
        add(name)

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = re.split(r'\t|  {2,}|\|', line)
        cell = cols[0].strip()
        cell = re.sub(r'^\d+[\.\)\s]+', '', cell).strip()
        if re.match(r'^[A-Za-z]', cell) and len(cell) >= 3:
            words = cell.split()
            if not all(w.lower() in SKIP_WORDS for w in words):
                add(cell)

    for m in re.finditer(r'\b([A-Z][a-z]{3,19}(?:[- ][A-Z][a-z]{2,15})?)\b', text):
        word = m.group(1).strip()
        if word.lower() not in SKIP_WORDS and word not in COMMON_ENGLISH:
            add(word)

    return found

def ocr_extract_text(filepath):
    if not OCR_AVAILABLE:
        return """
        Medicine  Morning   Afternoon  Night
        Paracetamol  1    -    1
        Antibiotic   1    1    1
        Dolo         1    -    -
        """
    try:
        ext = filepath.rsplit('.', 1)[1].lower()
        if ext == 'pdf':
            pages = pdf2image.convert_from_path(filepath)
            text = ''
            for page in pages:
                text += pytesseract.image_to_string(page)
        else:
            img = Image.open(filepath)
            text = pytesseract.image_to_string(img)
        return text
    except Exception as e:
        return f"OCR Error: {str(e)}"

def init_tablet_grid(medicine_id, days=30):
    start = date.today()
    for i in range(1, days + 1):
        scheduled = start + timedelta(days=i-1)
        grid = TabletGrid(medicine_id=medicine_id, day_number=i, scheduled_date=scheduled)
        db.session.add(grid)
    db.session.commit()

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        age = int(request.form['age'])
        user = User(
            name=request.form['name'], age=age,
            dob=request.form['dob'], phone=request.form['phone'],
            alt_phone=request.form.get('alt_phone',''),
            email=request.form.get('email',''),
            language=request.form.get('language','en'),
            voice_enabled=(age >= 45)
        )
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        session['user_name'] = user.name
        session['voice_enabled'] = user.voice_enabled
        return redirect(url_for('medical_details'))
    return render_template('register.html')

@app.route('/user/login', methods=['GET', 'POST'])
def user_login():
    if request.method == 'POST':
        user = User.query.filter_by(phone=request.form['phone']).first()
        if user:
            session['user_id'] = user.id
            session['user_name'] = user.name
            session['voice_enabled'] = user.voice_enabled
            return redirect(url_for('user_dashboard'))
        flash('User not found. Please register first.', 'error')
    return render_template('user_login.html')

@app.route('/medical-details', methods=['GET', 'POST'])
@login_required
def medical_details():
    if request.method == 'POST':
        user = db.session.get(User, session['user_id'])
        diseases = request.form.getlist('diseases')
        # If "Other" is selected, replace it with the custom text entered
        other_disease = request.form.get('other_disease', '').strip()
        if 'Other' in diseases and other_disease:
            diseases.remove('Other')
            diseases.append(other_disease)
        elif 'Other' in diseases and not other_disease:
            diseases.remove('Other')  # don't save blank "Other"
        user.diseases = ','.join(diseases)
        user.physical_conditions = request.form.get('physical_conditions','')
        db.session.commit()
        return redirect(url_for('caretaker_mapping'))
    return render_template('medical_details.html')

@app.route('/caretaker-mapping', methods=['GET', 'POST'])
@login_required
def caretaker_mapping():
    if request.method == 'POST':
        user_id = session['user_id']
        existing = Caretaker.query.filter_by(user_id=user_id).first()
        if existing:
            db.session.delete(existing)
        ct = Caretaker(
            user_id=user_id, name=request.form['ct_name'],
            mobile=request.form['ct_mobile'],
            password_hash=generate_password_hash(request.form['ct_password'])
        )
        db.session.add(ct)
        db.session.commit()
        return redirect(url_for('prescription_upload'))
    return render_template('caretaker_mapping.html')

@app.route('/prescription-upload', methods=['GET', 'POST'])
@login_required
def prescription_upload():
    extracted_medicines = []
    raw_text = ''
    if request.method == 'POST':
        if 'prescription' in request.files:
            file = request.files['prescription']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                raw_text = ocr_extract_text(filepath)
                extracted_medicines = extract_medicines_from_text(raw_text)
                session['extracted_medicines'] = extracted_medicines

                user_id = session['user_id']
                for med_name in extracted_medicines:
                    existing = Medicine.query.filter_by(
                        user_id=user_id, name=med_name.strip()
                    ).first()
                    if not existing:
                        med = Medicine(
                            user_id=user_id,
                            name=med_name.strip(),
                            dosage='',
                            food_condition='anytime',
                            total_tablets=30,
                            tablets_remaining=30,
                        )
                        med.set_times([])
                        db.session.add(med)
                        db.session.flush()
                        init_tablet_grid(med.id, 30)
                db.session.commit()

    return render_template('prescription_upload.html',
                           extracted_medicines=extracted_medicines, raw_text=raw_text)

@app.route('/medicine-form', methods=['GET', 'POST'])
@login_required
def medicine_form():
    if request.method == 'POST':
        user_id = session['user_id']
        names   = request.form.getlist('med_name[]')
        dosages = request.form.getlist('med_dosage[]')
        foods   = request.form.getlist('med_food[]')
        tablets = request.form.getlist('med_tablets[]')

        submitted_names = [n.strip().lower() for n in names if n.strip()]

        existing_meds = Medicine.query.filter_by(user_id=user_id, active=True).all()
        for existing in existing_meds:
            if existing.name.lower() not in submitted_names:
                Medicine.query.filter_by(id=existing.id).delete()
        db.session.commit()

        for i, name in enumerate(names):
            if not name.strip():
                continue

            time_key = f'med_times_{i}[]'
            times_raw = request.form.getlist(time_key)
            times_clean = [t.strip() for t in times_raw if re.match(r'^\d{2}:\d{2}$', t.strip())]

            total = int(tablets[i]) if i < len(tablets) and tablets[i] else 30
            dosage = dosages[i] if i < len(dosages) else ''
            food = foods[i] if i < len(foods) else 'anytime'

            existing = Medicine.query.filter_by(user_id=user_id, name=name.strip()).first()

            if existing:
                existing.dosage = dosage
                existing.food_condition = food
                existing.set_times(times_clean)
                db.session.commit()
            else:
                med = Medicine(
                    user_id=user_id, name=name.strip(), dosage=dosage,
                    food_condition=food, total_tablets=total, tablets_remaining=total,
                )
                med.set_times(times_clean)
                db.session.add(med)
                db.session.flush()
                init_tablet_grid(med.id, total)

        db.session.commit()
        flash('Medicine schedule saved!', 'success')
        return redirect(url_for('user_dashboard'))

    user_id = session['user_id']
    existing_meds = Medicine.query.filter_by(user_id=user_id, active=True).all()
    existing_medicines = [
        {
            'name': m.name,
            'dosage': m.dosage or '',
            'food_condition': m.food_condition or 'anytime',
            'times': m.get_times(),
            'tablets_remaining': m.tablets_remaining,
        }
        for m in existing_meds
    ]
    return render_template('medicine_form.html',
                           existing_medicines=existing_medicines,
                           extracted_medicines=[])

@app.route('/dashboard')
@login_required
def user_dashboard():
    user = db.session.get(User, session['user_id'])
    medicines = Medicine.query.filter_by(user_id=user.id, active=True).all()
    alerts = Alert.query.filter_by(user_id=user.id).order_by(Alert.created_at.desc()).limit(10).all()
    today = date.today()
    today_grids = {}
    for med in medicines:
        grid = TabletGrid.query.filter_by(medicine_id=med.id, scheduled_date=today).first()
        if not grid:
            days_since = (today - (med.start_date or today)).days + 1
            grid = TabletGrid(medicine_id=med.id, day_number=max(days_since, 1),
                              scheduled_date=today, taken=False)
            db.session.add(grid)
            db.session.commit()
        else:
            if grid.taken and grid.taken_at:
                taken_date = grid.taken_at.date() if hasattr(grid.taken_at, 'date') else today
                # Fix: != today catches UTC/IST mismatches (was < today before)
                if taken_date != today:
                    grid.taken = False
                    grid.taken_at = None
                    db.session.commit()
            # If taken=True but no taken_at, keep it — don't wipe it
        today_grids[med.id] = grid

    med_schedule = []
    today_str = today.strftime('%Y-%m-%d')
    for med in medicines:
        times = med.get_times()
        grid = today_grids.get(med.id)
        actually_taken = False
        if grid and grid.taken:
            if grid.taken_at:
                taken_date_str = grid.taken_at.strftime('%Y-%m-%d')
                if taken_date_str == today_str:
                    actually_taken = True
                else:
                    grid.taken = False
                    grid.taken_at = None
                    db.session.commit()
        med_schedule.append({
            'id': med.id,
            'name': med.name,
            'dosage': med.dosage or '',
            'times': times,
            'food_condition': med.food_condition,
            'today_taken': actually_taken,
            'tablets_remaining': med.tablets_remaining,
        })

    return render_template('user_dashboard.html',
                           user=user, medicines=medicines,
                           alerts=alerts, today_grids=today_grids,
                           today=today, med_schedule=med_schedule)

@app.route('/tablet-grid/<int:medicine_id>')
@login_required
def tablet_grid(medicine_id):
    med = Medicine.query.get_or_404(medicine_id)
    grids = TabletGrid.query.filter_by(medicine_id=medicine_id).order_by(TabletGrid.day_number).all()
    today = date.today()
    now = datetime.now()
    scheduled_times = med.get_times()  # e.g. ['11:02', '14:00']

    for grid in grids:
        if grid.taken:
            continue  # already taken — don't touch
        if not grid.scheduled_date:
            continue

        if grid.scheduled_date < today:
            # Past day, not taken → definitely missed
            if not grid.missed:
                grid.missed = True

        elif grid.scheduled_date == today:
            # Today — check if ALL scheduled times have passed
            if scheduled_times:
                all_times_passed = all(
                    now > now.replace(hour=int(t.split(':')[0]), minute=int(t.split(':')[1]), second=0, microsecond=0)
                    for t in scheduled_times
                )
                # Mark missed only if last scheduled time passed by more than 6 minutes
                # (give time for 3 SMS reminders: each 2 min = 6 min total)
                last_time = max(scheduled_times)
                lh, lm = map(int, last_time.split(':'))
                last_dt = now.replace(hour=lh, minute=lm, second=0, microsecond=0)
                minutes_past = (now - last_dt).total_seconds() / 60
                if all_times_passed and minutes_past > 6 and not grid.missed:
                    grid.missed = True

    db.session.commit()
    return render_template('tablet_grid.html', medicine=med, grids=grids, today=today)

@app.route('/api/take-medicine', methods=['POST'])
@login_required
def take_medicine():
    data = request.json
    med_id = data.get('medicine_id')
    med = db.session.get(Medicine, med_id)
    if not med or med.user_id != session['user_id']:
        return jsonify({'success': False, 'message': 'Unauthorized'})
    today = date.today()
    grid = TabletGrid.query.filter_by(medicine_id=med_id, scheduled_date=today).first()
    # Fix: create grid for today if missing (prevents silent failure)
    if not grid:
        days_since = (today - (med.start_date or today)).days + 1
        grid = TabletGrid(medicine_id=med_id, day_number=max(days_since, 1),
                          scheduled_date=today, taken=False)
        db.session.add(grid)
        db.session.flush()
    if grid and not grid.taken:
        grid.taken = True
        grid.missed = False
        # Fix: use local time (not UTC) so IST date comparisons stay correct
        now_local = datetime.now()
        grid.taken_at = now_local

        # Check if taken within ±30 min of any scheduled time → Blue (on_time)
        scheduled_times = med.get_times()
        on_time = False
        for t_str in scheduled_times:
            try:
                h, m = map(int, t_str.split(':'))
                scheduled_dt = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
                diff_minutes = abs((now_local - scheduled_dt).total_seconds() / 60)
                if diff_minutes <= 30:
                    on_time = True
                    break
            except Exception:
                pass
        grid.taken_on_time = on_time

        if med.tablets_remaining > 0:
            med.tablets_remaining -= 1
        db.session.commit()
        msg = None
        if med.tablets_remaining <= 2:
            alert = Alert(user_id=session['user_id'], medicine_name=med.name,
                          alert_type='low_stock',
                          message=f'Only {med.tablets_remaining} tablet(s) of {med.name} remaining!')
            db.session.add(alert)
            db.session.commit()
            caretaker = Caretaker.query.filter_by(user_id=session['user_id']).first()
            user = db.session.get(User, session['user_id'])
            if caretaker:
                sms_msg = f"SmartHealth: {user.name}'s medicine {med.name} has only {med.tablets_remaining} tablet(s) left. Please arrange refill!"
                send_sms(caretaker.mobile, sms_msg)
            msg = f'Only {med.tablets_remaining} tablets left for {med.name}!'
        return jsonify({'success': True, 'tablets_remaining': med.tablets_remaining, 'low_stock_msg': msg})
    return jsonify({'success': False, 'message': 'Already taken or not scheduled today'})

@app.route('/api/missed-medicine', methods=['POST'])
@login_required
def missed_medicine():
    """
    Called by JS after 3 patient reminders with no response.
    We only save the alert here — the background scheduler already
    sends the caretaker SMS after its own 3-reminder flow.
    This avoids double-notifying the caretaker.
    """
    data = request.json
    med_id = data.get('medicine_id')
    scheduled_time = data.get('scheduled_time', '')
    med = db.session.get(Medicine, med_id)
    if not med:
        return jsonify({'success': False})
    caretaker = Caretaker.query.filter_by(user_id=session['user_id']).first()
    # Save alert record only — NO SMS here (scheduler handles it)
    alert = Alert(
        user_id=session['user_id'],
        medicine_name=med.name,
        alert_type='missed',
        message=f'{med.name} was not taken at {scheduled_time}. 3 reminders sent — no response.',
        caretaker_notified=False,
        sms_sent=False
    )
    db.session.add(alert)
    db.session.commit()
    return jsonify({'success': True, 'caretaker_name': caretaker.name if caretaker else 'Caretaker'})

@app.route('/api/emergency', methods=['POST'])
@login_required
def emergency():
    alert = Alert(user_id=session['user_id'], alert_type='emergency',
                  message='EMERGENCY: User pressed emergency button!',
                  caretaker_notified=True)
    db.session.add(alert)
    db.session.commit()
    caretaker = Caretaker.query.filter_by(user_id=session['user_id']).first()
    user = db.session.get(User, session['user_id'])
    if caretaker:
        sms_msg = f"SmartHealth EMERGENCY! {user.name} needs help NOW! Call immediately: {user.phone}"
        send_sms(caretaker.mobile, sms_msg)
    return jsonify({
        'success': True,
        'caretaker_name': caretaker.name if caretaker else 'Caretaker',
        'caretaker_mobile': caretaker.mobile if caretaker else 'N/A'
    })

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ─── CARETAKER ────────────────────────────────────────────────────────────────

@app.route('/caretaker/login', methods=['GET', 'POST'])
def caretaker_login():
    if request.method == 'POST':
        ct = Caretaker.query.filter_by(name=request.form['name']).first()
        if ct and check_password_hash(ct.password_hash, request.form['password']):
            session['caretaker_id'] = ct.id
            session['caretaker_name'] = ct.name
            return redirect(url_for('caretaker_dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('caretaker_login.html')

@app.route('/caretaker/dashboard')
@caretaker_login_required
def caretaker_dashboard():
    ct = db.session.get(Caretaker, session['caretaker_id'])
    user = db.session.get(User, ct.user_id)
    medicines = Medicine.query.filter_by(user_id=user.id, active=True).all()
    alerts = Alert.query.filter_by(user_id=user.id).order_by(Alert.created_at.desc()).limit(20).all()
    today = date.today()
    today_grids = {}
    for med in medicines:
        grid = TabletGrid.query.filter_by(medicine_id=med.id, scheduled_date=today).first()
        if not grid:
            days_since = (today - (med.start_date or today)).days + 1
            grid = TabletGrid(medicine_id=med.id, day_number=max(days_since, 1),
                              scheduled_date=today, taken=False)
            db.session.add(grid)
            db.session.commit()
        else:
            if grid.taken and grid.taken_at:
                taken_date = grid.taken_at.date() if hasattr(grid.taken_at, 'date') else today
                # Fix: != today catches UTC/IST mismatches
                if taken_date != today:
                    grid.taken = False
                    grid.taken_at = None
                    db.session.commit()
            # If taken=True but no taken_at, keep it — don't wipe it
        today_grids[med.id] = grid
    return render_template('caretaker_dashboard.html', user=user, medicines=medicines,
                           alerts=alerts, today_grids=today_grids, caretaker=ct)

@app.route('/caretaker/logout')
def caretaker_logout():
    session.pop('caretaker_id', None)
    session.pop('caretaker_name', None)
    return redirect(url_for('index'))

# ─── ADMIN ────────────────────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        admin = Admin.query.filter_by(username=request.form['username']).first()
        if admin and check_password_hash(admin.password_hash, request.form['password']):
            session['admin_id'] = admin.id
            return redirect(url_for('admin_dashboard'))
        flash('Invalid admin credentials.', 'error')
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    users = User.query.all()
    caretakers = Caretaker.query.all()
    doctors = Doctor.query.all()
    alerts = Alert.query.order_by(Alert.created_at.desc()).limit(30).all()
    total_medicines = Medicine.query.count()
    total_alerts = Alert.query.count()
    missed_alerts = Alert.query.filter_by(alert_type='missed').count()
    return render_template('admin_dashboard.html', users=users, caretakers=caretakers,
                           doctors=doctors, alerts=alerts, total_medicines=total_medicines,
                           total_alerts=total_alerts, missed_alerts=missed_alerts)

@app.route('/admin/doctor/add', methods=['POST'])
@admin_required
def admin_add_doctor():
    name = request.form.get('name','').strip()
    phone = request.form.get('phone','').strip()
    specialist = request.form.get('specialist','General Physician').strip()
    qualification = request.form.get('qualification','MBBS').strip()
    user_id = request.form.get('user_id') or None
    if user_id:
        user_id = int(user_id)
    doc = Doctor(name=name, phone=phone, specialist=specialist,
                 qualification=qualification, assigned_user_id=user_id)
    db.session.add(doc)
    db.session.commit()
    flash(f'Doctor {name} added successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/doctor/<int:doc_id>/assign', methods=['POST'])
@admin_required
def admin_assign_doctor(doc_id):
    doc = Doctor.query.get_or_404(doc_id)
    user_id = request.form.get('user_id') or None
    doc.assigned_user_id = int(user_id) if user_id else None
    db.session.commit()
    flash('Doctor assignment updated!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/user/<int:user_id>')
@admin_required
def admin_user_detail(user_id):
    user = User.query.get_or_404(user_id)
    medicines = Medicine.query.filter_by(user_id=user_id).all()
    alerts = Alert.query.filter_by(user_id=user_id).order_by(Alert.created_at.desc()).all()
    caretaker = Caretaker.query.filter_by(user_id=user_id).first()
    return render_template('admin_user_detail.html', user=user, medicines=medicines,
                           alerts=alerts, caretaker=caretaker)

@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    return redirect(url_for('index'))

# ─── DOCTOR ───────────────────────────────────────────────────────────────────

@app.route('/doctor/login', methods=['GET', 'POST'])
def doctor_login():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        doctor = Doctor.query.filter_by(phone=phone).first()
        if doctor:
            session['doctor_id'] = doctor.id
            session['doctor_name'] = doctor.name
            return redirect(url_for('doctor_dashboard'))
        flash('No doctor account found with this phone number.', 'error')
    return render_template('doctor_login.html')

@app.route('/doctor/dashboard')
@doctor_login_required
def doctor_dashboard():
    doctor = Doctor.query.get(session['doctor_id'])
    patient = None
    medicines = []
    alerts = []
    today_grids = {}
    reviews = []
    avg_rating = 0
    if doctor.assigned_user_id:
        patient = db.session.get(User, doctor.assigned_user_id)
        if patient:
            medicines = Medicine.query.filter_by(user_id=patient.id, active=True).all()
            alerts = Alert.query.filter_by(user_id=patient.id).order_by(Alert.created_at.desc()).limit(20).all()
            today = date.today()
            for med in medicines:
                grid = TabletGrid.query.filter_by(medicine_id=med.id, scheduled_date=today).first()
                if not grid:
                    days_since = (today - (med.start_date or today)).days + 1
                    grid = TabletGrid(medicine_id=med.id, day_number=max(days_since, 1),
                                      scheduled_date=today, taken=False)
                    db.session.add(grid)
                    db.session.commit()
                today_grids[med.id] = grid
    reviews = DoctorReview.query.filter_by(doctor_id=doctor.id).order_by(DoctorReview.created_at.desc()).all()
    if reviews:
        avg_rating = round(sum(r.rating for r in reviews) / len(reviews), 1)
    return render_template('doctor_dashboard.html', doctor=doctor, patient=patient,
                           medicines=medicines, alerts=alerts, today_grids=today_grids,
                           reviews=reviews, avg_rating=avg_rating, today=date.today())

@app.route('/doctor/logout')
def doctor_logout():
    session.pop('doctor_id', None)
    session.pop('doctor_name', None)
    return redirect(url_for('index'))

@app.route('/api/doctor/review', methods=['POST'])
@login_required
def submit_doctor_review():
    data = request.json
    doctor_id = data.get('doctor_id')
    rating = int(data.get('rating', 5))
    comment = data.get('comment', '')
    if not doctor_id:
        return jsonify({'success': False, 'message': 'Missing doctor id'})
    existing = DoctorReview.query.filter_by(doctor_id=doctor_id, user_id=session['user_id']).first()
    if existing:
        existing.rating = rating
        existing.comment = comment
        existing.created_at = datetime.utcnow()
    else:
        review = DoctorReview(doctor_id=doctor_id, user_id=session['user_id'],
                               rating=rating, comment=comment)
        db.session.add(review)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/get-doctor-for-patient')
@login_required
def get_doctor_for_patient():
    doctor = Doctor.query.filter_by(assigned_user_id=session['user_id']).first()
    if doctor:
        reviews = DoctorReview.query.filter_by(doctor_id=doctor.id).all()
        avg = round(sum(r.rating for r in reviews)/len(reviews), 1) if reviews else 0
        my_review = DoctorReview.query.filter_by(doctor_id=doctor.id,
                                                  user_id=session['user_id']).first()
        return jsonify({
            'found': True,
            'id': doctor.id,
            'name': doctor.name,
            'specialist': doctor.specialist,
            'qualification': doctor.qualification,
            'phone': doctor.phone,
            'avg_rating': avg,
            'review_count': len(reviews),
            'my_rating': my_review.rating if my_review else 0,
            'my_comment': my_review.comment if my_review else ''
        })
    return jsonify({'found': False})

# ─── INIT ─────────────────────────────────────────────────────────────────────

def migrate_db():
    """Auto-add missing columns to existing SQLite DB without losing data."""
    import sqlite3 as _sqlite3
    # Try both possible DB paths
    possible_paths = [
        os.path.join(app.instance_path, 'smart_health.db'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'smart_health.db'),
        'instance/smart_health.db',
    ]
    db_path = None
    for p in possible_paths:
        if os.path.exists(p):
            db_path = p
            break
    if not db_path:
        print("[Migration] No existing DB found — fresh start.")
        return
    print(f"[Migration] Found DB at: {db_path}")
    conn = _sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tablet_grid)")
    existing = {row[1] for row in cur.fetchall()}
    print(f"[Migration] Existing tablet_grid columns: {existing}")
    new_cols = [
        ("missed",        "INTEGER NOT NULL DEFAULT 0"),
        ("taken_on_time", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col, defn in new_cols:
        if col not in existing:
            try:
                cur.execute(f"ALTER TABLE tablet_grid ADD COLUMN {col} {defn}")
                conn.commit()
                print(f"[Migration] ✅ Added column: tablet_grid.{col}")
            except Exception as e:
                print(f"[Migration] ⚠️ Could not add {col}: {e}")
        else:
            print(f"[Migration] Column already exists: {col}")
    conn.close()

def init_db():
    with app.app_context():
        migrate_db()
        db.create_all()
        if not Admin.query.filter_by(username='admin').first():
            admin = Admin(username='admin', password_hash=generate_password_hash('admin123'))
            db.session.add(admin)
            db.session.commit()
        print("Database ready! All tables created (Doctor + DoctorReview included).")

# Auto-init DB on startup
with app.app_context():
    migrate_db()
    db.create_all()
    from werkzeug.security import generate_password_hash
    if not Admin.query.filter_by(username='admin').first():
        admin = Admin(username='admin', password_hash=generate_password_hash('admin123'))
        db.session.add(admin)
        db.session.commit()

if __name__ == '__main__':
    init_db()
    scheduler_thread = threading.Thread(target=check_medicine_reminders, daemon=True)
    scheduler_thread.start()
    print("SMS Reminder Scheduler started! (3 reminders -> caretaker alert)")
    # use_reloader=False prevents Flask from spawning a second process
    # which was causing the scheduler (and SMS) to run twice
    app.run(debug=False, use_reloader=False, host='0.0.0.0', port=5000)



