from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler 
from sqlalchemy import or_, case
import time

# --- 1. APP CONFIGURATION & INITIALIZATION ---
app = Flask(__name__)
# IMPORTANT: Change this secret key!
app.config['SECRET_KEY'] = 'your_super_secret_key_for_security' 
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///clinic.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login' 

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 2. DATABASE MODELS ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(50), default='user', nullable=False) 
    appointments = db.relationship('Appointment', backref='patient', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    specialty = db.Column(db.String(100), nullable=False) 
    appointments = db.relationship('Appointment', backref='doctor', lazy=True)

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=False)
    # Emergency level is 1-5 now
    emergency_level = db.Column(db.Integer, nullable=False) 
    priority_boost_counter = db.Column(db.Integer, default=0, nullable=False) 
    booking_time = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # Status can be 'pending', 'in_chamber', 'completed'
    status = db.Column(db.String(20), default='pending', nullable=False)

# --- PRIORITY QUEUE HELPER FUNCTION (CUSTOM EMERGENCY PRIORITY PRE-QUEUE) ---
def get_sorted_queue():
    """
    IMPLEMENTATION OF CUSTOM MLFQ-INSPIRED ALGORITHM:
    The first 4 pending appointments (FIFO) are sorted based on Emergency Level (highest first).
    The remaining appointments maintain their original FIFO order.
    """
    # 1. Fetch all pending appointments, initially ordered by FIFO (booking time)
    all_pending = db.session.query(Appointment) \
                            .filter(Appointment.status == 'pending') \
                            .order_by(Appointment.booking_time.asc()) \
                            .all()
    
    # 2. Split into the priority-boosted group (top 4) and the remainder
    top_four = all_pending[:4]
    the_rest = all_pending[4:]
    
    # 3. Define the custom sorting key for the top 4:
    # Primary Key: Emergency Level (Descending: -appt.emergency_level)
    # Secondary Key: Booking Time (Ascending: appt.booking_time)
    def custom_sort_key(appt):
        return (-appt.emergency_level, appt.booking_time)

    # 4. Sort the top 4
    sorted_top_four = sorted(top_four, key=custom_sort_key)
    
    # 5. Combine the sorted top 4 with the rest of the queue (which is already FIFO sorted)
    final_sorted_queue = sorted_top_four + the_rest
                            
    return final_sorted_queue

# --- PRIORITY BOOST FUNCTION (Runs every 1 minute to update the counter) ---
def priority_boost():
    """Increments the priority boost counter for all pending appointments."""
    with app.app_context():
        # Get all appointments that are still waiting (pending)
        pending_appointments = Appointment.query.filter_by(status='pending').all()
        
        for appt in pending_appointments:
            appt.priority_boost_counter += 1
            # Optional: Log the boost
            # print(f"Boosted Appointment ID {appt.id} to {appt.priority_boost_counter}")
            
        db.session.commit()

# --- 3. DATABASE CREATION & INITIAL ADMIN / DOCTOR SEEDING (Executed on startup) ---
with app.app_context():
    db.create_all()
    
    # Create the first Admin User if they don't exist
    if User.query.filter_by(role='admin').first() is None:
        admin_user = User(username='admin', role='admin')
        admin_user.set_password('adminpass') 
        db.session.add(admin_user)
        db.session.commit()
        print("--- Default Admin User Created: username='admin', password='adminpass' ---")

    # --- Check for and create Initial Doctors ---
    if Doctor.query.count() == 0:
        doctors_to_add = [
            Doctor(name='Dr. Alice Smith', specialty='General Practice'),
            Doctor(name='Dr. Bob Johnson', specialty='Cardiology'),
            Doctor(name='Dr. Carol Lee', specialty='Pediatrics'),
            Doctor(name='Dr. Shahriar Mahmud', specialty='Neuro Science'),
            Doctor(name='Dr. Maliha Binte Mahmud', specialty='Orthopedics'),
            Doctor(name='Dr. Mithila Rejwan', specialty='Dermatologist'),
            Doctor(name='Prof. Dr. Zarek Tia', specialty='General Practice'),
            Doctor(name='Prof. Dr. Obaydul Crowe', specialty='Oncology'),
            Doctor(name='Dr. Zimbabar FCPS(UIU) ', specialty='Nephrology')
        ]
        db.session.add_all(doctors_to_add)
        db.session.commit()
        print("--- Initial Doctors Seeded ---")


# --- 4. CORE ROUTES (Login, Home, Logout) ---

@app.route('/')
def home():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('user_dashboard'))

    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('home')) 
        else:
            flash('Login failed. Check username and password.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


# --- 5. USER REGISTRATION ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if User.query.filter_by(username=username).first():
            flash('Username already taken. Please choose another.', 'warning')
            return redirect(url_for('register'))
            
        new_user = User(username=username, role='user') 
        new_user.set_password(password)
        
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')


# --- 6. USER APPOINTMENT BOOKING ---
@app.route('/book_appointment', methods=['GET', 'POST'])
@login_required
def book_appointment():
    if current_user.role != 'user':
        flash('Permission denied.', 'danger')
        return redirect(url_for('home'))
        
    doctors = Doctor.query.all()

    if request.method == 'POST':
        doctor_id = request.form.get('doctor_id')
        emergency_level = request.form.get('emergency_level')
        
        try:
            # ORIGINAL LOGIC RESTORED: User is now allowed to book multiple pending appointments.
            
            new_appointment = Appointment(
                user_id=current_user.id,
                doctor_id=int(doctor_id),
                emergency_level=int(emergency_level),
                status='pending' 
            )
            db.session.add(new_appointment)
            db.session.commit()
            
            flash('Appointment booked successfully! It is now in the queue.', 'success')
            return redirect(url_for('user_dashboard'))
        
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred during booking: {e}', 'danger')
            
    return render_template('book.html', doctors=doctors)


# --- 7. USER DASHBOARD & CANCELLATION ---
@app.route('/user_dashboard')
@login_required
def user_dashboard():
    if current_user.role != 'user':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin_dashboard'))
        
    # Fetch all appointments for the user, ordering active ones first.
    appointments = Appointment.query.filter_by(user_id=current_user.id) \
                                    .order_by(db.case(
                                        (Appointment.status == 'pending', 0),
                                        (Appointment.status == 'in_chamber', 1),
                                        else_=2
                                    ).asc(), Appointment.booking_time.desc()) \
                                    .all()

    doctors = Doctor.query.all()
    doctor_map = {d.id: d.name for d in doctors}
    
    return render_template('user_dashboard.html', 
                           appointments=appointments,
                           doctor_map=doctor_map)

@app.route('/cancel_appointment/<int:id>', methods=['POST'])
@login_required
def cancel_appointment(id):
    if current_user.role != 'user':
        flash('Permission denied.', 'danger')
        return redirect(url_for('user_dashboard'))

    appointment = Appointment.query.get_or_404(id)
    
    if appointment.user_id != current_user.id:
        flash('You can only cancel your own appointments.', 'danger')
        return redirect(url_for('user_dashboard'))

    if appointment.status == 'pending':
        try:
            db.session.delete(appointment)
            db.session.commit()
            flash(f'Appointment #{id} has been successfully cancelled.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred while attempting to cancel the appointment: {e}', 'danger')
    else:
        flash('Appointment can only be cancelled if the status is Pending.', 'warning')
        
    return redirect(url_for('user_dashboard'))


# --- 8. ADMIN DOCTOR MANAGEMENT ---
@app.route('/admin_add_doctor', methods=['GET', 'POST'])
@login_required
def admin_add_doctor():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        specialty = request.form.get('specialty')
        
        new_doctor = Doctor(name=name, specialty=specialty)
        db.session.add(new_doctor)
        db.session.commit()
        flash(f'Dr. {name} ({specialty}) added successfully.', 'success')
        return redirect(url_for('admin_dashboard'))
        
    return render_template('admin_add_doctor.html')


# --- 9. ADMIN DASHBOARD & STATUS UPDATE ---

@app.route('/admin_dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        flash('Access denied. You must be an administrator.', 'danger')
        return redirect(url_for('user_dashboard'))

    # Get the selected doctor ID from the URL query parameters
    # The default value for the filter is an empty string for "All Doctors"
    selected_doctor_id_str = request.args.get('doctor_id', type=str, default='')
    
    selected_doctor_id = None
    if selected_doctor_id_str.isdigit():
        selected_doctor_id = int(selected_doctor_id_str)

    
    # Base query for appointments that are still active (pending or in_chamber)
    appointment_query = Appointment.query.filter(
        or_(Appointment.status == 'pending', Appointment.status == 'in_chamber')
    )
    
    # Apply doctor filter if selected
    if selected_doctor_id:
        appointment_query = appointment_query.filter(Appointment.doctor_id == selected_doctor_id)
    
    # Custom ordering: 'in_chamber' appointments first (value 0)
    appointment_query = appointment_query.order_by(
        db.case(
            *(
                (Appointment.status == 'in_chamber', 0), 
            ), 
            else_=1
        ),
        Appointment.booking_time.asc()
    )

    # Get all active appointments (In Chamber + Pending)
    active_appointments = appointment_query.all()
    
    # 1. Separate 'in_chamber' and 'pending' appointments
    in_chamber_list = [appt for appt in active_appointments if appt.status == 'in_chamber']
    pending_list = [appt for appt in active_appointments if appt.status == 'pending']
    
    # 2. Apply the custom sorting logic to the pending list (Emergency Priority Pre-Queue)
    priority_group_unsorted = pending_list[:4]
    fifo_queue = pending_list[4:]

    # Define the custom sorting key: Emergency Level (Descending) then Booking Time (Ascending)
    def custom_sort_key(appt):
        return (-appt.emergency_level, appt.booking_time)

    priority_group = sorted(priority_group_unsorted, key=custom_sort_key)
    
    # Get all doctors for the filter dropdown
    doctors = Doctor.query.order_by(Doctor.name).all()
    doctor_map = {d.id: d.name for d in doctors}
    
    # Also fetch a map of users for displaying patient usernames
    users = User.query.filter_by(role='user').all()
    user_map = {u.id: u.username for u in users}

    # PASSING THREE SEPARATE LISTS TO THE TEMPLATE
    # IMPORTANT: Pass the string version of the selected ID, which is '' for "All Doctors"
    return render_template('admin_dashboard.html', 
                           in_chamber_list=in_chamber_list,
                           priority_group=priority_group,
                           fifo_queue=fifo_queue,
                           doctors=doctors,
                           doctor_map=doctor_map,
                           user_map=user_map, 
                           selected_doctor_id=selected_doctor_id_str) 


@app.route('/update_appointment_status/<int:id>', methods=['POST'])
@login_required
def update_appointment_status(id):
    if current_user.role != 'admin':
        flash('Permission denied.', 'danger')
        return redirect(url_for('home'))

    appointment = Appointment.query.get_or_404(id)
    
    new_status = request.form.get('new_status')
    # Filter ID from the hidden form field. It is '' for 'All Doctors' or a doctor ID string.
    # CRITICAL: Use .get() with a default of '' to ensure we get a string, even if the field is missing
    filter_id_str = request.form.get('current_doctor_id', '') 
    
    
    if not new_status:
        flash('No status update action detected.', 'warning')
        return redirect(url_for('admin_dashboard'))

    doctor_id = appointment.doctor_id
    doctors = Doctor.query.all()
    doctor_map = {d.id: d.name for d in doctors}

    
    if new_status == 'in_chamber':
        # Check if another patient is already in the chamber for this doctor
        existing_in_chamber = Appointment.query.filter_by(
            doctor_id=doctor_id, status='in_chamber'
        ).first()
        
        if existing_in_chamber and existing_in_chamber.id != id:
            flash(f'Dr. {doctor_map.get(doctor_id, "N/A")} is already seeing a patient. You must mark them as completed first.', 'warning')
        else:
            appointment.status = 'in_chamber'
            appointment.priority_boost_counter = 0 
            db.session.commit()
            flash(f'Appointment #{id} moved to IN CHAMBER.', 'success')

    elif new_status == 'completed':
        appointment.status = 'completed'
        db.session.commit()
        flash(f'Appointment #{id} marked as COMPLETED. Queue updated.', 'success')
        
    else:
        flash('Invalid status update requested.', 'danger')

    # FINAL CRITICAL REDIRECTION FIX: Preserve Doctor Filter
    # If the filter_id_str is NOT empty and IS a digit, redirect with the parameter.
    # Otherwise, redirect to the clean, unfiltered URL.
    if filter_id_str and filter_id_str.isdigit():
        return redirect(url_for('admin_dashboard', doctor_id=filter_id_str))
        
    # Redirect to the default view (All Doctors)
    return redirect(url_for('admin_dashboard'))
    # END CRITICAL REDIRECTION FIX


# --- 10. STARTING THE APPLICATION ---
if __name__ == '__main__':
    # Scheduler logic will be activated here for Priority Boost
    scheduler = BackgroundScheduler()
    # Boost priority every 1 minute as requested for the custom scheduling logic
    scheduler.add_job(func=priority_boost, trigger="interval", minutes=1)
    scheduler.start()
    
    # NOTE: use_reloader=False is required when using BackgroundScheduler
    app.run(debug=True, use_reloader=False)
