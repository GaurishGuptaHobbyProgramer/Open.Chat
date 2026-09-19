const randomID = Math.floor(Math.random() * 9000) + 1000;
const username = "Anonymous " + randomID;
const socket = io();

const messageInput = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const chatBox = document.getElementById("chat-box");
const onlineUsers = document.getElementById("online-users");
const themeToggle = document.getElementById('theme-toggle');

function applyTheme(theme){
    document.documentElement.classList.remove('light-theme','dark-theme');
    document.documentElement.classList.add(theme === 'light' ? 'light-theme' : 'dark-theme');
    if(themeToggle) themeToggle.setAttribute('aria-pressed', theme === 'light' ? 'true' : 'false');
}

// Initialize theme from localStorage or system preference
const saved = localStorage.getItem('openchat-theme');
if(saved){
    applyTheme(saved);
} else {
    const prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
    applyTheme(prefersLight ? 'light' : 'dark');
}

if(themeToggle){
    themeToggle.addEventListener('click', ()=>{
        const isLight = document.documentElement.classList.contains('light-theme');
        const next = isLight ? 'dark' : 'light';
        applyTheme(next);
        localStorage.setItem('openchat-theme', next);
    });
}

function escapeHTML(str){
    if(!str) return "";
    return str.replace(/[&<>"']/g, function(tag){
        const chars = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"};
        return chars[tag] || tag;
    });
}

sendButton.addEventListener("click", function () {
    const message = messageInput.value;

    if (message.trim() === "") return;

    socket.send({ username: username, message: message });
    messageInput.value = "";
});

messageInput.addEventListener("keypress", function(event) {
    if (event.key === "Enter") {
        event.preventDefault();
        sendButton.click();
    }
});

socket.on("message", function(data) {
    const isOwn = data.username === username;
    const initials = (data.username || "?").split(' ').slice(-1)[0].slice(0,2).toUpperCase();
    const msgHTML = `
        <div class="message ${isOwn ? 'own' : ''}">
            <div class="avatar">${initials}</div>
            <div class="message-content">
                <div class="message-header">
                    <b>${escapeHTML(data.username)}</b>
                    <span>${escapeHTML(data.time)}</span>
                </div>
                <div class="message-body">${escapeHTML(data.message)}</div>
            </div>
        </div>
    `;

    chatBox.insertAdjacentHTML('beforeend', msgHTML);
    chatBox.scrollTop = chatBox.scrollHeight;
});

socket.on("online_users", function(count) {
    if (count === 1) {
        onlineUsers.innerHTML = "🟢 1 User Online";
    } else {
        onlineUsers.innerHTML = "🟢 " + count + " Users Online";
    }
});

