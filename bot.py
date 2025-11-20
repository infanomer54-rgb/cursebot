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
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

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
        # Извлекаем требования к оформлению
        font_info = self._extract_font_info(text)
        spacing_info = self._extract_spacing_info(text)
        margins_info = self._extract_margins_info(text)
        structure_info = self._extract_structure_info(text)
        requirements_info = self._extract_requirements(text)
        
        return {
            'font': font_info,
            'spacing': spacing_info,
            'margins': margins_info,
            'structure': structure_info,
            'requirements': requirements_info,
            'full_text': text[:4000]
        }
    
    def _extract_font_info(self, text):
        patterns = {
            'font_family': r'шрифт[:\s]*([^\n,\d]+)',
            'font_size': r'шрифт[:\s]*(\d+)',
            'font_size_pt': r'(\d+)[\s]*пт',
            'times_new_roman': r'Times New Roman|times new roman',
            'arial': r'Arial|arial'
        }
        
        font_info = {}
        for key, pattern in patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                font_info[key] = matches[0] if key == 'font_size' else matches
        
        # Устанавливаем значения по умолчанию если не найдены
        if not font_info.get('font_family'):
            font_info['font_family'] = ['Times New Roman']
        if not font_info.get('font_size'):
            font_info['font_size'] = '14'
        
        return font_info
    
    def _extract_spacing_info(self, text):
        patterns = {
            'line_spacing': r'интервал[:\s]*([^\n]+)',
            'spacing_1_5': r'[\s\.\d]1[,\.]5|полуторный',
            'spacing_1_0': r'[\s\.\d]1[,\.]0|одинарный'
        }
        
        spacing_info = {}
        for key, pattern in patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                spacing_info[key] = matches
        
        if not spacing_info.get('line_spacing'):
            spacing_info['line_spacing'] = ['1.5']
        
        return spacing_info
    
    def _extract_margins_info(self, text):
        patterns = {
            'margins': r'поля[:\s]*([^\n]+)',
            'margin_left': r'левое[:\s]*(\d+)',
            'margin_right': r'правое[:\s]*(\d+)',
            'margin_top': r'верхнее[:\s]*(\d+)',
            'margin_bottom': r'нижнее[:\s]*(\d+)'
        }
        
        margins_info = {}
        for key, pattern in patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                margins_info[key] = matches[0] if key.startswith('margin_') else matches
        
        # Значения по умолчанию для полей (в см)
        if not margins_info.get('margin_left'):
            margins_info['margin_left'] = '3'
        if not margins_info.get('margin_right'):
            margins_info['margin_right'] = '1'
        if not margins_info.get('margin_top'):
            margins_info['margin_top'] = '2'
        if not margins_info.get('margin_bottom'):
            margins_info['margin_bottom'] = '2'
        
        return margins_info
    
    def _extract_structure_info(self, text):
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
    
    def _extract_requirements(self, text):
        patterns = {
            'volume': r'объем[:\s]*([^\n]+)',
            'pages': r'страниц[:\s]*(\d+)',
            'deadline': r'срок[:\s]*([^\n]+)',
            'sections_count': r'раздел[ов]*[:\s]*(\d+)'
        }
        
        requirements = {}
        for key, pattern in patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                requirements[key] = matches[0] if key in ['pages', 'sections_count'] else matches
        
        return requirements

class WordDocumentGenerator:
    def __init__(self):
        self.doc = None
    
    def create_document(self, work_type, topic, subject, content, methodic_info, user_info=None):
        """Создает Word документ согласно требованиям методички"""
        try:
            self.doc = Document()
            
            # Применяем настройки из методички
            self._apply_formatting(methodic_info)
            
            # Создаем титульный лист
            self._create_title_page(work_type, topic, subject, user_info)
            
            # Добавляем содержание
            self._create_table_of_contents()
            
            # Добавляем основной текст
            self._add_main_content(content, methodic_info)
            
            # Добавляем список литературы
            self._add_bibliography()
            
            # Сохраняем в bytes для отправки
            file_stream = io.BytesIO()
            self.doc.save(file_stream)
            file_stream.seek(0)
            
            return file_stream
            
        except Exception as e:
            logger.error(f"Error creating Word document: {e}")
            return None
    
    def _apply_formatting(self, methodic_info):
        """Применяет форматирование из методички"""
        try:
            # Настройка шрифта для всего документа
            font_info = methodic_info.get('font', {})
            font_family = font_info.get('font_family', ['Times New Roman'])[0]
            font_size = int(font_info.get('font_size', '14'))
            
            # Настройка стилей
            style = self.doc.styles['Normal']
            font = style.font
            font.name = font_family
            font.size = Pt(font_size)
            
            # Настройка межстрочного интервала
            spacing_info = methodic_info.get('spacing', {})
            if spacing_info.get('spacing_1_5'):
                paragraph_format = style.paragraph_format
                paragraph_format.line_spacing = 1.5
            elif spacing_info.get('spacing_1_0'):
                paragraph_format = style.paragraph_format
                paragraph_format.line_spacing = 1.0
            
            # Настройка полей
            margins_info = methodic_info.get('margins', {})
            sections = self.doc.sections
            for section in sections:
                # Конвертируем см в дюймы (1 см = 0.393701 дюйма)
                section.left_margin = Inches(float(margins_info.get('margin_left', 3)) * 0.393701)
                section.right_margin = Inches(float(margins_info.get('margin_right', 1)) * 0.393701)
                section.top_margin = Inches(float(margins_info.get('margin_top', 2)) * 0.393701)
                section.bottom_margin = Inches(float(margins_info.get('margin_bottom', 2)) * 0.393701)
                
        except Exception as e:
            logger.error(f"Error applying formatting: {e}")
    
    def _create_title_page(self, work_type, topic, subject, user_info=None):
        """Создает титульный лист"""
        try:
            # Название работы
            work_type_names = {
                "coursework": "КУРСОВАЯ РАБОТА",
                "essay": "РЕФЕРАТ",
                "thesis": "ДИПЛОМНАЯ РАБОТА"
            }
            
            title = work_type_names.get(work_type, "АКАДЕМИЧЕСКАЯ РАБОТА")
            
            # Заголовок
            title_paragraph = self.doc.add_heading(title, 0)
            title_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            title_paragraph.paragraph_format.space_after = Pt(24)
            
            # Предмет
            subject_paragraph = self.doc.add_paragraph()
            subject_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            subject_run = subject_paragraph.add_run(f"по дисциплине: {subject}")
            subject_run.bold = True
            subject_paragraph.paragraph_format.space_after = Pt(18)
            
            # Тема
            topic_paragraph = self.doc.add_paragraph()
            topic_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            topic_run = topic_paragraph.add_run(f"на тему: \"{topic}\"")
            topic_run.bold = True
            topic_paragraph.paragraph_format.space_after = Pt(36)
            
            # Информация о студенте (если есть)
            if user_info:
                student_paragraph = self.doc.add_paragraph()
                student_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                student_paragraph.add_run(f"Выполнил(а): {user_info}")
                student_paragraph.paragraph_format.space_after = Pt(12)
            
            # Год
            year_paragraph = self.doc.add_paragraph()
            year_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            year_paragraph.add_run(f"{datetime.now().year} г.")
            year_paragraph.paragraph_format.space_after = Pt(36)
            
            # Разрыв страницы
            self.doc.add_page_break()
            
        except Exception as e:
            logger.error(f"Error creating title page: {e}")
    
    def _create_table_of_contents(self):
        """Создает оглавление"""
        try:
            toc_heading = self.doc.add_heading('СОДЕРЖАНИЕ', level=1)
            toc_heading.paragraph_format.space_after = Pt(12)
            
            # Здесь можно добавить автоматическое оглавление
            # Для простоты добавляем базовую структуру
            contents = [
                "Введение",
                "Основная часть",
                "Заключение", 
                "Список литературы"
            ]
            
            for content in contents:
                paragraph = self.doc.add_paragraph()
                paragraph.add_run(content)
                paragraph.paragraph_format.space_after = Pt(6)
            
            self.doc.add_page_break()
            
        except Exception as e:
            logger.error(f"Error creating table of contents: {e}")
    
    def _add_main_content(self, content, methodic_info):
        """Добавляет основной текст работы"""
        try:
            # Разбиваем контент на разделы
            sections = self._split_into_sections(content)
            
            for i, section in enumerate(sections):
                if i == 0:  # Введение
                    heading = self.doc.add_heading('ВВЕДЕНИЕ', level=1)
                elif i == len(sections) - 1:  # Заключение
                    heading = self.doc.add_heading('ЗАКЛЮЧЕНИЕ', level=1)
                else:  # Основная часть
                    heading = self.doc.add_heading(f'ГЛАВА {i}', level=1)
                
                heading.paragraph_format.space_after = Pt(12)
                
                # Добавляем текст раздела
                paragraphs = section.split('\n\n')
                for para in paragraphs:
                    if para.strip():
                        paragraph = self.doc.add_paragraph(para.strip())
                        paragraph.paragraph_format.space_after = Pt(6)
                        paragraph.paragraph_format.first_line_indent = Inches(0.5)  # Красная строка
            
        except Exception as e:
            logger.error(f"Error adding main content: {e}")
    
    def _split_into_sections(self, content):
        """Разбивает текст на разделы"""
        # Простая логика разбиения по ключевым словам
        sections = []
        current_section = []
        
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Проверяем, является ли строка заголовком раздела
            if any(keyword in line.lower() for keyword in ['введение', 'глава', 'заключение', 'вывод']):
                if current_section:
                    sections.append('\n'.join(current_section))
                    current_section = []
            
            current_section.append(line)
        
        if current_section:
            sections.append('\n'.join(current_section))
        
        return sections if sections else [content]
    
    def _add_bibliography(self):
        """Добавляет список литературы"""
        try:
            self.doc.add_page_break()
            heading = self.doc.add_heading('СПИСОК ЛИТЕРАТУРЫ', level=1)
            heading.paragraph_format.space_after = Pt(12)
            
            # Базовый список литературы
            bibliography = [
                "1. Пример источника 1",
                "2. Пример источника 2", 
                "3. Пример источника 3"
            ]
            
            for item in bibliography:
                paragraph = self.doc.add_paragraph(item)
                paragraph.paragraph_format.space_after = Pt(6)
                paragraph.paragraph_format.first_line_indent = Inches(-0.3)  # Висячий отступ
                paragraph.paragraph_format.left_indent = Inches(0.3)
                
        except Exception as e:
            logger.error(f"Error adding bibliography: {e}")

class AcademicWriter:
    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.api_url = DEEPSEEK_API_URL
    
    def generate_complete_work(self, work_type, topic, subject, methodic_info=None):
        """Генерирует полную работу включая структуру и содержание"""
        
        work_type_names = {
            "coursework": "курсовой работы",
            "essay": "реферата", 
            "thesis": "дипломной работы"
        }
        
        methodic_text = ""
        if methodic_info:
            methodic_text = f"""
ТРЕБОВАНИЯ МЕТОДИЧКИ ДЛЯ ОФОРМЛЕНИЯ:
- Шрифт: {methodic_info['font'].get('font_family', ['Times New Roman'])[0]}
- Размер шрифта: {methodic_info['font'].get('font_size', '14')} пт
- Межстрочный интервал: {methodic_info['spacing'].get('line_spacing', ['1.5'])[0]}
- Поля: левое {methodic_info['margins'].get('margin_left', '3')} см, правое {methodic_info['margins'].get('margin_right', '1')} см

ТРЕБОВАНИЯ К СОДЕРЖАНИЮ:
{methodic_info.get('requirements', {})}
"""
        
        system_prompt = f"""
Ты - профессиональный академический писатель. Напиши ПОЛНЫЙ ТЕКСТ {work_type} на тему "{topic}" по предмету "{subject}".

{methodic_text}

СТРУКТУРА РАБОТЫ ДОЛЖНА ВКЛЮЧАТЬ:
1. Титульный лист
2. Содержание/оглавление  
3. Введение (актуальность, цели, задачи, методы исследования)
4. Основную часть (2-3 главы с теоретическим и практическим анализом)
5. Заключение (выводы, результаты, рекомендации)
6. Список литературы (10-15 источников)

ТРЕБОВАНИЯ К СОДЕРЖАНИЮ:
- Академический стиль изложения
- Глубокое раскрытие темы
- Научная обоснованность
- Логическая последовательность
- Конкретные примеры, данные, исследования
- Объем: {self._get_work_volume(work_type)}
- Уникальность и оригинальность

Верни ПОЛНЫЙ ТЕКСТ работы включая все разделы. Текст должен быть готов для оформления в Word документ.
"""
        
        return self._make_api_call(system_prompt, f"Напиши полный текст {work_type_names[work_type]} на тему '{topic}'")
    
    def _get_work_volume(self, work_type):
        volumes = {
            "essay": "15-25 страниц (3000-5000 слов)",
            "coursework": "30-50 страниц (6000-10000 слов)", 
            "thesis": "60-100 страниц (12000-20000 слов)"
        }
        return volumes.get(work_type, "20-40 страниц")
    
    def _make_api_call(self, system_prompt, user_prompt):
        if not self.api_key:
            return "❌ Ошибка: API ключ DeepSeek не настроен"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # Увеличиваем лимит токенов для получения полного текста
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 8000  # Увеличиваем для получения полного текста
        }
        
        try:
            logger.info("Отправка запроса к DeepSeek API...")
            response = requests.post(self.api_url, headers=headers, json=data, timeout=180)
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
        self.doc_generator = WordDocumentGenerator()
        self.user_sessions = {}
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name, user.last_name)
        
        welcome_text = f"""🎓 <b>Академический помощник - Автописатель</b>

Привет, {user.first_name}! Я напишу для тебя полноценную академическую работу с нуля и сразу отправлю готовый Word документ.

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
                "Методичка поможет мне оформить работу по требованиям вашего учебного заведения."
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
                    f"🎯 Тема: <b>{user_message}</b>\n\nВыберите методичку для оформления:",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                # Если нет методичек, сразу начинаем генерацию
                await self.start_work_generation(update, session, None)
    
    async def start_work_generation(self, update, session, methodic_info):
        """Начинает процесс генерации работы"""
        # Определяем user_id в зависимости от типа update
        if hasattr(update, 'effective_user'):
            user_id = update.effective_user.id
        else:
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
        
        # Сразу начинаем генерацию полной работы
        await self.generate_complete_work(update, session)
    
    async def generate_complete_work(self, update, session):
        """Генерирует полную работу и создает Word документ"""
        # Определяем объект сообщения в зависимости от типа update
        if hasattr(update, 'message'):
            message_obj = update.message
        else:
            message_obj = update
        
        # Отправляем сообщение о начале генерации
        progress_msg = await message_obj.reply_text(
            "🔄 <b>Начинаю создание работы...</b>\n\n"
            "📝 Генерирую содержание...\n"
            "⏳ Это займет 3-5 минут\n"
            "📄 Результат будет в Word документе",
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
        
        # Обновляем прогресс
        await progress_msg.edit_text(
            "🔄 <b>Работа написана! Оформляю в Word...</b>\n\n"
            "🎨 Применяю форматирование по методичке\n"
            "📑 Создаю титульный лист и содержание\n"
            "⏳ Еще немного...",
            parse_mode='HTML'
        )
        
        # Сохраняем контент в БД
        self.db.update_work_content(session['work_id'], full_content)
        
        # Создаем Word документ
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
            await progress_msg.edit_text("❌ Ошибка при создании Word документа")
            return
        
        # Отправляем документ пользователю
        work_names = {
            'coursework': 'Курсовая работа',
            'essay': 'Реферат', 
            'thesis': 'Дипломная работа'
        }
        
        filename = f"{work_names[session['work_type']]} - {session['topic'][:30]}.docx"
        
        await message_obj.reply_document(
            document=doc_stream,
            filename=filename,
            caption=(
                f"🎉 <b>{work_names[session['work_type']]} ГОТОВА!</b>\n\n"
                f"📚 Тема: {session['topic']}\n"
                f"🔬 Предмет: {session['subject']}\n"
                f"📄 Формат: Word документ\n"
                f"🎨 Оформление: {'по методичке' if methodic_info else 'стандартное'}\n"
                f"📏 Объем: ~{len(full_content.split())} слов\n\n"
                f"<i>✅ Документ готов к сдаче!</i>"
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
            "✨ <b>Отлично! Работа завершена!</b>\n\n"
            "Вы можете начать новую работу или использовать /start для выбора другого типа работы.",
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
                    'font': json.loads(methodic_data[5]).get('font', {}) if methodic_data[5] else {},
                    'spacing': json.loads(methodic_data[5]).get('spacing', {}) if methodic_data[5] else {},
                    'margins': json.loads(methodic_data[5]).get('margins', {}) if methodic_data[5] else {}
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
                f"📋 Настройки оформления:\n"
                f"• Шрифт: {methodic_info['font'].get('font_family', ['Times New Roman'])[0]}\n"
                f"• Размер: {methodic_info['font'].get('font_size', '14')} пт\n"
                f"• Интервал: {methodic_info['spacing'].get('line_spacing', ['1.5'])[0]}\n\n"
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
        application.add_handler(CallbackQueryHandler(self.handle_new_work, pattern="^new_work$"))
        application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        application.add_error_handler(self.error_handler)
        
        logger.info("🤖 Бот-писатель с Word оформлением запущен!")
        print("=" * 60)
        print("🎓 Academic Auto-Writer Bot with Word Formatting Started!")
        print("📚 Автоматическое написание и оформление работ в Word")
        print("⚡ Прямая генерация в Word без промежуточных сообщений")
        print("📄 Поддержка методичек для точного оформления")
        print("=" * 60)
        
        application.run_polling()

if __name__ == "__main__":
    bot = CourseworkBot()
    bot.run()