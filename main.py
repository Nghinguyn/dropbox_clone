from flask import Flask, render_template, request, jsonify, session
from google.cloud import firestore, storage
import firebase_admin
from firebase_admin import auth, credentials
import local_constants

app = Flask(__name__)
app.secret_key = 'dropbox-clone-secret-key'

db = firestore.Client(project=local_constants.PROJECT_NAME)

firebase_admin.initialize_app()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    token = request.json.get('token')
    try:
        decoded = auth.verify_id_token(token)
        uid = decoded['uid']
        email = decoded['email']

        session['uid'] = uid
        session['email'] = email

        user_ref = db.collection('users').document(uid)
        if not user_ref.get().exists:
            user_ref.set({'email': email, 'uid': uid})
            db.collection('directories').add({
                'uid': uid,
                'name': 'root',
                'path': '/',
                'parent_path': None
            })

        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 401

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(debug=True)
