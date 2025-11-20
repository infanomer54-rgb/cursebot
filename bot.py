import os
import logging
import sqlite3
import re
import asyncio
from datetime import datetime
import json

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
os.makedirs("работы", exist_ok=True)

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
            CREATE TABLE IF NOT EXISTS works (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                work_type TEXT,
                topic TEXT,
                subject TEXT,
                structure TEXT,
                content TEXT,
                methodic_info TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS methodics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                file_path TEXT,
                requirements TEXT,
                structure TEXT,
                formatting TEXT,
                uploaded_by INTEGER,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    
    def create_work(self, user_id, work_type, topic, subject, methodic_info=None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        methodic_json = json.dumps(methodic_info) if methodic_info else None
        cursor.execute('''
            INSERT INTO works (user_id, work_type, topic, subject, methodic_info)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, work_type, topic, subject, methodic_json))
        work_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return work_id
    
    def update_work_structure(self, work_id, structure):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE works SET structure = ? WHERE id = ?', (structure, work_id))
        conn.commit()
        conn.close()
    
    def update_work_content(self, work_id, content):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE works SET content = ? WHERE id = ?', (content, work_id))
        conn.commit()
        conn.close()
    
    def get_work(self, work_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM works WHERE id = ?', (work_id,))
        result = cursor.fetchone()
        conn.close()
        return result
    
    def add_methodic(self, filename, file_path, requirements, structure, formatting, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO methodics (filename, file_path, requirements, structure, formatting, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (filename, file_path, requirements, structure, formatting, user_id))
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
    
    def get_methodic(self, methodic_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM methodics WHERE id = ?', (methodic_id,))
        result = cursor.fetchone()
        conn.close()
        return result

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
            return None
        
        if not text:
            return None
        
        return self.extract_methodic_info(text)
    
    def extract_methodic_info(self, text):
        requirements = self._extract_section(text, ['требован', 'объем', 'оформлен'])
        structure = self._extract_section(text, ['структур', 'содержан', 'введен', 'заключен', 'глава'])
        formatting = self._extract_section(text, ['шрифт', 'интервал', 'поля', 'отступ', 'ссылки', 'литератур'])
        
        return {
            'requirements': requirements,
            'structure': structure,
            'formatting': formatting,
            'full_text': text[:4000]
        }
    
    def _extract_section(self, text, keywords):
        sections = []
        for keyword in keywords:
            pattern = fr'{keyword}[а-яё]*[:\s]*([^\n]+)'
            matches = re.findall(pattern, text, re.IGNORECASE)
            sections.extend(matches)
        return sections if sections else ["Не указано"]

class AcademicWriter:
    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.api_url = DEEPSEEK_API_URL
    
    def generate_structure(self, work_type, topic, subject, methodic_info=None):
        """Генерирует структуру работы"""
        
        work_type_names = {
            "coursework": "курсовой работы",
            "essay": "реферата", 
            "thesis": "дипломной работы"
        }
        
        methodic_text = ""
        if methodic_info:
            methodic_text = f"""
УЧТИ ТРЕБОВАНИЯ МЕТОДИЧКИ:
Требования: {methodic_info.get('requirements', [])}
Структура: {methodic_info.get('structure', [])}
Оформление: {methodic_info.get('formatting', [])}
"""
        
        system_prompt = f"""
Ты - эксперт по созданию академических работ. Создай подробную структуру для {work_type_names[work_type]} на тему "{topic}" по предмету "{subject}".

{methodic_text}

Создай подробную структуру включая:
1. Титульный лист
2. Содержание/оглавление  
3. Введение с актуальностью, целями, задачами
4. Основную часть с главами и подразделами
5. Заключение с выводами
6. Список литературы
7. Приложения (если нужны)

Верни только чистую структуру без лишних комментариев.
"""
        
        return self._make_api_call(system_prompt, "Создай подробную структуру академической работы.")
    
    def generate_full_work(self, work_type, topic, subject, structure, methodic_info=None):
        """Генерирует полный текст работы"""
        
        methodic_text = ""
        if methodic_info:
            methodic_text = f"\nТРЕБОВАНИЯ МЕТОДИЧКИ: {methodic_info}"
        
        system_prompt = f"""
Ты - профессиональный академический писатель. Напиши ПОЛНЫЙ ТЕКСТ {work_type} на тему "{topic}" по предмету "{subject}".

СТРУКТУРА РАБОТЫ:
{structure}
{methodic_text}

Напиши полноценную академическую работу включая:
1. Введение (актуальность, цели, задачи)
2. Основную часть (теоретическая и практическая части)
3. Заключение (выводы и результаты)
4. Список литературы

Требования:
- Академический стиль изложения
- Глубокое раскрытие темы
- Научная обоснованность
- Логическая последовательность
- Объем: {self._get_work_volume(work_type)}
- Конкретные примеры и данные

Верни полный текст работы готовый к сдаче.
"""
        
        return self._make_api_call(system_prompt, "Напиши полный текст академической работы.")
    
    def _get_work_volume(self, work_type):
        volumes = {
            "essay": "15-25 страниц",
            "coursework": "30-50 страниц", 
            "thesis": "60-100 страниц"
        }
        return volumes.get(work_type, "20-40 страниц")
    
    def _make_api_call(self, system_prompt, user_prompt):
        if not self.api_key:
            return "❌ Ошибка: API ключ DeepSeek не настроен"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 4000
        }
        
        try:
            logger.info("Отправка запроса к DeepSeek API...")
            response = requests.post(self.api_url, headers=headers, json=data, timeout=120)
            response.raise_for_status()
            result = response.json()
            return result['choices'][0]['message']['content']
        except requests.exceptions.Timeout:
            return "⏰ Время ожидания истекло. Попробуйте еще раз."
        except requests.exceptions.RequestException as e:
            logger.error(f"API error: {e}")
            return "❌ Ошибка соединения с сервисом."
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return f"❌ Ошибка генерации: {str(e)}"

class CourseworkBot:
    def __init__(self):
        self.db = Database()
        self.doc_processor = DocumentProcessor()
        self.writer = AcademicWriter()
        self.user_sessions = {}
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name, user.last_name)
        
        welcome_text = f"""🎓 <b>Академический помощник - Автописатель</b>

Привет, {user.first_name}! Я напишу для тебя полноценную академическую работу с нуля.

Выбери тип работы:"""

        keyboard = [
            [InlineKeyboardButton("📚 Курсовая работа", callback_data="work_coursework")],
            [InlineKeyboardButton("📝 Реферат", callback_data="work_essay")],
            [InlineKeyboardButton("🎓 Дипломная работа", callback_data="work_thesis")],
            [InlineKeyboardButton("📄 Загрузить методичку", callback_data="upload_methodic")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='HTML')
    
    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        if data.startswith('work_'):
            work_type = data.split('_')[1]
            self.user_sessions[user_id] = {
                'work_type': work_type,
                'stage': 'subject'
            }
            
            work_names = {
                'coursework': 'курсовой работы',
                'essay': 'реферата',
                'thesis': 'дипломной работы'
            }
            
            await query.edit_message_text(
                f"📝 Выбран тип: <b>{work_names[work_type]}</b>\n\nВведите предмет или дисциплину:",
                parse_mode='HTML'
            )
        
        elif data == 'upload_methodic':
            await query.edit_message_text(
                "📎 Отправьте файл методички (PDF, DOCX, TXT):\n\n"
                "Методичка поможет мне точнее соблюсти требования вашего учебного заведения."
            )
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user_message = update.message.text.strip()
        
        session = self.user_sessions.get(user_id, {})
        
        if not session:
            await update.message.reply_text("🤔 Пожалуйста, начните с команды /start")
            return
        
        current_stage = session.get('stage')
        
        if current_stage == 'subject':
            # Получили предмет, запрашиваем тему
            session['subject'] = user_message
            session['stage'] = 'topic'
            self.user_sessions[user_id] = session
            
            await update.message.reply_text(
                f"📚 Предмет: <b>{user_message}</b>\n\nТеперь введите тему работы:",
                parse_mode='HTML'
            )
        
        elif current_stage == 'topic':
            # Получили тему, предлагаем выбрать методичку или продолжить без нее
            session['topic'] = user_message
            session['stage'] = 'methodic_choice'
            self.user_sessions[user_id] = session
            
            methodics = self.db.get_methodics()
            if methodics:
                keyboard = []
                for methodic_id, filename in methodics:
                    display_name = filename[:25] + "..." if len(filename) > 25 else filename
                    keyboard.append([InlineKeyboardButton(f"📄 {display_name}", callback_data=f"methodic_{methodic_id}")])
                keyboard.append([InlineKeyboardButton("🚫 Без методички", callback_data="no_methodic")])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    f"🎯 Тема: <b>{user_message}</b>\n\nВыберите методичку или продолжите без нее:",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                await self.start_generation(update, session, None)
    
    async def start_generation(self, update, session, methodic_info):
        """Начинает процесс генерации работы"""
        # Определяем user_id в зависимости от типа update
        if hasattr(update, 'effective_user'):
            user_id = update.effective_user.id
        else:
            # Если это callback query, используем from_user
            user_id = update.from_user.id
        
        # Создаем запись в БД
        work_id = self.db.create_work(
            user_id=user_id,
            work_type=session['work_type'],
            topic=session['topic'],
            subject=session['subject'],
            methodic_info=methodic_info
        )
        session['work_id'] = work_id
        self.user_sessions[user_id] = session
        
        # Начинаем генерацию структуры
        await self.generate_structure(update, session)
    
    async def generate_structure(self, update, session):
        """Генерирует структуру работы"""
        # Определяем объект сообщения в зависимости от типа update
        if hasattr(update, 'message'):
            message_obj = update.message
        else:
            message_obj = update
        
        generating_msg = await message_obj.reply_text("🔄 Создаю структуру работы...")
        
        methodic_info = session.get('methodic_info')
        
        structure = self.writer.generate_structure(
            work_type=session['work_type'],
            topic=session['topic'],
            subject=session['subject'],
            methodic_info=methodic_info
        )
        
        if structure.startswith("❌") or structure.startswith("⏰"):
            await generating_msg.edit_text(f"❌ Не удалось создать структуру: {structure}")
            return
        
        # Сохраняем структуру
        self.db.update_work_structure(session['work_id'], structure)
        
        keyboard = [
            [InlineKeyboardButton("✅ Написать полную работу", callback_data="generate_full")],
            [InlineKeyboardButton("🔄 Перегенерировать структуру", callback_data="regenerate_structure")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Разбиваем длинное сообщение на части
        structure_preview = structure[:1500] + "..." if len(structure) > 1500 else structure
        
        await generating_msg.edit_text(
            f"📋 <b>Структура работы готова!</b>\n\n"
            f"{structure_preview}\n\n"
            f"Выберите дальнейшее действие:",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    async def handle_methodic_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик выбора методички"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        session = self.user_sessions.get(user_id, {})
        
        if data == 'no_methodic':
            session['methodic_info'] = None
            self.user_sessions[user_id] = session
            await self.start_generation(query, session, None)
        elif data.startswith('methodic_'):
            methodic_id = int(data.split('_')[1])
            methodic_data = self.db.get_methodic(methodic_id)
            if methodic_data:
                methodic_info = {
                    'requirements': methodic_data[3],
                    'structure': methodic_data[4],
                    'formatting': methodic_data[5]
                }
                session['methodic_info'] = methodic_info
                session['methodic_id'] = methodic_id
                self.user_sessions[user_id] = session
                await self.start_generation(query, session, methodic_info)
            else:
                await query.message.reply_text("❌ Методичка не найдена")
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик загрузки методичек"""
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
            
            processing_msg = await update.message.reply_text("🔄 Анализирую методичку...")
            
            methodic_info = await self.doc_processor.process_methodic(file_path)
            
            if not methodic_info:
                await processing_msg.edit_text("❌ Не удалось обработать методичку")
                return
            
            # Сохраняем методичку в БД
            methodic_id = self.db.add_methodic(
                filename=filename,
                file_path=file_path,
                requirements=str(methodic_info['requirements']),
                structure=str(methodic_info['structure']),
                formatting=str(methodic_info['formatting']),
                user_id=user_id
            )
            
            await processing_msg.edit_text(
                f"✅ Методичка загружена!\n"
                f"📋 Найдено требований: {len(methodic_info['requirements'])}\n"
                f"🏗️ Элементов структуры: {len(methodic_info['structure'])}\n\n"
                f"Теперь начните создание работы через /start"
            )
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            await update.message.reply_text("❌ Ошибка загрузки файла")
    
    async def handle_generation_requests(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок генерации"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        session = self.user_sessions.get(user_id, {})
        
        if not session:
            await query.message.reply_text("❌ Сессия не найдена. Начните с /start")
            return
        
        if data == 'generate_full':
            await self.generate_full_work(query, session)
        elif data == 'regenerate_structure':
            await self.generate_structure(query, session)
    
    async def generate_full_work(self, query, session):
        """Генерирует полный текст работы"""
        generating_msg = await query.message.reply_text(
            "🔄 Пишу полный текст работы...\n"
            "Это может занять 2-5 минут. Пожалуйста, подождите."
        )
        
        # Получаем структуру из БД
        work_data = self.db.get_work(session['work_id'])
        structure = work_data[5] if work_data else ""
        
        methodic_info = session.get('methodic_info')
        
        # Генерируем полный текст
        full_content = self.writer.generate_full_work(
            work_type=session['work_type'],
            topic=session['topic'],
            subject=session['subject'],
            structure=structure,
            methodic_info=methodic_info
        )
        
        if full_content.startswith("❌") or full_content.startswith("⏰"):
            await generating_msg.edit_text(f"❌ Не удалось создать работу: {full_content}")
            return
        
        # Сохраняем контент
        self.db.update_work_content(session['work_id'], full_content)
        
        # Отправляем работу частями (Telegram ограничение 4096 символов)
        work_names = {
            'coursework': 'Курсовая работа',
            'essay': 'Реферат', 
            'thesis': 'Дипломная работа'
        }
        
        # Отправляем заголовок
        await query.message.reply_text(
            f"🎉 <b>{work_names[session['work_type']]} ГОТОВА!</b>\n\n"
            f"📚 Тема: {session['topic']}\n"
            f"🔬 Предмет: {session['subject']}\n"
            f"📄 Объем: ~{len(full_content.split())} слов\n\n"
            f"<i>Работа разделена на несколько сообщений...</i>",
            parse_mode='HTML'
        )
        
        # Отправляем работу частями
        chunk_size = 3500
        for i in range(0, len(full_content), chunk_size):
            chunk = full_content[i:i + chunk_size]
            await query.message.reply_text(chunk)
            
            # Небольшая задержка между сообщениями
            await asyncio.sleep(1)
        
        await generating_msg.delete()
        
        # Предлагаем начать новую работу
        keyboard = [
            [InlineKeyboardButton("🔄 Написать новую работу", callback_data="new_work")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            "✅ <b>Работа завершена!</b>\n\n"
            "Вы можете начать новую работу или использовать /start для выбора другого типа работы.",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    async def handle_new_work(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопки новой работы"""
        query = update.callback_query
        await query.answer()
        
        # Очищаем сессию и начинаем заново
        user_id = query.from_user.id
        if user_id in self.user_sessions:
            del self.user_sessions[user_id]
        
        await self.start(query, context)
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}")
        
        # Отправляем сообщение об ошибке пользователю
        try:
            if update and hasattr(update, 'effective_chat'):
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Произошла непредвиденная ошибка. Пожалуйста, попробуйте еще раз или начните с /start"
                )
        except Exception as e:
            logger.error(f"Error in error handler: {e}")
    
    def run(self):
        if not BOT_TOKEN:
            logger.error("❌ BOT_TOKEN не найден!")
            return
        
        if not DEEPSEEK_API_KEY:
            logger.warning("⚠️ DEEPSEEK_API_KEY не найден! Бот будет работать с ограничениями.")
        
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Обработчики
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CallbackQueryHandler(self.handle_button, pattern="^(work_|upload_methodic)"))
        application.add_handler(CallbackQueryHandler(self.handle_methodic_selection, pattern="^(methodic_|no_methodic)"))
        application.add_handler(CallbackQueryHandler(self.handle_generation_requests, pattern="^(generate_full|regenerate_structure)"))
        application.add_handler(CallbackQueryHandler(self.handle_new_work, pattern="^new_work$"))
        application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        application.add_error_handler(self.error_handler)
        
        logger.info("🤖 Бот-писатель запущен!")
        print("=" * 50)
        print("🎓 Academic Auto-Writer Bot Started!")
        print("📚 Автоматическое написание курсовых, рефератов и дипломов")
        print("=" * 50)
        
        application.run_polling()

if __name__ == "__main__":
    bot = CourseworkBot()
    bot.run()