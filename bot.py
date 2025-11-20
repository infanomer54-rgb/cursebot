import os
import logging
import sqlite3
import asyncio
from datetime import datetime
from dotenv import load_dotenv

import requests
import PyPDF2
import docx2txt
import aiofiles

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# Создаем директории
os.makedirs("методички", exist_ok=True)
os.makedirs("uploads", exist_ok=True)

# Настройка логирования для Railway
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler()  # Вывод в stdout для Railway
    ]
)

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path="bot_database.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица методичек
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS methodics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                file_path TEXT,
                uploaded_by INTEGER,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (uploaded_by) REFERENCES users (user_id)
            )
        ''')
        
        # Таблица сессий
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                work_type TEXT,
                subject TEXT,
                methodic_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (methodic_id) REFERENCES methodics (id)
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
    def __init__(self, methodics_dir):
        self.methodics_dir = methodics_dir
    
    def extract_text_from_pdf(self, file_path):
        """Извлекает текст из PDF файла"""
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
            logger.error(f"Ошибка при чтении PDF: {e}")
            return ""
    
    def extract_text_from_docx(self, file_path):
        """Извлекает текст из DOCX файла"""
        try:
            text = docx2txt.process(file_path)
            return text.strip() if text else ""
        except Exception as e:
            logger.error(f"Ошибка при чтении DOCX: {e}")
            return ""
    
    def extract_text_from_txt(self, file_path):
        """Извлекает текст из TXT файла"""
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as file:
                text = await file.read()
                return text.strip()
        except Exception as e:
            logger.error(f"Ошибка при чтении TXT: {e}")
            return ""
    
    async def process_methodic(self, file_path):
        """Обрабатывает методичку и извлекает ключевую информацию"""
        file_extension = file_path.lower().split('.')[-1]
        text = ""
        
        if file_extension == 'pdf':
            text = self.extract_text_from_pdf(file_path)
        elif file_extension == 'docx':
            text = self.extract_text_from_docx(file_path)
        elif file_extension == 'txt':
            text = await self.extract_text_from_txt(file_path)
        else:
            return {"error": "Неподдерживаемый формат файла"}
        
        if not text:
            return {"error": "Не удалось извлечь текст из файла"}
        
        # Извлекаем ключевую информацию из методички
        methodic_info = self.extract_methodic_info(text)
        return methodic_info
    
    def extract_methodic_info(self, text):
        """Извлекает структурированную информацию из текста методички"""
        import re
        
        info = {
            'requirements': self.extract_requirements(text),
            'structure': self.extract_structure(text),
            'formatting': self.extract_formatting(text),
            'deadlines': self.extract_deadlines(text),
            'full_text': text[:3000]  # Ограничиваем длину для API
        }
        return info
    
    def extract_requirements(self, text):
        """Извлекает требования из текста"""
        requirements_patterns = [
            r'требован[а-яё]*[:\s]*(.*?)(?=\n\n|\n[A-ZА-Я]|$)',
            r'объем[:\s]*(\d+[-\d\s]*(страниц|листов|стр))',
            r'оформлен[а-яё]*[:\s]*(.*?)(?=\n\n|\n[A-ZА-Я]|$)'
        ]
        
        requirements = []
        for pattern in requirements_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.DOTALL)
            for match in matches:
                if isinstance(match, tuple):
                    requirements.append(match[0])
                else:
                    requirements.append(match)
        
        return requirements if requirements else ["Требования не найдены в методичке"]
    
    def extract_structure(self, text):
        """Извлекает структуру работы"""
        structure_patterns = [
            r'структур[а-яё]*[:\s]*(.*?)(?=\n\n|\n[A-ZА-Я]|$)',
            r'содержан[а-яё]*[:\s]*(.*?)(?=\n\n|\n[A-ZА-Я]|$)',
            r'введен[а-яё]*[:\s]*(.*?)(?=\n\n|\n[A-ZА-Я]|$)',
            r'заключен[а-яё]*[:\s]*(.*?)(?=\n\n|\n[A-ZА-Я]|$)'
        ]
        
        structure = []
        for pattern in structure_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.DOTALL)
            for match in matches:
                if isinstance(match, tuple):
                    structure.append(match[0])
                else:
                    structure.append(match)
        
        return structure if structure else ["Структура не найдена в методичке"]
    
    def extract_formatting(self, text):
        """Извлекает правила оформления"""
        formatting_patterns = [
            r'шрифт[:\s]*([^\n]+)',
            r'интервал[:\s]*([^\n]+)',
            r'пол[я-яё]*[:\s]*([^\n]+)',
            r'отступ[ы-ыё]*[:\s]*([^\n]+)',
            r'ссылки[:\s]*([^\n]+)'
        ]
        
        formatting = []
        for pattern in formatting_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            formatting.extend(matches)
        
        return formatting if formatting else ["Правила оформления не найдены"]
    
    def extract_deadlines(self, text):
        """Извлекает сроки сдачи"""
        deadline_patterns = [
            r'срок[и-яё]*[:\s]*([^\n]+)',
            r'дедлайн[:\s]*([^\n]+)',
            r'дата[:\s]*([^\n]+)'
        ]
        
        deadlines = []
        for pattern in deadline_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            deadlines.extend(matches)
        
        return deadlines if deadlines else ["Сроки не указаны"]

class DeepSeekAPI:
    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.api_url = DEEPSEEK_API_URL
    
    def generate_response(self, prompt, methodic_info, work_type, subject):
        """Генерирует ответ с учетом методички"""
        
        if not self.api_key:
            return "❌ Ошибка: API ключ DeepSeek не настроен. Пожалуйста, проверьте настройки бота."
        
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
            
            result = response.json()
            return result['choices'][0]['message']['content']
        
        except requests.exceptions.Timeout:
            return "⏰ Время ожидания ответа от сервиса истекло. Попробуйте позже."
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка DeepSeek API: {e}")
            return "❌ Ошибка соединения с сервисом. Попробуйте позже."
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            return "⚠️ Произошла непредвиденная ошибка. Попробуйте еще раз."
    
    def _create_system_prompt(self, methodic_info, work_type, subject):
        """Создает системный промпт на основе методички"""
        
        work_type_names = {
            "coursework": "курсовой работы",
            "essay": "реферата",
            "thesis": "дипломной работы"
        }
        
        work_name = work_type_names.get(work_type, "академической работы")
        
        # Форматируем информацию из методички
        requirements_text = "\n".join([f"- {req}" for req in methodic_info.get('requirements', [])])
        structure_text = "\n".join([f"- {struct}" for struct in methodic_info.get('structure', [])])
        formatting_text = "\n".join([f"- {fmt}" for fmt in methodic_info.get('formatting', [])])
        deadlines_text = "\n".join([f"- {dl}" for dl in methodic_info.get('deadlines', [])])
        
        system_prompt = f"""
Ты - академический помощник для студентов. Помогаешь писать {work_name} по предмету "{subject}".

ИНФОРМАЦИЯ ИЗ МЕТОДИЧКИ:
Требования:
{requirements_text}

Структура:
{structure_text}

Оформление:
{formatting_text}

Сроки:
{deadlines_text}

ОСНОВНЫЕ ПРАВИЛА:
1. Строго следуй требованиям методички
2. Помогай с структурой, содержанием и оформлением
3. Предлагай конкретные идеи и формулировки
4. Объясняй сложные понятия простым языком
5. Помогай с академическим стилем письма
6. Будь точным и профессиональным

Отвечай подробно и профессионально, но доступно для студента.
Все рекомендации должны соответствовать методичке.
"""
        
        return system_prompt

class CourseworkBot:
    def __init__(self):
        self.db = Database()
        self.doc_processor = DocumentProcessor("методички")
        self.deepseek_api = DeepSeekAPI()
        self.user_sessions = {}
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name, user.last_name)
        
        welcome_text = f"""
👋 Привет, {user.first_name}!

Я - твой академический помощник для написания:
• 📚 Курсовых работ
• 📝 Рефератов  
• 🎓 Дипломных работ

Я помогу тебе:
✅ Создать структуру работы
✅ Написать содержание по методичке
✅ Правильно оформить работу
✅ Подготовить к защите

Для начала выбери тип работы или загрузи методичку!
        """
        
        keyboard = [
            [InlineKeyboardButton("📚 Курсовая", callback_data="work_coursework")],
            [InlineKeyboardButton("📝 Реферат", callback_data="work_essay")],
            [InlineKeyboardButton("🎓 Диплом", callback_data="work_thesis")],
            [InlineKeyboardButton("📄 Загрузить методичку", callback_data="upload_methodic")],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        help_text = """
📖 **Как пользоваться ботом:**

1. **Выберите тип работы** - курсовая, реферат или диплом
2. **Введите тему работы** - предмет или конкретную тему
3. **Загрузите методичку** (опционально) - для точного следования требованиям
4. **Задавайте вопросы** по структуре, содержанию и оформлению

**Примеры запросов:**
• "Помоги со структурой работы"
• "Какие должны быть разделы?"
• "Помоги написать введение"
• "Как оформить список литературы?"
• "Какие требования к объему?"

**Поддерживаемые форматы методичек:** PDF, DOCX, TXT
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий кнопок"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        if data.startswith('work_'):
            work_type = data.split('_')[1]
            self.user_sessions[user_id] = {'work_type': work_type}
            
            work_names = {
                'coursework': 'курсовой работы',
                'essay': 'реферата', 
                'thesis': 'дипломной работы'
            }
            
            await query.edit_message_text(
                text=f"📝 Вы выбрали: {work_names.get(work_type, 'работы')}\n\nВведите название предмета или темы работы:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]])
            )
        
        elif data == 'upload_methodic':
            await query.edit_message_text(
                text="📎 Отправьте файл методички (PDF, DOCX, TXT):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]])
            )
        
        elif data == 'help':
            await self.show_help(query)
        
        elif data == 'back_to_main':
            await self.show_main_menu(query)
    
    async def show_help(self, query):
        """Показывает справку"""
        help_text = """
📖 **Инструкция по использованию:**

1. **Выбор типа работы** - курсовая, реферат или диплом
2. **Указание темы** - предмет или конкретная тема работы  
3. **Загрузка методички** - для следования конкретным требованиям
4. **Вопросы и помощь** - по структуре, содержанию, оформлению

**Примеры вопросов:**
• Структура работы
• Требования к содержанию
• Правила оформления
• Помощь с написанием разделов
• Подготовка к защите

Для начала работы нажмите 'Назад' и выберите тип работы.
        """
        await query.edit_message_text(
            text=help_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]]),
            parse_mode='Markdown'
        )
    
    async def show_main_menu(self, query):
        """Показывает главное меню"""
        keyboard = [
            [InlineKeyboardButton("📚 Курсовая", callback_data="work_coursework")],
            [InlineKeyboardButton("📝 Реферат", callback_data="work_essay")],
            [InlineKeyboardButton("🎓 Диплом", callback_data="work_thesis")],
            [InlineKeyboardButton("📄 Загрузить методичку", callback_data="upload_methodic")],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text="Выберите тип работы или загрузите методичку:",
            reply_markup=reply_markup
        )
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик загрузки методичек"""
        user_id = update.effective_user.id
        
        try:
            if update.message.document:
                document = update.message.document
                filename = document.file_name
                file_extension = filename.lower().split('.')[-1] if filename else ''
            else:
                await update.message.reply_text("❌ Файл не распознан. Попробуйте еще раз.")
                return
            
            # Проверяем тип файла
            allowed_extensions = ['pdf', 'docx', 'txt']
            if file_extension not in allowed_extensions:
                await update.message.reply_text(
                    "❌ Поддерживаются только файлы:\n"
                    "• PDF (.pdf)\n" 
                    "• Word (.docx)\n"
                    "• Текст (.txt)"
                )
                return
            
            # Скачиваем файл
            file = await context.bot.get_file(document.file_id)
            file_path = os.path.join("методички", filename)
            
            await file.download_to_drive(file_path)
            
            # Показываем сообщение о обработке
            processing_msg = await update.message.reply_text("🔄 Обрабатываю методичку...")
            
            # Обрабатываем методичку
            methodic_info = await self.doc_processor.process_methodic(file_path)
            
            if 'error' in methodic_info:
                await processing_msg.edit_text(f"❌ {methodic_info['error']}")
                return
            
            # Сохраняем в базу
            methodic_id = self.db.add_methodic(filename, file_path, user_id)
            
            requirements_count = len(methodic_info.get('requirements', []))
            formatting_count = len(methodic_info.get('formatting', []))
            structure_count = len(methodic_info.get('structure', []))
            
            await processing_msg.edit_text(
                f"✅ Методичка '{filename}' успешно загружена!\n\n"
                f"📊 Статистика обработки:\n"
                f"• 📋 Требований: {requirements_count}\n"
                f"• 📖 Правил оформления: {formatting_count}\n"
                f"• 🏗️ Элементов структуры: {structure_count}\n\n"
                f"Теперь вы можете выбрать эту методичку при работе."
            )
            
        except Exception as e:
            logger.error(f"Ошибка при загрузке файла: {e}")
            await update.message.reply_text("❌ Произошла ошибка при обработке файла. Попробуйте еще раз.")
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений"""
        user_id = update.effective_user.id
        user_message = update.message.text.strip()
        
        session = self.user_sessions.get(user_id, {})
        
        if 'work_type' in session and 'subject' not in session:
            # Пользователь вводит тему работы
            session['subject'] = user_message
            self.user_sessions[user_id] = session
            
            # Предлагаем выбрать методичку
            methodics = self.db.get_methodics()
            if methodics:
                keyboard = []
                for methodic_id, filename in methodics:
                    # Обрезаем длинные названия
                    display_name = filename[:30] + "..." if len(filename) > 30 else filename
                    keyboard.append([InlineKeyboardButton(f"📄 {display_name}", callback_data=f"methodic_{methodic_id}")])
                keyboard.append([InlineKeyboardButton("🚫 Без методички", callback_data="no_methodic")])
                keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    "📚 Выберите методичку для работы (или продолжите без методички):",
                    reply_markup=reply_markup
                )
            else:
                session['methodic_id'] = None
                self.user_sessions[user_id] = session
                await update.message.reply_text(
                    "🎯 Отлично! Теперь можете задавать вопросы по вашей работе.\n\n"
                    "**Примеры запросов:**\n"
                    "• Помоги со структурой работы\n"
                    "• Какие должны быть разделы?\n"  
                    "• Помоги написать введение\n"
                    "• Как оформить список литературы?",
                    parse_mode='Markdown'
                )
        
        elif 'work_type' in session and 'subject' in session:
            # Обычный запрос пользователя
            if len(user_message) < 3:
                await update.message.reply_text("❌ Запрос слишком короткий. Пожалуйста, опишите ваш вопрос подробнее.")
                return
            
            # Показываем индикатор набора
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
            await update.message.reply_text(
                "🤔 Я не совсем понимаю контекст. Пожалуйста, начните с выбора типа работы через меню /start"
            )
    
    async def get_methodic_info(self, methodic_id):
        """Получает информацию о методичке"""
        if not methodic_id:
            return {}
        
        file_path = self.db.get_methodic_path(methodic_id)
        if file_path and os.path.exists(file_path):
            return await self.doc_processor.process_methodic(file_path)
        
        return {}
    
    async def handle_methodic_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик выбора методички"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        session = self.user_sessions.get(user_id, {})
        
        if data == 'no_methodic':
            session['methodic_id'] = None
            self.user_sessions[user_id] = session
            
            # Создаем сессию в БД
            self.db.create_session(
                user_id=user_id,
                work_type=session['work_type'],
                subject=session['subject'],
                methodic_id=None
            )
            
            await query.edit_message_text(
                "🎯 Готово! Теперь можете задавать вопросы по вашей работе.\n\n"
                "**Примеры запросов:**\n"
                "• Помоги со структурой работы\n"
                "• Какие должны быть разделы?\n"
                "• Помоги написать введение\n" 
                "• Как оформить список литературы?\n"
                "• Какие источники использовать?",
                parse_mode='Markdown'
            )
        elif data.startswith('methodic_'):
            methodic_id = int(data.split('_')[1])
            session['methodic_id'] = methodic_id
            self.user_sessions[user_id] = session
            
            # Создаем сессию в БД
            self.db.create_session(
                user_id=user_id,
                work_type=session['work_type'],
                subject=session['subject'],
                methodic_id=methodic_id
            )
            
            await query.edit_message_text(
                "✅ Методичка выбрана! Теперь я буду учитывать её требования при ответах.\n\n"
                "**Можете задавать вопросы:**\n"
                "• Помоги со структурой согласно методичке\n"
                "• Какие требования к оформлению?\n" 
                "• Помоги написать основную часть\n"
                "• Как оформить титульный лист?\n"
                "• Проверь соответствие требованиям",
                parse_mode='Markdown'
            )
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Ошибка: {context.error}", exc_info=context.error)
        
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id if update else None,
                text="❌ Произошла непредвиденная ошибка. Пожалуйста, попробуйте еще раз."
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения об ошибке: {e}")
    
    def run(self):
        """Запускает бота"""
        if not BOT_TOKEN:
            logger.error("❌ BOT_TOKEN не найден в переменных окружения!")
            return
        
        if not DEEPSEEK_API_KEY:
            logger.warning("⚠️ DEEPSEEK_API_KEY не найден! Бот будет работать с ограниченной функциональностью")
        
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Обработчики команд
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help_command))
        
        # Обработчики кнопок
        application.add_handler(CallbackQueryHandler(self.handle_button, pattern="^(work_|upload_methodic|back_to_main|help)"))
        application.add_handler(CallbackQueryHandler(self.handle_methodic_selection, pattern="^(methodic_|no_methodic)"))
        
        # Обработчики сообщений
        application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        
        # Обработчик ошибок
        application.add_error_handler(self.error_handler)
        
        # Запуск бота
        logger.info("🤖 Бот запущен и готов к работе!")
        print("=" * 50)
        print("🎓 Academic Assistant Bot Started!")
        print("📚 Помощь с курсовыми, рефератами и дипломами")
        print("=" * 50)
        
        application.run_polling()

if __name__ == "__main__":
    bot = CourseworkBot()
    bot.run()