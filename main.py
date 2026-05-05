from flask import Flask, render_template, request, jsonify
from google.cloud import firestore
from google.cloud.firestore import FieldFilter
import firebase_admin
from firebase_admin import auth
import local_constants

app = Flask(__name__)

db = firestore.Client(project=local_constants.PROJECT_NAME, database='dropbox-db')

firebase_admin.initialize_app()


def get_current_user():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None, None
    token = auth_header.split('Bearer ')[1]
    try:
        decoded = auth.verify_id_token(token)
        return decoded['uid'], decoded.get('email', '')
    except Exception:
        return None, None


def ensure_user_exists(uid, email):
    user_ref = db.collection('users').document(uid)
    if not user_ref.get().exists:
        user_ref.set({'email': email, 'uid': uid})
        db.collection('directories').add({
            'uid': uid,
            'name': 'root',
            'path': '/',
            'parent_path': None
        })


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['POST'])
def login():
    uid, email = get_current_user()
    if not uid:
        return jsonify({'error': 'Invalid token'}), 401
    ensure_user_exists(uid, email)
    return jsonify({'status': 'ok'})


@app.route('/logout', methods=['POST'])
def logout():
    return jsonify({'status': 'ok'})


@app.route('/directories', methods=['GET'])
def list_directories():
    uid, _ = get_current_user()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    current_path = request.args.get('path', '/')
    dirs = (db.collection('directories')
            .where(filter=FieldFilter('uid', '==', uid))
            .where(filter=FieldFilter('parent_path', '==', current_path))
            .stream())
    result = [{'id': d.id, 'name': d.to_dict()['name'], 'path': d.to_dict()['path']} for d in dirs]
    return jsonify(result)


@app.route('/directories', methods=['POST'])
def create_directory():
    uid, _ = get_current_user()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    data = request.json
    name = data.get('name', '').strip()
    current_path = data.get('current_path', '/')

    if not name:
        return jsonify({'error': 'Directory name is required'}), 400

    new_path = '/' + name + '/' if current_path == '/' else current_path + name + '/'

    existing = (db.collection('directories')
                .where(filter=FieldFilter('uid', '==', uid))
                .where(filter=FieldFilter('parent_path', '==', current_path))
                .where(filter=FieldFilter('name', '==', name))
                .get())
    if len(existing) > 0:
        return jsonify({'error': 'A directory with that name already exists here'}), 400

    db.collection('directories').add({
        'uid': uid,
        'name': name,
        'path': new_path,
        'parent_path': current_path
    })
    return jsonify({'status': 'ok', 'path': new_path})


@app.route('/directories/<dir_id>', methods=['DELETE'])
def delete_directory(dir_id):
    uid, _ = get_current_user()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    dir_ref = db.collection('directories').document(dir_id)
    dir_doc = dir_ref.get()

    if not dir_doc.exists or dir_doc.to_dict()['uid'] != uid:
        return jsonify({'error': 'Directory not found'}), 404

    dir_path = dir_doc.to_dict()['path']

    if dir_path == '/':
        return jsonify({'error': 'Cannot delete root directory'}), 400

    subdirs = (db.collection('directories')
               .where(filter=FieldFilter('uid', '==', uid))
               .where(filter=FieldFilter('parent_path', '==', dir_path))
               .get())
    if len(subdirs) > 0:
        return jsonify({'error': 'Directory is not empty (contains subdirectories)'}), 400

    files = (db.collection('files')
             .where(filter=FieldFilter('uid', '==', uid))
             .where(filter=FieldFilter('directory_path', '==', dir_path))
             .get())
    if len(files) > 0:
        return jsonify({'error': 'Directory is not empty (contains files)'}), 400

    dir_ref.delete()
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    app.run(debug=True)
