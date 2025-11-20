import os
import logging
import sqlite3
import re
import asyncio
from datetime import datetime
from enum import Enum

import requests
import PyPDF2
import docx2txt
import aiofiles
from docx import Document
from docx.shared import Inches

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

class WorkType(Enum):
    COURSEWORK = "coursework"
    ESSAY = "essay" 
    THESIS = "thesis"

class WorkStage(Enum):
    TOPIC = "topic"
    METHODIC = "methodic"
    STRUCTURE = "structure"
    CONTENT = "content"
    COMPLETE = "complete"

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
                methodic_id INTEGER,
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
    
    def create_work(self, user_id, work_type, topic, subject, methodic_id=None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO works (user_id, work_type, topic, subject, methodic_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, work_type, topic, subject, methodic_id))
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
            WorkType.COURSEWORK.value: "курсовой работы",
            WorkType.ESSAY.value: "реферата", 
            WorkType.THESIS.value: "дипломной работы"
        }
        
        system_prompt = f"""
Ты - эксперт по созданию академических работ. Создай подробную структуру для {work_type_names[work_type]} на тему "{topic}" по предмету "{subject}".

{"УЧТИ ТРЕБОВАНИЯ МЕТОДИЧКИ: " + str(methodic_info) if methodic_info else ""}

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
        
        return self._make_api_call(system_prompt, "Создаю структуру работы...")
    
    def generate_section(self, work_type, topic, subject, section_name, section_guidance, methodic_info=None, previous_content=""):
        """Генерирует содержание раздела"""
        
        system_prompt = f"""
Ты - профессиональный академический писатель. Напиши раздел "{section_name}" для {work_type} на тему "{topic}" по предмету "{subject}".

РУКОВОДСТВО ПО РАЗДЕЛУ: {section_guidance}

{"ТРЕБОВАНИЯ МЕТОДИЧКИ: " + str(methodic_info) if methodic_info else ""}

{"ПРЕДЫДУЩЕЕ СОДЕРЖАНИЕ: " + previous_content if previous_content else ""}

Напиши полноценный, качественный академический текст:
- Используй научный стиль
- Приводи конкретные примеры и данные
- Соблюдай логическую последовательность
- Объем: {self._get_section_volume(work_type, section_name)}
- Используй подзаголовки если необходимо

Верни только чистый текст раздела без комментариев.
"""
        
        return self._make_api_call(system_prompt, f"Пишу раздел '{section_name}'...")
    
    def generate_full_work(self, work_type, topic, subject, structure, methodic_info=None):
        """Генерирует полный текст работы"""
        
        system_prompt = f"""
Ты - профессиональный академический писатель. Напиши полный текст {work_type} на тему "{topic}" по предмету "{subject}".

СТРУКТУРА РАБОТЫ:
{structure}

{"ТРЕБОВАНИЯ МЕТОДИЧКИ: " + str(methodic_info) if methodic_info else ""}

Требования к работе:
1. Академический стиль изложения
2. Глубокое раскрытие темы
3. Научная обоснованность
4. Логическая последовательность
5. Соответствие структуре
6. Объем: {self._get_work_volume(work_type)}

Напиши полноценную готовую к сдаче работу включая все разделы.
"""
        
        return self._make_api_call(system_prompt, "Пишу полный текст работы...")
    
    def _get_work_volume(self, work_type):
        volumes = {
            WorkType.ESSAY.value: "15-25 страниц",
            WorkType.COURSEWORK.value: "30-50 страниц", 
            WorkType.THESIS.value: "60-100 страниц"
        }
        return volumes.get(work_type, "20-40 страниц")
    
    def _get_section_volume(self, work_type, section_name):
        base_volumes = {
            WorkType.ESSAY.value: {"введение": "2-3 стр", "основная часть": "10-15 стр", "заключение": "2-3 стр"},
            WorkType.COURSEWORK.value: {"введение": "3-5 стр", "основная часть": "20-35 стр", "заключение": "3-5 стр"},
            WorkType.THESIS.value: {"введение": "5-8 стр", "основная часть": "45-80 стр", "заключение": "5-8 стр"}
        }
        volume_info = base_volumes.get(work_type, {})
        return volume_info.get(section_name.lower(), "5-10 страниц")
    
    def _make_api_call(self, system_prompt, user_prompt):
        if not self.api_key:
            return "❌ API ключ не настроен"
        
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
            response = requests.post(self.api_url, headers=headers, json=data, timeout=60)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']
        except Exception as e:
            logger.error(f"API error: {e}")
            return f"❌ Ошибка генерации: {str(e)}"

class DocxGenerator:
    def create_document(self, work_type, topic, subject, content, filename):
        """Создает DOCX документ с работой"""
        try:
            doc = Document()
            
            # Титульный лист
            title = doc.add_heading(f'{self._get_work_type_name(work_type)}', 0)
            title.alignment = 1
            
            doc.add_heading(f'по предмету: "{subject}"', 1).alignment = 1
            doc.add_heading(f'на тему: "{topic}"', 1).alignment = 1
            doc.add_page_break()
            
            # Содержание
            doc.add_heading('СОДЕРЖАНИЕ', level=1)
            doc.add_paragraph("Введение")
            doc.add_paragraph("Основная часть") 
            doc.add_paragraph("Заключение")
            doc.add_paragraph("Список литературы")
            doc.add_page_break()
            
            # Основной текст
            paragraphs = content.split('\n\n')
            for paragraph in paragraphs:
                if paragraph.strip():
                    if any(keyword in paragraph.lower() for keyword in ['введение', 'глава', 'заключение', 'литература']):
                        doc.add_heading(paragraph, level=1)
                    else:
                        doc.add_paragraph(paragraph)
            
            # Сохраняем файл
            filepath = os.path.join("работы", filename)
            doc.save(filepath)
            return filepath
            
        except Exception as e:
            logger.error(f"DOCX error: {e}")
            return None
    
    def _get_work_type_name(self, work_type):
        names = {
            WorkType.ESSAY.value: "РЕФЕРАТ",
            WorkType.COURSEWORK.value: "КУРСОВАЯ РАБОТА",
            WorkType.THESIS.value: "ДИПЛОМНАЯ РАБОТА"
        }
        return names.get(work_type, "АКАДЕМИЧЕСКАЯ РАБОТА")

class CourseworkBot:
    def __init__(self):
        self.db = Database()
        self.doc_processor = DocumentProcessor()
        self.writer = AcademicWriter()
        self.docx_generator = DocxGenerator()
        self.user_sessions = {}
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name, user.last_name)
        
        welcome_text = f"""🎓 <b>Академический помощник</b>

Привет, {user.first_name}! Я напишу для тебя полноценную академическую работу.

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
                'stage': WorkStage.TOPIC.value
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
        
        if current_stage == WorkStage.TOPIC.value:
            # Получили предмет, запрашиваем тему
            session['subject'] = user_message
            session['stage'] = WorkStage.METHODIC.value
            self.user_sessions[user_id] = session
            
            methodics = self.db.get_methodics()
            if methodics:
                keyboard = []
                for methodic_id, filename in methodics:
                    display_name = filename[:30] + "..." if len(filename) > 30 else filename
                    keyboard.append([InlineKeyboardButton(f"📄 {display_name}", callback_data=f"methodic_{methodic_id}")])
                keyboard.append([InlineKeyboardButton("🚫 Без методички", callback_data="no_methodic")])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    f"📚 Предмет: <b>{user_message}</b>\n\nТеперь введите тему работы:",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text(
                    f"📚 Предмет: <b>{user_message}</b>\n\nТеперь введите тему работы:",
                    parse_mode='HTML'
                )
        
        elif current_stage == WorkStage.METHODIC.value:
            # Получили тему, переходим к генерации структуры
            session['topic'] = user_message
            session['stage'] = WorkStage.STRUCTURE.value
            self.user_sessions[user_id] = session
            
            # Создаем запись в БД
            work_id = self.db.create_work(
                user_id=user_id,
                work_type=session['work_type'],
                topic=session['topic'],
                subject=session['subject'],
                methodic_id=session.get('methodic_id')
            )
            session['work_id'] = work_id
            self.user_sessions[user_id] = session
            
            await self.generate_structure(update, session)
        
        elif current_stage == WorkStage.CONTENT.value:
            # Обработка дополнительных запросов по содержанию
            await update.message.reply_text("⏳ Обрабатываю ваш запрос...")
    
    async def generate_structure(self, update, session):
        """Генерирует структуру работы"""
        user_id = session['user_id'] if 'user_id' in session else update.effective_user.id
        
        methodic_info = None
        if session.get('methodic_id'):
            methodic_data = self.db.get_methodic(session['methodic_id'])
            if methodic_data:
                methodic_info = {
                    'requirements': methodic_data[3],
                    'structure': methodic_data[4],
                    'formatting': methodic_data[5]
                }
        
        generating_msg = await update.message.reply_text("🔄 Создаю структуру работы...")
        
        structure = self.writer.generate_structure(
            work_type=session['work_type'],
            topic=session['topic'],
            subject=session['subject'],
            methodic_info=methodic_info
        )
        
        if structure.startswith("❌"):
            await generating_msg.edit_text(structure)
            return
        
        # Сохраняем структуру
        self.db.update_work_structure(session['work_id'], structure)
        
        keyboard = [
            [InlineKeyboardButton("✅ Сгенерировать полный текст", callback_data="generate_full")],
            [InlineKeyboardButton("🔄 Изменить структуру", callback_data="regenerate_structure")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await generating_msg.edit_text(
            f"📋 <b>Структура работы готова!</b>\n\n"
            f"{structure}\n\n"
            f"Хотите сгенерировать полный текст работы?",
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
            session['methodic_id'] = None
            self.user_sessions[user_id] = session
            await query.edit_message_text("📝 Введите тему работы:")
        elif data.startswith('methodic_'):
            methodic_id = int(data.split('_')[1])
            session['methodic_id'] = methodic_id
            self.user_sessions[user_id] = session
            await query.edit_message_text("📝 Введите тему работы:")
    
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
        
        if data == 'generate_full':
            await self.generate_full_work(query, session)
        elif data == 'regenerate_structure':
            await self.generate_structure(query, session)
    
    async def generate_full_work(self, query, session):
        """Генерирует полный текст работы"""
        generating_msg = await query.message.reply_text("🔄 Пишу полный текст работы...\nЭто может занять несколько минут.")
        
        # Получаем структуру
        work_data = self.db.get_methodic(session['work_id'])  # Временно используем эту функцию
        structure = work_data[3] if work_data else ""
        
        # Получаем информацию о методичке если есть
        methodic_info = None
        if session.get('methodic_id'):
            methodic_data = self.db.get_methodic(session['methodic_id'])
            if methodic_data:
                methodic_info = {
                    'requirements': methodic_data[3],
                    'structure': methodic_data[4],
                    'formatting': methodic_data[5]
                }
        
        # Генерируем полный текст
        full_content = self.writer.generate_full_work(
            work_type=session['work_type'],
            topic=session['topic'],
            subject=session['subject'],
            structure=structure,
            methodic_info=methodic_info
        )
        
        if full_content.startswith("❌"):
            await generating_msg.edit_text(full_content)
            return
        
        # Сохраняем контент
        self.db.update_work_content(session['work_id'], full_content)
        
        # Создаем DOCX файл
        work_names = {
            'coursework': 'курсовая',
            'essay': 'реферат', 
            'thesis': 'диплом'
        }
        filename = f"{work_names[session['work_type']]}_{session['topic'][:20]}.docx"
        docx_path = self.docx_generator.create_document(
            work_type=session['work_type'],
            topic=session['topic'],
            subject=session['subject'],
            content=full_content,
            filename=filename
        )
        
        if docx_path:
            # Отправляем файл пользователю
            with open(docx_path, 'rb') as docx_file:
                await query.message.reply_document(
                    document=docx_file,
                    filename=filename,
                    caption=f"🎉 <b>Ваша работа готова!</b>\n\n"
                           f"📚 Тип: {work_names[session['work_type']]}\n"
                           f"📝 Тема: {session['topic']}\n"
                           f"🔬 Предмет: {session['subject']}\n\n"
                           f"Файл готов к сдаче!",
                    parse_mode='HTML'
                )
            await generating_msg.delete()
        else:
            # Если не удалось создать DOCX, отправляем текстом
            await generating_msg.edit_text(
                f"🎉 <b>Работа готова!</b>\n\n"
                f"{full_content[:1000]}...\n\n"
                f"<i>Полный текст сохранен в базе данных</i>",
                parse_mode='HTML'
            )
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}")
    
    def run(self):
        if not BOT_TOKEN:
            logger.error("❌ BOT_TOKEN не найден!")
            return
        
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Обработчики
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CallbackQueryHandler(self.handle_button, pattern="^(work_|upload_methodic)"))
        application.add_handler(CallbackQueryHandler(self.handle_methodic_selection, pattern="^(methodic_|no_methodic)"))
        application.add_handler(CallbackQueryHandler(self.handle_generation_requests, pattern="^(generate_full|regenerate_structure)"))
        application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        application.add_error_handler(self.error_handler)
        
        logger.info("🤖 Бот-писатель запущен!")
        application.run_polling()

if __name__ == "__main__":
    bot = CourseworkBot()
    bot.run()