from flask import Flask, render_template, request, jsonify, redirect, session, url_for, send_file, abort
import pandas as pd
import io
from datetime import datetime, timedelta
from db import recharges, users  # Import recharges and users collections
from bson.json_util import dumps
import re
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from bson import ObjectId
from bson.objectid import ObjectId
import pytz

IST = pytz.timezone('Asia/Kolkata')

app = Flask(__name__)

app.secret_key = 'your_secret_key_here'  # Needed for session

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or session['role'] != 'admin':
            return "Unauthorized access", 403
        return f(*args, **kwargs)
    return decorated_function

@app.route('/register', methods=['GET', 'POST'])
@admin_required
def register():
        
    if request.method == 'POST':
        uname = request.form['username']
        pwd = request.form['password']
        role = request.form['role']

        # Check if user already exists
        if users.find_one({"username": uname}):
            return render_template("register.html", msg="Username already exists")

        hashed = generate_password_hash(pwd)

        users.insert_one({
            "username": uname,
            "password": hashed,
            "role": role
        })

        # Delete dummy admin if exists
        users.delete_one({"username": "admin", "is_dummy": True})

        return render_template("register.html", msg="User registered successfully")
    
    return render_template("register.html")

@app.route('/history/<mobile>')
def history(mobile):
    entries = list(recharges.find({'mobile_no': mobile}).sort('recharge_date', -1))

    total_paid = 0.0
    total_unpaid = 0.0
    ist = pytz.timezone('Asia/Kolkata')

    for e in entries:
        e['_id'] = str(e['_id'])

        # Convert to float safely
        recharge_amt = float(e.get('recharge_amount', 0) or 0)
        paid_amt = float(e.get('amount_paid', 0) or 0)
        due_amt = recharge_amt - paid_amt

        e['due_amount'] = round(due_amt, 2)

        if paid_amt > 0:
            total_paid += paid_amt
        if due_amt > 0:
            total_unpaid += due_amt

        # 🔁 Safely convert recharge_date to string
        r_date = e.get('recharge_date')
        if isinstance(r_date, datetime):
            r_date = r_date.astimezone(ist)
            e['recharge_date'] = r_date.strftime('%d-%m-%Y')
        elif isinstance(r_date, str):
            try:
                dt = datetime.strptime(r_date, "%Y-%m-%dT%H:%M:%S")
                e['recharge_date'] = dt.strftime('%d-%m-%Y')
            except:
                e['recharge_date'] = r_date
        else:
            e['recharge_date'] = "N/A"

    return render_template(
        'history.html',
        entries=entries,
        mobile=mobile,
        total_paid=round(total_paid, 2),
        total_unpaid=round(total_unpaid, 2)
    )


@app.route('/export')
@admin_required
def export_excel():
    records = list(recharges.find())
    
    # Convert ObjectId to string
    for r in records:
        r['_id'] = str(r['_id'])

    # Create DataFrame
    df = pd.DataFrame(records)
    if '_id' in df.columns:
        df = df.drop(columns=['_id'])  # Optional: remove _id from Excel

    # Save to Excel in memory
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Recharges')

    output.seek(0)

    return send_file(output,
                     download_name="recharge_data.xlsx",
                     as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/search')
def search():
    query = request.args.get('query', '').strip()
    status = request.args.get('status', '').strip()
    month = request.args.get('month', '').strip()
    due_by_days = request.args.get('dueByDays', '').strip()

    # Initialize query_filter as an empty dictionary
    query_filter = {}
    filters = []
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)

    if query:
        regex = re.compile(f".*{re.escape(query)}.*", re.IGNORECASE)
        filters.append({
            "$or": [
                {"subscriber_name": regex},
                {"stb_no": regex},
                {"vc_no": regex},
                {"mobile_no": regex},
                {"_id": query}
            ]
        })

    if status in ['Paid', 'Unpaid']:
        filters.append({"status": status})

    if month:
        try:
            start = datetime.strptime(month, "%Y-%m")
            start = ist.localize(start)
            end = datetime(start.year + int(start.month == 12), (start.month % 12) + 1, 1)
            end = ist.localize(end)
            filters.append({
                "recharge_date": {
                    "$gte": start,
                    "$lt": end
                }
            })
        except:
            pass  # ignore invalid month

    if due_by_days and due_by_days.isdigit():
        days = int(due_by_days)
        threshold_date = now_ist - timedelta(days=days)
        filters.append({
            "status": "Unpaid",
            "recharge_date": {
                "$lte": threshold_date,
                "$exists": True
            }
        })

    # Only apply $and if we have filters
    if filters:
        query_filter = {"$and": filters}

    results = list(recharges.find(query_filter).sort('recharge_date', 1))  # oldest to newest

    # Track latest recharge amount per mobile
    last_recharge_map = {}

    for r in results:
        r['_id'] = str(r['_id'])

        # Safe numeric conversions
        try:
            recharge_amt = float(r.get('recharge_amount', 0) or 0)
        except:
            recharge_amt = 0

        try:
            paid_amt = float(r.get('amount_paid', 0) or 0)
        except:
            paid_amt = 0

        # Auto-set status
        r['status'] = "Paid" if paid_amt >= recharge_amt else "Unpaid"

        # Due amount
        r['due_amount'] = round(recharge_amt - paid_amt, 2)

        # Track latest recharge amount per mobile
        mobile = r.get('mobile_no')
        if mobile:
            last_recharge_map[mobile] = recharge_amt

        # Calculate days overdue (if unpaid and date present)
        if r['status'] == 'Unpaid' and r.get('recharge_date'):
            dt = r['recharge_date']
            if isinstance(dt, str):
                try:
                    dt = datetime.strptime(dt, "%Y-%m-%dT%H:%M:%S")
                    dt = ist.localize(dt)
                except:
                    try:
                        dt = datetime.strptime(dt, "%Y-%m-%d")
                        dt = ist.localize(dt)
                    except:
                        dt = None
            elif dt.tzinfo is None:
                dt = ist.localize(dt)

            if dt:
                r['days_overdue'] = (now_ist - dt).days

    # Add last recharge amount to each record
    for r in results:
        r['last_recharge_amount'] = last_recharge_map.get(r.get('mobile_no'), 0)

    return jsonify(results)

# Example route to save recharge data (we’ll build the form next)
@app.route('/submit', methods=['POST'])
def submit():
    data = request.form.to_dict()
    _id = data.pop('_id', '').strip()
    ist = pytz.timezone('Asia/Kolkata')

    # Handle dates
    if data.get('recharge_date'):
        try:
            naive_datetime = datetime.strptime(data['recharge_date'], "%Y-%m-%dT%H:%M")
            aware_datetime = ist.localize(naive_datetime)
            data['recharge_date'] = aware_datetime
        except ValueError as e:
            return jsonify({'status': 'error', 'message': f'Invalid recharge date format: {str(e)}'})

    if data.get('paid_date'):
        try:
            naive_date = datetime.strptime(data['paid_date'], "%Y-%m-%d")
            aware_date = ist.localize(naive_date)
            data['paid_date'] = aware_date
        except ValueError as e:
            return jsonify({'status': 'error', 'message': f'Invalid paid date format: {str(e)}'})

    # Convert recharge_amount and amount_paid to float for comparison
    try:
        amt_paid = float(data.get('amount_paid', 0))
        recharge_amt = float(data.get('recharge_amount', 0))
        data['status'] = "Paid" if amt_paid >= recharge_amt else "Unpaid"
    except ValueError:
        data['status'] = "Unpaid"  # fallback if invalid input

    if _id:
        try:
            obj_id = ObjectId(_id)
            result = recharges.update_one({'_id': obj_id}, {'$set': data})
            if result.matched_count:
                return jsonify({
                    'status': 'success',
                    'message': 'Entry updated successfully',
                    'new_id': str(obj_id)
                })
            else:
                return jsonify({'status': 'error', 'message': 'No entry found to update'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})
    else:
        insert_result = recharges.insert_one(data)
        return jsonify({
            'status': 'success',
            'message': 'New entry created',
            'new_id': str(insert_result.inserted_id)
        })

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        uname = request.form['username']
        pwd = request.form['password']
        user = users.find_one({"username": uname})

        if user and check_password_hash(user['password'], pwd):
            session['username'] = uname
            session['role'] = user['role']
            return redirect(url_for('dashboard'))
        else:
            return render_template("login.html", error="Invalid username or password")
    
    return render_template("login.html")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/summary')
def summary():
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    
    all_data = list(recharges.find({}))

    total_recharges = len(all_data)
    total_amount = sum(float(x.get('amount_paid', 0)) for x in all_data if x.get('amount_paid'))

    paid_recharges = sum(1 for x in all_data if x.get('status') == 'Paid')
    unpaid_recharges = sum(1 for x in all_data if x.get('status') == 'Unpaid')

    # Calculate overdue counts
    overdue_2_days = 0
    overdue_7_days = 0
    overdue_10_days = 0
    
    for entry in all_data:
        if entry.get('status') == 'Unpaid' and entry.get('recharge_date'):
            recharge_date = entry['recharge_date']
            if isinstance(recharge_date, str):
                try:
                    recharge_date = datetime.strptime(recharge_date, "%Y-%m-%dT%H:%M:%S")
                    recharge_date = ist.localize(recharge_date)
                except ValueError:
                    try:
                        recharge_date = datetime.strptime(recharge_date, "%Y-%m-%d")
                        recharge_date = ist.localize(recharge_date)
                    except ValueError:
                        continue
            
            if recharge_date and recharge_date.tzinfo is None:
                recharge_date = ist.localize(recharge_date)
            
            if recharge_date:
                days_overdue = (now_ist - recharge_date).days
                
                if days_overdue > 10:
                    overdue_10_days += 1
                elif days_overdue > 7:
                    overdue_7_days += 1
                elif days_overdue > 2:
                    overdue_2_days += 1

    # Filter by current month in IST
    this_month = sum(
        1 for x in all_data
        if isinstance(x.get('recharge_date'), datetime)
        and x['recharge_date'].astimezone(ist).year == now_ist.year
        and x['recharge_date'].astimezone(ist).month == now_ist.month
    )

    return jsonify({
        "total_recharges": total_recharges,
        "total_amount": total_amount,
        "paid_recharges": paid_recharges,
        "unpaid_recharges": unpaid_recharges,
        "this_month": this_month,
        "overdue_2_days": overdue_2_days,
        "overdue_7_days": overdue_7_days,
        "overdue_10_days": overdue_10_days
    })

@app.route('/get/<id>')
def get_entry(id):
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    try:
        entry = recharges.find_one({'_id': ObjectId(id)})
        if entry:
            entry['_id'] = str(entry['_id'])
            entry['due_amount'] = round(
                float(entry.get('recharge_amount', 0)) - float(entry.get('amount_paid', 0)), 2
            )

            return jsonify(entry)
        else:
            return jsonify({'status': 'error', 'message': 'Not found'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

"""@app.route('/update/<id>', methods=['PUT'])
def update_entry(id):
    if 'username' not in session:
        return jsonify({'status': 'unauthorized'}), 401

    data = request.form.to_dict()
    data.pop('_id', None)  # Remove _id from update data

    try:
        obj_id = ObjectId(id)
        
        # First delete the existing record
        delete_result = recharges.delete_one({'_id': obj_id})
        
        if delete_result.deleted_count == 1:
            # Handle date fields
            if data.get('recharge_date'):
                data['recharge_date'] = datetime.strptime(data['recharge_date'], "%Y-%m-%dT%H:%M")
            if data.get('paid_date'):
                data['paid_date'] = datetime.strptime(data['paid_date'], "%Y-%m-%d")
            
            # Insert the updated data as new record
            insert_result = recharges.insert_one(data)
            return jsonify({
                'status': 'success',
                'message': 'Entry updated successfully',
                'new_id': str(insert_result.inserted_id)
            })
        else:
            return jsonify({'status': 'error', 'message': 'Failed to update - document not found'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})"""

@app.route('/delete/<id>', methods=['DELETE'])
def delete_entry(id):
    if 'username' not in session or session.get('role') != 'admin':
        return jsonify({'status': 'unauthorized'}), 401

    try:
        result = recharges.delete_one({'_id': ObjectId(id)})
        if result.deleted_count:
            return jsonify({'status': 'success', 'message': 'Entry deleted'})
        else:
            return jsonify({'status': 'error', 'message': 'Entry not found'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/users')
@admin_required
def list_users():
    all_users = list(users.find({}, {"password": 0}))  # Hide passwords
    for u in all_users:
        u['_id'] = str(u['_id'])
    return render_template("users.html", users=all_users)

@app.route('/delete_user/<id>', methods=['POST'])
@admin_required
def delete_user(id):
    user = users.find_one({'_id': ObjectId(id)})
    
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'})

    if user['role'] == 'admin':
        admin_count = users.count_documents({'role': 'admin'})
        if admin_count <= 1:
            return jsonify({'status': 'error', 'message': 'At least one admin must remain.'})
    
    users.delete_one({'_id': ObjectId(id)})
    return jsonify({'status': 'success', 'message': 'User deleted'})

@app.route('/')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    current_date = datetime.now().strftime('%Y-%m-%d')
    return render_template("index.html", user=session['username'], role=session['role'], current_date=current_date)

if __name__ == '__main__':
    # Run dummy admin check before starting app
    if users.count_documents({}) == 0:
        users.insert_one({
            "username": "admin",
            "password": generate_password_hash("admin123"),
            "role": "admin",
            "is_dummy": True
        })
    app.run(host="0.0.0.0", port = 5000, debug=True)
