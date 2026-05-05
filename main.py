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
gcs = storage.Client(project=local_constants.PROJECT_NAME)
bucket = gcs.bucket(local_constants.BUCKET_NAME)


def verify_firebase_token(token):
    """Verify a Firebase ID token using the Firebase REST API."""
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
    token = auth_header.split('Bearer ')[1]
    return verify_firebase_token(token)


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
    result = [{'id': f.id, 'name': f.to_dict()['name'], 'size': f.to_dict().get('size', 0), 'hash': f.to_dict().get('hash', '')} for f in files]
    return jsonify(result)


@app.route('/files/upload', methods=['POST'])
def upload_file():
    # File uploads use FormData so token comes from form field, not header
    token = request.form.get('token', '')
    uid, _ = verify_firebase_token(token)
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

    # Check if file already exists in this directory
    existing = (db.collection('files')
                .where(filter=FieldFilter('uid', '==', uid))
                .where(filter=FieldFilter('directory_path', '==', current_path))
                .where(filter=FieldFilter('name', '==', filename))
                .get())

    if len(existing) > 0 and not overwrite:
        return jsonify({'error': 'File already exists', 'exists': True}), 409

    # Store in Cloud Storage
    blob_path = f"{uid}{current_path}{filename}"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(file_bytes, content_type=uploaded_file.content_type)

    # Save or update metadata in Firestore
    if len(existing) > 0 and overwrite:
        existing[0].reference.update({
            'size': len(file_bytes),
            'hash': file_hash,
            'blob_path': blob_path
        })
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


@app.route('/files/<file_id>/download', methods=['GET'])
def download_file(file_id):
    # Support token via query param for direct download links
    token = request.args.get('token', '')
    if token:
        uid, _ = verify_firebase_token(token)
    else:
        uid, _ = get_current_user()
    if not uid:
        return jsonify({'error': 'Not logged in'}), 401

    file_ref = db.collection('files').document(file_id)
    file_doc = file_ref.get()

    if not file_doc.exists or file_doc.to_dict()['uid'] != uid:
        return jsonify({'error': 'File not found'}), 404

    file_data = file_doc.to_dict()
    blob = bucket.blob(file_data['blob_path'])
    file_bytes = blob.download_as_bytes()

    return send_file(
        io.BytesIO(file_bytes),
        download_name=file_data['name'],
        as_attachment=True
    )


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


if __name__ == '__main__':
    app.run(debug=True)
