# This is copy of bot3.py with added credit system

import os
import logging
import time
from datetime import time as dt_time
import shutil
import asyncio
import re
import json
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from datetime import date
from dotenv import load_dotenv

from telegram import Update, MessageEntity
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium_stealth import stealth

# --- Configuration ---
load_dotenv()
selenium_lock = asyncio.Lock()

# --- Paths ---
LOCAL_PROFILE_PATH = os.path.join(os.getcwd(), "chrome_profile")
CONFIG_FILE = os.path.join(os.getcwd(), "config.json") # <-- ADDED: Path for persistent config
USER_DATA_FILE = os.path.join(os.getcwd(), "user_data.json")

# --- Credentials (will be loaded dynamically) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))
CHESS_USERNAME = None
CHESS_PASSWORD = None

# --- Script Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- NEW: Configuration Management Functions ---

def save_credentials(username, password):
    """Saves credentials to the config.json file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump({'CHESS_USERNAME': username, 'CHESS_PASSWORD': password}, f, indent=4)
        logger.info(f"Credentials saved to {CONFIG_FILE}")
        return True
    except Exception as e:
        logger.error(f"Failed to save credentials: {e}")
        return False

def load_credentials():
    """Loads credentials, prioritizing config.json over .env file."""
    global CHESS_USERNAME, CHESS_PASSWORD
    try:
        # Prioritize loading from config.json
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            CHESS_USERNAME = config.get('CHESS_USERNAME')
            CHESS_PASSWORD = config.get('CHESS_PASSWORD')
            if CHESS_USERNAME and CHESS_PASSWORD:
                logger.info("Loaded credentials from config.json")
                return
    except (FileNotFoundError, json.JSONDecodeError):
        # Fallback to .env file if config.json is missing or invalid
        logger.info("config.json not found or invalid, falling back to .env file.")
    
    CHESS_USERNAME = os.getenv("CHESS_USERNAME")
    CHESS_PASSWORD = os.getenv("CHESS_PASSWORD")
    logger.info("Loaded credentials from .env file.")



async def set_credits_command(update: Update, context: CallbackContext) -> None:
    """
    Admin-only command to set a user's credits.
    Usage: /setcredits <user_id> <amount>
    """
    requesting_user_id = update.message.from_user.id

    # --- Admin Check ---
    if requesting_user_id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return

    try:
        target_user_id_str = context.args[0]
        new_credit_amount = int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /setcredits <user_id> <amount>")
        return

    user_data = load_user_data()

    if target_user_id_str not in user_data:
        await update.message.reply_text(f"Error: User with ID {target_user_id_str} not found in database.")
        return

    # Update the credits and save
    user_data[target_user_id_str]['credits'] = new_credit_amount
    save_user_data(user_data)

    logger.info(f"Admin {requesting_user_id} set credits for user {target_user_id_str} to {new_credit_amount}.")
    await update.message.reply_text(f"Success! User {target_user_id_str}'s credits have been set to {new_credit_amount}.")


# --- NEW: User Data Management Functions ---

def load_user_data():
    """Loads user data from the JSON file."""
    try:
        with open(USER_DATA_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # If the file doesn't exist or is empty, return an empty dictionary
        return {}

def save_user_data(data):
    """Saves user data to the JSON file."""
    with open(USER_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)


def clean_chrome_profile():
    """
    Cleans the Chrome profile by keeping only essential files for session persistence,
    preventing bloat and performance degradation over time.
    """
    logger.info("--- Starting Chrome Profile Cleanup ---")
    temp_profile_path = LOCAL_PROFILE_PATH + "_temp"
    
    # The essential files and directories to keep the session alive
    whitelist_items = {
        "files": [
            "Local State",
            os.path.join("Default", "Cookies"),
            os.path.join("Default", "Preferences"),
            os.path.join("Default", "Visited Links")
        ],
        "dirs": [
            os.path.join("Default", "Local Storage"),
            os.path.join("Default", "Session Storage"),
            os.path.join("Default", "databases"),
            os.path.join("Default", "Network")
        ]
    }

    try:
        # Ensure we start with a clean slate for the temp directory
        if os.path.exists(temp_profile_path):
            shutil.rmtree(temp_profile_path)
            
        # Create the temporary profile directory structure
        os.makedirs(temp_profile_path)
        os.makedirs(os.path.join(temp_profile_path, "Default"))

        logger.info(f"Copying whitelisted items to {temp_profile_path}")

        # Copy whitelisted directories
        for dir_path in whitelist_items["dirs"]:
            source_dir = os.path.join(LOCAL_PROFILE_PATH, dir_path)
            dest_dir = os.path.join(temp_profile_path, dir_path)
            if os.path.exists(source_dir):
                shutil.copytree(source_dir, dest_dir)

        # Copy whitelisted files
        for file_path in whitelist_items["files"]:
            source_file = os.path.join(LOCAL_PROFILE_PATH, file_path)
            dest_file = os.path.join(temp_profile_path, file_path)
            if os.path.exists(source_file):
                # Ensure destination directory exists before copying file
                os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                shutil.copy2(source_file, dest_file) # copy2 preserves metadata

        # Atomically replace the old profile with the cleaned one
        logger.info("Replacing old profile with the cleaned version...")
        shutil.rmtree(LOCAL_PROFILE_PATH)
        os.rename(temp_profile_path, LOCAL_PROFILE_PATH)
        
        logger.info("✅ Chrome profile cleanup successful.")

    except Exception as e:
        logger.error(f"❌ An error occurred during profile cleanup: {e}")
        # If cleanup fails, try to remove the temp dir to avoid issues on next run
        if os.path.exists(temp_profile_path):
            shutil.rmtree(temp_profile_path)



# --- NEW: Scheduled Job Function ---

async def reset_all_credits_daily():
    """
    This job runs every day at midnight to reset credits for all users.
    """
    logger.info("--- Running Daily Credit Reset Job ---")
    user_data = load_user_data()
    if not user_data:
        logger.info("No user data to reset. Exiting job.")
        return

    users_reset_count = 0
    for user_id in user_data:
        # We simply reset the credits, the 'last_seen' date will be updated
        # the next time they use the bot.
        user_data[user_id]['credits'] = 3
        users_reset_count += 1
    
    save_user_data(user_data)
    logger.info(f"✅ Daily reset complete. Credits reset for {users_reset_count} user(s).")


# --- Main Chess Logic (Unchanged) ---
def run_chess_login_flow(game_url: str):
    """
    Final version combining a file-based check with a self-healing login.
    - If no profile exists, it does a clean initial login.
    - If a profile exists, it navigates directly and re-logs in only if the session has expired.
    """
    logger.info("--- Starting Final Combined Chess.com Flow ---")

    if not CHESS_USERNAME or not CHESS_PASSWORD:
        logger.error("Chess.com username or password is not set. Please use /setconfig.")
        return

    profile_exists = os.path.exists(LOCAL_PROFILE_PATH) and os.listdir(LOCAL_PROFILE_PATH)

    options = webdriver.ChromeOptions()
    #options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")
    options.add_argument(f"--user-data-dir={LOCAL_PROFILE_PATH}")

    driver = None
    try:
        logger.info("Initializing WebDriver with Stealth...")
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", fix_hairline=True)
        
        if profile_exists:
            # --- PATH 1: PROFILE EXISTS (SELF-HEALING LOGIC) ---
            logger.info("Profile found. Using direct navigation with self-healing check.")
            driver.get(game_url)
            try:
                # Wait up to 15 seconds to see if the session is still active.
                wait = WebDriverWait(driver, 15)
                wait.until(EC.element_to_be_clickable((By.XPATH, "//span[text()='Start Review']")))
                logger.info("✅ Session is active. Analysis page loaded directly.")
            except TimeoutException:
                # Session expired, so we re-authenticate.
                logger.warning("Session expired despite profile existing. Re-authenticating...")
                
                # Standard Login Flow
                login_wait = WebDriverWait(driver, 10)
                username_field = login_wait.until(EC.element_to_be_clickable((By.ID, "login-username")))
                username_field.clear()
                username_field.send_keys(CHESS_USERNAME)
                password_field = driver.find_element(By.ID, "login-password")
                password_field.clear()
                password_field.send_keys(CHESS_PASSWORD)
                driver.find_element(By.ID, "login").click()

                login_wait.until(EC.url_contains("/home"))
                logger.info("Re-authentication successful. Navigating back to game URL...")
                driver.get(game_url)
        else:
            # --- PATH 2: NO PROFILE (CLEAN INITIAL LOGIN) ---
            logger.info("No profile found. Performing clean initial login.")
            driver.get("https://www.chess.com/login")
            
            wait = WebDriverWait(driver, 20)
            try: # Handle cookie banner
                cookie_button = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Accept')] | //button[contains(., 'Allow all')]")))
                cookie_button.click()
                time.sleep(1)
            except TimeoutException:
                logger.info("Cookie banner not found, continuing.")
            
            username_field = wait.until(EC.element_to_be_clickable((By.ID, "login-username")))
            username_field.clear()
            username_field.send_keys(CHESS_USERNAME)
            password_field = driver.find_element(By.ID, "login-password")
            password_field.clear()
            password_field.send_keys(CHESS_PASSWORD)
            driver.find_element(By.ID, "login").click()

            wait.until(EC.url_contains("/home"))
            logger.info("Initial login successful. Now navigating to game URL.")
            driver.get(game_url)

        # --- COMMON FINALIZATION LOGIC ---
        logger.info("Waiting for final confirmation of analysis page...")
        final_wait = WebDriverWait(driver, 20)
        wait.until(EC.element_to_be_clickable((By.XPATH, "//span[text()='Start Review']")))
        logger.info("Page confirmed. Taking screenshot.")
        
        driver.save_screenshot("new_tab_screenshot.png")
        logger.info("Screenshot of analysis page saved as 'new_tab_screenshot.png'.")

    except Exception as e:
        logger.error(f"An error occurred in the main process: {e}")
        if driver:
            driver.save_screenshot("error.png")
            logger.info("Error screenshot saved as 'error.png'.")
    
    finally:
        if driver:
            logger.info("Closing WebDriver...")
            driver.quit()
        logger.info("--- Chess.com Flow Finished ---")
# --- Telegram Handler Functions ---

async def set_config_command(update: Update, context: CallbackContext) -> None:
    """
    Handles the /setconfig command to update Chess.com credentials.
    This is an admin-only command and is protected by the selenium_lock.
    """
    requesting_user_id = update.message.from_user.id
    if requesting_user_id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return

    # --- NEW: Acquire the lock before modifying the profile ---
    logger.info("Admin command /setconfig waiting for selenium lock...")
    async with selenium_lock:
        logger.info("Lock acquired for /setconfig.")
        global CHESS_USERNAME, CHESS_PASSWORD
        
        args = context.args
        if len(args) != 2:
            await update.message.reply_text("Usage: /setconfig <username> <password>")
            return # The 'with' block will automatically release the lock
            
        new_username, new_password = args[0], args[1]
        
        if save_credentials(new_username, new_password):
            CHESS_USERNAME = new_username
            CHESS_PASSWORD = new_password
            
            reply_message = "✅ Configuration updated successfully!"
            
            try:
                if os.path.exists(LOCAL_PROFILE_PATH):
                    shutil.rmtree(LOCAL_PROFILE_PATH)
                    logger.info(f"Removed old chrome profile at: {LOCAL_PROFILE_PATH}")
                    reply_message += "\n🧹 The old browser session has been cleared."
                else:
                    logger.info("No existing chrome profile to remove.")
                    
            except Exception as e:
                logger.error(f"Failed to remove chrome profile: {e}")
                reply_message += "\n⚠️ Could not remove the old browser session."

            await update.message.reply_text(reply_message)
                
        else:
            await update.message.reply_text("❌ Failed to save new configuration. Please check the logs.")
    # The lock is released here when the 'with' block finishes.
    logger.info("Lock for /setconfig released.")

async def my_id_command(update: Update, context: CallbackContext) -> None:
    """Replies with the user's Telegram ID."""
    user_id = update.message.from_user.id
    await update.message.reply_text(f"Your Telegram User ID is: `{user_id}`", parse_mode='MarkdownV2')

async def handle_game_link(update: Update, context: CallbackContext) -> None:
    """
    Parses multiple chess.com URL formats to extract a game ID,
    checks user credits, and triggers the login flow if credits are available.
    """
    message = update.message
    if not message or not message.text:
        return

    urls = message.parse_entities(types=[MessageEntity.URL])
    if not urls:
        return

    first_url = list(urls.values())[0]

    # --- NEW: VERSATILE URL PARSING ---
    # This regex pattern finds the numerical game ID in various chess.com links.
    # It looks for patterns like /live/game/ID, /game/ID, or /analysis/game/live/ID.
    pattern = r"chess\.com\/(?:live\/game|game|analysis\/game\/live)\/(\d+)"
    match = re.search(pattern, first_url)

    if not match:
        # If the URL doesn't match a known game link format, inform the user.
        await message.reply_text("Please send a valid chess.com game link.")
        return

    # The game ID is the first captured group from our regex.
    game_id = match.group(1)
    logger.info(f"Extracted game ID: {game_id} from URL: {first_url}")

    # --- CREDIT SYSTEM LOGIC (No changes here) ---
    user_id = message.from_user.id
    user_id_str = str(user_id) # JSON keys must be strings
    today = date.today().isoformat()

    user_data = load_user_data()

    if user_id_str not in user_data or user_data[user_id_str].get('last_seen') != today:
        user_data[user_id_str] = {'credits': 3, 'last_seen': today}
        save_user_data(user_data)
        logger.info(f"Initialized/Refreshed credits for user {user_id}")

    if user_data[user_id_str]['credits'] <= 0:
        logger.info(f"User {user_id} has no credits left.")
        
        # This is the new, formatted message string
        premium_message = (
            "⚠️ *Daily Limit Reached* ⚠️\n\n"
            "You've used all your free analyses for today\\.\n"
            "_Your credits will reset at midnight\\._\n\n"
            "\\-\\-\\-\n\n"
            "🚀 **Want More\\? Go Premium\\!**\n"
            "Enjoy unlimited analyses and faster, priority support\\.\n"
            "`[Link to Your Premium Offer]`"
        )
        
        # Make sure to send it with the correct parse mode
        await message.reply_text(premium_message, parse_mode='MarkdownV2')
        return # Stop processing the request

    # --- MAIN PROCESSING LOGIC ---
    if not CHESS_USERNAME or not CHESS_PASSWORD:
        await message.reply_text("Chess.com credentials are not set by the admin.")
        return

    user_data[user_id_str]['credits'] -= 1
    save_user_data(user_data)
    logger.info(f"User {user_id} used a credit. {user_data[user_id_str]['credits']} remaining.")

# Build the final, standardized analysis URL
    analysis_url = f"https://www.chess.com/analysis/game/live/{game_id}/review"
    
    async with selenium_lock:
        # Send the initial status message
        status_message = await message.reply_text("*Preparing analysis...*", parse_mode='Markdown')

        # 1. Start the actual analysis in a background task
        logger.info("Starting Selenium task in the background.")
        analysis_task = asyncio.create_task(
            asyncio.to_thread(run_chess_login_flow, game_url=analysis_url)
        )

        # 2. While the task runs, simulate a progress bar for the user
        total_duration = 15  # Total estimated time for the analysis in seconds
        steps = 10          # We will update the bar 100 times
        for i in range(steps + 1):
            percentage = i * 10
            progress_bar = "█" * i + "░" * (steps - i) # Creates a visual bar

            # We use MarkdownV2 for the code block `` which makes the bar look clean
            text = f"*Analyzing your game\\.\\.\\.*\n\n`{progress_bar} {percentage}%`"
            
            try:
                await status_message.edit_text(text, parse_mode='MarkdownV2')
            except Exception: # Ignore potential "message is not modified" error
                pass
            
            # Don't sleep on the final 100% step
            if i < steps:
                await asyncio.sleep(total_duration / steps)

        # 3. Wait for the background task to actually finish
        logger.info("Progress simulation finished. Awaiting Selenium task completion...")
        try:
            await analysis_task
            logger.info("Selenium task completed successfully.")

            # Delete the status message before sending the final result
            await status_message.delete()

            # --- MESSAGE 1: The Game Link ---
            link_message = f"Here is your Game review:\n{analysis_url}"
            await message.reply_text(link_message, disable_web_page_preview=False)

            await asyncio.sleep(1)

            # --- MESSAGE 2: Credits and Info ---
            credits_left = user_data[user_id_str]['credits']
            info_message = (
                f"📊 *Credits Remaining:* **{credits_left}**\n"
                f"_Credits reset daily at midnight\\._\n\n"
                f"🚀 **Go Premium\\!**\n"
                f"Get unlimited reviews & priority support\\.\n"
                f"Msg @HeyDmc for Premium\n\n"
            )
            await message.reply_text(info_message, parse_mode='MarkdownV2', disable_web_page_preview=True)

        except Exception as e:
            logger.error(f"The chess flow failed: {e}")
            await status_message.edit_text("Sorry, something went wrong while analyzing the game. Please try again later.")

        finally:
             # This block runs after the 'try' or 'except'
             logger.info("Replying to user complete. Starting background profile cleanup.")
             await asyncio.to_thread(clean_chrome_profile)

# REPLACE this entire function

async def start_command(update: Update, context: CallbackContext) -> None:
    """Sends a welcome message and instructions when the /start command is issued."""
    # We've added '\' before each special character '!' and '.'
    welcome_message = (
        "👋 **Welcome to the Chess Game Review Bot\\!**\n\n"
        "I can provide a free analysis of your games from Chess\\.com\\.\n\n"
        "\n"
        "**How to use me:**\n"
        "\n"
        "Simply  Share me the game, and I'll get to work\\.\n\n"
        " It only work for Mobile Users 📲\\.\n\n"
        " For PC Users 💻 please msg @HeyDmc\\.\n\n"
        "You get **3 free reviews** every day\\. Enjoy\\!"
    )
    await update.message.reply_text(welcome_message, parse_mode='MarkdownV2')

# --- Main Bot Execution ---
async def main() -> None:
    """Initializes and runs the bot."""
    # Load credentials on startup
    load_credentials()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing. Please check your .env file.")
        return

    # Use the 'async with' block for robust startup and shutdown
    async with Application.builder().token(TELEGRAM_BOT_TOKEN).build() as application:
        # --- Add Scheduler and Handlers ---
        application.job_queue.run_daily(
            reset_all_credits_daily,
            time=dt_time(hour=0, minute=0, second=0),
            job_kwargs={'misfire_grace_time': 3600}
        )
        logger.info("Daily credit reset job scheduled successfully.")


        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("setconfig", set_config_command))
        application.add_handler(CommandHandler("myid", my_id_command))
        application.add_handler(CommandHandler("setcredits", set_credits_command))
        #application.add_handler(MessageHandler(filters.Entity(MessageEntity.URL), handle_game_link))
        application.add_handler(MessageHandler(filters.Regex(r'chess\.com'), handle_game_link))
        logger.info("Bot starting...")
        
        # This part runs the bot indefinitely until a shutdown signal is received
        # (like pressing Ctrl+C)
        try:
            await application.initialize()
            await application.start()
            await application.updater.start_polling()
            
            # Keep the application running
            while True:
                await asyncio.sleep(3600) # Sleep for a long time
        except (KeyboardInterrupt, SystemExit):
            logger.info("Bot shutting down gracefully...")
        finally:
            # The 'async with' block will automatically call application.shutdown()
            # but we can also be explicit if needed.
            if application.updater.running:
                await application.updater.stop()
            if application.running:
                await application.stop()

if __name__ == '__main__':
    asyncio.run(main())
