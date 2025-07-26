from flask import Flask, request, jsonify
import google.generativeai as genai
import os
import sqlite3
import json 
from dotenv import load_dotenv # This tool helps us load secrets from our .env file.
import smtplib # For sending emails
from email.mime.text import MIMEText # For creating email content
from email.mime.multipart import MIMEMultipart # For creating more complex email messages
import google.api_core.exceptions # Import for more specific API error handling
import requests # Import for making HTTP requests (used for Slack)

# Firebase Admin SDK imports
import firebase_admin
from firebase_admin import credentials, auth

# --- Step 1: Load environment variables ---
load_dotenv()

# --- Step 2: Initialize our Flask application (the "brain" of our toy) ---
app = Flask(__name__)

# --- Firebase Admin SDK Initialization ---
firebase_service_account_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY_JSON')
SERVICE_ACCOUNT_KEY_PATH = 'serviceAccountKey.json' # Keep for local fallback check

if firebase_service_account_json:
    
    try:
    
        cred = credentials.Certificate(json.loads(firebase_service_account_json))
        firebase_admin.initialize_app(cred)
        print("--- Firebase Admin SDK initialized from environment variable. ---")
    except Exception as e:
        print(f"--- ERROR: Failed to initialize Firebase Admin SDK from environment variable: {e} ---")
        print("--- Firebase authentication features may be disabled. ---")
       
else:
    
    if os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
        try:
            cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
            firebase_admin.initialize_app(cred)
            print("--- Firebase Admin SDK initialized from local file. ---")
        except Exception as e:
            print(f"--- ERROR: Failed to initialize Firebase Admin SDK from local file: {e} ---")
            print("--- Please ensure 'serviceAccountKey.json' is valid and accessible locally. ---")
            print("--- Firebase authentication features will be disabled. ---")
    else:
        print(f"--- WARNING: Firebase service account key file not found at {SERVICE_ACCOUNT_KEY_PATH}. "
              "And 'FIREBASE_SERVICE_ACCOUNT_KEY_JSON' environment variable is not set. "
              "Firebase authentication features will not work. ---")
        print("--- Please download 'serviceAccountKey.json' from Firebase Console -> Project Settings -> Service Accounts, "
              "or set the environment variable for deployment. ---")


# --- Step 3: Configure the Gemini AI Helper ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("ERROR: GOOGLE_API_KEY not found in environment variables. "
                     "Please make sure you have created a '.env' file in your project folder "
                     "and it contains GOOGLE_API_KEY='YOUR_API_KEY_HERE' (with your actual key).")


genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel(
    'models/gemini-2.5-pro', 
    safety_settings={
        genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT: genai.types.HarmBlockThreshold.BLOCK_NONE,
        genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH: genai.types.HarmBlockThreshold.BLOCK_NONE,
        genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: genai.types.HarmBlockThreshold.BLOCK_NONE,
        genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: genai.types.HarmBlockThreshold.BLOCK_NONE,
    }
)

# --- Step 4: Configure the SQLite Database (Our "Memory Box") ---
DATABASE = 'workflows.db' 

def init_db():
    """
    This function sets up our database. It creates the 'workflows' table if it doesn't already exist.
    """
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            natural_language_command TEXT NOT NULL,
            parsed_workflow_json TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def save_workflow_to_db(command, parsed_workflow):
    """
    This function saves a new workflow entry into our database.
    """
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO workflows (natural_language_command, parsed_workflow_json) VALUES (?, ?)",
            (command, json.dumps(parsed_workflow))
        )
        conn.commit()
        print("--- Workflow saved to database successfully! ---")
    except Exception as e:
        print(f"--- Error saving workflow to database: {e} ---")
    finally:
        conn.close()

# --- Email Configuration ---
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_EMAIL_PASSWORD = os.getenv("SENDER_EMAIL_PASSWORD")

# Slack Configuration
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
if not SLACK_WEBHOOK_URL:
    print("WARNING: SLACK_WEBHOOK_URL not found in environment variables. Slack actions will not work.")

# --- Workflow Execution Functions ---
def execute_send_email_action(action_details):
    """
    Executes the 'send_email' action.
    action_details expects a dictionary like:
    {
        "to": "recipient@example.com",
        "subject": "Email Subject",
        "body": "Email Body Content"
    }
    """
    to_email = action_details.get("recipient")
    subject = action_details.get("subject", "No Subject")
    body = action_details.get("body", "")

    if not all([SENDER_EMAIL, SENDER_EMAIL_PASSWORD, to_email]):
        return False, "Email sender credentials or recipient missing/invalid."

    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Connect to Gmail's SMTP server securely
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(SENDER_EMAIL, SENDER_EMAIL_PASSWORD)
            smtp.send_message(msg)
        return True, f"Email sent successfully to {to_email}."
    except Exception as e:
       
        return False, f"Failed to send email: {str(e)}"

def execute_send_slack_notification_action(action_details):
    """
    Executes the 'send_slack_notification' action using an Incoming Webhook.
    action_details expects a dictionary like:
    {
        "channel": "#general", # Optional, if not specified, uses default webhook channel
        "message": "Your Slack message content"
    }
    """
    message = action_details.get("message")
    channel = action_details.get("channel") # Can be used to override default channel

    if not SLACK_WEBHOOK_URL:
        return False, "Slack Webhook URL is not configured in .env."
    if not message:
        return False, "No message provided for Slack notification."

    payload = {
        "text": message
    }
    if channel:
        payload["channel"] = channel # Override the default channel if specified

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload)
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
        return True, f"Slack notification sent successfully."
    except requests.exceptions.RequestException as e:
        return False, f"Failed to send Slack notification: {str(e)}"


def execute_actions(actions):
    """
    Iterates through a list of actions and executes them.
    Returns a list of results for each action.
    """
    results = []
    if not isinstance(actions, list):
        # If Gemini sometimes returns a single action dict instead of list
        actions = [actions]

    for action in actions:
        action_type = action.get("type")
        action_details = action.get("details", {})

        if action_type == "send_email":
            success, message = execute_send_email_action(action_details)
            results.append({"action_type": action_type, "success": success, "message": message})
        elif action_type == "send_slack_notification":
            success, message = execute_send_slack_notification_action(action_details)
            results.append({"action_type": action_type, "success": success, "message": message})
        else:
            results.append({"action_type": action_type, "success": False, "message": f"Unknown or unimplemented action type: {action_type}"})
    return results


# --- Step 5: Run database initialization when the app starts ---
with app.app_context():
    print("Attempting to initialize database...")
    init_db()
    print("Database initialization attempt finished.")

# --- Step 6: Define Flask Routes ---

@app.route('/')
def home():
    return "<h1>Flask App is Running!</h1><p>Navigate to /automate (POST only) or use the Streamlit app to interact.</p>"

# Firebase Authentication Endpoints
@app.route('/register', methods=['POST'])
def register_user():
    email = request.json.get('email')
    password = request.json.get('password')

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    try:
        user = auth.create_user(email=email, password=password)
        return jsonify({
            "message": "User created successfully!",
            "uid": user.uid,
            "email": user.email
        }), 201
    except Exception as e:
        error_message = str(e)
        if "email-already-exists" in error_message:
            return jsonify({"error": "Email already registered."}), 409
        return jsonify({"error": f"Failed to register user: {error_message}"}), 500

@app.route('/verify_token', methods=['POST'])
def verify_firebase_token():
    id_token = request.json.get('idToken')

    if not id_token:
        return jsonify({"error": "ID token is missing."}), 400

    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        email = decoded_token.get('email')

        return jsonify({
            "message": "Token verified successfully!",
            "uid": uid,
            "email": email
        }), 200
    except Exception as e:
        error_message = str(e)
        if "auth/invalid-id-token" in error_message:
            return jsonify({"error": "Invalid or malformed ID token."}), 401
        elif "auth/id-token-expired" in error_message:
            return jsonify({"error": "ID token has expired."}), 401
        elif "auth/user-disabled" in error_message:
            return jsonify({"error": "User account is disabled."}), 403
        return jsonify({"error": f"Failed to verify token: {error_message}"}), 401

@app.route('/automate', methods=['POST'])
def automate_workflow():
    user_command = request.json.get('command')

    if not user_command:
        return jsonify({"error": "No 'command' provided in the request body. Please send your automation request here."}), 400

    print(f"\n--- Received user command: {user_command} ---")

    prompt = f"""
    The user wants to automate a workflow. Analyze the following natural language command and extract **only a single, complete JSON object** containing the 'trigger' and 'action(s)' in a structured format.
    Do NOT include any surrounding text, markdown code blocks (```json), or other characters outside of the JSON object itself.
    If a trigger or action is unclear, mark it as "unclear".

    **For sending an email, the action should be formatted as:**
    {{
        "type": "send_email",
        "details": {{
            "subject": "Your Email Subject",
            "body": "Your Email Body Content",
            "recipient": "recipient@example.com"
        }}
    }}
    Ensure the 'recipient' is a valid email address.

    **For sending a Slack notification, the action should be formatted as:**
    {{
        "type": "send_slack_notification",
        "details": {{
            "channel": "#your-channel-name", // Optional: if omitted, uses default webhook channel
            "message": "Your message content"
        }}
    }}


    Example 1: "When a new user signs up, send them a welcome email."
    {{
        "trigger": {{
            "type": "new_user_signup",
            "details": {{}}
        }},
        "actions": [
            {{
                "type": "send_email",
                "details": {{
                    "subject": "Welcome!",
                    "body": "Thank you for signing up.",
                    "recipient": "new_user_email_placeholder"
                }}
            }}
        ]
    }}

    Example 2: "Send an email to support@example.com about a critical issue with subject 'Critical Bug Report' and body 'The payment gateway is down.'"
    {{
        "trigger": {{
            "type": "manual_trigger",
            "details": {{}}
        }},
        "actions": [
            {{
                "type": "send_email",
                "details": {{
                    "subject": "Critical Bug Report",
                    "body": "The payment gateway is down.",
                    "recipient": "support@example.com"
                }}
            }}
        ]
    }}

    Example 3: "Notify on Slack in the #general channel when a payment is received."
    {{
        "trigger": {{
            "type": "payment_received",
            "details": {{}}
        }},
        "actions": [
            {{
                "type": "send_slack_notification",
                "details": {{
                    "channel": "general",
                    "message": "A new payment has been received!"
                }}
            }}
        ]
    }}

    Example 4: "Just send a quick message to #random channel on Slack saying 'Daily report is ready!'"
    {{
        "trigger": {{
            "type": "manual_trigger",
            "details": {{}}
        }},
        "actions": [
            {{
                "type": "send_slack_notification",
                "details": {{
                    "channel": "random",
                    "message": "Daily report is ready!"
                }}
            }}
        ]
    }}

    User command: "{user_command}"
    """

    parsed_workflow = None
    gemini_raw_response = ""
    try:
        print("--- Sending command to Gemini for processing... ---")
        response = model.generate_content(prompt)
        gemini_raw_response = response.text.strip()

        # Robustly extract JSON: try to remove markdown blocks if present
        json_string = gemini_raw_response
        if json_string.startswith("```json"):
            json_string = json_string[len("```json"):-len("```")].strip()

        parsed_workflow = json.loads(json_string)

        # Ensure the parsed_workflow is a dictionary. If Gemini returns a list, take the first.
        if isinstance(parsed_workflow, list) and len(parsed_workflow) > 0:
            print("--- Warning: Gemini returned a list of workflows. Taking the first one. ---")
            parsed_workflow = parsed_workflow[0]
        elif not isinstance(parsed_workflow, dict):
            raise ValueError(f"Gemini response was not a dictionary or list of dictionaries. "
                             f"Received type: {type(parsed_workflow)}, Raw content: '{gemini_raw_response}'")

        print("--- Gemini response parsed successfully. ---")
        print("--- Parsed Workflow (from Gemini): ---")
        print(json.dumps(parsed_workflow, indent=2))
        print("---------------------------------------")

    except genai.types.BlockedPromptException as e:
        error_message = f"Prompt blocked by safety settings. Details: {e.response.prompt_feedback}"
        print(f"--- ERROR: {error_message} ---")
        return jsonify({
            "error": error_message,
            "details": str(e),
            "gemini_raw_response": gemini_raw_response
        }), 400
    except google.api_core.exceptions.GoogleAPICallError as e: # Catch the base API error
        # This will catch ResourceExhausted (429), PermissionDenied (403), etc.
        status_code = e.code if hasattr(e, 'code') else 500 # Get status code from exception
        error_detail = e.message if hasattr(e, 'message') else str(e) # Get message from exception

        if status_code == 429:
            error_message = "Gemini API rate limit exceeded. Please try again later."
        elif status_code == 404: 
            error_message = "Gemini API Error: Model not found or unavailable. This often indicates a quota limit on the free tier or an incorrect model name."
        else:
            error_message = f"Gemini API Error: {error_detail}"

        print(f"--- ERROR: {error_message} (Status: {status_code}) ---")
        return jsonify({
            "error": error_message,
            "details": error_detail,
            "status_code": status_code,
            "gemini_raw_response": gemini_raw_response
        }), status_code
    except json.JSONDecodeError as e:
        print(f"--- ERROR: Gemini's response was not valid JSON. Raw response: '{gemini_raw_response}' ---")
        return jsonify({
            "error": "AI could not generate a valid workflow plan (JSON parse error). "
                     "Please refine your command.",
            "details": str(e),
            "gemini_raw_response": gemini_raw_response
        }), 500
    except ValueError as e: # Catch custom ValueErrors like "Gemini response was not a dictionary..."
        print(f"--- ERROR: {e} ---")
        return jsonify({
            "error": str(e),
            "gemini_raw_response": gemini_raw_response
        }), 400
    except Exception as e:
        print(f"--- UNEXPECTED ERROR during Gemini API call or response processing: {e} ---")
        return jsonify({
            "error": "An unexpected error occurred during AI processing.",
            "details": str(e),
            "gemini_raw_response": gemini_raw_response
        }), 500

    # --- Part 2: Save the original command and the parsed workflow to the database ---
    if parsed_workflow and isinstance(parsed_workflow, dict):
        try:
            save_workflow_to_db(user_command, parsed_workflow)

            # --- NEW: Part 3: Execute the parsed workflow actions ---
            execution_results = []
            if "actions" in parsed_workflow and isinstance(parsed_workflow["actions"], list):
                print("--- Executing parsed workflow actions... ---")
                execution_results = execute_actions(parsed_workflow["actions"])
                print("--- Workflow actions execution finished. ---")
            else:
                print("--- No executable actions found in parsed workflow. ---")
                execution_results.append({"action_type": "None", "success": True, "message": "No specific actions to execute."})

            return jsonify({
                "message": "Workflow parsed, saved, and executed!",
                "original_command": user_command,
                "parsed_workflow": parsed_workflow,
                "execution_results": execution_results # Send execution results to frontend
            })
        except Exception as e:
            print(f"--- ERROR saving or executing workflow: {e} ---")
            return jsonify({"error": "Failed to save or execute workflow.", "details": str(e)}), 500
    else:
        return jsonify({"error": "Gemini could not generate a valid structured workflow."}), 500

# --- Step 7: Run the Flask development server ---
if __name__ == '__main__':
    app.run(debug=True, port=5000) # Ensure port is 5000 as used by Streamlit
