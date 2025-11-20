import os
import logging
import sqlite3
import re
import asyncio
from datetime import datetime
import json
import io

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
        requirements = self._extract_requirements(text)
        structure = self._extract_structure(text)
        
        return {
            'requirements': requirements,
            'structure': structure,
            'full_text': text[:4000]
        }
    
    def _extract_requirements(self, text):
        patterns = {
            'volume': r'объем[:\s]*([^\n]+)',
            'pages': r'страниц[:\s]*(\d+)',
            'deadline': r'срок[:\s]*([^\n]+)',
            'sections_count': r'раздел[ов]*[:\s]*(\d+)',
            'font': r'шрифт[:\s]*([^\n]+)',
            'spacing': r'интервал[:\s]*([^\n]+)',
            'margins': r'поля[:\s]*([^\n]+)'
        }
        
        requirements = {}
        for key, pattern in patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                requirements[key] = matches[0] if key in ['pages', 'sections_count'] else matches
        
        return requirements
    
    def _extract_structure(self, text):
        patterns = {
            'sections': r'структур[а-яё]*[:\s]*([^\n]+)',
            'introduction': r'введен[а-яё]*[:\s]*([^\n]+)',
            'chapters': r'глава|раздел[:\s]*([^\n]+)',
            'conclusion': r'заключен[а-яё]*[:\s]*([^\n]+)',
            'bibliography': r'литератур[а-яё]*[:\s]*([^\n]+)'
        }
        
        structure_info = {}
        for key, pattern in patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                structure_info[key] = matches
        
        return structure_info

class TextDocumentGenerator:
    def create_document(self, work_type, topic, subject, content, methodic_info, user_info=None):
        """Создает чистый текстовый документ без лишних символов"""
        try:
            # Очищаем контент от лишних символов
            clean_content = self._clean_content(content)
            
            # Создаем структурированный документ
            document_text = self._create_document_structure(work_type, topic, subject, clean_content, user_info)
            
            # Сохраняем в bytes для отправки
            file_stream = io.BytesIO()
            file_stream.write(document_text.encode('utf-8'))
            file_stream.seek(0)
            
            return file_stream
            
        except Exception as e:
            logger.error(f"Error creating text document: {e}")
            return None
    
    def _clean_content(self, content):
        """Очищает контент от лишних символов форматирования"""
        # Убираем HTML теги
        clean = re.sub(r'<[^>]+>', '', content)
        # Убираем маркдаун символы
        clean = re.sub(r'[*_~`#]', '', clean)
        # Убираем лишние переносы строк
        clean = re.sub(r'\n\s*\n', '\n\n', clean)
        # Убираем лишние пробелы
        clean = re.sub(r' +', ' ', clean)
        # Убираем специфические символы
        clean = re.sub(r'[➤•▪▶]', '', clean)
        return clean.strip()
    
    def _create_document_structure(self, work_type, topic, subject, content, user_info):
        """Создает структурированный текст документа"""
        
        work_type_names = {
            "coursework": "КУРСОВАЯ РАБОТА",
            "essay": "РЕФЕРАТ",
            "thesis": "ДИПЛОМНАЯ РАБОТА"
        }
        
        title = work_type_names.get(work_type, "АКАДЕМИЧЕСКАЯ РАБОТА")
        
        # Создаем документ с правильным форматированием
        document_lines = []
        
        # Титульный лист
        document_lines.append(" " * 20 + "=" * 40)
        document_lines.append(" " * 30 + title)
        document_lines.append("")
        document_lines.append(" " * 25 + f"по дисциплине: {subject}")
        document_lines.append("")
        document_lines.append(" " * 20 + f'на тему: "{topic}"')
        document_lines.append("")
        if user_info:
            document_lines.append(" " * 25 + f"Выполнил(а): {user_info}")
        document_lines.append("")
        document_lines.append(" " * 35 + f"{datetime.now().year} г.")
        document_lines.append(" " * 20 + "=" * 40)
        document_lines.append("\n" * 5)
        
        # Добавляем основной текст (уже очищенный)
        document_lines.append(content)
        
        return "\n".join(document_lines)

class AcademicWriter:
    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.api_url = DEEPSEEK_API_URL
    
    def generate_complete_work(self, work_type, topic, subject, methodic_info=None):
        """Генерирует полную работу с улучшенным качеством"""
        
        work_type_names = {
            "coursework": "курсовой работы",
            "essay": "реферата", 
            "thesis": "дипломной работы"
        }
        
        # Создаем очень детальный системный промпт
        system_prompt = self._create_detailed_system_prompt(work_type, topic, subject, methodic_info)
        
        # Разбиваем на части для лучшего качества
        work_parts = []
        
        # Часть 1: Введение
        intro_prompt = f"""
Напиши ВВЕДЕНИЕ для {work_type_names[work_type]} на тему "{topic}" по предмету "{subject}".

Введение должно содержать:
1. Актуальность темы - почему эта тема важна в современных условиях
2. Цель работы - какую главную цель преследует работа
3. Задачи исследования - конкретные задачи для достижения цели
4. Объект и предмет исследования
5. Методы исследования
6. Теоретическая и практическая значимость

Объем: {self._get_section_volume(work_type, 'введение')}
Стиль: естественный, академический, но не слишком формальный
"""
        intro = self._make_api_call(system_prompt, intro_prompt)
        if not intro.startswith("❌"):
            work_parts.append(f"ВВЕДЕНИЕ\n\n{intro}\n")
        
        # Часть 2: Основная часть
        main_part_prompt = f"""
Напиши ОСНОВНУЮ ЧАСТЬ для {work_type_names[work_type]} на тему "{topic}" по предмету "{subject}".

Основная часть должна включать:
ГЛАВА 1. ТЕОРЕТИЧЕСКИЕ ОСНОВЫ ИССЛЕДОВАНИЯ
- Анализ существующих исследований по теме
- Теоретические концепции и подходы
- Определение ключевых понятий

ГЛАВА 2. ПРАКТИЧЕСКОЕ ИССЛЕДОВАНИЕ
- Методология исследования
- Анализ данных или примеров
- Результаты исследования

ГЛАВА 3. АНАЛИЗ И ВЫВОДЫ
- Интерпретация полученных результатов
- Сравнение с существующими исследованиями
- Предварительные выводы

Объем: {self._get_section_volume(work_type, 'основная часть')}
Стиль: естественный, с конкретными примерами и анализом
"""
        main_part = self._make_api_call(system_prompt, main_part_prompt)
        if not main_part.startswith("❌"):
            work_parts.append(f"ОСНОВНАЯ ЧАСТЬ\n\n{main_part}\n")
        
        # Часть 3: Заключение
        conclusion_prompt = f"""
Напиши ЗАКЛЮЧЕНИЕ для {work_type_names[work_type]} на тему "{topic}" по предмету "{subject}".

Заключение должно содержать:
1. Основные выводы по работе
2. Достигнута ли цель исследования
3. Решены ли поставленные задачи
4. Практическая значимость работы
5. Перспективы дальнейшего исследования
6. Рекомендации

Объем: {self._get_section_volume(work_type, 'заключение')}
Стиль: итоговый, с четкими выводами
"""
        conclusion = self._make_api_call(system_prompt, conclusion_prompt)
        if not conclusion.startswith("❌"):
            work_parts.append(f"ЗАКЛЮЧЕНИЕ\n\n{conclusion}\n")
        
        # Часть 4: Список литературы
        bibliography_prompt = f"""
Составь СПИСОК ЛИТЕРАТУРЫ для {work_type_names[work_type]} на тему "{topic}" по предмету "{subject}".

Включи 10-15 актуальных источников:
- Научные статьи и монографии
- Учебные пособия
- Нормативные документы (если применимо)
- Интернет-ресурсы (при необходимости)

Формат: ГОСТ 7.1-2003
"""
        bibliography = self._make_api_call(system_prompt, bibliography_prompt)
        if not bibliography.startswith("❌"):
            work_parts.append(f"СПИСОК ЛИТЕРАТУРЫ\n\n{bibliography}")
        
        # Объединяем все части
        full_work = "\n\n".join(work_parts)
        
        # Если какая-то часть не сгенерировалась, пробуем сгенерировать полную работу
        if len(full_work.split()) < self._get_min_word_count(work_type):
            full_prompt = f"""
Напиши ПОЛНЫЙ ТЕКСТ {work_type_names[work_type]} на тему "{topic}" по предмету "{subject}".

Требования:
- Естественный стиль, как будто работу пишет студент
- Глубокое раскрытие темы
- Конкретные примеры и анализ
- Логическая структура
- Объем: {self._get_work_volume(work_type)}
- Без лишних форматирующих символов

Структура:
1. Введение
2. Основная часть (2-3 главы)
3. Заключение
4. Список литературы

Верни чистый текст без лишних символов (*, <br>, и т.д.)
"""
            full_work = self._make_api_call(system_prompt, full_prompt)
        
        return full_work
    
    def _create_detailed_system_prompt(self, work_type, topic, subject, methodic_info):
        """Создает детальный системный промпт"""
        
        methodic_text = ""
        if methodic_info:
            methodic_text = f"""
ДОПОЛНИТЕЛЬНЫЕ ТРЕБОВАНИЯ ИЗ МЕТОДИЧКИ:
{methodic_info.get('requirements', {})}
{methodic_info.get('structure', {})}
"""
        
        return f"""
Ты - опытный академический писатель. Твоя задача - написать качественную академическую работу, которая выглядит так, будто её написал студент.

ОСНОВНЫЕ ПРАВИЛА:
1. ЕСТЕСТВЕННЫЙ СТИЛЬ - работа должна выглядеть так, будто её писал студент, а не ИИ
2. ГЛУБОКОЕ РАСКРЫТИЕ ТЕМЫ - подробный анализ, конкретные примеры
3. ЛОГИЧЕСКАЯ СТРУКТУРА - четкое разделение на разделы
4. АКАДЕМИЧЕСКИЙ ЯЗЫК - но без излишней формальности
5. КОНКРЕТИКА - конкретные примеры, данные, исследования
6. ЧИСТЫЙ ТЕКСТ - без лишних символов форматирования (*, <br>, и т.д.)

ТИП РАБОТЫ: {work_type}
ТЕМА: {topic}
ПРЕДМЕТ: {subject}

{methodic_text}

ВАЖНО: 
- Не используй маркдаун разметку
- Не используй HTML теги
- Не используй специальные символы для форматирования
- Пиши естественным, связным текстом
- Соблюдай логическую последовательность
- Используй академическую лексику, но не слишком сложную
"""
    
    def _get_work_volume(self, work_type):
        volumes = {
            "essay": "15-25 страниц (4000-7000 слов)",
            "coursework": "30-50 страниц (8000-12000 слов)", 
            "thesis": "60-100 страниц (15000-25000 слов)"
        }
        return volumes.get(work_type, "20-40 страниц")
    
    def _get_section_volume(self, work_type, section):
        base_volumes = {
            "essay": {"введение": "2-3 страницы", "основная часть": "10-18 страниц", "заключение": "2-3 страницы"},
            "coursework": {"введение": "3-5 страниц", "основная часть": "20-35 страниц", "заключение": "3-5 страниц"},
            "thesis": {"введение": "5-8 страниц", "основная часть": "45-80 страниц", "заключение": "5-8 страниц"}
        }
        volume_info = base_volumes.get(work_type, {})
        return volume_info.get(section.lower(), "5-10 страниц")
    
    def _get_min_word_count(self, work_type):
        word_counts = {
            "essay": 4000,
            "coursework": 8000,
            "thesis": 15000
        }
        return word_counts.get(work_type, 5000)
    
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
            "temperature": 0.8,  # Немного увеличиваем для разнообразия
            "max_tokens": 4000
        }
        
        try:
            logger.info(f"Отправка запроса к DeepSeek API: {user_prompt[:100]}...")
            response = requests.post(self.api_url, headers=headers, json=data, timeout=120)
            response.raise_for_status()
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            # Проверяем длину контента
            word_count = len(content.split())
            logger.info(f"Получен ответ: {word_count} слов")
            
            return content
            
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
        self.doc_generator = TextDocumentGenerator()
        self.user_sessions = {}
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name, user.last_name)
        
        welcome_text = f"""🎓 <b>Академический помощник</b>

Привет, {user.first_name}! Я напишу для тебя качественную академическую работу, которая будет выглядеть естественно и содержательно.

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
                "Методичка поможет учесть особые требования вашего учебного заведения."
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
            # Получили тему, предлагаем выбрать методичку
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
                    f"🎯 Тема: <b>{user_message}</b>\n\nВыберите методичку для учета требований:",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                # Если нет методичек, сразу начинаем генерацию
                await self.start_work_generation(update, session, None)
    
    async def start_work_generation(self, update, session, methodic_info):
        """Начинает процесс генерации работы"""
        user_id = update.effective_user.id if hasattr(update, 'effective_user') else update.from_user.id
        
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
        
        # Сразу начинаем генерацию полной работы
        await self.generate_complete_work(update, session)
    
    async def generate_complete_work(self, update, session):
        """Генерирует полную работу и создает документ"""
        message_obj = update.message if hasattr(update, 'message') else update
        
        # Отправляем сообщение о начале генерации
        progress_msg = await message_obj.reply_text(
            "🔄 <b>Начинаю создание качественной работы...</b>\n\n"
            "📝 Пишу введение...\n"
            "⏳ Это займет 5-7 минут\n"
            "✨ Работа будет выглядеть естественно и содержательно",
            parse_mode='HTML'
        )
        
        methodic_info = session.get('methodic_info', {})
        
        # Генерируем полную работу
        full_content = self.writer.generate_complete_work(
            work_type=session['work_type'],
            topic=session['topic'],
            subject=session['subject'],
            methodic_info=methodic_info
        )
        
        if full_content.startswith("❌") or full_content.startswith("⏰"):
            await progress_msg.edit_text(f"❌ Не удалось создать работу: {full_content}")
            return
        
        # Проверяем объем работы
        word_count = len(full_content.split())
        expected_min = self.writer._get_min_word_count(session['work_type'])
        
        if word_count < expected_min * 0.7:  # Если объем меньше 70% от ожидаемого
            await progress_msg.edit_text(
                "⚠️ <b>Объем работы меньше ожидаемого. Дописываю...</b>",
                parse_mode='HTML'
            )
            # Пробуем дополнить работу
            additional_content = self.writer._make_api_call(
                "Дополни работу, чтобы увеличить объем и глубину анализа.",
                f"Дополни следующий текст, увеличив объем до {expected_min} слов: {full_content[:1000]}..."
            )
            if not additional_content.startswith("❌"):
                full_content += "\n\n" + additional_content
        
        # Сохраняем контент в БД
        self.db.update_work_content(session['work_id'], full_content)
        
        # Создаем текстовый документ
        user_info = f"{message_obj.from_user.first_name} {message_obj.from_user.last_name or ''}".strip()
        
        doc_stream = self.doc_generator.create_document(
            work_type=session['work_type'],
            topic=session['topic'],
            subject=session['subject'],
            content=full_content,
            methodic_info=methodic_info,
            user_info=user_info
        )
        
        if not doc_stream:
            await progress_msg.edit_text("❌ Ошибка при создании документа")
            return
        
        # Отправляем документ пользователю
        work_names = {
            'coursework': 'Курсовая работа',
            'essay': 'Реферат', 
            'thesis': 'Дипломная работа'
        }
        
        filename = f"{work_names[session['work_type']]} - {session['topic'][:30]}.txt"
        word_count = len(full_content.split())
        
        await message_obj.reply_document(
            document=doc_stream,
            filename=filename,
            caption=(
                f"🎉 <b>{work_names[session['work_type']]} ГОТОВА!</b>\n\n"
                f"📚 Тема: {session['topic']}\n"
                f"🔬 Предмет: {session['subject']}\n"
                f"📄 Объем: {word_count} слов\n"
                f"🎨 Стиль: естественный студенческий\n"
                f"✅ Качество: полное раскрытие темы\n\n"
                f"<i>Документ готов к использованию!</i>"
            ),
            parse_mode='HTML'
        )
        
        await progress_msg.delete()
        
        # Предлагаем начать новую работу
        keyboard = [
            [InlineKeyboardButton("🔄 Написать новую работу", callback_data="new_work")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message_obj.reply_text(
            "✨ <b>Работа успешно завершена!</b>\n\n"
            f"📊 Статистика: {word_count} слов, полное раскрытие темы\n"
            "🎯 Качество: работа выглядит естественно\n\n"
            "Вы можете начать новую работу:",
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
            await self.start_work_generation(query, session, None)
        elif data.startswith('methodic_'):
            methodic_id = int(data.split('_')[1])
            methodic_data = self.db.get_methodic(methodic_id)
            if methodic_data:
                methodic_info = {
                    'requirements': json.loads(methodic_data[3]) if methodic_data[3] else {},
                    'structure': json.loads(methodic_data[4]) if methodic_data[4] else {},
                }
                session['methodic_info'] = methodic_info
                session['methodic_id'] = methodic_id
                self.user_sessions[user_id] = session
                await self.start_work_generation(query, session, methodic_info)
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
                requirements=json.dumps(methodic_info['requirements']),
                structure=json.dumps(methodic_info['structure']),
                formatting=json.dumps(methodic_info),
                user_id=user_id
            )
            
            await processing_msg.edit_text(
                f"✅ Методичка загружена!\n"
                f"📋 Учтены требования к:\n"
                f"• Структуре работы\n"
                f"• Объему и содержанию\n"
                f"• Оформлению\n\n"
                f"Теперь начните создание работы через /start"
            )
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            await update.message.reply_text("❌ Ошибка загрузки файла")
    
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
        application.add_handler(CallbackQueryHandler(self.handle_new_work, pattern="^new_work$"))
        application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        application.add_error_handler(self.error_handler)
        
        logger.info("🤖 Улучшенный бот-писатель запущен!")
        print("=" * 60)
        print("🎓 Quality Academic Writer Bot Started!")
        print("📚 Генерация качественных работ с естественным стилем")
        print("⚡ Увеличенный объем и улучшенное качество текста")
        print("🎨 Чистый текст без лишних символов")
        print("=" * 60)
        
        application.run_polling()

if __name__ == "__main__":
    bot = CourseworkBot()
    bot.run()