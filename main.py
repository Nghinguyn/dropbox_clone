from flask import Flask, render_template, request, jsonify, send_file
from google.cloud import firestore, storage
from google.cloud.firestore import FieldFilter
import local_constants
import hashlib
import io
import urllib.request
import json

app = Flask(__name__)

db = firestore.Client(project=local_constants.PROJECT_NAME, database='dropbox-db')
bucket = storage.Client(project=local_constants.PROJECT_NAME).bucket(local_constants.BUCKET_NAME)


def verify_firebase_token(token):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={local_constants.FIREBASE_API_KEY}"
    data = json.dumps({"idToken": token}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
            user = result['users'][0]
            return user['localId'], user.get('email', '')
    except Exception:
        return None, None


def get_current_user():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None, None
    return verify_firebase_token(auth_header.split('Bearer ')[1])


def resolve_uid():
    """Resolve uid from query param token or Authorization header."""
    token = request.args.get('token', '')
    if token:
        uid, _ = verify_firebase_token(token)
        return uid
    uid, _ = get_current_user()
    return uid


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


def compute_hash(file_bytes):
    return hashlib.md5(file_bytes).hexdigest()


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
    return jsonify([{'id': d.id, 'name': d.to_dict()['name'], 'path': d.to_dict()['path']} for d in dirs])


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
    if existing:
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
    if subdirs:
        return jsonify({'error': 'Directory is not empty (contains subdirectories)'}), 400

    files = (db.collection('files')
             .where(filter=FieldFilter('uid', '==', uid))
             .where(filter=FieldFilter('directory_path', '==', dir_path))
             .get())
    if files:
        return jsonify({'error': 'Directory is not empty (contains files)'}), 400

    dir_ref.delete()
    return jsonify({'status': 'ok'})


@app.route('/files', methods=['GET'])
def list_files():
    uid, _ = get_current_user()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    current_path = request.args.get('path', '/')
    files = (db.collection('files')
             .where(filter=FieldFilter('uid', '==', uid))
             .where(filter=FieldFilter('directory_path', '==', current_path))
             .stream())
    result = []
    for f in files:
        d = f.to_dict()
        result.append({'id': f.id, 'name': d['name'], 'size': d.get('size', 0), 'hash': d.get('hash', '')})
    return jsonify(result)


@app.route('/files/upload', methods=['POST'])
def upload_file():
    # File uploads use FormData so token comes from form field, not header
    uid, _ = verify_firebase_token(request.form.get('token', ''))
    if not uid:
        return jsonify({'error': 'Invalid token'}), 401

    current_path = request.form.get('current_path', '/')
    overwrite = request.form.get('overwrite', 'false') == 'true'
    uploaded_file = request.files.get('file')

    if not uploaded_file:
        return jsonify({'error': 'No file provided'}), 400

    filename = uploaded_file.filename
    file_bytes = uploaded_file.read()
    file_hash = compute_hash(file_bytes)

    existing = (db.collection('files')
                .where(filter=FieldFilter('uid', '==', uid))
                .where(filter=FieldFilter('directory_path', '==', current_path))
                .where(filter=FieldFilter('name', '==', filename))
                .get())

    if existing and not overwrite:
        return jsonify({'error': 'File already exists', 'exists': True}), 409

    blob_path = f"{uid}{current_path}{filename}"
    bucket.blob(blob_path).upload_from_string(file_bytes, content_type=uploaded_file.content_type)

    if existing and overwrite:
        existing[0].reference.update({'size': len(file_bytes), 'hash': file_hash, 'blob_path': blob_path})
    else:
        db.collection('files').add({
            'uid': uid,
            'name': filename,
            'directory_path': current_path,
            'blob_path': blob_path,
            'size': len(file_bytes),
            'hash': file_hash
        })

    return jsonify({'status': 'ok'})


def send_blob(file_doc):
    """Stream a Firestore file document's blob to the client."""
    data = file_doc.to_dict()
    file_bytes = bucket.blob(data['blob_path']).download_as_bytes()
    return send_file(io.BytesIO(file_bytes), download_name=data['name'], as_attachment=True)


@app.route('/files/<file_id>/download', methods=['GET'])
def download_file(file_id):
    uid = resolve_uid()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    file_doc = db.collection('files').document(file_id).get()
    if not file_doc.exists or file_doc.to_dict()['uid'] != uid:
        return jsonify({'error': 'File not found'}), 404

    return send_blob(file_doc)


@app.route('/files/<file_id>', methods=['DELETE'])
def delete_file(file_id):
    uid, _ = get_current_user()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    file_ref = db.collection('files').document(file_id)
    file_doc = file_ref.get()

    if not file_doc.exists or file_doc.to_dict()['uid'] != uid:
        return jsonify({'error': 'File not found'}), 404

    blob = bucket.blob(file_doc.to_dict()['blob_path'])
    if blob.exists():
        blob.delete()

    file_ref.delete()
    return jsonify({'status': 'ok'})


@app.route('/files/duplicates', methods=['GET'])
def find_duplicates():
    uid, _ = get_current_user()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    hash_map = {}
    for f in db.collection('files').where(filter=FieldFilter('uid', '==', uid)).stream():
        d = f.to_dict()
        h = d.get('hash', '')
        if h:
            hash_map.setdefault(h, []).append({
                'id': f.id,
                'name': d['name'],
                'path': d['directory_path'],
                'size': d.get('size', 0)
            })

    return jsonify([group for group in hash_map.values() if len(group) > 1])


@app.route('/files/<file_id>/share', methods=['POST'])
def share_file(file_id):
    uid, _ = get_current_user()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    file_ref = db.collection('files').document(file_id)
    file_doc = file_ref.get()

    if not file_doc.exists or file_doc.to_dict()['uid'] != uid:
        return jsonify({'error': 'File not found'}), 404

    target_email = request.json.get('email', '').strip().lower()
    if not target_email:
        return jsonify({'error': 'Email is required'}), 400

    target_users = db.collection('users').where(filter=FieldFilter('email', '==', target_email)).get()
    if not target_users:
        return jsonify({'error': 'No user found with that email'}), 404

    target_uid = target_users[0].to_dict()['uid']
    if target_uid == uid:
        return jsonify({'error': 'Cannot share with yourself'}), 400

    shared_with = file_doc.to_dict().get('shared_with', [])
    if target_uid not in shared_with:
        shared_with.append(target_uid)
        file_ref.update({'shared_with': shared_with})

    return jsonify({'status': 'ok'})


@app.route('/shared', methods=['GET'])
def list_shared_files():
    uid, _ = get_current_user()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    result = []
    for f in db.collection('files').where(filter=FieldFilter('shared_with', 'array_contains', uid)).stream():
        d = f.to_dict()
        owner = db.collection('users').document(d['uid']).get()
        owner_email = owner.to_dict().get('email', 'Unknown') if owner.exists else 'Unknown'
        result.append({
            'id': f.id,
            'name': d['name'],
            'path': d['directory_path'],
            'owner': owner_email,
            'size': d.get('size', 0)
        })
    return jsonify(result)


@app.route('/files/<file_id>/download/shared', methods=['GET'])
def download_shared_file(file_id):
    uid = resolve_uid()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    file_doc = db.collection('files').document(file_id).get()
    if not file_doc.exists:
        return jsonify({'error': 'File not found'}), 404

    d = file_doc.to_dict()
    if d['uid'] != uid and uid not in d.get('shared_with', []):
        return jsonify({'error': 'Access denied'}), 403

    return send_blob(file_doc)


if __name__ == '__main__':
    app.run(debug=True)
