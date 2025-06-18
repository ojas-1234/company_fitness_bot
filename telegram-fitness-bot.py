import logging
import sqlite3
from datetime import datetime, timedelta
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import os
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database setup
def init_db():
    conn = sqlite3.connect('fitness_tracker.db')
    c = conn.cursor()
    
    # Create tables
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS challenges
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  challenge_text TEXT,
                  frequency TEXT,
                  created_at TIMESTAMP,
                  active BOOLEAN DEFAULT 1,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS completions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  challenge_id INTEGER,
                  completed_at TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id),
                  FOREIGN KEY (challenge_id) REFERENCES challenges (id))''')
    
    conn.commit()
    conn.close()

# Database helper functions
def add_user(user_id, username, first_name):
    conn = sqlite3.connect('fitness_tracker.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?)", 
              (user_id, username, first_name))
    conn.commit()
    conn.close()

def add_challenge(user_id, challenge_text, frequency):
    conn = sqlite3.connect('fitness_tracker.db')
    c = conn.cursor()
    # Deactivate previous challenges for this user
    c.execute("UPDATE challenges SET active = 0 WHERE user_id = ?", (user_id,))
    # Add new challenge
    c.execute("INSERT INTO challenges (user_id, challenge_text, frequency, created_at) VALUES (?, ?, ?, ?)",
              (user_id, challenge_text, frequency, datetime.now()))
    conn.commit()
    conn.close()

def get_active_challenge(user_id):
    conn = sqlite3.connect('fitness_tracker.db')
    c = conn.cursor()
    c.execute("SELECT id, challenge_text, frequency FROM challenges WHERE user_id = ? AND active = 1", 
              (user_id,))
    result = c.fetchone()
    conn.close()
    return result

def record_completion(user_id, challenge_id):
    conn = sqlite3.connect('fitness_tracker.db')
    c = conn.cursor()
    c.execute("INSERT INTO completions (user_id, challenge_id, completed_at) VALUES (?, ?, ?)",
              (user_id, challenge_id, datetime.now()))
    conn.commit()
    conn.close()

def get_monthly_stats():
    conn = sqlite3.connect('fitness_tracker.db')
    c = conn.cursor()
    
    # Get completions from the last 30 days
    thirty_days_ago = datetime.now() - timedelta(days=30)
    
    query = '''
    SELECT u.first_name, u.username, COUNT(c.id) as completion_count
    FROM users u
    LEFT JOIN completions c ON u.user_id = c.user_id 
        AND c.completed_at > ?
    GROUP BY u.user_id
    ORDER BY completion_count DESC
    '''
    
    c.execute(query, (thirty_days_ago,))
    results = c.fetchall()
    conn.close()
    return results

# Bot command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    user = update.effective_user
    add_user(user.id, user.username, user.first_name)
    
    keyboard = [
        [
            InlineKeyboardButton("Daily Challenge", callback_data='daily'),
            InlineKeyboardButton("Weekly Challenge", callback_data='weekly')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f'Hi {user.first_name}! 💪\n\n'
        'Welcome to the Fitness Tracker Bot!\n'
        'Choose your challenge frequency:',
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses"""
    query = update.callback_query
    await query.answer()
    
    if query.data in ['daily', 'weekly']:
        context.user_data['frequency'] = query.data
        await query.edit_message_text(
            f"Great! You've chosen a {query.data} challenge.\n\n"
            "Now, type your challenge (e.g., '35 pushups per day'):"
        )
    elif query.data.startswith('complete_'):
        challenge_id = int(query.data.split('_')[1])
        record_completion(query.from_user.id, challenge_id)
        await query.edit_message_text("✅ Great job! Challenge marked as complete!")
        
        # Schedule next reminder
        frequency = context.user_data.get('current_frequency', 'daily')
        if frequency == 'daily':
            next_time = datetime.now() + timedelta(days=1)
        else:
            next_time = datetime.now() + timedelta(weeks=1)
        
        # You would implement the scheduling logic here

async def receive_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the challenge text from user"""
    if 'frequency' not in context.user_data:
        await update.message.reply_text(
            "Please use /start to begin setting up your challenge."
        )
        return
    
    challenge_text = update.message.text
    frequency = context.user_data['frequency']
    user_id = update.effective_user.id
    
    add_challenge(user_id, challenge_text, frequency)
    
    await update.message.reply_text(
        f"✅ Challenge set!\n\n"
        f"📋 Your {frequency} challenge: {challenge_text}\n\n"
        f"I'll remind you {frequency} to check in on your progress!"
    )
    
    # Clear the frequency from user data
    context.user_data.clear()

async def check_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual progress check"""
    user_id = update.effective_user.id
    challenge = get_active_challenge(user_id)
    
    if not challenge:
        await update.message.reply_text(
            "You don't have an active challenge. Use /start to create one!"
        )
        return
    
    challenge_id, challenge_text, frequency = challenge
    
    keyboard = [[
        InlineKeyboardButton("✅ Yes, completed!", callback_data=f'complete_{challenge_id}'),
        InlineKeyboardButton("❌ Not yet", callback_data='not_complete')
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Did you complete your {frequency} challenge?\n\n"
        f"📋 {challenge_text}",
        reply_markup=reply_markup
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show monthly statistics"""
    stats = get_monthly_stats()
    
    if not stats:
        await update.message.reply_text("No statistics available yet!")
        return
    
    message = "🏆 **Monthly Leaderboard** (Last 30 days)\n\n"
    
    for i, (first_name, username, count) in enumerate(stats, 1):
        name = first_name or username or "Unknown"
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "👤"
        message += f"{emoji} {name}: {count} completions\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def remind_users(context: ContextTypes.DEFAULT_TYPE):
    """Send reminders to users based on their schedule"""
    # This function would be called periodically to check which users need reminders
    # Implementation would depend on your scheduling needs
    pass

def main():
    """Start the bot."""
    # Initialize database
    init_db()
    
    # Create the Application
    # Replace 'YOUR_BOT_TOKEN' with your actual bot token
    application = Application.builder().token("BOT_TOKEN").build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", check_progress))
    application.add_handler(CommandHandler("stats", stats))
    
    # Register callback query handler for buttons
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Register message handler for challenge text
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, receive_challenge
    ))

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()