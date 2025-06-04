from flask import Flask, request, redirect, url_for, render_template, flash, send_from_directory
import os
import sqlite3
from werkzeug.utils import secure_filename
from config import UPLOAD_FOLDER, ALLOWED_EXTENSIONS, MAX_CONTENT_LENGTH

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = 'supersecretkey'
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Предопределённые разделы
SECTIONS = ["Спецификации", "Чертежи", "Сборки", "Детали"]

# Создание папки хранения при необходимости
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- Инициализация базы данных ---
def init_db():
    conn = sqlite3.connect('pdm.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    section TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )''')
    conn.commit()
    conn.close()

init_db()

# --- Вспомогательная функция ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Главная страница ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Получение проекта: либо из списка, либо новый
        existing_project = request.form.get('existing_project', '').strip()
        new_project = request.form.get('new_project', '').strip()

        if new_project:
            project = new_project
        elif existing_project:
            project = existing_project
        else:
            flash('Выберите или введите проект')
            return redirect(request.url)

        # Раздел
        section = request.form['section'].strip()
        if section not in SECTIONS:
            flash('Недопустимый раздел')
            return redirect(request.url)

        # Файл
        file = request.files.get('file')
        if not file:
            flash('Файл не выбран')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            folder_path = os.path.join(app.config['UPLOAD_FOLDER'], project, section)
            os.makedirs(folder_path, exist_ok=True)
            filepath = os.path.join(folder_path, filename)
            file.save(filepath)

            # Запись в базу данных (REPLACE — заменяет по уникальности, здесь просто вставка)
            conn = sqlite3.connect('pdm.db')
            c = conn.cursor()
            c.execute("INSERT INTO documents (project, section, filename) VALUES (?, ?, ?)",
                      (project, section, filename))
            conn.commit()
            conn.close()

            flash('Файл успешно загружен')
            return redirect(url_for('index'))

        else:
            flash('Разрешены только PDF файлы')
            return redirect(request.url)

    # --- Обработка GET-запроса (отображение страницы) ---
    selected_project = request.args.get('project_filter', '').strip()

    conn = sqlite3.connect('pdm.db')
    c = conn.cursor()

    # Получить список проектов
    c.execute("SELECT DISTINCT project FROM documents ORDER BY project ASC")
    projects = [row[0] for row in c.fetchall()]

    # Получить список файлов (по фильтру или все)
    if selected_project:
        c.execute("SELECT id, project, section, filename, upload_date FROM documents WHERE project = ? ORDER BY upload_date DESC", (selected_project,))
    else:
        c.execute("SELECT id, project, section, filename, upload_date FROM documents ORDER BY upload_date DESC")
    files = c.fetchall()
    conn.close()

    return render_template('index.html',
                           files=files,
                           projects=projects,
                           sections=SECTIONS,
                           selected_project=selected_project)

# --- Загрузка файла ---
@app.route('/download/<project>/<section>/<filename>')
def download_file(project, section, filename):
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], project, section),
                               filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
