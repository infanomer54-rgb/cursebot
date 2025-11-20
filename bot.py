import os
import logging
import sqlite3
import re
import asyncio
from datetime import datetime

import requests
import PyPDF2
import docx2txt
import aiofiles

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# Создаем директории
os.makedirs("методички", exist_ok=True)
os.makedirs("uploads", exist_ok=True)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path="bot_database.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS methodics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                file_path TEXT,
                uploaded_by INTEGER,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                work_type TEXT,
                subject TEXT,
                methodic_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_user(self, user_id, username, first_name, last_name):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name))
        conn.commit()
        conn.close()
    
    def add_methodic(self, filename, file_path, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO methodics (filename, file_path, uploaded_by)
            VALUES (?, ?, ?)
        ''', (filename, file_path, user_id))
        methodic_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return methodic_id
    
    def get_methodics(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT id, filename FROM methodics ORDER BY uploaded_at DESC')
        methodics = cursor.fetchall()
        conn.close()
        return methodics
    
    def get_methodic_path(self, methodic_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT file_path FROM methodics WHERE id = ?', (methodic_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def create_session(self, user_id, work_type, subject, methodic_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sessions (user_id, work_type, subject, methodic_id)
            VALUES (?, ?, ?, ?)
        ''', (user_id, work_type, subject, methodic_id))
        session_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return session_id

class DocumentProcessor:
    def extract_text_from_pdf(self, file_path):
        try:
            with open(file_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                text = ""
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                return text.strip()
        except Exception as e:
            logger.error(f"PDF error: {e}")
            return ""
    
    def extract_text_from_docx(self, file_path):
        try:
            text = docx2txt.process(file_path)
            return text.strip() if text else ""
        except Exception as e:
            logger.error(f"DOCX error: {e}")
            return ""
    
    async def extract_text_from_txt(self, file_path):
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as file:
                return await file.read()
        except Exception as e:
            logger.error(f"TXT error: {e}")
            return ""
    
    async def process_methodic(self, file_path):
        file_extension = file_path.lower().split('.')[-1]
        text = ""
        
        if file_extension == 'pdf':
            text = self.extract_text_from_pdf(file_path)
        elif file_extension == 'docx':
            text = self.extract_text_from_docx(file_path)
        elif file_extension == 'txt':
            text = await self.extract_text_from_txt(file_path)
        else:
            return {"error": "Unsupported format"}
        
        if not text:
            return {"error": "No text extracted"}
        
        return self.extract_methodic_info(text)
    
    def extract_methodic_info(self, text):
        info = {
            'requirements': self._extract_section(text, ['требован', 'объем', 'оформлен']),
            'structure': self._extract_section(text, ['структур', 'содержан', 'введен', 'заключен']),
            'formatting': self._extract_section(text, ['шрифт', 'интервал', 'поля', 'отступ', 'ссылки']),
            'deadlines': self._extract_section(text, ['срок', 'дедлайн', 'дата']),
            'full_text': text[:3000]
        }
        return info
    
    def _extract_section(self, text, keywords):
        sections = []
        for keyword in keywords:
            pattern = fr'{keyword}[а-яё]*[:\s]*([^\n]+)'
            matches = re.findall(pattern, text, re.IGNORECASE)
            sections.extend(matches)
        return sections if sections else [f"Раздел не найден"]

class DeepSeekAPI:
    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.api_url = DEEPSEEK_API_URL
    
    def generate_response(self, prompt, methodic_info, work_type, subject):
        if not self.api_key:
            return "❌ API ключ DeepSeek не настроен"
        
        system_prompt = self._create_system_prompt(methodic_info, work_type, subject)
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        try:
            response = requests.post(self.api_url, headers=headers, json=data, timeout=30)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']
        except Exception as e:
            logger.error(f"API error: {e}")
            return "❌ Ошибка сервиса. Попробуйте позже."
    
    def _create_system_prompt(self, methodic_info, work_type, subject):
        work_names = {
            "coursework": "курсовой работы",
            "essay": "реферата",
            "thesis": "дипломной работы"
        }
        work_name = work_names.get(work_type, "академической работы")
        
        return f"""
Ты - академический помощник для студентов. Помогаешь писать {work_name} по предмету "{subject}".

ИНФОРМАЦИЯ ИЗ МЕТОДИЧКИ:
Требования: {methodic_info.get('requirements', ['Не указаны'])}
Структура: {methodic_info.get('structure', ['Не указана'])}
Оформление: {methodic_info.get('formatting', ['Не указано'])}
Сроки: {methodic_info.get('deadlines', ['Не указаны'])}

Правила:
1. Строго следуй требованиям методички
2. Помогай с структурой и оформлением
3. Будь точным и профессиональным
4. Объясняй сложные понятия простым языком

Отвечай подробно, но доступно для студента.
"""

class CourseworkBot:
    def __init__(self):
        self.db = Database()
        self.doc_processor = DocumentProcessor()
        self.deepseek_api = DeepSeekAPI()
        self.user_sessions = {}
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name, user.last_name)
        
        welcome_text = f"""👋 Привет, {user.first_name}!

Я помогу тебе с написанием:
• 📚 Курсовых работ
• 📝 Рефератов  
• 🎓 Дипломных работ

Выбери тип работы:"""
        
        keyboard = [
            [InlineKeyboardButton("📚 Курсовая", callback_data="work_coursework")],
            [InlineKeyboardButton("📝 Реферат", callback_data="work_essay")],
            [InlineKeyboardButton("🎓 Диплом", callback_data="work_thesis")],
            [InlineKeyboardButton("📄 Загрузить методичку", callback_data="upload_methodic")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
📖 **Как пользоваться ботом:**

1. **Выберите тип работы** 
2. **Введите тему работы**
3. **Загрузите методичку** (если есть)
4. **Задавайте вопросы**

**Примеры запросов:**
• Помоги со структурой работы
• Какие должны быть разделы?
• Помоги написать введение
• Как оформить список литературы?
"""
        await update.message.reply_text(help_text)
    
    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        if data.startswith('work_'):
            work_type = data.split('_')[1]
            self.user_sessions[user_id] = {'work_type': work_type}
            
            work_names = {'coursework': 'курсовой', 'essay': 'реферата', 'thesis': 'диплома'}
            
            await query.edit_message_text(
                text=f"📝 Введите тему {work_names.get(work_type, 'работы')}:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]])
            )
        
        elif data == 'upload_methodic':
            await query.edit_message_text(
                text="📎 Отправьте файл методички (PDF, DOCX, TXT):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]])
            )
        
        elif data == 'back_to_main':
            await self.show_main_menu(query)
    
    async def show_main_menu(self, query):
        keyboard = [
            [InlineKeyboardButton("📚 Курсовая", callback_data="work_coursework")],
            [InlineKeyboardButton("📝 Реферат", callback_data="work_essay")],
            [InlineKeyboardButton("🎓 Диплом", callback_data="work_thesis")],
            [InlineKeyboardButton("📄 Загрузить методичку", callback_data="upload_methodic")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите тип работы:", reply_markup=reply_markup)
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        try:
            document = update.message.document
            filename = document.file_name
            file_extension = filename.lower().split('.')[-1]
            
            allowed_extensions = ['pdf', 'docx', 'txt']
            if file_extension not in allowed_extensions:
                await update.message.reply_text("❌ Поддерживаются только PDF, DOCX, TXT файлы")
                return
            
            file = await context.bot.get_file(document.file_id)
            file_path = os.path.join("методички", filename)
            await file.download_to_drive(file_path)
            
            processing_msg = await update.message.reply_text("🔄 Обрабатываю методичку...")
            methodic_info = await self.doc_processor.process_methodic(file_path)
            
            if 'error' in methodic_info:
                await processing_msg.edit_text(f"❌ {methodic_info['error']}")
                return
            
            methodic_id = self.db.add_methodic(filename, file_path, user_id)
            requirements_count = len(methodic_info.get('requirements', []))
            
            await processing_msg.edit_text(
                f"✅ Методичка загружена!\n"
                f"📋 Найдено требований: {requirements_count}\n"
                f"Теперь можно выбрать её при работе."
            )
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            await update.message.reply_text("❌ Ошибка загрузки файла")
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user_message = update.message.text.strip()
        
        session = self.user_sessions.get(user_id, {})
        
        if 'work_type' in session and 'subject' not in session:
            session['subject'] = user_message
            self.user_sessions[user_id] = session
            
            methodics = self.db.get_methodics()
            if methodics:
                keyboard = []
                for methodic_id, filename in methodics:
                    display_name = filename[:25] + "..." if len(filename) > 25 else filename
                    keyboard.append([InlineKeyboardButton(f"📄 {display_name}", callback_data=f"methodic_{methodic_id}")])
                keyboard.append([InlineKeyboardButton("🚫 Без методички", callback_data="no_methodic")])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text("📚 Выберите методичку:", reply_markup=reply_markup)
            else:
                session['methodic_id'] = None
                self.user_sessions[user_id] = session
                await update.message.reply_text("🎯 Теперь задавайте вопросы по работе!")
        
        elif 'work_type' in session and 'subject' in session:
            if len(user_message) < 3:
                await update.message.reply_text("❌ Слишком короткий запрос")
                return
            
            processing_msg = await update.message.reply_text("💭 Думаю над ответом...")
            methodic_info = await self.get_methodic_info(session.get('methodic_id'))
            
            response = self.deepseek_api.generate_response(
                prompt=user_message,
                methodic_info=methodic_info,
                work_type=session['work_type'],
                subject=session['subject']
            )
            
            await processing_msg.edit_text(response)
        
        else:
            await update.message.reply_text("🤔 Начните с /start")
    
    async def get_methodic_info(self, methodic_id):
        if not methodic_id:
            return {}
        
        file_path = self.db.get_methodic_path(methodic_id)
        if file_path and os.path.exists(file_path):
            return await self.doc_processor.process_methodic(file_path)
        return {}
    
    async def handle_methodic_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        session = self.user_sessions.get(user_id, {})
        
        if data == 'no_methodic':
            session['methodic_id'] = None
            self.user_sessions[user_id] = session
            self.db.create_session(user_id, session['work_type'], session['subject'], None)
            await query.edit_message_text("✅ Готово! Задавайте вопросы по работе.")
        elif data.startswith('methodic_'):
            methodic_id = int(data.split('_')[1])
            session['methodic_id'] = methodic_id
            self.user_sessions[user_id] = session
            self.db.create_session(user_id, session['work_type'], session['subject'], methodic_id)
            await query.edit_message_text("✅ Методичка выбрана! Задавайте вопросы.")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}")
    
    def run(self):
        if not BOT_TOKEN:
            logger.error("❌ BOT_TOKEN не найден!")
            return
        
        application = Application.builder().token(BOT_TOKEN).build()
        
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CallbackQueryHandler(self.handle_button, pattern="^(work_|upload_methodic|back_to_main)"))
        application.add_handler(CallbackQueryHandler(self.handle_methodic_selection, pattern="^(methodic_|no_methodic)"))
        application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        application.add_error_handler(self.error_handler)
        
        logger.info("🤖 Бот запущен!")
        application.run_polling()

if __name__ == "__main__":
    bot = CourseworkBot()
    bot.run()