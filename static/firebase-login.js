const firebaseConfig = {
  apiKey: "AIzaSyAY3B6ay0kS_GbpiNSKkwS4or_CknNWyq4",
  authDomain: "project-c5e774e1-67b1-43e3-a83.firebaseapp.com",
  projectId: "project-c5e774e1-67b1-43e3-a83",
  storageBucket: "project-c5e774e1-67b1-43e3-a83.firebasestorage.app",
  messagingSenderId: "1051652579236",
  appId: "1:1051652579236:web:803086146f8d8149aeeb31"
};

firebase.initializeApp(firebaseConfig);

// Helper: make authenticated fetch requests using Firebase token
async function authFetch(url, options = {}) {
    const user = firebase.auth().currentUser;
    if (!user) throw new Error('Not logged in');
    const token = await user.getIdToken();
    options.headers = options.headers || {};
    options.headers['Authorization'] = 'Bearer ' + token;
    options.headers['Content-Type'] = options.headers['Content-Type'] || 'application/json';
    return fetch(url, options);
}

function googleLogin() {
    const provider = new firebase.auth.GoogleAuthProvider();
    firebase.auth().signInWithPopup(provider)
        .catch((error) => console.error('Login error:', error));
}

function googleLogout() {
    firebase.auth().signOut()
        .then(() => fetch('/logout', { method: 'POST' }))
        .then(() => window.location.reload())
        .catch((error) => console.error('Logout error:', error));
}
