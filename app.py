import os
import sys
import io
import csv
import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# Load secret key from environment variable or fallback for local testing
app.secret_key = os.environ.get("SECRET_KEY", "churnguard_default_dev_key_change_me")

DATABASE_NAME = os.environ.get("DATABASE_PATH", "churnguard.db")

# ==========================================
# DATABASE SETUP & HELPERS
# ==========================================

def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'agent'
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenure INTEGER,
            contract TEXT,
            monthly_charges REAL,
            total_charges REAL,
            internet_service TEXT,
            payment_method TEXT,
            churn TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            prediction TEXT,
            probability REAL,
            risk_level TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers (customer_id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS retention_campaigns (
            campaign_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            offer_type TEXT,
            discount_percent REAL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'Sent'
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action_performed TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()


# ==========================================
# AUTHENTICATION HELPER & DECORATOR
# ==========================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# ==========================================
# AUTHENTICATION ROUTES
# ==========================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        if not name or not email or not password:
            flash("All fields (Name, Email, Password) are required.", "danger")
            return render_template('register.html')

        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
                (name, email, hashed_password, 'agent')
            )
            conn.commit()
            flash("Registration successful! Please log in.", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("An account with this email already exists. Please log in.", "danger")
        except Exception as e:
            flash(f"Database error during registration: {str(e)}", "danger")
        finally:
            conn.close()

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        if not email or not password:
            flash("Please enter both email and password.", "danger")
            return render_template('login.html')

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['user_id']
            session['user_name'] = user['name']
            session['user_role'] = user['role']
            flash("Login successful! Welcome back.", "success")
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid email or password. Please try again.", "danger")

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out successfully.", "info")
    return redirect(url_for('login'))


# ==========================================
# DASHBOARD & ANALYTICS (RENDER PROXY FIX)
# ==========================================

@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')


@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) AS total FROM customers")
        total_res = cursor.fetchone()
        total_customers = total_res['total'] if total_res and total_res['total'] else 0

        cursor.execute("SELECT COUNT(*) AS churns FROM predictions WHERE prediction = 'Churn'")
        churn_res = cursor.fetchone()
        churn_count = churn_res['churns'] if churn_res and churn_res['churns'] else 0

        non_churn_count = max(0, total_customers - churn_count)
        churn_rate = round((churn_count / total_customers * 100), 2) if total_customers > 0 else 0.0

        cursor.execute("SELECT AVG(monthly_charges) AS avg_monthly FROM customers")
        avg_res = cursor.fetchone()
        avg_monthly = round(avg_res['avg_monthly'], 2) if avg_res and avg_res['avg_monthly'] else 0.0

        contract_labels = ['Month-to-month', 'One year', 'Two year']
        contract_churned = []
        contract_retained = []

        for c_type in contract_labels:
            cursor.execute('''
                SELECT 
                    SUM(CASE WHEN p.prediction = 'Churn' THEN 1 ELSE 0 END) as churned,
                    SUM(CASE WHEN p.prediction != 'Churn' OR p.prediction IS NULL THEN 1 ELSE 0 END) as retained
                FROM customers c
                LEFT JOIN predictions p ON c.customer_id = p.customer_id
                WHERE c.contract = ?
            ''', (c_type,))
            res = cursor.fetchone()
            contract_churned.append(res['churned'] if res and res['churned'] else 0)
            contract_retained.append(res['retained'] if res and res['retained'] else 0)

    except Exception as e:
        flash(f"Error fetching analytics data: {str(e)}", "danger")
        total_customers, churn_count, non_churn_count, churn_rate, avg_monthly = 0, 0, 0, 0.0, 0.0
        contract_labels = ['Month-to-month', 'One year', 'Two year']
        contract_churned, contract_retained = [0, 0, 0], [0, 0, 0]
    finally:
        conn.close()

    return render_template(
        'dashboard.html',
        total_customers=total_customers,
        churn_count=churn_count,
        non_churn_count=non_churn_count,
        churn_rate=churn_rate,
        avg_monthly=avg_monthly,
        contract_labels=contract_labels,
        contract_churned=contract_churned,
        contract_retained=contract_retained
    )


# ==========================================
# PREDICTION & INTERACTIVE SIMULATOR
# ==========================================

@app.route('/predict', methods=['GET', 'POST'])
@login_required
def predict():
    if request.method == 'POST':
        try:
            tenure = int(request.form.get('tenure', 12))
            monthly_charges = float(request.form.get('monthly_charges', 75.0))
            contract = request.form.get('contract', 'One year')
            internet_service = request.form.get('internet_service', 'DSL')
            payment_method = request.form.get('payment_method', 'Electronic check')
            
            total_charges_raw = request.form.get('total_charges')
            total_charges = float(total_charges_raw) if total_charges_raw else round(tenure * monthly_charges, 2)

            base_score = 0.0
            if contract == 'Month-to-month':
                base_score += 40.0
            elif contract == 'One year':
                base_score += 15.0
                
            if internet_service == 'Fiber optic':
                base_score += 25.0
            elif internet_service == 'DSL':
                base_score += 10.0
                
            if payment_method == 'Electronic check':
                base_score += 15.0

            if tenure < 12:
                base_score += 20.0
            elif tenure > 24:
                base_score -= 15.0

            probability = min(max(base_score, 5.0), 95.0)
            prediction = 'Churn' if probability >= 50.0 else 'No Churn'
            risk_level = 'High Risk' if probability >= 50.0 else 'Low Risk'

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO customers (tenure, contract, monthly_charges, total_charges, internet_service, payment_method, churn)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (tenure, contract, monthly_charges, total_charges, internet_service, payment_method, prediction))
            
            customer_id = cursor.lastrowid

            cursor.execute('''
                INSERT INTO predictions (customer_id, prediction, probability, risk_level)
                VALUES (?, ?, ?, ?)
            ''', (customer_id, prediction, probability, risk_level))

            conn.commit()
            conn.close()

            return render_template(
                'predict.html',
                tenure=tenure,
                monthly_charges=monthly_charges,
                total_charges=total_charges,
                contract=contract,
                internet_service=internet_service,
                payment_method=payment_method,
                prediction=prediction,
                probability=probability,
                risk_level=risk_level
            )
        except Exception as e:
            flash(f"Error processing churn prediction: {str(e)}", "danger")

    return render_template('predict.html', prediction=None)


# ==========================================
# BATCH PREDICTION
# ==========================================

@app.route('/batch_predict', methods=['GET', 'POST'])
@login_required
def batch_predict():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or file.filename == '':
            flash("Please upload a valid CSV file.", "danger")
            return render_template('batch_predict.html')

        try:
            stream = io.StringIO(file.stream.read().decode("UTF-8"), newline=None)
            csv_input = csv.DictReader(stream)
            
            records_processed = 0
            conn = get_db_connection()
            cursor = conn.cursor()

            for row in csv_input:
                tenure = int(row.get('tenure', 12))
                monthly_charges = float(row.get('monthly_charges', 75.0))
                contract = row.get('contract', 'Month-to-month')
                internet_service = row.get('internet_service', 'DSL')
                payment_method = row.get('payment_method', 'Electronic check')
                total_charges = float(row.get('total_charges', tenure * monthly_charges))

                probability = 75.0 if contract == 'Month-to-month' else 25.0
                prediction = 'Churn' if probability >= 50.0 else 'No Churn'
                risk_level = 'High Risk' if probability >= 50.0 else 'Low Risk'

                cursor.execute('''
                    INSERT INTO customers (tenure, contract, monthly_charges, total_charges, internet_service, payment_method, churn)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (tenure, contract, monthly_charges, total_charges, internet_service, payment_method, prediction))
                
                customer_id = cursor.lastrowid

                cursor.execute('''
                    INSERT INTO predictions (customer_id, prediction, probability, risk_level)
                    VALUES (?, ?, ?, ?)
                ''', (customer_id, prediction, probability, risk_level))
                
                records_processed += 1

            conn.commit()
            conn.close()

            flash(f"Batch prediction completed! Processed {records_processed} records.", "success")
            return redirect(url_for('customers'))

        except Exception as e:
            flash(f"Failed to process CSV: {str(e)}", "danger")

    return render_template('batch_predict.html')


# ==========================================
# CUSTOMER DIRECTORY & CAMPAIGNS
# ==========================================

@app.route('/customers')
@login_required
def customers():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            c.customer_id,
            c.tenure,
            c.contract,
            c.monthly_charges,
            c.total_charges,
            p.risk_level,
            p.probability
        FROM customers c
        LEFT JOIN predictions p ON c.customer_id = p.customer_id
        ORDER BY c.customer_id DESC
    ''')
    customer_list = cursor.fetchall()
    conn.close()

    return render_template('customers.html', customers=customer_list)


@app.route('/send_campaign/<int:customer_id>', methods=['POST'])
@login_required
def send_campaign(customer_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Look up risk profile to generate dynamic discount offer
    cursor.execute('''
        SELECT risk_level FROM predictions WHERE customer_id = ?
    ''', (customer_id,))
    pred = cursor.fetchone()
    
    risk_level = pred['risk_level'] if pred and pred['risk_level'] else 'Low Risk'

    if risk_level == 'High Risk':
        offer_type = '30% Urgent VIP Retention Discount'
        discount_percent = 30.0
    else:
        offer_type = '10% Loyalty Contract Renewal'
        discount_percent = 10.0

    cursor.execute('''
        INSERT INTO retention_campaigns (customer_id, offer_type, discount_percent, status)
        VALUES (?, ?, ?, 'Sent')
    ''', (customer_id, offer_type, discount_percent))
    
    conn.commit()
    conn.close()

    flash(f"Custom {discount_percent}% campaign sent to Customer #{customer_id} ({risk_level})!", "success")
    return redirect(url_for('customers'))


@app.route('/campaigns')
@login_required
def campaigns():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            rc.campaign_id,
            rc.customer_id,
            rc.offer_type,
            rc.discount_percent,
            rc.sent_at,
            rc.status,
            p.risk_level
        FROM retention_campaigns rc
        LEFT JOIN predictions p ON rc.customer_id = p.customer_id
        ORDER BY rc.campaign_id DESC
    ''')
    campaign_list = cursor.fetchall()
    conn.close()

    return render_template('campaigns.html', campaigns=campaign_list)


@app.route('/export_customers')
@login_required
def export_customers():
    risk_filter = request.args.get('risk')
    
    conn = get_db_connection()
    cursor = conn.cursor()

    if risk_filter:
        cursor.execute('''
            SELECT c.customer_id, c.tenure, c.contract, c.monthly_charges, c.total_charges, p.risk_level, p.probability
            FROM customers c
            LEFT JOIN predictions p ON c.customer_id = p.customer_id
            WHERE p.risk_level = ?
        ''', (risk_filter,))
    else:
        cursor.execute('''
            SELECT c.customer_id, c.tenure, c.contract, c.monthly_charges, c.total_charges, p.risk_level, p.probability
            FROM customers c
            LEFT JOIN predictions p ON c.customer_id = p.customer_id
        ''')
        
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Customer ID', 'Tenure (Months)', 'Contract', 'Monthly Charges', 'Total Charges', 'Risk Level', 'Probability (%)'])

    for row in rows:
        writer.writerow([
            row['customer_id'],
            row['tenure'],
            row['contract'],
            row['monthly_charges'],
            row['total_charges'],
            row['risk_level'] if row['risk_level'] else 'N/A',
            row['probability'] if row['probability'] is not None else '0.0'
        ])

    filename = "high_risk_customers.csv" if risk_filter else "all_customers.csv"
    
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


# ==========================================
# DEV SEED ROUTE (POPULATE MOCK DATA)
# ==========================================

@app.route('/seed_data')
def seed_data():
    conn = get_db_connection()
    cursor = conn.cursor()

    mock_customers = [
        (2, 'Month-to-month', 85.5, 171.0, 'Fiber optic', 'Electronic check', 'Churn', 82.5, 'High Risk'),
        (36, 'Two year', 45.0, 1620.0, 'DSL', 'Credit card', 'No Churn', 12.0, 'Low Risk'),
        (12, 'One year', 65.0, 780.0, 'DSL', 'Bank transfer', 'No Churn', 28.0, 'Low Risk'),
        (1, 'Month-to-month', 95.0, 95.0, 'Fiber optic', 'Electronic check', 'Churn', 91.0, 'High Risk'),
        (48, 'Two year', 110.0, 5280.0, 'Fiber optic', 'Credit card', 'No Churn', 18.0, 'Low Risk'),
    ]

    for c in mock_customers:
        cursor.execute('''
            INSERT INTO customers (tenure, contract, monthly_charges, total_charges, internet_service, payment_method, churn)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (c[0], c[1], c[2], c[3], c[4], c[5], c[6]))
        
        cid = cursor.lastrowid

        cursor.execute('''
            INSERT INTO predictions (customer_id, prediction, probability, risk_level)
            VALUES (?, ?, ?, ?)
        ''', (cid, c[6], c[7], c[8]))

    conn.commit()
    conn.close()

    flash("Database seeded with mock customer profiles!", "success")
    return redirect(url_for('customers'))


# ==========================================
# GLOBAL ERROR HANDLERS (WITH SAFE FALLBACKS)
# ==========================================

@app.errorhandler(404)
def page_not_found(e):
    try:
        return render_template('404.html'), 404
    except Exception:
        return "<h1>404 - Page Not Found</h1><p>The page you are looking for does not exist.</p>", 404

@app.errorhandler(500)
def internal_server_error(e):
    try:
        return render_template('500.html'), 500
    except Exception:
        return "<h1>500 - Internal Server Error</h1><p>An unexpected error occurred on the server.</p>", 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)