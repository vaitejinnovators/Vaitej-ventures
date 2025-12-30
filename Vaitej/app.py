from flask import (
    Flask, render_template, redirect,
    url_for, request, session, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

from validators import (
    validate_common,
    validate_founder,
    validate_investor
)
from config import Config
from datetime import date,timedelta,datetime
import os, time

# -------------------------------------------------
# APP SETUP
# -------------------------------------------------
app = Flask(__name__)
app.config.from_object(Config)

# Session + flash
app.secret_key = app.config.get("SECRET_KEY", "dev-secret-key")
# -------------------------------------------------
# FILE UPLOAD CONFIG
# -------------------------------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)
def calculate_match_score(founder, investor, pitch_score):
    score = 0
    reasons = []

    # 1️⃣ Stage fit (30)
    if founder["stage"] and founder["stage"] in investor["investment_stage"]:
        score += 30
        reasons.append("stage alignment")

    # 2️⃣ Sector fit (25)
    if founder["sector"] and investor["sector_focus"]:
        if founder["sector"].lower() in investor["sector_focus"].lower():
            score += 25
            reasons.append("sector alignment")

    # 3️⃣ Check size fit (15)
    if (
        investor["typical_check_min"]
        and investor["typical_check_max"]
        and founder["min_check_size"]
    ):
        if investor["typical_check_min"] <= founder["min_check_size"] <= investor["typical_check_max"]:
            score += 15
            reasons.append("check size compatibility")

    # 4️⃣ Geography fit (10)
    if founder["country"] and investor["geography_focus"]:
        if founder["country"].lower() in investor["geography_focus"].lower():
            score += 10
            reasons.append("geographic focus")

    # 5️⃣ Trust & activity (10)
    if investor["verification_status"] == "verified":
        score += 6
        reasons.append("verified investor")

    if investor["activity_status"] == "active":
        score += 4

    # 6️⃣ Founder readiness boost (10)
    if pitch_score >= 80:
        score += 10
        reasons.append("strong pitch readiness")
    elif pitch_score >= 60:
        score += 5

    return score, ", ".join(reasons)

# -------------------------------------------------
# ENTRY PAGE
# -------------------------------------------------
@app.route("/")
def entry():
    return render_template("entry.html")

# -------------------------------------------------
# ROLE SELECTION
# -------------------------------------------------
@app.route("/continue/<role>")
def continue_as(role):
    if role not in ["founder", "investor"]:
        return redirect(url_for("entry"))

    session.clear()  # reset any stale session
    session["selected_role"] = role

    return redirect(url_for("register", role=role))

# -------------------------------------------------
# LOGIN (WORKING AUTH)
# -------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    role = session.get("selected_role")

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template(
                "login.html",
                error="Email and password are required.",
                role=role
            )

        user = db.session.execute(
            text("""
                SELECT id, role, password_hash
                FROM users
                WHERE email = :email
            """),
            {"email": email}
        ).fetchone()

        if not user or not check_password_hash(user.password_hash, password):
            return render_template(
                "login.html",
                error="Invalid email or password.",
                role=role
            )

        # -------- SESSION SET --------
        session.clear()
        session["user_id"] = user.id
        session["role"] = user.role

        # -------- REDIRECT --------
        if user.role == "founder":
            return redirect(url_for("founder_home"))

        if user.role == "investor":
            return redirect(url_for("investor_home"))

    return render_template("login.html", role=role)

# -------------------------------------------------
# REGISTER (STRICT ONBOARDING)
# -------------------------------------------------
@app.route("/register/<role>", methods=["GET", "POST"])
def register(role):
    if role not in ["founder", "investor"]:
        return redirect(url_for("entry"))

    if request.method == "POST":
        form = request.form

        # ---------- COMMON VALIDATION ----------
        if not validate_common(form):
            return render_template(
                f"register_{role}.html",
                error="Please fill all required fields."
            )

        # ---------- ROLE VALIDATION ----------
        if role == "founder" and not validate_founder(form):
            return render_template(
                "register_founder.html",
                error="Please complete all founder fields."
            )

        if role == "investor" and not validate_investor(form):
            return render_template(
                "register_investor.html",
                error="Please complete all investor fields."
            )

        try:
            # ---------- CHECK EMAIL ----------
            existing = db.session.execute(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": form["email"]}
            ).fetchone()

            if existing:
                return render_template(
                    f"register_{role}.html",
                    error="An account with this email already exists."
                )

            # ---------- CREATE USER ----------
            result = db.session.execute(
                text("""
                    INSERT INTO users
                    (role, full_name, email, password_hash,
                     phone, country, referral_source)
                    VALUES
                    (:role, :full_name, :email, :password,
                     :phone, :country, :referral)
                """),
                {
                    "role": role,
                    "full_name": form["full_name"],
                    "email": form["email"],
                    "password": generate_password_hash(form["password"]),
                    "phone": form["phone"],
                    "country": form["country"],
                    "referral": form.get("referral")
                }
            )

            user_id = result.lastrowid

            # ---------- ROLE PROFILE ----------
            if role == "founder":
                db.session.execute(
                    text("""
                        INSERT INTO founder_profiles
                        (user_id, company_name, founding_year,
                         stage, sector, business_model, actively_raising)
                        VALUES
                        (:user_id, :company_name, :founding_year,
                         :stage, :sector, :business_model, :actively_raising)
                    """),
                    {
                        "user_id": user_id,
                        "company_name": form["company_name"],
                        "founding_year": form["founding_year"],
                        "stage": form["stage"],
                        "sector": form["sector"],
                        "business_model": form["business_model"],
                        "actively_raising": form["actively_raising"]
                    }
                )

            if role == "investor":
                db.session.execute(
                    text("""
                        INSERT INTO investor_profiles
                        (user_id, fund_name, investment_stage,
                         sector_focus, geography_focus,
                         typical_check_min, accredited)
                        VALUES
                        (:user_id, :fund_name, :investment_stage,
                         :sector_focus, :geography_focus,
                         :check_size, :accredited)
                    """),
                    {
                        "user_id": user_id,
                        "fund_name": form["fund_name"],
                        "investment_stage": form["investment_stage"],
                        "sector_focus": form["sector_focus"],
                        "geography_focus": form["geography_focus"],
                        "check_size": form["check_size"],
                        "accredited": form["accredited"]
                    }
                )

            db.session.commit()

            # Show success animation, then user goes to login
            return render_template(f"register_{role}.html", success=True)

        except Exception as e:
            db.session.rollback()
            return render_template(
                f"register_{role}.html",
                error="Something went wrong. Please try again."
            )

    return render_template(f"register_{role}.html")

# -------------------------------------------------
# FOUNDER DASHBOARD (PROTECTED)
# -------------------------------------------------
@app.route("/founder/home")
def founder_home():
    if session.get("role") != "founder":
        return redirect(url_for("login"))

    user_id = session.get("user_id")

    founder = db.session.execute(
        text("""
            SELECT 
                u.full_name,
                u.email,
                u.phone,
                u.country,
                f.company_name,
                f.founding_year,
                f.stage,
                f.sector,
                f.business_model,
                f.actively_raising,
                f.raise_target,
                f.raise_raised,
                f.fundraising_status,
                f.fundraising_start_date
            FROM users u
            JOIN founder_profiles f ON u.id = f.user_id
            WHERE u.id = :uid
        """),
        {"uid": user_id}
    ).fetchone()

    # ------------------------------------------------
    # PROFILE COMPLETION (already built)
    # ------------------------------------------------
    required_fields = [
        founder.full_name, founder.email, founder.phone, founder.country,
        founder.company_name, founder.founding_year, founder.stage,
        founder.sector, founder.business_model
    ]
    completion_percent = int(
        (sum(1 for f in required_fields if f) / len(required_fields)) * 100
    )

    missing_fields = []
    if not founder.phone:
        missing_fields.append("Phone number")
    if not founder.sector:
        missing_fields.append("Sector")
    if not founder.business_model:
        missing_fields.append("Business model")

    # ------------------------------------------------
    # PITCH READINESS (already built)
    # ------------------------------------------------
    pitch_score = 0
    if founder.company_name: pitch_score += 10
    if founder.stage: pitch_score += 15
    if founder.sector: pitch_score += 15
    if founder.business_model: pitch_score += 15
    if founder.actively_raising: pitch_score += 15
    if founder.founding_year: pitch_score += 10
    if completion_percent >= 80: pitch_score += 20

    pitch_label = (
        "Investor-Ready" if pitch_score >= 80
        else "Good" if pitch_score >= 50
        else "Needs Work"
    )

    # ------------------------------------------------
    # RAISE PROGRESS
    # ------------------------------------------------
    raise_target = founder.raise_target or 0
    raise_raised = founder.raise_raised or 0
    raise_percent = int((raise_raised / raise_target) * 100) if raise_target else 0

    # ------------------------------------------------
    # FUNDRAISING TIMELINE (NEW)
    # ------------------------------------------------
    weeks_elapsed = None
    if founder.fundraising_start_date:
        delta = date.today() - founder.fundraising_start_date
        weeks_elapsed = delta.days // 7

    # ------------------------------------------------
    # RECENT INVESTOR ACTIVITY (LAST 7 DAYS)
    # ------------------------------------------------
    recent_views = db.session.execute(
        text("""
            SELECT COUNT(*) 
            FROM investor_profile_views
            WHERE founder_id = :fid
              AND viewed_at >= NOW() - INTERVAL 7 DAY
        """),
        {"fid": user_id}
    ).scalar()

    expressed_interest = db.session.execute(
    text("""
        SELECT COUNT(*)
        FROM matches
        WHERE founder_id = :fid
          AND status = 'interested'
    """),
    {"fid": user_id}
).scalar()


    # Simple text-based AI alert (MVP)
    ai_alert = None
    if completion_percent < 80:
        ai_alert = "Complete your profile to increase investor visibility."
    elif pitch_score < 70:
        ai_alert = "Your pitch is decent, but missing key investor signals."
    else:
        ai_alert = "Your profile is investor-ready. Start outreach."

    return render_template(
        "dashboard/founder_home.html",
        founder=founder,
        completion_percent=completion_percent,
        missing_fields=missing_fields,
        pitch_score=pitch_score,
        pitch_label=pitch_label,
        raise_target=raise_target,
        raise_raised=raise_raised,
        raise_percent=raise_percent,
        weeks_elapsed=weeks_elapsed,
        recent_views=recent_views,
        expressed_interest=expressed_interest,
        ai_alert=ai_alert
    )

@app.route("/founder/matches/generate")
def generate_matches():
    if session.get("role") != "founder":
        return redirect(url_for("login"))

    user_id = session.get("user_id")

    # Founder data
    founder = db.session.execute(
        text("""
            SELECT f.id, f.stage, f.sector, f.min_check_size,
                   u.country
            FROM founder_profiles f
            JOIN users u ON f.user_id = u.id
            WHERE f.user_id = :uid
        """),
        {"uid": user_id}
    ).mappings().first()

    pitch_score = 70  # already computed earlier (reuse later)

    # Eligible investors
    investors = db.session.execute(
        text("""
            SELECT
                ip.id,
                ip.investment_stage,
                ip.sector_focus,
                ip.geography_focus,
                ip.typical_check_min,
                ip.typical_check_max,
                ip.verification_status,
                ip.activity_status
            FROM investor_profiles ip
            WHERE ip.activity_status = 'active'
              AND ip.verification_status != 'rejected'
        """)
    ).mappings().all()

    for investor in investors:
        score, reason = calculate_match_score(founder, investor, pitch_score)

        if score < 40:
            continue  # not worth saving

        db.session.execute(
            text("""
                INSERT INTO matches
                (founder_id, investor_id, match_score, status, ai_reason)
                VALUES
                (:fid, :iid, :score, 'new', :reason)
                ON DUPLICATE KEY UPDATE
                    match_score = :score,
                    ai_reason = :reason,
                    updated_at = NOW()
            """),
            {
                "fid": founder["id"],
                "iid": investor["id"],
                "score": score,
                "reason": reason
            }
        )

    db.session.commit()
    return redirect(url_for("founder_matches"))
@app.route("/founder/matches")
def founder_matches():
    if session.get("role") != "founder":
        return redirect(url_for("login"))

    user_id = session.get("user_id")

    matches = db.session.execute(
        text("""
            SELECT
                m.id AS match_id,
                m.match_score,
                m.status,
                m.ai_reason,
                u.full_name AS investor_name,
                ip.fund_name,
                ip.investment_stage,
                ip.sector_focus,
                ip.verification_status
            FROM matches m
            JOIN investor_profiles ip ON m.investor_id = ip.id
            JOIN users u ON ip.user_id = u.id
            WHERE m.founder_id = (
                SELECT id FROM founder_profiles WHERE user_id = :uid
            )
              AND m.status != 'declined'
            ORDER BY m.match_score DESC
            LIMIT 10
        """),
        {"uid": user_id}
    ).fetchall()

    return render_template(
        "dashboard/founder_matches.html",
        matches=matches
    )
@app.route("/match/<int:match_id>/<action>")
def update_match_status(match_id, action):
    if session.get("role") != "founder":
        return redirect(url_for("login"))

    if action not in ["interested", "saved", "declined"]:
        return redirect(url_for("founder_matches"))

    db.session.execute(
        text("""
            UPDATE matches
            SET status = :status,
                updated_at = NOW()
            WHERE id = :mid
        """),
        {"status": action, "mid": match_id}
    )

    db.session.commit()
    return redirect(url_for("founder_matches"))


@app.route("/founder/pitch")
def founder_pitch():
    if session.get("role") != "founder":
        return redirect(url_for("login"))

    user_id = session.get("user_id")

    deck = db.session.execute(
        text("""
            SELECT *
            FROM pitch_decks
            WHERE founder_id = (
                SELECT id FROM founder_profiles WHERE user_id = :uid
            )
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"uid": user_id}
    ).fetchone()

    return render_template(
        "dashboard/founder_pitch.html",
        deck=deck
    )

@app.route("/founder/pitch/upload", methods=["POST"])
def upload_pitch():
    if session.get("role") != "founder":
        return redirect(url_for("login"))

    file = request.files.get("pitch_deck")

    if not file or not file.filename.lower().endswith(".pdf"):
        flash("Only PDF files are allowed.")
        return redirect(url_for("founder_pitch"))

    user_id = session.get("user_id")

    # ---------- SAFE FILENAME ----------
    filename = f"pitch_{user_id}_{int(time.time())}.pdf"

    # ---------- FILESYSTEM PATH ----------
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(file_path)

    # ---------- PUBLIC URL ----------
    file_url = f"/static/uploads/{filename}"

    founder_id = db.session.execute(
        text("SELECT id FROM founder_profiles WHERE user_id = :uid"),
        {"uid": user_id}
    ).scalar()

    pitch_score = 70  # temporary

    db.session.execute(
        text("""
            INSERT INTO pitch_decks (founder_id, file_url, deck_score)
            VALUES (:fid, :file_url, :score)
        """),
        {
            "fid": founder_id,
            "file_url": file_url,
            "score": pitch_score
        }
    )

    db.session.commit()
    flash("Pitch deck uploaded successfully.")
    return redirect(url_for("founder_pitch"))

# -------------------------------------------------
# INVESTOR DASHBOARD (PLACEHOLDER)
# -------------------------------------------------
@app.route("/investor/home")
def investor_home():
    if session.get("role") != "investor":
        return redirect(url_for("login"))

    return "Investor Dashboard (Coming Soon)"

# -------------------------------------------------
# LOGOUT
# -------------------------------------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("entry"))

# -------------------------------------------------
# RUN APP
# -------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
