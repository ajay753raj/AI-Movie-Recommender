from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os

app = Flask(__name__)
app.secret_key = 'cinematch_super_secret_key_change_me_in_production'

# Database configuration and setup
DATABASE = os.path.join(os.path.dirname(__file__), 'users.db')

def get_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                movie_title TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        conn.commit()

# Run database setup on startup
init_db()

# Load dataset
data_path = os.path.join(os.path.dirname(__file__), 'movies.csv')
try:
    movies_df = pd.read_csv(data_path)
    # Combine genres and overview for content-based filtering
    movies_df['combined_features'] = movies_df['Genre'].fillna('') + " " + movies_df['Overview'].fillna('')
    
    # Initialize TF-IDF Vectorizer
    tfidf = TfidfVectorizer(stop_words='english')
    
    # Fit and transform the text into numerical vectors
    tfidf_matrix = tfidf.fit_transform(movies_df['combined_features'])
    
    # Calculate cosine similarity matrix
    cosine_sim = cosine_similarity(tfidf_matrix, tfidf_matrix)
except Exception as e:
    print(f"Error loading data: {e}")
    movies_df = pd.DataFrame(columns=['Title', 'Genre', 'Overview'])
    cosine_sim = []

def get_recommendations(title, cosine_sim=cosine_sim):
    try:
        # Get the index of the movie that matches the title
        idx = movies_df[movies_df['Title'] == title].index[0]

        # Get the pairwise similarity scores of all movies with that movie
        sim_scores = list(enumerate(cosine_sim[idx]))

        # Sort the movies based on the similarity scores
        sim_scores = sorted(sim_scores, key=lambda x: x[1], reverse=True)

        # Get the scores of the 5 most similar movies (ignoring the first one which is the movie itself)
        sim_scores = sim_scores[1:6]

        # Get the movie indices
        movie_indices = [i[0] for i in sim_scores]

        # Return the top 5 most similar movies
        return movies_df.iloc[movie_indices][['Title', 'Genre']].to_dict('records')
    except Exception as e:
        print(f"Error getting recommendations: {e}")
        return []

def get_user_history_list(user_id):
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            'SELECT movie_title FROM search_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10',
            (user_id,)
        )
        return [row['movie_title'] for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error getting history list: {e}")
        return []

@app.route('/')
def home():
    if 'user_id' not in session:
        return render_template('landing.html')
    
    # User is logged in, fetch their search history
    user_id = session['user_id']
    history = get_user_history_list(user_id)
    movie_list = sorted(movies_df['Title'].tolist())
    return render_template('index.html', movie_list=movie_list, username=session.get('username'), history=history)

@app.route('/auth')
def auth():
    if 'user_id' in session:
        return redirect(url_for('home'))
    default_tab = request.args.get('tab', 'login')
    return render_template('auth.html', default_tab=default_tab)

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    
    if not username or not password:
        flash('Username and password are required.', 'error')
        return redirect(url_for('auth', tab='login'))
        
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        
        if user and check_password_hash(user['password'], password):
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('home'))
        else:
            flash('Invalid username or password.', 'error')
            return redirect(url_for('auth', tab='login'))
    except Exception as e:
        print(f"Login error: {e}")
        flash('An error occurred during sign in. Please try again.', 'error')
        return redirect(url_for('auth', tab='login'))

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    
    if not username or not password:
        flash('Username and password are required.', 'error')
        return redirect(url_for('auth', tab='register'))
        
    if len(password) < 6:
        flash('Password must be at least 6 characters long.', 'error')
        return redirect(url_for('auth', tab='register'))
        
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            flash('Username is already taken.', 'error')
            return redirect(url_for('auth', tab='register'))
            
        hashed_password = generate_password_hash(password)
        cursor.execute(
            'INSERT INTO users (username, password) VALUES (?, ?)',
            (username, hashed_password)
        )
        db.commit()
        
        # Automatically log in the user after registration
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        session.clear()
        session['user_id'] = user['id']
        session['username'] = user['username']
        return redirect(url_for('home'))
        
    except Exception as e:
        print(f"Registration error: {e}")
        flash('An error occurred during registration. Please try again.', 'error')
        return redirect(url_for('auth', tab='register'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/recommend', methods=['POST'])
def recommend():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized. Please log in first.'}), 401
        
    selected_movie = request.form.get('movie')
    if not selected_movie:
        return jsonify({'error': 'No movie selected'}), 400
        
    recommendations = get_recommendations(selected_movie)
    
    if recommendations:
        try:
            db = get_db()
            cursor = db.cursor()
            # Remove previous search for this movie to keep it at the top of history
            cursor.execute(
                'DELETE FROM search_history WHERE user_id = ? AND movie_title = ?',
                (session['user_id'], selected_movie)
            )
            cursor.execute(
                'INSERT INTO search_history (user_id, movie_title) VALUES (?, ?)',
                (session['user_id'], selected_movie)
            )
            db.commit()
        except Exception as e:
            print(f"Error saving search history: {e}")
            
    return jsonify({
        'recommendations': recommendations,
        'history': get_user_history_list(session['user_id'])
    })

if __name__ == '__main__':
    app.run(debug=True)

