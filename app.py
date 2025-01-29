from flask import Flask, render_template
import sqlite3

app = Flask(__name__)

# Подключение к базе данных
conn = sqlite3.connect('space_world.db', check_same_thread=False)
cursor = conn.cursor()

@app.route('/')
def show_space():
    cursor.execute("SELECT planets.*, users.username FROM planets JOIN users ON planets.user_id = users.user_id")
    planets = cursor.fetchall()
    return render_template('index.html', planets=planets)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))