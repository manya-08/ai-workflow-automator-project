import streamlit as st # Our Streamlit control panel tool
import requests # Our tool to send messages (requests) to the Flask backend
import json # For handling JSON data
import os # For accessing environment variables
# from dotenv import load_dotenv # REMOVED: Replit Secrets handle env vars automatically
import pyrebase # For client-side Firebase authentication

# --- Load Environment Variables (from Render) ---
# load_dotenv() # REMOVED: Render handles env vars automatically

# --- Firebase Client Configuration (from Render Environment Variables) ---
# Ensure these are present in your Render environment variables and match your Firebase project's Web App config.
firebaseConfig = {
    "apiKey": os.getenv("FIREBASE_API_KEY"),
    "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
    "projectId": os.getenv("FIREBASE_PROJECT_ID"),
    "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
    "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID"),
    "appId": os.getenv("FIREBASE_APP_ID"),
    "measurementId": os.getenv("FIREBASE_MEASUREMENT_ID"),
    "databaseURL": "" # Not strictly needed for auth, but part of standard config
}

# --- Initialize Firebase ---
try:
    # Check if essential Firebase client config values are present
    if not all([firebaseConfig.get("apiKey"), firebaseConfig.get("authDomain"), firebaseConfig.get("projectId"), firebaseConfig.get("appId")]):
        st.error("Firebase client configuration is incomplete. Please add all FIREBASE_API_KEY, FIREBASE_AUTH_DOMAIN, FIREBASE_PROJECT_ID, FIREBASE_APP_ID to Render Environment Variables.")
        st.stop() # Stop the app if Firebase isn't configured correctly

    firebase = pyrebase.initialize_app(firebaseConfig)
    auth = firebase.auth()
    # db = firebase.database() # We're not using this for now, but often initialized
    print("--- Firebase client initialized successfully! ---")
except Exception as e:
    st.error(f"Failed to initialize Firebase client: {e}. Please check your Render Environment Variables for Firebase configuration.")
    st.stop() # Stop the app if Firebase isn't configured correctly

# --- Streamlit Page Configuration ---
st.set_page_config(layout="wide")
st.title("🧙‍♂️ AI-Powered No-Code Workflow Automator")

# --- Session State Management for Authentication ---
# These variables will persist across reruns of the Streamlit app
if "user_token" not in st.session_state:
    st.session_state.user_token = None
if "user_email" not in st.session_state:
    st.session_state.user_email = None

# --- Authentication Functions ---
def login_user(email, password):
    try:
        user = auth.sign_in_with_email_and_password(email, password)
        # Store the ID token and email in session state
        st.session_state.user_token = user['idToken']
        st.session_state.user_email = user['email']
        st.success("Logged in successfully!")
        st.rerun() # Rerun the app to update the UI based on login status
    except Exception as e:
        error_message = str(e)
        if "EMAIL_NOT_FOUND" in error_message or "INVALID_LOGIN_CREDENTIALS" in error_message:
            st.error("Invalid email or password.")
        elif "TOO_MANY_ATTEMPTS_TRY_LATER" in error_message:
            st.error("Too many failed login attempts. Please try again later.")
        else:
            st.error(f"Login failed: {error_message}")

def register_user(email, password):
    # Try to register the user via Flask backend (Admin SDK)
    # This ensures the user is created in Firebase Auth directly by the backend,
    # and the backend can apply any server-side validation/logic.
    # CORRECTED: Use localhost for internal communication
    flask_register_url = "http://localhost:5000/register"
    try:
        response = requests.post(flask_register_url, json={"email": email, "password": password})
        if response.status_code == 201:
            st.success("User registered successfully! You can now log in.")
            return True
        else:
            error_details = response.json()
            st.error(f"Backend registration failed: {error_details.get('error', 'Unknown error')}")
            return False
    except requests.exceptions.ConnectionError:
        st.error("Could not connect to Flask backend for registration. Is `app.py` running on port 5000?")
        return False
    except Exception as e:
        st.error(f"An unexpected error occurred during backend registration: {e}")
        return False

def logout_user():
    st.session_state.user_token = None
    st.session_state.user_email = None
    try:
        auth.sign_out() # Optional, but good practice for client-side logout
    except Exception as e:
        st.warning(f"Error during client-side logout (might be already logged out): {e}")
    st.success("Logged out successfully.")
    st.rerun() # Rerun the app to update the UI

# --- UI for Authentication (in sidebar) ---
st.sidebar.title("Authentication")

if st.session_state.user_token:
    # If user is logged in
    st.sidebar.success(f"Logged in as: {st.session_state.user_email}")
    st.sidebar.button("Logout", on_click=logout_user)
else:
    # If user is not logged in, show login/register options
    auth_choice = st.sidebar.radio("Choose action", ["Login", "Register"])

    with st.sidebar.form("auth_form"):
        email = st.text_input("Email", key="auth_email")
        password = st.text_input("Password", type="password", key="auth_password")
        submitted = st.form_submit_button(auth_choice)

        if submitted:
            if auth_choice == "Login":
                login_user(email, password)
            elif auth_choice == "Register":
                if register_user(email, password):
                    # After successful backend registration, prompt user to log in
                    st.info("Registration successful. Please use the 'Login' tab to sign in.")

# --- Main Content: Workflow Automation (only visible if logged in) ---
if st.session_state.user_token:
    st.markdown("### Tell me what you want to automate in plain English!")

    user_input = st.text_area(
        "Enter your automation command here (e.g., 'Send an email to john.doe@example.com with subject 'Meeting Reminder' and body 'Don't forget the team meeting at 3 PM.''):",
        height=150,
        key="automation_command" # Added a unique key for the text area
    )

    # The "Automate!" Button
    if st.button("Automate Workflow!"):
        if user_input: # Check if the user actually typed something
            # Show a loading spinner while processing
            with st.spinner('Thinking... Sending command to AI backend...'):
                try:
                    # CORRECTED: Use localhost for internal communication
                    flask_backend_url = "http://localhost:5000/automate"

                    # If you later decide to require authentication for the /automate endpoint
                    # (which is a good idea for a real application), you would pass the token like this:
                    # headers = {"Authorization": f"Bearer {st.session_state.user_token}"}
                    # response = requests.post(flask_backend_url, json={"command": user_input}, headers=headers)

                    # For now, it's open as per our previous iteration, but we have the token ready
                    response = requests.post(flask_backend_url, json={"command": user_input})

                    # Check if the request was successful (status code 200)
                    if response.status_code == 200:
                        result = response.json()
                        st.success(result.get("message", "Workflow processed successfully!"))

                        # Display the original command
                        st.subheader("Original Command:")
                        st.write(result.get("original_command", "N/A (Command not returned by backend)"))

                        # Access and display the 'parsed_workflow' part of the response
                        st.subheader("AI-Parsed Workflow (from Gemini):")
                        if 'parsed_workflow' in result:
                            parsed_workflow = result['parsed_workflow']
                            # Parsed workflow is expected to be a dict as per app.py's new handling
                            if isinstance(parsed_workflow, dict):
                                st.json(parsed_workflow)
                            else:
                                st.error(f"Unexpected format for parsed workflow: {type(parsed_workflow)}. Raw content: {parsed_workflow}")
                        else:
                            st.error("No 'parsed_workflow' found in the response from the backend. Raw response:")
                            st.json(result) # Show the full response if parsed_workflow is missing

                        # NEW: Display Execution Results
                        st.subheader("Action Execution Results:")
                        execution_results = result.get("execution_results")
                        if execution_results:
                            for exec_res in execution_results:
                                action_type = exec_res.get("action_type", "Unknown Action")
                                success = exec_res.get("success")
                                message = exec_res.get("message", "No message provided.")

                                if success:
                                    st.success(f"✅ **{action_type}**: {message}")
                                else:
                                    st.error(f"❌ **{action_type}**: {message}")
                        else:
                            st.info("No specific actions were executed or no execution results returned.")

                        st.info("This workflow has been saved to your database!")
                    else:
                        # If the backend sent an error (e.g., Status: 500, 400, 429)
                        st.error(f"🚫 Error from backend (Status: {response.status_code}):")
                        try:
                            # Attempt to parse the error details if they are in JSON format
                            error_details = response.json()
                            st.json(error_details) # Display full error JSON from backend
                            if "error" in error_details and "rate limit" in error_details["error"].lower():
                                st.warning("Google Gemini API quota limit hit. Please wait a while before trying again.")
                        except json.JSONDecodeError:
                            # If the response is not valid JSON, display a generic error
                            st.error("❌ Received an unreadable error response from the backend.")

                        st.subheader("The command that caused the error was:")
                        st.code(user_input) # Show the original command that failed

                except requests.exceptions.ConnectionError:
                    st.error("❌ Could not connect to the Flask backend! Make sure your `app.py` is running in another terminal at `http://localhost:5000`.")
                except Exception as e:
                    st.error(f"An unexpected error occurred in Streamlit: {e}")
        else:
            st.warning("Please enter a command to automate!")
else:
    # Message displayed when not logged in
    st.info("Please log in or register to use the workflow automator.")
