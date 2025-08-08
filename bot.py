# Telegram Bot with Integrated Standalone Chess Logic
#
# MODERN ASYNC VERSION (for python-telegram-bot v20+)
#
# LOCAL-ONLY VERSION: This script now uses a persistent local Chrome profile
# and includes a /setconfig command to change credentials on the fly.
#
# 1. Activate your virtual environment.
# 2. Ensure you have the necessary libraries:
#    pip install "python-telegram-bot>=20.0" selenium webdriver-manager python-dotenv selenium-stealth
# 3. Create a .env file with your Telegram and initial Chess.com credentials.

import os
import logging
import time
import shutil
import asyncio
import re
import json # <-- ADDED for config management
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

# --- Credentials (will be loaded dynamically) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
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


# --- Main Chess Logic (Unchanged) ---
def run_chess_login_flow(game_url: str):
    """The main standalone function to run the login and game analysis process."""
    logger.info("--- Starting Chess.com Login Flow (Local Profile Mode) ---")

    if not CHESS_USERNAME or not CHESS_PASSWORD:
        logger.error("Chess.com username or password is not set. Please use /setconfig.")
        return # Exit if credentials are not configured

    os.makedirs(LOCAL_PROFILE_PATH, exist_ok=True)
    logger.info(f"Using persistent local profile at: {LOCAL_PROFILE_PATH}")

    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
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

        logger.info("Navigating to chess.com...")
        driver.get("https://www.chess.com/login")
        wait = WebDriverWait(driver, 20)

        time.sleep(3)
        if "/home" in driver.current_url:
            logger.info("✅ Login successful using existing local profile.")
        else:
            logger.info("Not logged in. Performing manual login...")
            try:
                cookie_button = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Accept')] | //button[contains(., 'Allow all')]")))
                cookie_button.click()
                time.sleep(1)
            except TimeoutException:
                logger.info("Cookie banner not found, continuing.")
            
            username_field = wait.until(EC.element_to_be_clickable((By.ID, "login-username")))
            username_field.send_keys(CHESS_USERNAME)
            time.sleep(0.5)
            
            driver.find_element(By.ID, "login-password").send_keys(CHESS_PASSWORD)
            time.sleep(0.5)
            
            driver.find_element(By.ID, "login").click()
            
            wait.until(EC.url_contains("/home"))
            logger.info("✅ Manual login successful! Session saved to local profile.")

        logger.info("Login successful. Taking screenshot of home page...")
        home_screenshot_path = "home_page_screenshot.png"
        driver.save_screenshot(home_screenshot_path)
        logger.info(f"Screenshot saved as '{home_screenshot_path}'.")

        logger.info("Opening new tab for the game link...")
        driver.switch_to.new_window('tab')
        
        logger.info(f"Navigating to {game_url}...")
        driver.get(game_url)
        time.sleep(10)
        new_tab_screenshot_path = "new_tab_screenshot.png"
        driver.save_screenshot(new_tab_screenshot_path)
        logger.info(f"Screenshot of new tab saved as '{new_tab_screenshot_path}'.")

    except Exception as e:
        logger.error(f"An error occurred in the main process: {e}")
        if driver:
            driver.save_screenshot("error.png")
            logger.info("Error screenshot saved as 'error.png'.")
    
    finally:
        if driver:
            logger.info("Closing WebDriver...")
            driver.quit()
        logger.info("--- Chess.com Login Flow Finished ---")


# --- Telegram Handler Functions ---

async def set_config_command(update: Update, context: CallbackContext) -> None:
    """
    Handles the /setconfig command to update Chess.com credentials
    and removes the old Chrome profile to force a new login.
    """
    global CHESS_USERNAME, CHESS_PASSWORD
    
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Usage: /setconfig <username> <password>")
        return
        
    new_username, new_password = args[0], args[1]
    
    if save_credentials(new_username, new_password):
        # Update the running config immediately
        CHESS_USERNAME = new_username
        CHESS_PASSWORD = new_password
        
        reply_message = "✅ Configuration updated successfully!"
        
        # Now, remove the old chrome profile directory if it exists
        try:
            if os.path.exists(LOCAL_PROFILE_PATH):
                shutil.rmtree(LOCAL_PROFILE_PATH)
                logger.info(f"Removed old chrome profile at: {LOCAL_PROFILE_PATH}")
                reply_message += "\n🧹 The old browser session has been cleared."
            else:
                logger.info("No existing chrome profile to remove.")
                
        except Exception as e:
            logger.error(f"Failed to remove chrome profile: {e}")
            reply_message += "\n⚠️ Could not remove the old browser session. You may need to delete it manually."

        await update.message.reply_text(reply_message)
            
    else:
        await update.message.reply_text("❌ Failed to save new configuration. Please check the logs.")


async def handle_game_link(update: Update, context: CallbackContext) -> None:
    """Extracts a specific chess.com game URL, sends timed replies, and triggers the login flow."""
    message = update.message
    if not message or not message.text:
        return

    urls = message.parse_entities(types=[MessageEntity.URL])
    if not urls:
        return

    first_url = list(urls.values())[0]
    pattern = r"^https://www.chess.com/live/game/\d+$"

    if re.match(pattern, first_url):
        if not CHESS_USERNAME or not CHESS_PASSWORD:
            await message.reply_text("Chess.com credentials are not set. Please use the /setconfig command first.")
            return

        await message.reply_text("Hang on, I am reviewing your game... 🤔")
        await asyncio.sleep(1)
        await message.reply_text("Looks like you found some tactics in the match... 🧐")
        await asyncio.sleep(1)
        await message.reply_text("Searching for any brilliant moves... 💎")
        await message.reply_text("Wait a moment, this will take about 10 seconds... ⏳")
        
        analysis_url = first_url.replace('/live/game/', '/analysis/game/live/') + '/review'
        
        async with selenium_lock:
            try:
                # This runs the entire Selenium process
                await asyncio.to_thread(run_chess_login_flow, game_url=analysis_url)
                
                # --- Screenshot Sending Logic ---
                # 1. Send the login screenshot
                if os.path.exists("home_page_screenshot.png"):
                    await message.reply_photo("home_page_screenshot.png", caption="Login was successful!")
                
                # 2. Send the analysis URL
                await message.reply_text("Alright, here is the review of your game! 👇")
                await message.reply_text(f"\n{analysis_url}\n")
                
                # 3. Send the analysis page screenshot
                if os.path.exists("new_tab_screenshot.png"):
                    await message.reply_photo("new_tab_screenshot.png", caption="Analysis page is ready. ✨")

            except Exception as e:
                logger.error(f"The chess flow failed: {e}")
                # 4. Send an error screenshot if the process fails
                if os.path.exists("error.png"):
                    await message.reply_photo("error.png", caption="An error occurred during the process.")
    else:
        await message.reply_text("Please send a valid chess.com game link, like 'https://www.chess.com/live/game/123456789'.")

# --- Main Bot Execution ---
def main() -> None:
    """Starts the bot and listens for commands."""
    # Load credentials on startup
    load_credentials()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing. Please check your .env file.")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- ADDED: Register the new /setconfig command handler ---
    application.add_handler(CommandHandler("setconfig", set_config_command))
    
    # This handler will trigger for any message containing a URL
    application.add_handler(MessageHandler(filters.Entity(MessageEntity.URL), handle_game_link))

    logger.info("Bot started. Send a chess.com game link or use /setconfig.")
    application.run_polling()


if __name__ == '__main__':
    main()
