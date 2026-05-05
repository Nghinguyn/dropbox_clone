const firebaseConfig = {
  apiKey: "AIzaSyAY3B6ay0kS_GbpiNSKkwS4or_CknNWyq4",
  authDomain: "project-c5e774e1-67b1-43e3-a83.firebaseapp.com",
  projectId: "project-c5e774e1-67b1-43e3-a83",
  storageBucket: "project-c5e774e1-67b1-43e3-a83.firebasestorage.app",
  messagingSenderId: "1051652579236",
  appId: "1:1051652579236:web:803086146f8d8149aeeb31"
};

firebase.initializeApp(firebaseConfig);

// Helper: make authenticated fetch using the provided user (avoids currentUser timing issues)
async function authFetch(url, options = {}, user = null) {
    const authUser = user || firebase.auth().currentUser;
    if (!authUser) throw new Error('Not logged in');
    const token = await authUser.getIdToken();
    options.headers = options.headers || {};
    options.headers['Authorization'] = 'Bearer ' + token;
    if (!options.headers['Content-Type'] && !(options.body instanceof FormData)) {
        options.headers['Content-Type'] = 'application/json';
    }
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
