from flask import Flask, request, redirect, url_for, render_template, flash, send_from_directory, jsonify
import os
import sqlite3
from werkzeug.utils import secure_filename
from datetime import datetime
import mimetypes
from config import UPLOAD_FOLDER, ALLOWED_EXTENSIONS, MAX_CONTENT_LENGTH

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = 'supersecretkey'
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Предопределённые разделы
SECTIONS = ["Спецификации", "Чертежи", "Сборки", "Детали", "Документация", "Протоколы"]

# Создание папки хранения при необходимости
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- Инициализация базы данных ---
def init_db():
    conn = sqlite3.connect('pdm.db')
    c = conn.cursor()
    
    # Основная таблица документов
    c.execute('''CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    section TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    file_size INTEGER,
                    description TEXT,
                    version TEXT DEFAULT '1.0',
                    status TEXT DEFAULT 'Активный',
                    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    modified_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )''')
    
    # Таблица проектов
    c.execute('''CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'Активный'
                )''')
    
    # Таблица истории версий
    c.execute('''CREATE TABLE IF NOT EXISTS document_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER,
                    version TEXT,
                    filename TEXT,
                    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    comment TEXT,
                    FOREIGN KEY (document_id) REFERENCES documents (id)
                )''')
    
    conn.commit()
    conn.close()

init_db()

# --- Вспомогательные функции ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_file_size_mb(size_bytes):
    return round(size_bytes / (1024 * 1024), 2)

def get_db_connection():
    conn = sqlite3.connect('pdm.db')
    conn.row_factory = sqlite3.Row
    return conn

# --- Главная страница ---
@app.route('/')
def index():
    conn = get_db_connection()
    
    # Статистика
    stats = {}
    stats['total_projects'] = conn.execute("SELECT COUNT(DISTINCT project) FROM documents").fetchone()[0]
    stats['total_documents'] = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    stats['total_size'] = conn.execute("SELECT SUM(file_size) FROM documents").fetchone()[0] or 0
    
    # Последние загруженные документы
    recent_docs = conn.execute("""
        SELECT project, section, filename, upload_date 
        FROM documents 
        ORDER BY upload_date DESC 
        LIMIT 5
    """).fetchall()
    
    conn.close()
    
    return render_template('index.html', stats=stats, recent_docs=recent_docs)

# --- Страница проектов ---
@app.route('/projects')
def projects():
    search = request.args.get('search', '').strip()
    
    conn = get_db_connection()
    
    if search:
        projects_data = conn.execute("""
            SELECT p.name, p.description, p.created_date, p.status,
                   COUNT(d.id) as doc_count,
                   COALESCE(SUM(d.file_size), 0) as total_size
            FROM projects p
            LEFT JOIN documents d ON p.name = d.project
            WHERE p.name LIKE ? OR p.description LIKE ?
            GROUP BY p.name
            ORDER BY p.created_date DESC
        """, (f'%{search}%', f'%{search}%')).fetchall()
    else:
        projects_data = conn.execute("""
            SELECT p.name, p.description, p.created_date, p.status,
                   COUNT(d.id) as doc_count,
                   COALESCE(SUM(d.file_size), 0) as total_size
            FROM projects p
            LEFT JOIN documents d ON p.name = d.project
            GROUP BY p.name
            ORDER BY p.created_date DESC
        """).fetchall()
    
    conn.close()
    
    return render_template('projects.html', projects=projects_data, search=search)

# --- Страница создания проекта ---
@app.route('/create_project', methods=['GET', 'POST'])
def create_project():
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        
        if not name:
            flash('Название проекта обязательно', 'error')
            return redirect(request.url)
        
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO projects (name, description) VALUES (?, ?)", 
                        (name, description))
            conn.commit()
            flash('Проект успешно создан', 'success')
            return redirect(url_for('projects'))
        except sqlite3.IntegrityError:
            flash('Проект с таким названием уже существует', 'error')
        finally:
            conn.close()
    
    return render_template('create_project.html')

# --- Страница проекта ---
@app.route('/project/<project_name>')
def project_detail(project_name):
    section_filter = request.args.get('section', '')
    
    conn = get_db_connection()
    
    # Информация о проекте
    project = conn.execute("SELECT * FROM projects WHERE name = ?", (project_name,)).fetchone()
    if not project:
        flash('Проект не найден', 'error')
        return redirect(url_for('projects'))
    
    # Документы проекта
    if section_filter:
        documents = conn.execute("""
            SELECT * FROM documents 
            WHERE project = ? AND section = ?
            ORDER BY modified_date DESC
        """, (project_name, section_filter)).fetchall()
    else:
        documents = conn.execute("""
            SELECT * FROM documents 
            WHERE project = ?
            ORDER BY modified_date DESC
        """, (project_name,)).fetchall()
    
    # Статистика по разделам
    section_stats = conn.execute("""
        SELECT section, COUNT(*) as count, COALESCE(SUM(file_size), 0) as size
        FROM documents
        WHERE project = ?
        GROUP BY section
        ORDER BY section
    """, (project_name,)).fetchall()
    
    conn.close()
    
    return render_template('project_detail.html', 
                         project=project, 
                         documents=documents, 
                         sections=SECTIONS,
                         section_stats=section_stats,
                         current_section=section_filter)

# --- Страница документов ---
@app.route('/documents')
def documents():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    search = request.args.get('search', '').strip()
    project_filter = request.args.get('project', '').strip()
    section_filter = request.args.get('section', '').strip()
    
    conn = get_db_connection()
    
    # Базовый запрос
    query = "SELECT * FROM documents WHERE 1=1"
    params = []
    
    if search:
        query += " AND (filename LIKE ? OR description LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%'])
    
    if project_filter:
        query += " AND project = ?"
        params.append(project_filter)
    
    if section_filter:
        query += " AND section = ?"
        params.append(section_filter)
    
    # Подсчет общего количества
    count_query = query.replace("SELECT *", "SELECT COUNT(*)")
    total = conn.execute(count_query, params).fetchone()[0]
    
    # Получение документов с пагинацией
    query += " ORDER BY modified_date DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    
    documents = conn.execute(query, params).fetchall()
    
    # Список проектов для фильтра
    projects = conn.execute("SELECT DISTINCT project FROM documents ORDER BY project").fetchall()
    
    conn.close()
    
    # Вычисление пагинации
    total_pages = (total + per_page - 1) // per_page
    
    return render_template('documents.html', 
                         documents=documents,
                         projects=projects,
                         sections=SECTIONS,
                         current_page=page,
                         total_pages=total_pages,
                         search=search,
                         project_filter=project_filter,
                         section_filter=section_filter)

# --- Загрузка документа ---
@app.route('/upload', methods=['GET', 'POST'])
def upload_document():
    if request.method == 'POST':
        # Получение данных формы
        project = request.form.get('project', '').strip()
        section = request.form.get('section', '').strip()
        description = request.form.get('description', '').strip()
        version = request.form.get('version', '1.0').strip()
        
        if not project or not section:
            flash('Проект и раздел обязательны', 'error')
            return redirect(request.url)
        
        if section not in SECTIONS:
            flash('Недопустимый раздел', 'error')
            return redirect(request.url)
        
        file = request.files.get('file')
        if not file or file.filename == '':
            flash('Файл не выбран', 'error')
            return redirect(request.url)
        
        if not allowed_file(file.filename):
            flash('Разрешены только PDF файлы', 'error')
            return redirect(request.url)
        
        # Сохранение файла
        original_filename = file.filename
        filename = secure_filename(file.filename)
        
        # Добавление версии к имени файла если она не 1.0
        if version != '1.0':
            name, ext = os.path.splitext(filename)
            filename = f"{name}_v{version}{ext}"
        
        folder_path = os.path.join(app.config['UPLOAD_FOLDER'], project, section)
        os.makedirs(folder_path, exist_ok=True)
        filepath = os.path.join(folder_path, filename)
        
        file.save(filepath)
        file_size = os.path.getsize(filepath)
        
        # Сохранение в базу данных
        conn = get_db_connection()
        
        # Создание проекта если не существует
        conn.execute("INSERT OR IGNORE INTO projects (name) VALUES (?)", (project,))
        
        # Добавление документа
        cursor = conn.execute("""
            INSERT INTO documents (project, section, filename, original_filename, 
                                 file_size, description, version, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (project, section, filename, original_filename, file_size, 
              description, version, 'Активный'))
        
        document_id = cursor.lastrowid
        
        # Добавление в историю версий
        conn.execute("""
            INSERT INTO document_history (document_id, version, filename, comment)
            VALUES (?, ?, ?, ?)
        """, (document_id, version, filename, f"Загружена версия {version}"))
        
        conn.commit()
        conn.close()
        
        flash('Документ успешно загружен', 'success')
        return redirect(url_for('project_detail', project_name=project))
    
    # GET запрос - отображение формы
    conn = get_db_connection()
    projects = conn.execute("SELECT name FROM projects ORDER BY name").fetchall()
    conn.close()
    
    return render_template('upload.html', projects=projects, sections=SECTIONS)

# --- Просмотр документа ---
@app.route('/document/<int:doc_id>')
def document_detail(doc_id):
    conn = get_db_connection()
    
    document = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not document:
        flash('Документ не найден', 'error')
        return redirect(url_for('documents'))
    
    # История версий
    history = conn.execute("""
        SELECT * FROM document_history 
        WHERE document_id = ? 
        ORDER BY upload_date DESC
    """, (doc_id,)).fetchall()
    
    conn.close()
    
    return render_template('document_detail.html', document=document, history=history)

# --- Скачивание файла ---
@app.route('/download/<int:doc_id>')
def download_document(doc_id):
    conn = get_db_connection()
    document = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    
    if not document:
        flash('Документ не найден', 'error')
        return redirect(url_for('documents'))
    
    folder_path = os.path.join(app.config['UPLOAD_FOLDER'], document['project'], document['section'])
    return send_from_directory(folder_path, document['filename'], 
                             as_attachment=True, 
                             download_name=document['original_filename'])

# --- API для статистики ---
@app.route('/api/stats')
def api_stats():
    conn = get_db_connection()
    
    stats = {
        'projects': conn.execute("SELECT COUNT(DISTINCT project) FROM documents").fetchone()[0],
        'documents': conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        'total_size_mb': get_file_size_mb(conn.execute("SELECT COALESCE(SUM(file_size), 0) FROM documents").fetchone()[0])
    }
    
    # Статистика по разделам
    section_stats = conn.execute("""
        SELECT section, COUNT(*) as count 
        FROM documents 
        GROUP BY section 
        ORDER BY count DESC
    """).fetchall()
    
    stats['sections'] = [dict(row) for row in section_stats]
    
    conn.close()
    
    return jsonify(stats)

# --- Удаление документа ---
@app.route('/delete/<int:doc_id>', methods=['POST'])
def delete_document(doc_id):
    conn = get_db_connection()
    
    document = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not document:
        flash('Документ не найден', 'error')
        return redirect(url_for('documents'))
    
    # Удаление файла
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], 
                           document['project'], 
                           document['section'], 
                           document['filename'])
    
    if os.path.exists(filepath):
        os.remove(filepath)
    
    # Удаление из базы данных
    conn.execute("DELETE FROM document_history WHERE document_id = ?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()
    
    flash('Документ успешно удален', 'success')
    return redirect(url_for('documents'))

# --- Фильтр для шаблонов ---
@app.template_filter('file_size_mb')
def file_size_mb_filter(size_bytes):
    if size_bytes is None:
        return 0
    return get_file_size_mb(size_bytes)

if __name__ == '__main__':
    app.run(debug=True)